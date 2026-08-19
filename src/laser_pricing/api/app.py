"""שכבת ה-API של מנוע התמחור.

שלושה סוגי קלט נשפכים לאותו טיפוס `PartGeometry`, ולכן לתמחור אין
מושג מאיפה הגיע החלק: העלאת DXF, קלט ידני, ובהמשך מודל שנבנה בממשק.

הערה על מצב: הגיאומטריות יושבות בזיכרון התהליך בין ההעלאה לתמחור.
זה מספיק לשרת יחיד; אם יומיום יעבור למספר מכונות, זה המקום שיצטרך
אחסון משותף — ולא שום דבר אחר.
"""

from __future__ import annotations

import base64
import hmac
import json
import math
import os
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..cad.base import CadReadError
from ..cad.dxf_reader import read_dxf
from ..domain.geometry import Contour, PartGeometry, circle, rectangle
from ..domain.part import Part, PartSource
from ..nesting.plate import check_manufacturability
from ..nesting.splitting import plan_split
from ..pricing.engine import PricingError, price_order
from ..pricing.tariff import InvalidTariffError, MissingTariffError
from . import identity
from .serialize import geometry_to_json, quote_to_json
from .simple_tariff import MONEY_FIELDS, apply_form, to_form
from .store import STORE, GeometryExpired, StoredGeometry
from .tariff_store import STATE

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
WEB_DIR = Path(__file__).resolve().parents[3] / "web"

app = FastAPI(
    title="מנוע תמחור לייזר",
    description="תמחור חיתוך לייזר לפי טבלת התמחור של ינון.",
    version="0.2.0",
)


# ---- נעילה ----

OPEN_PATHS = frozenset({"/health", "/login", "/api/login"})
"""מה שנפתח בלי זהות. `/login` ו-`/api/login` הם המסך שבו משיגים זהות,
ו-`/health` הוא בדיקת הפלטפורמה — ולכן שלושתם מחזירים בוליאנים ומסכים
בלבד, ולעולם לא מחירים."""

EDITOR_PATHS = ("/prices", "/api/prices")
"""המסלולים היחידים ש-EDITOR_PASSWORD פותח.

הפרדת המפתחות היא העיקר כאן. אבא של ינון צריך סיסמה שאפשר להכתיב
בטלפון, אבל אותה סיסמה על *כל* המערכת הייתה נותנת לכל אחד גם לתמחר,
גם לראות הצעות וגם להחליף את הטבלה כולה דרך עורך ה-JSON. לכן מפתח
העריכה פותח אך ורק את טופס המחירים, ומפתח המערכת נשאר נפרד וחזק.
"""


def _is_editor_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in EDITOR_PATHS)


def _editor_key(request: Request) -> str:
    """המפתח מגיע מהלינק עצמו או מכותרת, כדי שלינק בודד יספיק."""
    return request.headers.get("x-editor-key") or request.query_params.get("k", "")


PRICE_TABLE_PATHS = ("/prices", "/api/prices", "/api/tariff")
"""מה שדורש `prices:edit`: טופס אבא ועורך ה-JSON הגולמי.

עורך ה-JSON נכנס לרשימה הזאת ולא לרשימת המשתמשים הרגילים, כי `PUT`
עליו מחליף את הטבלה **כולה** — כולל הכיול שאף אחד לא רואה בטופס.
"""


def _required_capability(path: str) -> str:
    if any(path == p or path.startswith(p + "/") for p in PRICE_TABLE_PATHS):
        return identity.CAP_PRICES_EDIT
    return identity.CAP_QUOTE_USE


def _gate_is_on() -> bool:
    """השער פעיל אם יש סיסמת מערכת או אם קיימים משתמשים.

    בפיתוח מקומי אין לא זה ולא זה, והשער כבוי. **בפרודקשן זה בדיוק
    המקום שדפק ב-18.8**: המשתנים לא היו ביחידה, ה-middleware ויתר על
    השער בשקט, והשרת היה פתוח לכתיבה לכל האינטרנט. לכן היום זה גם
    מדווח ב-`/health`, ולא רק מתרחש.
    """
    if os.environ.get("APP_PASSWORD", ""):
        return True
    try:
        return identity.user_count() > 0
    except sqlite3.Error:
        # מסד פגום לא יפתח את השער. כישלון סגור, לא כישלון פתוח.
        return True


def _wants_html(request: Request) -> bool:
    return request.method == "GET" and "text/html" in request.headers.get("accept", "")


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """מי אתה, ומה מותר לך.

    כל הזהות מגיעה מ-`identity.current_user` — סשן, Basic, או טוקן
    שירות של ה-CRM. הקובץ הזה מחליט רק *מה נדרש לאיזה מסלול*, ולא
    מאיפה מגיעה הזהות; החלפת מקור הזהות ל-CRM לא נוגעת כאן.
    """
    path = request.url.path
    if path in OPEN_PATHS or not _gate_is_on():
        return await call_next(request)

    user = identity.current_user(request)

    # הקישור של אבא: מפתח מוגבל למסלול, לא אדם. נשאר בתוקף כדי שלא
    # ניקח ממנו את הטופס באמצע המילוי.
    if user is None and _is_editor_path(path):
        editor_password = os.environ.get("EDITOR_PASSWORD", "")
        if editor_password and hmac.compare_digest(_editor_key(request), editor_password):
            user = identity.EDITOR_LINK_USER

    if user is None:
        if _is_editor_path(path):
            # לאבא אין שם משתמש, ומסך כניסה רק יבלבל אותו.
            return Response(
                status_code=401,
                content="הקישור אינו תקין או שפג תוקפו. בקש מינון קישור חדש.",
                media_type="text/plain; charset=utf-8",
            )
        if _wants_html(request):
            # דפדפן מקבל מסך כניסה; curl וכלים מקבלים 401 כמו תמיד.
            return RedirectResponse(f"/login?next={quote_plus(path)}", status_code=303)
        return Response(status_code=401, content="נדרשת התחברות")

    required = _required_capability(path)
    if not user.can(required):
        return Response(
            status_code=403,
            content=f"אין לך הרשאה ל-{required}. בקש מינון.",
            media_type="text/plain; charset=utf-8",
        )

    request.state.user = user
    return await call_next(request)


# ---- מודלים של הבקשות ----


class HoleSpec(BaseModel):
    diameter_mm: float = Field(gt=0)
    x_mm: float | None = None
    y_mm: float | None = None


class ManualPartSpec(BaseModel):
    name: str = "חלק ידני"
    shape: Literal["rect", "circle", "polygon"] = "rect"
    width_mm: float | None = Field(default=None, gt=0)
    height_mm: float | None = Field(default=None, gt=0)
    diameter_mm: float | None = Field(default=None, gt=0)
    points: list[tuple[float, float]] | None = None
    holes: list[HoleSpec] = Field(default_factory=list)


class PartRequest(BaseModel):
    geometry_id: str
    name: str = ""
    material_key: str
    thickness_mm: float = Field(gt=0)
    quantity: int = Field(default=1, ge=1)
    bend_count: int = Field(default=0, ge=0)
    """כמה כיפופים — הצהרה של המזמין. DXF שטוח לא יודע שהוא יתכופף."""
    bend_length_mm: float = Field(default=0.0, ge=0)


class QuoteRequest(BaseModel):
    parts: list[PartRequest] = Field(min_length=1)


# ---- מידע כללי ----


@app.get("/health")
def health() -> dict:
    """מצב הטבלה — כולל השאלה אם מה שבזיכרון קיים גם על הדיסק.

    שני כללים קובעים את הצורה של התשובה הזאת:

    1. **תמיד 200.** הנתיב הזה הוא `healthCheckPath` של הפלטפורמה.
       פלטפורמה שרואה "לא בריא" מפעילה מחדש — וההפעלה מחדש היא בדיוק
       מה שמוחק את הטבלה מהזיכרון, כלומר את העותק היחיד ששרד במקרה
       שהקובץ נעלם. בדיקת בריאות שמגיבה לאובדן בהריגת הניצול האחרון
       היא לא הגנה אלא הדק. הסטייה מדווחת ב-`warnings`, לא בקוד HTTP.

    2. **בלי אישור, ולכן בלי תוכן.** `/health` פתוח לכל האינטרנט
       (`OPEN_PATHS`), ולכן יוצאים מכאן בוליאנים, ספירות ומחרוזת
       המקור בלבד — לא שמות חומרים, לא עוביים ולא מחירים. גם טקסט
       שגיאת הטעינה נשאר בפנים, כי הוא מצטט שורות מהטבלה.
    """
    disk_present, disk_matches = STATE.disk_state()
    rates = list(STATE.tariff.rates.values()) if STATE.tariff else []
    ready = STATE.is_ready

    warnings: list[str] = []
    if STATE.error:
        warnings.append("טעינת הטבלה נכשלה — ראה /api/config עם התחברות.")
    if not ready:
        warnings.append("הטבלה עדיין ריקה ממחירים — כל סכום ייצא 0.")
    # TARIFF_JSON מגיע מהסביבה ושורד הפעלה מחדש בעצמו, ולכן מצב הדיסק
    # שם אינו מעיד על סכנה. האזהרה נכונה רק כשהדיסק הוא מה שאמור לשרוד.
    if ready and STATE.origin != "TARIFF_JSON":
        if not disk_present:
            warnings.append("הטבלה קיימת בזיכרון בלבד — הפעלה מחדש תמחק אותה.")
        elif not disk_matches:
            warnings.append("הטבלה בזיכרון שונה מזו שעל הדיסק — הפעלה מחדש תחזיר את גרסת הדיסק.")

    gate_on = _gate_is_on()
    if not gate_on:
        # זו התאונה של 18.8 בדיוק. אם היא תחזור — שתצעק, ולא בשקט.
        warnings.append("השער כבוי — הכתובת פתוחה לקריאה ולכתיבה לכל אחד.")
    if gate_on and identity.SESSION_SECRET_IS_EPHEMERAL:
        warnings.append("אין SESSION_SECRET — כל הפעלה מחדש תנתק את המשתמשים.")

    return {
        "status": "ok",
        "gate_on": gate_on,
        "tariff_ready": ready,
        "tariff_source": STATE.origin,
        "tariff_error": bool(STATE.error),
        "rate_rows": len(rates),
        "priced_rows": sum(1 for r in rates if r.plate_price > 0 or r.cut_rate_per_m > 0),
        "materials_count": len({r.material_key for r in rates}),
        "disk_present": disk_present,
        "disk_matches_memory": disk_matches,
        "warnings": warnings,
    }


# ---- כניסה ----


class LoginRequest(BaseModel):
    username: str
    password: str
    next: str = "/"


_LOGIN_FAILURES: dict[str, list[float]] = {}
_MAX_FAILURES = 8
_FAILURE_WINDOW_SECONDS = 600


def _throttled(key: str) -> bool:
    """חסימה פשוטה בזיכרון על ניסיונות כושלים.

    מסך כניסה פתוח לאינטרנט בלי הגבלת קצב הוא הזמנה לניחוש סיסמאות.
    בזיכרון התהליך ולא במסד — ארבעה משתמשים ותהליך אחד, וזה מספיק.
    """
    now = time.time()
    recent = [t for t in _LOGIN_FAILURES.get(key, []) if now - t < _FAILURE_WINDOW_SECONDS]
    _LOGIN_FAILURES[key] = recent
    return len(recent) >= _MAX_FAILURES


def _record_failure(key: str) -> None:
    _LOGIN_FAILURES.setdefault(key, []).append(time.time())


@app.post("/api/login")
def login(body: LoginRequest, request: Request) -> JSONResponse:
    client = request.client.host if request.client else "?"
    key = f"{client}|{body.username.strip().lower()}"
    if _throttled(key):
        raise HTTPException(status_code=429, detail="יותר מדי ניסיונות. נסה שוב בעוד כמה דקות.")

    user = identity.authenticate(body.username, body.password)
    if user is None:
        _record_failure(key)
        # אותה הודעה לשם לא קיים ולסיסמה שגויה — אחרת הטופס מדליף
        # אילו שמות משתמש קיימים במערכת.
        raise HTTPException(status_code=401, detail="שם משתמש או סיסמה שגויים.")

    _LOGIN_FAILURES.pop(key, None)

    # מי שיש לו רק prices:edit יקבל 403 על המנוע, והמסך הראשי ייפתח
    # על שגיאה. אבא ממלא מחירים — שיגיע לטופס ולא לדף שבור.
    target = body.next if body.next.startswith("/") else "/"
    if target == "/" and not user.can(identity.CAP_QUOTE_USE):
        target = "/prices"

    response = JSONResponse(
        {
            "username": user.username,
            "display_name": user.display_name,
            "capabilities": sorted(user.capabilities),
            "next": target,
        }
    )
    response.set_cookie(
        identity.SESSION_COOKIE,
        identity.issue_session(user.username),
        max_age=identity.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        # Secure רק כשבאמת ב-HTTPS: אחרת פיתוח מקומי על http לא יעבוד
        # והמפתח יכבה את הדגל לגמרי — וזה נגמר בפרודקשן בלי Secure.
        secure=request.url.scheme == "https",
        path="/",
    )
    return response


@app.post("/api/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(identity.SESSION_COOKIE, path="/")
    return response


@app.get("/api/me")
def me(request: Request) -> dict:
    """מי מחובר עכשיו. הממשק מציג את זה בכותרת."""
    user = getattr(request.state, "user", None)
    if user is None:
        return {"authenticated": False, "gate": _gate_is_on()}
    return {
        "authenticated": True,
        "username": user.username,
        "display_name": user.display_name,
        "capabilities": sorted(user.capabilities),
        "source": user.source,
    }


# ---- מצב המערכת ----

BACKUP_STATUS_PATH = Path(
    os.environ.get("BACKUP_STATUS_FILE", Path(__file__).resolve().parents[3] / "config" / "backup-status.json")
)
BACKUP_STALE_AFTER_MINUTES = 180
"""הטיימר שעתי. שלוש שעות בלי ריצה זו תקלה ולא איחור."""


def _backup_state() -> dict:
    """מתי גובה לאחרונה — מתוך קובץ המצב שהסקריפט מדווח.

    השירות אינו יכול לקרוא את `/var/backups/laser-tariff` (700 root),
    וזה מכוון: תהליך שמגיש דפים לאינטרנט לא צריך גישת קריאה לכל
    הגרסאות של טבלת המחירים. לכן הוא נשען על חותמות זמן בלבד.
    """
    try:
        raw = json.loads(BACKUP_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"reporting": False, "stale": True, "minutes_since_run": None}

    minutes = None
    try:
        last = datetime.strptime(raw["last_run"], "%Y-%m-%dT%H:%M:%S")
        minutes = max(0, int((datetime.now() - last).total_seconds() // 60))
    except (KeyError, TypeError, ValueError):
        pass

    return {
        "reporting": True,
        "last_run": raw.get("last_run"),
        "ok": bool(raw.get("ok", False)),
        "minutes_since_run": minutes,
        "stale": minutes is None or minutes > BACKUP_STALE_AFTER_MINUTES,
        "tariff": raw.get("tariff", {}),
        "users": raw.get("users", {}),
    }


def _table_coverage() -> dict:
    """כמה מהטבלה באמת מלא, ואיפה חסר.

    זה הלב של המסך התפעולי: "הטבלה ריקה" הוא מצב שאפשר לעבוד איתו,
    אבל רק אם רואים בדיוק **מה** ריק. הספירה היא על תאים כספיים —
    שורה × שדות מחיר — ולא על שורות, כי שורה עם מחיר פלטה בלבד עדיין
    תייצר הצעה חלקית.
    """
    fields = [f for f, _ in MONEY_FIELDS]
    materials: dict[str, dict] = {}
    filled_cells = 0
    total_cells = 0

    for row in STATE.raw.get("rates", []):
        key = row.get("material_key", "")
        entry = materials.setdefault(
            key,
            {
                "key": key,
                "name": row.get("material_name", key),
                "rows": 0,
                "priced_rows": 0,
                "filled_cells": 0,
                "total_cells": 0,
                "empty_fields": {f: 0 for f in fields},
            },
        )
        entry["rows"] += 1
        row_has_price = False
        for field in fields:
            total_cells += 1
            entry["total_cells"] += 1
            try:
                value = float(row.get(field, 0) or 0)
            except (TypeError, ValueError):
                value = 0.0
            if value > 0:
                filled_cells += 1
                entry["filled_cells"] += 1
                if field in ("plate_price", "cut_rate_per_m"):
                    row_has_price = True
            else:
                entry["empty_fields"][field] += 1
        if row_has_price:
            entry["priced_rows"] += 1

    return {
        "materials": sorted(materials.values(), key=lambda m: str(m["name"])),
        "filled_cells": filled_cells,
        "total_cells": total_cells,
        "field_labels": [{"field": f, "label": lbl} for f, lbl in MONEY_FIELDS],
    }


@app.get("/api/dashboard")
def dashboard() -> dict:
    """המסך התפעולי: מה מלא, מה בריא, ומתי גובה לאחרונה.

    **לא מסך מכירות.** היסטוריית ההצעות יושבת ב-CRM לפי קו הגבול
    שסוכם, והמנוע אינו שומר הצעות כלל.
    """
    disk_present, disk_matches = STATE.disk_state()
    coverage = _table_coverage()
    return {
        "tariff": {
            "ready": STATE.is_ready,
            "source": STATE.origin,
            "error": STATE.error,
            **coverage,
        },
        "engine": {
            "disk_present": disk_present,
            "disk_matches_memory": disk_matches,
            "gate_on": _gate_is_on(),
            "session_secret_ephemeral": identity.SESSION_SECRET_IS_EPHEMERAL,
        },
        "backup": _backup_state(),
        "users": {"count": identity.user_count()},
    }


@app.get("/api/config")
def config() -> dict:
    """כל מה שהממשק צריך כדי להיפתח: פלטה, חומרים ומצב הטבלה."""
    tariff = STATE.tariff
    if tariff is None:
        return {
            "tariff_ready": False,
            "tariff_error": STATE.error,
            "tariff_source": STATE.origin,
            "materials": [],
        }
    plate = tariff.plate
    return {
        "tariff_ready": STATE.is_ready,
        "tariff_error": STATE.error,
        "tariff_source": STATE.origin,
        "currency": tariff.currency,
        "vat_pct": tariff.vat_pct,
        "margin_pct": tariff.margin_pct,
        "part_gap_mm": tariff.part_gap_mm,
        "plate": {
            "width_mm": plate.width_mm,
            "height_mm": plate.height_mm,
            "edge_margin_mm": plate.edge_margin_mm,
            "usable_width_mm": round(plate.usable_width, 2),
            "usable_height_mm": round(plate.usable_height, 2),
            "label": plate.label,
        },
        "materials": tariff.materials,
        "waste_tiers": [
            {"max_waste_pct": t.max_waste_pct, "multiplier": t.multiplier, "label": t.label}
            for t in tariff.waste_tiers
        ],
    }


# ---- קלט ----


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    """מעלה קובץ ייצור ומחלץ ממנו את הגיאומטריה.

    הקובץ עצמו לא נשמר ולא נשלח לשום מקום — הוא מפוענח מקומית ונמחק.
    """
    name = file.filename or "upload.dxf"
    suffix = Path(name).suffix.lower()

    if suffix in {".step", ".stp"}:
        raise HTTPException(
            status_code=415,
            detail="קבצי STEP עדיין לא נתמכים. ייצא ל-DXF, או הזן את המידות ידנית.",
        )
    if suffix != ".dxf":
        raise HTTPException(status_code=415, detail=f"סוג קובץ לא נתמך: {suffix or 'ללא סיומת'}. נדרש DXF.")

    payload = await file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="הקובץ גדול מ-10MB.")
    if not payload:
        raise HTTPException(status_code=400, detail="הקובץ ריק.")

    with tempfile.NamedTemporaryFile(suffix=".dxf", delete=True) as tmp:
        tmp.write(payload)
        tmp.flush()
        try:
            extraction = read_dxf(tmp.name)
        except CadReadError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # קובץ פגום מגיע בצורות רבות ובלתי צפויות
            raise HTTPException(status_code=422, detail=f"קריאת ה-DXF נכשלה: {exc}") from exc

    stem = Path(name).stem
    key = STORE.put(
        StoredGeometry(
            geometry=extraction.geometry,
            name=stem,
            source=PartSource.DXF.value,
            units_detected=extraction.units_detected,
            warnings=tuple(extraction.warnings),
        )
    )
    return _geometry_response(key, stem, PartSource.DXF.value, extraction.geometry) | {
        "units_detected": extraction.units_detected,
        "scale_to_mm": extraction.scale_to_mm,
        "warnings": extraction.warnings,
        "skipped_entities": extraction.skipped_entities,
        "layers_seen": extraction.layers_seen,
    }


@app.post("/api/manual")
def manual(spec: ManualPartSpec) -> dict:
    """בונה גיאומטריה ממידות שהוקלדו — אותו טיפוס בדיוק כמו מ-DXF."""
    try:
        geometry = _build_manual_geometry(spec)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    key = STORE.put(
        StoredGeometry(geometry=geometry, name=spec.name, source=PartSource.MANUAL.value)
    )
    return _geometry_response(key, spec.name, PartSource.MANUAL.value, geometry)


# ---- תמחור ----


@app.post("/api/quote")
def quote(request: QuoteRequest) -> dict:
    tariff = _require_tariff()

    parts: list[Part] = []
    for item in request.parts:
        try:
            stored = STORE.get(item.geometry_id)
        except GeometryExpired as exc:
            raise HTTPException(
                status_code=410,
                detail=f'הגיאומטריה של "{item.name or item.geometry_id}" כבר לא בזיכרון. העלה את הקובץ מחדש.',
            ) from exc
        try:
            parts.append(
                Part(
                    name=item.name or stored.name,
                    geometry=stored.geometry,
                    material_key=item.material_key,
                    thickness_mm=item.thickness_mm,
                    quantity=item.quantity,
                    source=PartSource(stored.source),
                    bend_count=item.bend_count,
                    bend_length_mm=item.bend_length_mm,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        result = price_order(parts, tariff)
    except MissingTariffError as exc:
        # לא שגיאת שרת: הטבלה פשוט לא מכסה את הצירוף, וזה מכוון.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PricingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return quote_to_json(result)


# ---- טבלת התמחור ----


@app.get("/api/tariff")
def get_tariff() -> dict:
    return {"raw": STATE.raw, "source": STATE.origin, "ready": STATE.is_ready, "error": STATE.error}


@app.put("/api/tariff")
async def put_tariff(raw: dict) -> dict:
    """מחליף את הטבלה הפעילה.

    בפריסת ענן הכתיבה לדיסק אינה שורדת הפעלה מחדש, ולכן התשובה אומרת
    במפורש אם הצליחה — והממשק מציע להוריד את הקובץ ולהזין אותו כמשתנה
    סביבה. עדיף לומר את זה מאשר שינון יגלה בעצמו שהמחירים נעלמו.
    """
    try:
        STATE.replace(raw)
    except (InvalidTariffError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    saved = STATE.persist()
    return {
        "ready": STATE.is_ready,
        "persisted": saved is not None,
        "persist_note": (
            "נשמר בדיסק השרת. בענן זה זמני — הורד את הקובץ והזן אותו כמשתנה הסביבה TARIFF_JSON כדי שישרוד פריסה מחדש."
            if saved
            else "לא ניתן היה לכתוב לדיסק. הטבלה פעילה בזיכרון בלבד — הורד אותה."
        ),
    }


# ---- טופס המחירים הפשוט ----


@app.get("/api/prices")
def get_prices() -> dict:
    """הטבלה בצורת טופס, בלי מבנה נתונים ובלי מונחים טכניים."""
    return {
        "form": to_form(STATE.raw or {}),
        "ready": STATE.is_ready,
        "source": STATE.origin,
    }


@app.put("/api/prices")
async def put_prices(form: dict) -> dict:
    """שומר את המחירים מהטופס לתוך הטבלה הקיימת.

    הטופס נוגע רק במחירים; מדרגות הבזבוז, מידות הפלטה ומדיניות
    השאריות עוברות דרכו ללא שינוי.
    """
    try:
        merged = apply_form(STATE.raw or {}, form)
        STATE.replace(merged)
    except (InvalidTariffError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    saved = STATE.persist()
    return {
        "ready": STATE.is_ready,
        "persisted": saved is not None,
        "message": (
            "המחירים נשמרו והמערכת מחשבת לפיהם."
            if STATE.is_ready
            else "נשמר, אבל כל המחירים עדיין 0 — המערכת תציג 0 בכל הצעה."
        ),
    }


@app.get("/api/tariff/download")
def download_tariff() -> JSONResponse:
    return JSONResponse(
        content=STATE.raw,
        headers={"Content-Disposition": 'attachment; filename="tariff.json"'},
    )


# ---- עזר ----


def _require_tariff():
    try:
        return STATE.require()
    except InvalidTariffError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _geometry_response(key: str, name: str, source: str, geometry: PartGeometry) -> dict:
    """תשובת השרת על חלק בודד, מיד אחרי חילוץ הגיאומטריה.

    `manufacturable` נשאר True גם לחלק שגדול מפלטה: מאז החלטת הפיצול
    כל חלק ניתן לייצור, והשאלה היא רק בכמה חתיכות ובכמה ריתוך.
    """
    plate = _plate()
    verdict = check_manufacturability(geometry.bbox, plate)
    plan = plan_split(geometry.bbox, plate)
    return {
        "geometry_id": key,
        "name": name,
        "source": source,
        "manufacturable": True,
        "fits_single_plate": verdict.ok,
        "manufacturability_reason": "" if verdict.ok else verdict.reason,
        "rotated_to_fit": verdict.rotated,
        "pieces": plan.piece_count,
        "split_columns": plan.columns,
        "split_rows": plan.rows,
        "weld_length_mm": round(plan.seam_length_mm, 2),
        **geometry_to_json(geometry),
    }


def _plate():
    tariff = STATE.tariff
    if tariff is not None:
        return tariff.plate
    from ..nesting.plate import STANDARD_PLATE

    return STANDARD_PLATE


def _build_manual_geometry(spec: ManualPartSpec) -> PartGeometry:
    if spec.shape == "rect":
        if not spec.width_mm or not spec.height_mm:
            raise ValueError("למלבן נדרשים רוחב וגובה.")
        outer = rectangle(spec.width_mm, spec.height_mm)
        span_w, span_h = spec.width_mm, spec.height_mm
    elif spec.shape == "circle":
        if not spec.diameter_mm:
            raise ValueError("לעיגול נדרש קוטר.")
        radius = spec.diameter_mm / 2.0
        outer = circle(radius, center=(radius, radius), segments=256)
        span_w = span_h = spec.diameter_mm
    else:
        if not spec.points or len(spec.points) < 3:
            raise ValueError("למצולע נדרשות לפחות 3 נקודות.")
        outer = Contour(points=[(float(x), float(y)) for x, y in spec.points], closed=True)
        box = outer.bbox
        span_w, span_h = box.width, box.height

    contours = [outer]
    box = outer.bbox
    for index, hole in enumerate(spec.holes):
        radius = hole.diameter_mm / 2.0
        if hole.x_mm is not None and hole.y_mm is not None:
            center = (hole.x_mm, hole.y_mm)
        else:
            # פריסה על ציר המרכז: המיקום אינו משנה את השטח או אורך החיתוך,
            # אבל חורים חופפים היו נספרים פעמיים ולכן מפזרים אותם.
            slot = (index + 1) / (len(spec.holes) + 1)
            center = (box.min_x + span_w * slot, box.min_y + span_h / 2.0)
        if radius * 2 > min(span_w, span_h):
            raise ValueError(f'קוטר החור {hole.diameter_mm} מ"מ גדול מהחלק עצמו.')
        contours.append(circle(radius, center=center, segments=_hole_segments(radius)))

    return PartGeometry(contours=contours)


def _hole_segments(radius: float) -> int:
    """מספיק צלעות כדי שהשגיאה בהיקף תהיה זניחה מול דיוק החיתוך."""
    return max(32, min(256, int(math.ceil(radius)) * 8))


# ---- הממשק הסטטי ----

if WEB_DIR.exists():

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/login")
    def login_page() -> FileResponse:
        """מסך הכניסה. עצמאי לחלוטין, מאותה סיבה כמו טופס המחירים:
        דף שנטען לפני שיש זהות לא יכול להסתמך על /static שמאחורי השער."""
        return FileResponse(WEB_DIR / "login.html")

    @app.get("/prices")
    def prices_form() -> FileResponse:
        """מסך המחירים. עצמאי לחלוטין — CSS ו-JS בתוך הקובץ.

        הסיבה טכנית ולא סגנונית: מפתח העריכה מגיע בכתובת עצמה, והדפדפן
        לא יצרף אותו לבקשות ל-/static. דף שתלוי בקבצים חיצוניים היה
        נטען שבור אצל מי שנכנס עם הלינק.
        """
        return FileResponse(WEB_DIR / "prices.html")

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


__all__ = ["app"]
