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
import math
import os
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
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
from .serialize import geometry_to_json, quote_to_json
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

OPEN_PATHS = frozenset({"/health"})


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """שער סיסמה פשוט, פעיל רק כאשר APP_PASSWORD מוגדר.

    כתובת ציבורית בלי שער היא לא רק "מישהו יראה מחירים" — היא מאפשרת
    לכל אחד גם *לשנות* את טבלת התמחור דרך PUT /api/tariff. לכן השער
    חל על הכל, כולל הממשק, ולא רק על העריכה.

    בפיתוח מקומי המשתנה לא מוגדר והשער כבוי.
    """
    password = os.environ.get("APP_PASSWORD", "")
    if not password or request.url.path in OPEN_PATHS:
        return await call_next(request)

    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
            user, _, supplied = decoded.partition(":")
        except (ValueError, UnicodeDecodeError):
            user = supplied = ""
        expected_user = os.environ.get("APP_USER", "ynon")
        # השוואה בזמן קבוע — הפרש זמנים על סיסמה קצרה הוא דליפה אמיתית.
        if hmac.compare_digest(user, expected_user) and hmac.compare_digest(supplied, password):
            return await call_next(request)

    return Response(
        status_code=401,
        content="נדרשת התחברות",
        headers={"WWW-Authenticate": 'Basic realm="laser-pricing", charset="UTF-8"'},
    )


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


class QuoteRequest(BaseModel):
    parts: list[PartRequest] = Field(min_length=1)


# ---- מידע כללי ----


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


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

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


__all__ = ["app"]
