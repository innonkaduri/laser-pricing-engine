"""בדיקות שכבת ה-API.

הדגש כאן אינו על "מחזיר 200" אלא על שלושת הכללים שהמערכת מבטיחה:
הטבלה היא מקור האמת היחיד, האילוץ הפיזי חוסם לפני כל חישוב כספי,
וכשלון תמיד מפורש ולעולם לא שקט.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from laser_pricing.api import identity, tariff_store
# היבוא הפרטי מכוון: מונה הניסיונות הכושלים הוא מצב של התהליך, ובדיקה
# חייבת לאפס אותו כדי לא לחסום את הבדיקה הבאה.
from laser_pricing.api.app import (
    _EDITOR_ATTEMPTS,
    _ENGINE_CALLS,
    _LOGIN_FAILURES,
    _SIGNUPS,
    app,
)

# `from laser_pricing.api import app` מחזיר את אובייקט ה-FastAPI ולא את
# המודול, כי החבילה מייצאת אותו. למי שצריך לשנות משתנה ברמת המודול
# (monkeypatch) זו טעות שקטה: הערך נדבק על האובייקט הלא נכון.
app_module = sys.modules["laser_pricing.api.app"]

TEST_TARIFF = {
    "rates": [
        {
            "material_key": "st37",
            "material_name": "פלדה שחורה",
            "thickness_mm": 3.0,
            "plate_price": 900.0,
            "cut_rate_per_m": 12.0,
            "pierce_price": 1.5,
        }
    ],
    "waste_tiers": [
        {"max_waste_pct": 30, "multiplier": 1.1, "label": "ניצולת טובה"},
        {"max_waste_pct": 100, "multiplier": 2.0, "label": "ניצולת נמוכה"},
    ],
    "vat_pct": 18.0,
}


@pytest.fixture(autouse=True)
def isolated_tariff(tmp_path, monkeypatch):
    """הטבלה הפעילה היא מצב גלובלי של התהליך.

    בלי הבידוד הזה בדיקה שמזינה מחירים הייתה דולפת לבדיקות אחרות, וגרוע
    מכך — הכתיבה לדיסק הייתה יוצרת config/tariff.json אמיתי בריפו.
    """
    monkeypatch.setattr(tariff_store, "LIVE_PATH", tmp_path / "tariff.json")
    state = tariff_store.STATE
    before = (
        state.raw,
        state.tariff,
        state.origin,
        state.error,
        state.memory_hash,
        state._disk_stat,
        state._disk_hash,
    )
    yield
    (
        state.raw,
        state.tariff,
        state.origin,
        state.error,
        state.memory_hash,
        state._disk_stat,
        state._disk_hash,
    ) = before


@pytest.fixture(autouse=True)
def isolated_users(tmp_path, monkeypatch):
    """מסד המשתמשים גם הוא מצב גלובלי — ובדיקה לא תיצור אותו בריפו.

    גם מונה הניסיונות הכושלים מתאפס: הוא חי בזיכרון התהליך בכוונה, ולכן
    בדיקה שממצה אותו הייתה חוסמת את הבדיקה הבאה (וזה בדיוק מה שקרה) —
    ומאז ההרשמה הציבורית זה נכון גם למונה ההרשמות ולמונה התמחורים.
    """
    monkeypatch.setattr(identity, "DB_PATH", tmp_path / "users.db")
    _LOGIN_FAILURES.clear()
    _SIGNUPS.clear()
    _ENGINE_CALLS.clear()
    _EDITOR_ATTEMPTS.clear()
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def priced_client(client: TestClient) -> TestClient:
    """לקוח שהטבלה שלו מכילה מחירים אמיתיים."""
    assert client.put("/api/tariff", json=TEST_TARIFF).status_code == 200
    return client


def _manual(client: TestClient, **spec) -> dict:
    response = client.post("/api/manual", json={"shape": "rect", **spec})
    assert response.status_code == 200, response.text
    return response.json()


class TestExtraction:
    def test_dxf_upload_returns_measurements_not_the_file(self, client, rect_with_hole_dxf):
        with open(rect_with_hole_dxf, "rb") as handle:
            response = client.post("/api/upload", files={"file": ("bracket.dxf", handle)})
        assert response.status_code == 200
        data = response.json()
        assert data["bbox"]["width_mm"] == pytest.approx(400.0)
        assert data["bbox"]["height_mm"] == pytest.approx(250.0)
        assert data["pierces"] == 2 and data["holes"] == 1
        assert data["skipped_entities"].get("TEXT") == 1  # טקסט לא נחתך

    def test_inch_file_is_converted_not_taken_literally(self, client, inch_dxf):
        with open(inch_dxf, "rb") as handle:
            data = client.post("/api/upload", files={"file": ("inch.dxf", handle)}).json()
        assert data["bbox"]["width_mm"] == pytest.approx(254.0)
        assert data["units_detected"] == "inches"

    def test_step_is_refused_with_a_useful_message(self, client):
        response = client.post("/api/upload", files={"file": ("part.step", b"ISO-10303-21;")})
        assert response.status_code == 415
        assert "DXF" in response.json()["detail"]

    def test_corrupt_file_fails_loudly(self, client):
        response = client.post("/api/upload", files={"file": ("junk.dxf", b"not a dxf at all")})
        assert response.status_code == 422

    def test_manual_input_produces_the_same_shape_as_dxf(self, client):
        data = _manual(client, width_mm=400, height_mm=250, holes=[{"diameter_mm": 50}])
        assert data["source"] == "manual"
        assert data["holes"] == 1 and data["pierces"] == 2
        assert data["net_area_mm2"] == pytest.approx(400 * 250 - 3.14159 * 25**2, rel=1e-3)

    def test_hole_larger_than_the_part_is_rejected(self, client):
        response = client.post(
            "/api/manual",
            json={"shape": "rect", "width_mm": 100, "height_mm": 100, "holes": [{"diameter_mm": 150}]},
        )
        assert response.status_code == 400


class TestOversizedPartsAreSplit:
    """מאז 2026-08-12 חלק גדול מפלטה מיוצר בכמה חתיכות עם ריתוך."""

    def test_oversized_part_is_flagged_as_split_at_upload(self, client, oversized_dxf):
        with open(oversized_dxf, "rb") as handle:
            data = client.post("/api/upload", files={"file": ("big.dxf", handle)}).json()
        assert data["manufacturable"] is True
        assert data["fits_single_plate"] is False
        assert data["pieces"] == 2
        assert data["weld_length_mm"] > 0
        assert "ריתוך" in data["manufacturability_reason"]

    def test_normal_part_reports_a_single_piece(self, client):
        data = _manual(client, width_mm=400, height_mm=250)
        assert data["fits_single_plate"] is True
        assert data["pieces"] == 1
        assert data["weld_length_mm"] == 0
        assert data["manufacturability_reason"] == ""

    def test_oversized_part_gets_a_price(self, priced_client):
        big = _manual(priced_client, width_mm=3200, height_mm=1000)
        response = priced_client.post(
            "/api/quote",
            json={"parts": [{"geometry_id": big["geometry_id"], "material_key": "st37", "thickness_mm": 3.0}]},
        )
        assert response.status_code == 200
        quote = response.json()
        assert quote["has_split_parts"] is True
        assert quote["lines"][0]["pieces"] == 2
        assert quote["lines"][0]["line_total"] > 0
        assert not quote["rejected"]


class TestPricing:
    def test_quote_breaks_the_price_down(self, priced_client):
        part = _manual(priced_client, name="תושבת", width_mm=400, height_mm=250)
        quote = priced_client.post(
            "/api/quote",
            json={
                "parts": [
                    {
                        "geometry_id": part["geometry_id"],
                        "name": "תושבת",
                        "material_key": "st37",
                        "thickness_mm": 3.0,
                        "quantity": 25,
                    }
                ]
            },
        ).json()
        line = quote["lines"][0]
        assert line["material_cost"] > 0 and line["cutting_cost"] > 0
        assert quote["total"] > quote["total_before_vat"]
        assert quote["groups"][0]["layouts"][0]["placements"]

    def test_missing_row_in_the_table_is_an_explicit_failure(self, priced_client):
        part = _manual(priced_client, width_mm=200, height_mm=200)
        response = priced_client.post(
            "/api/quote",
            json={"parts": [{"geometry_id": part["geometry_id"], "material_key": "st37", "thickness_mm": 9.0}]},
        )
        assert response.status_code == 422
        assert "לא ננחש מחיר" in response.json()["detail"]

    def test_expired_geometry_asks_for_a_re_upload(self, priced_client):
        response = priced_client.post(
            "/api/quote",
            json={"parts": [{"geometry_id": "g-does-not-exist", "material_key": "st37", "thickness_mm": 3.0}]},
        )
        assert response.status_code == 410
        assert "העלה" in response.json()["detail"]

    def test_more_parts_never_cost_more_per_unit(self, priced_client):
        part = _manual(priced_client, width_mm=500, height_mm=500)

        def unit_price(qty: int) -> float:
            body = {
                "parts": [
                    {"geometry_id": part["geometry_id"], "material_key": "st37", "thickness_mm": 3.0, "quantity": qty}
                ]
            }
            return priced_client.post("/api/quote", json=body).json()["lines"][0]["unit_price"]

        assert unit_price(10) <= unit_price(2) <= unit_price(1)


class TestTariffEditing:
    def test_invalid_table_is_refused_and_the_live_one_survives(self, priced_client):
        broken = {"rates": TEST_TARIFF["rates"], "waste_tiers": [{"max_waste_pct": 50, "multiplier": 1.2}]}
        assert priced_client.put("/api/tariff", json=broken).status_code == 422
        assert priced_client.get("/api/config").json()["tariff_ready"] is True

    def test_config_reports_whether_real_prices_exist(self, priced_client):
        config = priced_client.get("/api/config").json()
        assert config["tariff_ready"] is True
        assert config["plate"]["usable_width_mm"] == pytest.approx(2960.0)
        assert any(m["key"] == "st37" for m in config["materials"])


class TestAuthGate:
    """הכתובת הפומבית מאפשרת גם *עריכת* מחירים, ולכן השער חל על הכל."""

    def test_gate_is_off_when_no_password_is_configured(self, client, monkeypatch):
        monkeypatch.delenv("APP_PASSWORD", raising=False)
        assert client.get("/api/config").status_code == 200

    def test_gate_blocks_everything_including_tariff_edits(self, client, monkeypatch):
        monkeypatch.setenv("APP_PASSWORD", "sod")
        assert client.get("/").status_code == 401
        assert client.get("/api/config").status_code == 401
        assert client.put("/api/tariff", json=TEST_TARIFF).status_code == 401

    def test_health_stays_open_so_render_can_probe_it(self, client, monkeypatch):
        monkeypatch.setenv("APP_PASSWORD", "sod")
        assert client.get("/health").status_code == 200

    def test_correct_credentials_pass(self, client, monkeypatch):
        monkeypatch.setenv("APP_PASSWORD", "sod")
        monkeypatch.setenv("APP_USER", "ynon")
        assert client.get("/api/config", auth=("ynon", "sod")).status_code == 200
        assert client.get("/api/config", auth=("ynon", "wrong")).status_code == 401


class TestHealth:
    """בדיקת הבריאות שומרת על ההבחנה בין "בזיכרון" ל"על הדיסק".

    המקרה שהוליד אותה: הקובץ נמחק מהשרת, השירות המשיך להחזיר
    `ready=True` מהזיכרון, ורק הפעלה מחדש הייתה חושפת שאין טבלה.
    """

    def test_reports_that_the_table_lives_only_in_memory(self, priced_client, tmp_path):
        (tmp_path / "tariff.json").unlink()
        body = priced_client.get("/health").json()
        assert body["tariff_ready"] is True
        assert body["disk_present"] is False
        assert body["disk_matches_memory"] is False
        assert any("הפעלה מחדש תמחק אותה" in w for w in body["warnings"])

    def test_stays_200_on_drift_so_the_platform_will_not_restart_us(self, priced_client, tmp_path):
        """הפעלה מחדש היא בדיוק מה שהורג את העותק היחיד שנשאר."""
        (tmp_path / "tariff.json").unlink()
        assert priced_client.get("/health").status_code == 200

    def test_disk_and_memory_agree_right_after_a_save(self, priced_client):
        body = priced_client.get("/health").json()
        assert body["disk_present"] is True
        assert body["disk_matches_memory"] is True
        # אין אזהרת סטייה. אזהרת "השער כבוי" כן מופיעה כאן, כי בבדיקות
        # אין סיסמה ואין משתמשים — וזה בדיוק מה שהיא אמורה לומר.
        assert not any("זיכרון" in w or "דיסק" in w for w in body["warnings"])

    def test_notices_a_file_changed_behind_the_service(self, priced_client, tmp_path):
        live = tmp_path / "tariff.json"
        live.write_text(live.read_text(encoding="utf-8").replace("900.0", "111.0"), encoding="utf-8")
        body = priced_client.get("/health").json()
        assert body["disk_present"] is True
        assert body["disk_matches_memory"] is False
        assert any("שונה מזו שעל הדיסק" in w for w in body["warnings"])

    def test_leaks_no_material_names_or_prices(self, priced_client):
        """הנתיב פתוח בלי אישור — ספירות ובוליאנים בלבד."""
        raw = priced_client.get("/health").text
        for secret in ("st37", "פלדה שחורה", "900", "12.0", "1.5"):
            assert secret not in raw
        body = priced_client.get("/health").json()
        assert body["materials_count"] == 1
        assert body["rate_rows"] == 1
        assert body["priced_rows"] == 1

    def test_does_not_read_the_file_on_every_call(self, priced_client, monkeypatch):
        """הבדיקה הזאת רצה כל דקה. `stat` מספיק כשהקובץ לא זז."""
        reads = 0
        original = Path.read_bytes

        def counting_read(self):
            nonlocal reads
            reads += 1
            return original(self)

        monkeypatch.setattr(Path, "read_bytes", counting_read)
        for _ in range(5):
            assert priced_client.get("/health").json()["disk_matches_memory"] is True
        assert reads == 0


class TestSimplePriceForm:
    """המסך שאבא של ינון ממלא — טופס, לא JSON."""

    def test_form_exposes_prices_and_hides_calibration(self, client):
        form = client.get("/api/prices").json()["form"]
        assert form["materials"], "הטופס חייב להציג חומרים"
        row = form["materials"][0]["rows"][0]
        assert "plate_price" in row and "weld_rate_per_m" in row
        # מדרגות בזבוז, מידות פלטה ומדיניות שאריות הן כיול של ינון
        assert "waste_tiers" not in form
        assert "plate" not in form
        assert "remnant_policy" not in form

    def test_saving_the_form_makes_the_engine_ready(self, client):
        form = client.get("/api/prices").json()["form"]
        assert client.get("/api/prices").json()["ready"] is False
        for material in form["materials"]:
            for row in material["rows"]:
                row["plate_price"] = 500.0
                row["cut_rate_per_m"] = 12.0
        res = client.put("/api/prices", json=form)
        assert res.status_code == 200
        assert res.json()["ready"] is True
        assert client.get("/api/config").json()["tariff_ready"] is True

    def test_form_never_wipes_fields_it_does_not_show(self):
        """הבאג המסוכן בטופס פשוט: לבנות טבלה חדשה ולמחוק את הכיול."""
        from laser_pricing.api.simple_tariff import apply_form, to_form

        raw = {
            "rates": [
                {
                    "material_key": "st37",
                    "material_name": "פלדה",
                    "thickness_mm": 3.0,
                    "plate_price": 0.0,
                    "cut_rate_per_m": 0.0,
                    "density_kg_m3": 7850.0,
                }
            ],
            "waste_tiers": [{"max_waste_pct": 100.0, "multiplier": 2.0}],
            "plate": {"width_mm": 3000.0, "height_mm": 1500.0, "edge_margin_mm": 20.0},
            "remnant_policy": {"min_usable_short_side_mm": 200.0},
        }
        form = to_form(raw)
        form["materials"][0]["rows"][0]["plate_price"] = 450.0
        merged = apply_form(raw, form)

        assert merged["rates"][0]["plate_price"] == 450.0
        assert merged["waste_tiers"] == raw["waste_tiers"]
        assert merged["plate"] == raw["plate"]
        assert merged["remnant_policy"] == raw["remnant_policy"]
        assert merged["rates"][0]["density_kg_m3"] == 7850.0

    def test_blank_field_means_zero_not_an_error(self):
        from laser_pricing.api.simple_tariff import apply_form

        raw = {"rates": [{"material_key": "a", "material_name": "A", "thickness_mm": 1.0}]}
        form = {"materials": [{"key": "a", "name": "A", "rows": [{"thickness_mm": 1.0, "plate_price": ""}]}]}
        assert apply_form(raw, form)["rates"][0]["plate_price"] == 0.0

    def test_new_thickness_can_be_added_without_touching_json(self):
        from laser_pricing.api.simple_tariff import apply_form

        raw = {"rates": [{"material_key": "a", "material_name": "A", "thickness_mm": 1.0}]}
        form = {"materials": [{"key": "a", "name": "A", "rows": [{"thickness_mm": 12.0, "plate_price": 99.0}]}]}
        merged = apply_form(raw, form)
        assert len(merged["rates"]) == 2
        assert merged["rates"][1]["thickness_mm"] == 12.0
        assert merged["rates"][1]["density_kg_m3"] == 7850.0

    def test_bad_numbers_are_rejected_with_a_clear_error(self, client):
        form = client.get("/api/prices").json()["form"]
        form["materials"][0]["rows"][0]["plate_price"] = -5.0
        assert client.put("/api/prices", json=form).status_code == 422


class TestBendsThroughTheApi:
    """הכיפופים נכנסים מהממשק ולא מהקובץ — ולכן הם עוברים בבקשה."""

    @pytest.fixture
    def bent_client(self, client):
        tariff = {
            **TEST_TARIFF,
            "rates": [{**TEST_TARIFF["rates"][0], "bend_price": 12.0, "bend_rate_per_m": 30.0}],
        }
        assert client.put("/api/tariff", json=tariff).status_code == 200
        return client

    def _quote(self, client, **extra) -> dict:
        part = _manual(client, width_mm=400, height_mm=250)
        body = {
            "parts": [
                {
                    "geometry_id": part["geometry_id"],
                    "material_key": "st37",
                    "thickness_mm": 3.0,
                    **extra,
                }
            ]
        }
        response = client.post("/api/quote", json=body)
        assert response.status_code == 200, response.text
        return response.json()["lines"][0]

    def test_declared_bends_are_charged_from_the_table(self, bent_client):
        line = self._quote(bent_client, bend_count=3, bend_length_mm=1000)
        assert line["bend_count"] == 3
        assert line["bending_cost"] == pytest.approx(3 * 12.0 + 30.0)

    def test_the_same_part_without_bends_costs_the_same_as_before(self, bent_client):
        plain = self._quote(bent_client)
        assert plain["bending_cost"] == 0.0
        assert plain["bend_count"] == 0

    def test_negative_bend_count_is_refused_by_the_api(self, bent_client):
        part = _manual(bent_client, width_mm=400, height_mm=250)
        body = {
            "parts": [
                {
                    "geometry_id": part["geometry_id"],
                    "material_key": "st37",
                    "thickness_mm": 3.0,
                    "bend_count": -2,
                }
            ]
        }
        assert bent_client.post("/api/quote", json=body).status_code == 422

    def test_dad_can_enter_the_bend_price_in_his_form(self, client):
        """המסלול המלא: הטופס של אבא → הטבלה → ההצעה."""
        form = client.get("/api/prices").json()["form"]
        assert any(lbl["field"] == "bend_price" for lbl in form["labels"]["money"])
        for material in form["materials"]:
            for row in material["rows"]:
                row["plate_price"] = 500.0
                row["cut_rate_per_m"] = 12.0
                row["bend_price"] = 8.0
        assert client.put("/api/prices", json=form).status_code == 200

        line = self._quote(client, bend_count=2)
        assert line["bending_cost"] == pytest.approx(16.0)


class TestEditorKeyIsScoped:
    """הסיסמה הפשוטה פותחת את טופס המחירים בלבד."""

    @pytest.fixture
    def gated(self, monkeypatch):
        monkeypatch.setenv("APP_PASSWORD", "strong-secret")
        monkeypatch.setenv("EDITOR_PASSWORD", "123")
        from laser_pricing.api.app import app

        return TestClient(app)

    def test_editor_key_opens_the_price_form(self, gated):
        assert gated.get("/api/prices", headers={"X-Editor-Key": "123"}).status_code == 200
        assert gated.get("/prices?k=123").status_code == 200

    def test_editor_key_does_not_open_the_engine(self, gated):
        assert gated.get("/api/config", headers={"X-Editor-Key": "123"}).status_code == 401
        assert gated.get("/", headers={"X-Editor-Key": "123"}).status_code == 401

    def test_editor_key_does_not_open_the_raw_json_editor(self, gated):
        """הנקודה המרכזית: 123 לא נותן להחליף את הטבלה כולה."""
        assert gated.get("/api/tariff", headers={"X-Editor-Key": "123"}).status_code == 401
        assert gated.put("/api/tariff", json={}, headers={"X-Editor-Key": "123"}).status_code == 401

    def test_wrong_editor_key_is_refused(self, gated):
        assert gated.get("/api/prices", headers={"X-Editor-Key": "124"}).status_code == 401
        assert gated.get("/prices?k=oops").status_code == 401

    def test_main_password_still_opens_everything(self, gated):
        auth = ("ynon", "strong-secret")
        assert gated.get("/api/config", auth=auth).status_code == 200
        assert gated.get("/api/prices", auth=auth).status_code == 200


class TestUsersAndCapabilities:
    """התפר: זהות אחת, שתי יכולות, ומקור זהות שאפשר להחליף.

    מה שנבדק כאן הוא לא "יש מסך כניסה" אלא ההבטחות: שאבא ממשיך לעבוד
    בלי חשבון, שמי שנכנס לתמחר אינו יכול לגעת בטבלה, ושה-CRM מדבר עם
    המנוע כשירות ולא כאדם.
    """

    @pytest.fixture
    def users(self):
        identity.create_user("itai", "sod-arok-1", "איתי", {identity.CAP_QUOTE_USE})
        identity.create_user(
            "aba", "sod-arok-2", "אבא", {identity.CAP_PRICES_EDIT, identity.CAP_QUOTE_USE}
        )
        return identity.list_users()

    @pytest.fixture
    def gated(self, users, monkeypatch):
        """שער דלוק בלי APP_PASSWORD — קיום משתמשים לבדו מספיק."""
        monkeypatch.delenv("APP_PASSWORD", raising=False)
        monkeypatch.setenv("EDITOR_PASSWORD", "123")
        return TestClient(app)

    def _login(self, client, username, password):
        return client.post("/api/login", json={"username": username, "password": password})

    def _login_ok(self, client, username, password):
        response = self._login(client, username, password)
        assert response.status_code == 200, response.text
        return response

    # ---- השער עצמו ----

    def test_users_alone_turn_the_gate_on(self, gated):
        """בלי המשתמשים ובלי APP_PASSWORD השרת פתוח. זו התאונה של 18.8."""
        assert gated.get("/api/config").status_code == 401
        assert gated.get("/health").json()["gate_on"] is True

    def test_login_page_is_reachable_without_identity(self, gated):
        assert gated.get("/login").status_code == 200
        assert gated.post("/api/login", json={"username": "x", "password": "y"}).status_code == 401

    def test_browser_gets_the_login_screen_and_curl_gets_401(self, gated):
        page = gated.get("/", headers={"accept": "text/html"}, follow_redirects=False)
        assert page.status_code == 303 and page.headers["location"].startswith("/login")
        assert gated.get("/", headers={"accept": "*/*"}).status_code == 401

    # ---- זהות ----

    def test_session_cookie_opens_the_engine(self, gated):
        assert self._login(gated, "itai", "sod-arok-1").status_code == 200
        assert gated.get("/api/config").status_code == 200
        assert gated.get("/api/me").json()["username"] == "itai"

    def test_wrong_password_says_nothing_about_which_part_was_wrong(self, gated):
        no_user = self._login(gated, "mi-ze", "sod-arok-1")
        bad_password = self._login(gated, "itai", "lo-nachon")
        assert no_user.status_code == bad_password.status_code == 401
        assert no_user.json()["detail"] == bad_password.json()["detail"]

    def test_logout_closes_the_session(self, gated):
        self._login_ok(gated, "itai", "sod-arok-1")
        assert gated.post("/api/logout").status_code == 200
        assert gated.get("/api/config").status_code == 401

    def test_disabled_user_is_out_immediately_even_with_a_live_session(self, gated):
        self._login_ok(gated, "itai", "sod-arok-1")
        assert gated.get("/api/config").status_code == 200
        identity.set_disabled("itai", True)
        # הזהות נטענת בכל בקשה, ולכן חסימה אינה מחכה לפקיעת העוגייה.
        assert gated.get("/api/config").status_code == 401

    def test_a_forged_cookie_is_refused(self, gated):
        forged = identity.issue_session("itai").split(".")[0] + ".zayefti-et-hachatima"
        gated.cookies.set(identity.SESSION_COOKIE, forged)
        assert gated.get("/api/config").status_code == 401

    def test_login_throttles_repeated_failures(self, gated):
        codes = [self._login(gated, "itai", f"nisayon-{i}").status_code for i in range(12)]
        assert 429 in codes, "מסך כניסה פומבי בלי הגבלת קצב הוא ניחוש סיסמאות חופשי"

    # ---- יכולות ----

    def test_quote_user_cannot_touch_the_price_table(self, gated):
        self._login_ok(gated, "itai", "sod-arok-1")
        assert gated.get("/api/prices").status_code == 403
        assert gated.put("/api/tariff", json=TEST_TARIFF).status_code == 403
        # אבל לתמחר הוא כן יכול — זו כל הפואנטה של ההפרדה.
        assert gated.get("/api/config").status_code == 200

    def test_price_editor_can_do_both(self, gated):
        self._login_ok(gated, "aba", "sod-arok-2")
        assert gated.get("/api/prices").status_code == 200
        assert gated.get("/api/config").status_code == 200

    # ---- מה שאסור שיישבר ----

    def test_dads_link_keeps_working_without_an_account(self, gated):
        """הסעיף שהאב הדגיש: אבא באמצע מילוי, ואין להכניס אותו למסך כניסה."""
        assert gated.get("/prices?k=123").status_code == 200
        assert gated.get("/api/prices", headers={"X-Editor-Key": "123"}).status_code == 200
        assert gated.get("/prices?k=lo-nachon").status_code == 401

    def test_dads_link_still_does_not_open_the_engine(self, gated):
        assert gated.get("/api/config", headers={"X-Editor-Key": "123"}).status_code == 401
        assert gated.put("/api/tariff", json={}, headers={"X-Editor-Key": "123"}).status_code == 401

    def test_the_historical_password_still_works(self, gated, monkeypatch):
        """ינון וסקריפטים משתמשים בה, ולכן היא לא נשברת ביום המעבר."""
        monkeypatch.setenv("APP_PASSWORD", "strong-secret")
        monkeypatch.setenv("APP_USER", "ynon")
        assert gated.get("/api/config", auth=("ynon", "strong-secret")).status_code == 200
        assert gated.get("/api/prices", auth=("ynon", "strong-secret")).status_code == 200
        assert gated.get("/api/config", auth=("ynon", "lo-nachon")).status_code == 401

    # ---- ה-CRM ----

    def test_service_token_prices_but_never_edits(self, gated, monkeypatch):
        """הגבול: ה-CRM מתמחר, ולעולם לא נוגע בטבלה של אבא."""
        monkeypatch.setenv("SERVICE_TOKEN", "token-shel-hacrm")
        headers = {"X-Service-Token": "token-shel-hacrm"}
        assert gated.get("/api/config", headers=headers).status_code == 200
        assert gated.get("/api/prices", headers=headers).status_code == 403
        assert gated.put("/api/tariff", json=TEST_TARIFF, headers=headers).status_code == 403
        assert gated.get("/api/me", headers=headers).json()["source"] == "service"

    def test_a_wrong_service_token_is_nobody(self, gated, monkeypatch):
        monkeypatch.setenv("SERVICE_TOKEN", "token-shel-hacrm")
        assert gated.get("/api/config", headers={"X-Service-Token": "nisayon"}).status_code == 401


class TestOperationalDashboard:
    """מסך תפעולי ולא מסך מכירות — היסטוריית ההצעות יושבת ב-CRM."""

    def test_coverage_counts_cells_and_not_rows(self, client):
        """שורה עם מחיר פלטה בלבד עדיין מייצרת הצעה חלקית."""
        body = client.get("/api/dashboard").json()["tariff"]
        assert body["total_cells"] == 22 * len(body["field_labels"])
        assert body["filled_cells"] == 0
        assert body["ready"] is False

    def test_a_single_filled_row_moves_the_counter(self, priced_client):
        body = priced_client.get("/api/dashboard").json()["tariff"]
        assert body["ready"] is True
        assert body["filled_cells"] == 3  # plate_price, cut_rate_per_m, pierce_price
        material = body["materials"][0]
        assert material["priced_rows"] == 1
        # השדות שאיש לא מילא באף שורה — זה מה שהמסך מציג לאבא.
        assert material["empty_fields"]["bend_price"] == material["rows"]

    def test_backup_state_is_read_from_the_report_not_the_backup_dir(self, client, tmp_path, monkeypatch):
        """תיקיית הגיבויים היא 700 root; השירות רואה חותמות זמן בלבד."""
        missing = client.get("/api/dashboard").json()["backup"]
        assert missing["reporting"] is False and missing["stale"] is True

        status = tmp_path / "backup-status.json"
        status.write_text(
            json.dumps(
                {
                    "last_run": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                    "ok": True,
                    "tariff": {"last_backup": None, "count": 0},
                    "users": {"last_backup": None, "count": 0},
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(app_module, "BACKUP_STATUS_PATH", status)
        fresh = client.get("/api/dashboard").json()["backup"]
        assert fresh["reporting"] is True and fresh["stale"] is False
        assert fresh["minutes_since_run"] == 0

    def test_an_old_report_is_stale(self, client, tmp_path, monkeypatch):
        old = datetime.now() - timedelta(hours=5)
        status = tmp_path / "backup-status.json"
        status.write_text(
            json.dumps({"last_run": old.strftime("%Y-%m-%dT%H:%M:%S"), "ok": True}), encoding="utf-8"
        )
        monkeypatch.setattr(app_module, "BACKUP_STATUS_PATH", status)
        body = client.get("/api/dashboard").json()["backup"]
        assert body["stale"] is True
        assert body["minutes_since_run"] >= 300

    def test_the_dashboard_is_behind_the_gate(self, monkeypatch):
        identity.create_user("itai", "sod-arok-1", "איתי", {identity.CAP_QUOTE_USE})
        monkeypatch.delenv("APP_PASSWORD", raising=False)
        gated = TestClient(app)
        assert gated.get("/api/dashboard").status_code == 401
        assert gated.post("/api/login", json={"username": "itai", "password": "sod-arok-1"}).status_code == 200
        # מסך תפעולי הוא של כל מי שנכנס, ולא רק של מי שעורך מחירים.
        assert gated.get("/api/dashboard").status_code == 200


class TestLoginLandsWhereTheUserCanWork:
    def test_a_prices_only_user_is_sent_to_the_form(self, monkeypatch):
        """אבא ממלא מחירים. דף ראשי שנפתח על 403 נראה כמו תקלה."""
        identity.create_user("aba", "sod-arok-2", "אבא", {identity.CAP_PRICES_EDIT})
        monkeypatch.delenv("APP_PASSWORD", raising=False)
        client = TestClient(app)
        body = client.post("/api/login", json={"username": "aba", "password": "sod-arok-2"}).json()
        assert body["next"] == "/prices"

    def test_an_engine_user_lands_on_the_engine(self, monkeypatch):
        identity.create_user("itai", "sod-arok-1", "איתי", {identity.CAP_QUOTE_USE})
        monkeypatch.delenv("APP_PASSWORD", raising=False)
        client = TestClient(app)
        body = client.post("/api/login", json={"username": "itai", "password": "sod-arok-1"}).json()
        assert body["next"] == "/"

    def test_an_explicit_destination_is_kept(self, monkeypatch):
        identity.create_user("aba", "sod-arok-2", "אבא", {identity.CAP_PRICES_EDIT})
        monkeypatch.delenv("APP_PASSWORD", raising=False)
        client = TestClient(app)
        body = client.post(
            "/api/login", json={"username": "aba", "password": "sod-arok-2", "next": "/prices?k=x"}
        ).json()
        assert body["next"] == "/prices?k=x"


class TestKnowingWhoYouAreIsNotACapability:
    """`/api/me` ו-`/api/logout` פתוחים לכל מי שנכנס.

    אבא, שיש לו `prices:edit` בלבד, קיבל "אין לך הרשאה ל-quote:use"
    על השאלה מי הוא — הודעת הרשאה על משהו שאינו הרשאה, בכל כניסה.
    """

    @pytest.fixture
    def aba(self, monkeypatch):
        identity.create_user("aba", "sod-arok-2", "אבא", {identity.CAP_PRICES_EDIT})
        monkeypatch.delenv("APP_PASSWORD", raising=False)
        client = TestClient(app)
        assert client.post("/api/login", json={"username": "aba", "password": "sod-arok-2"}).status_code == 200
        return client

    def test_a_prices_only_user_can_ask_who_they_are(self, aba):
        body = aba.get("/api/me").json()
        assert body["authenticated"] is True
        assert body["username"] == "aba"
        assert body["capabilities"] == ["prices:edit"]

    def test_a_prices_only_user_can_log_out(self, aba):
        assert aba.post("/api/logout").status_code == 200
        assert aba.get("/api/me").status_code == 401

    def test_but_the_engine_is_still_closed_to_them(self, aba):
        assert aba.get("/api/config").status_code == 403

    def test_me_still_needs_an_identity(self, monkeypatch):
        identity.create_user("itai", "sod-arok-1", "איתי", {identity.CAP_QUOTE_USE})
        monkeypatch.delenv("APP_PASSWORD", raising=False)
        assert TestClient(app).get("/api/me").status_code == 401


class TestRemovingRowsFromDadsForm:
    """22 השורות בתבנית הן ניחוש. אבא ימלא רק את מה שהוא מוכר."""

    def test_a_row_marked_for_removal_disappears(self, client):
        from laser_pricing.api.simple_tariff import apply_form

        raw = {
            "rates": [
                {"material_key": "st37", "material_name": "פלדה", "thickness_mm": 1.0},
                {"material_key": "st37", "material_name": "פלדה", "thickness_mm": 3.0},
            ]
        }
        form = {
            "materials": [
                {
                    "key": "st37",
                    "name": "פלדה",
                    "rows": [
                        {"thickness_mm": 1.0, "remove": True},
                        {"thickness_mm": 3.0, "plate_price": 900.0},
                    ],
                }
            ]
        }
        merged = apply_form(raw, form)
        assert [r["thickness_mm"] for r in merged["rates"]] == [3.0]
        assert merged["rates"][0]["plate_price"] == 900.0

    def test_a_row_the_form_never_mentions_survives(self, client):
        """"לא הזכרת" אינו "מחק" — אחרת טופס חלקי מוחק חצי טבלה בשקט."""
        from laser_pricing.api.simple_tariff import apply_form

        raw = {"rates": [{"material_key": "st37", "material_name": "פלדה", "thickness_mm": 8.0}]}
        merged = apply_form(raw, {"materials": []})
        assert len(merged["rates"]) == 1

    def test_removal_survives_the_round_trip_through_the_api(self, client):
        assert client.put("/api/tariff", json=TEST_TARIFF).status_code == 200
        form = client.get("/api/prices").json()["form"]
        form["materials"][0]["rows"][0]["remove"] = True
        assert client.put("/api/prices", json=form).status_code == 200
        assert client.get("/api/tariff").json()["raw"]["rates"] == []


class TestFormEchoesDadsNumberBackToHim:
    """שטח הפלטה נשלח לטופס כדי להחזיר לאבא את המספר שלו במ"ר.

    זו לא ולידציה ולא שיפוט על המחיר — זו הצגה של אותו מספר ביחידה
    שהוא חושב בה. 640 לפלטה הם 142 ש"ח למ"ר; מי שהקליד 640000 יראה
    142,222 ויתפוס את האפס לפני שהוא שומר.
    """

    def test_plate_area_is_in_the_form(self, client):
        form = client.get("/api/prices").json()["form"]
        assert form["plate_area_m2"] == pytest.approx(4.5)  # 3000x1500

    def test_it_follows_a_non_standard_plate(self, client):
        assert client.put(
            "/api/tariff",
            json={**TEST_TARIFF, "plate": {"width_mm": 2000.0, "height_mm": 1000.0}},
        ).status_code == 200
        form = client.get("/api/prices").json()["form"]
        assert form["plate_area_m2"] == pytest.approx(2.0)

    def test_the_plate_itself_stays_out_of_the_editable_form(self, client):
        """מידות הפלטה הן כיול של ינון. מוצג — לא נערך."""
        form = client.get("/api/prices").json()["form"]
        assert "plate" not in form
        assert not any(f["field"] == "plate_area_m2" for f in form["labels"]["money"])


class TestUploadCeiling:
    """התקרה נמדדה ולא נבחרה: 56MB עלו 41 שניות ו-823MB על מק מהיר,
    והקופסה היא שתי ליבות משותפות לחמישה פרויקטים."""

    def test_a_file_over_the_ceiling_is_refused_with_what_to_do(self, client):
        from laser_pricing.api.app import MAX_UPLOAD_BYTES

        huge = b"0" * (MAX_UPLOAD_BYTES + 1)
        response = client.post("/api/upload", files={"file": ("plan.dxf", huge)})
        assert response.status_code == 413
        detail = response.json()["detail"]
        # לא רק "נכשל" — גם מה לעשות במקום.
        assert "המתאר של החלק בלבד" in detail

    def test_the_default_ceiling_is_25mb(self):
        """אם מישהו משנה את המספר — שיראה גם את המדידה שבדוקסטרינג."""
        from laser_pricing.api.app import MAX_UPLOAD_BYTES

        assert MAX_UPLOAD_BYTES == 25 * 1024 * 1024


class TestOpenLinesAreAQuestionNotAGuess:
    """קו פתוח בשרטוט פח הוא לרוב כיפוף או סימון, לא חיתוך.

    עד 20.8.2026 הוא נספר כחיתוך בשקט: קו כיפוף אחד על מלבן 250x150
    ניפח את אורך החיתוך מ-800 ל-1,050 מ"מ — 31% מחיר חיתוך פנטום,
    בלי שום סימן. עכשיו הוא אינו מחויב כברירת מחדל, מוכרז, ומי
    שמזמין יכול לומר שזה כן חיתוך.
    """

    @pytest.fixture
    def bent(self, client, tmp_path):
        import ezdxf

        doc = ezdxf.new("R2010")
        doc.units = 4
        msp = doc.modelspace()
        msp.add_lwpolyline([(0, 0), (250, 0), (250, 150), (0, 150)], close=True, dxfattribs={"layer": "CUT"})
        msp.add_line((0, 75), (250, 75), dxfattribs={"layer": "BEND"})
        path = tmp_path / "bent.dxf"
        doc.saveas(path)
        with open(path, "rb") as handle:
            return client.post("/api/upload", files={"file": ("bent.dxf", handle)}).json()

    def test_the_open_line_is_reported_separately(self, bent):
        assert bent["cut_length_mm"] == pytest.approx(800.0)  # ההיקף בלבד
        assert bent["open_line_count"] == 1
        assert bent["open_length_mm"] == pytest.approx(250.0)

    def test_the_upload_says_so_and_names_the_layer(self, bent):
        text = " ".join(bent["warnings"])
        assert "קווים פתוחים" in text
        assert "BEND" in text  # שם השכבה, כדי שאפשר יהיה לזהות מה זה
        assert "אינם מחויבים כחיתוך" in text

    def test_by_default_the_bend_line_is_not_charged(self, priced_client, bent):
        body = {"parts": [{"geometry_id": bent["geometry_id"], "material_key": "st37", "thickness_mm": 3.0}]}
        quote = priced_client.post("/api/quote", json=body).json()
        assert quote["lines"][0]["cut_length_mm"] == pytest.approx(800.0)
        assert any("קווים פתוחים" in w for w in quote["warnings"])

    def test_the_customer_can_say_it_really_is_a_cut(self, priced_client, bent):
        body = {
            "parts": [
                {
                    "geometry_id": bent["geometry_id"],
                    "material_key": "st37",
                    "thickness_mm": 3.0,
                    "cut_open_lines": True,
                }
            ]
        }
        quote = priced_client.post("/api/quote", json=body).json()
        assert quote["lines"][0]["cut_length_mm"] == pytest.approx(1050.0)
        # הוכרע — ולכן אין יותר שאלה פתוחה להכריז עליה.
        assert not any("קווים פתוחים" in w for w in quote["warnings"])

    def test_a_part_without_open_lines_is_untouched(self, priced_client):
        part = _manual(priced_client, width_mm=400, height_mm=250)
        assert part["open_line_count"] == 0
        quote = priced_client.post(
            "/api/quote",
            json={"parts": [{"geometry_id": part["geometry_id"], "material_key": "st37", "thickness_mm": 3.0}]},
        ).json()
        assert not any("קווים פתוחים" in w for w in quote["warnings"])


class TestHealthReportsWhatIsRunning:
    """מבחוץ, בלי SSH ובלי חשבון, אפשר לשאול איזה קוד רץ.

    הצורך נולד מפריסה שנכשלה והשאירה קבצים ישנים תחת `git log` חדש —
    מצב שאי אפשר היה לראות אלא מהקופסה עצמה.
    """

    def test_health_carries_the_running_commit(self, client):
        commit = client.get("/health").json()["commit"]
        # בסביבת פיתוח יש .git; בפריסה מארכיון הערך יהיה None וזה תקין.
        assert commit is None or (len(commit) == 12 and all(c in "0123456789abcdef" for c in commit))

    def test_it_matches_what_git_says(self, client):
        import subprocess

        commit = client.get("/health").json()["commit"]
        if commit is None:
            pytest.skip("אין .git — פריסה מארכיון")
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=Path(__file__).parent.parent
        ).stdout.strip()
        assert head.startswith(commit)

    def test_it_stays_open_and_leaks_nothing_else(self, client, monkeypatch):
        monkeypatch.setenv("APP_PASSWORD", "sod")
        body = client.get("/health").json()
        assert "commit" in body
        assert not any(k in body for k in ("materials", "rates", "prices"))


class TestPublicSignup:
    """הרשמה עצמית ציבורית — והגבול שהיא לא חוצה.

    ההכרעה (ינון, דרך האב, 23.8.2026): כל אחד יכול להירשם, ומי שנרשם
    רואה **מחיר סופי אחד**. כל מה שנבדק כאן הוא הגבול הזה: שהמנוע
    נפתח, שהפירוק לא יוצא, ושטבלת המחירים של אבא נשארת סגורה.
    """

    @pytest.fixture
    def gated(self, monkeypatch):
        monkeypatch.delenv("APP_PASSWORD", raising=False)
        identity.create_user(
            "ynon", "sod-arok-1", "ינון", {identity.CAP_PRICES_EDIT, identity.CAP_QUOTE_USE}
        )
        return TestClient(app)

    def _signup(self, client, username="orach", password="sisma-arukah"):
        return client.post(
            "/api/signup", json={"username": username, "password": password, "display_name": "אורח"}
        )

    def _public_client(self, gated):
        assert self._signup(gated).status_code == 201
        return gated

    # ---- שההרשמה בכלל עובדת ----

    def test_the_signup_screen_is_reachable_without_identity(self, gated):
        assert gated.get("/signup").status_code == 200
        assert gated.get("/login").status_code == 200

    def test_signing_up_creates_a_public_user_and_logs_them_in(self, gated):
        response = self._signup(gated)
        assert response.status_code == 201, response.text
        assert response.json()["capabilities"] == [identity.CAP_QUOTE_TOTAL]
        # נכנס ישר פנימה — מסך שמסתיים ב"עכשיו תתחבר" הוא אותו טופס פעמיים.
        assert gated.get("/api/me").json()["username"] == "orach"

    def test_a_taken_name_is_refused_and_says_so(self, gated):
        self._signup(gated)
        second = self._signup(gated)
        assert second.status_code == 400
        assert "כבר קיים" in second.json()["detail"]

    def test_names_that_impersonate_the_system_are_reserved(self, gated):
        for taken in ("crm", "admin", "editor-link"):
            assert self._signup(gated, username=taken).status_code == 400

    def test_a_short_password_is_refused_at_the_door(self, gated):
        assert self._signup(gated, password="1234").status_code == 400

    def test_signup_is_rate_limited_per_address(self, gated):
        codes = [self._signup(gated, username=f"orach{i}").status_code for i in range(9)]
        assert 429 in codes, "נקודת הרשמה ציבורית בלי הגבלת קצב היא יצירת חשבונות חופשית"

    def test_it_can_be_closed_without_a_deploy(self, gated, monkeypatch):
        monkeypatch.setattr(app_module, "SIGNUP_MODE", "closed")
        assert self._signup(gated).status_code == 403

    def test_approval_mode_creates_a_blocked_account(self, gated, monkeypatch):
        monkeypatch.setattr(app_module, "SIGNUP_MODE", "approval")
        response = self._signup(gated)
        assert response.status_code == 201 and response.json()["pending"] is True
        assert identity.list_users()[0]["disabled"] is True
        # ובלי סשן: אין מה לפתוח עד שמשחררים.
        assert gated.get("/api/config").status_code == 401

    # ---- הגבול: מחיר סופי אחד ----

    def test_a_public_quote_is_one_number_and_nothing_else(self, gated):
        client = self._public_client(gated)
        assert client.put("/api/tariff", json=TEST_TARIFF).status_code == 403
        # הטבלה מוזנת בידי מי שרשאי, דרך אותו תהליך.
        tariff_store.STATE.replace(TEST_TARIFF)

        part = client.post(
            "/api/manual", json={"shape": "rect", "width_mm": 200, "height_mm": 100}
        ).json()
        body = client.post(
            "/api/quote",
            json={
                "parts": [
                    {
                        "geometry_id": part["geometry_id"],
                        "material_key": "st37",
                        "thickness_mm": 3.0,
                        "quantity": 3,
                    }
                ]
            },
        ).json()

        assert body["total"] > 0
        assert body["detailed"] is False
        # **המדידה שהולידה את ההכרעה.** מ-material_cost לצד billed_area_mm2
        # משחזרים את מחיר הפלטה של אבא בחילוק אחד, ומ-margin_amount חלקי
        # subtotal את אחוז המרווח. אף אחד מהם לא נשלח.
        forbidden = (
            "lines", "groups", "material_cost", "cutting_cost", "piercing_cost",
            "billed_area_mm2", "cut_length_mm", "margin_amount", "subtotal",
            "parts_subtotal", "total_before_vat", "vat_amount", "unit_price",
            "tariff_source", "warnings",
        )
        leaked = [key for key in forbidden if key in body]
        assert not leaked, f"דלפו שדות פירוק למשתמש ציבורי: {leaked}"

    def test_the_same_quote_is_fully_detailed_for_an_internal_user(self, gated):
        tariff_store.STATE.replace(TEST_TARIFF)
        assert gated.post(
            "/api/login", json={"username": "ynon", "password": "sod-arok-1"}
        ).status_code == 200
        part = gated.post(
            "/api/manual", json={"shape": "rect", "width_mm": 200, "height_mm": 100}
        ).json()
        body = gated.post(
            "/api/quote",
            json={
                "parts": [
                    {
                        "geometry_id": part["geometry_id"],
                        "material_key": "st37",
                        "thickness_mm": 3.0,
                        "quantity": 3,
                    }
                ]
            },
        ).json()
        assert body["detailed"] is True
        assert body["lines"][0]["material_cost"] > 0

    def test_the_margin_is_not_in_the_public_config_either(self, gated):
        client = self._public_client(gated)
        tariff_store.STATE.replace(TEST_TARIFF)
        body = client.get("/api/config").json()
        # החומרים והעוביים חייבים לצאת — בלעדיהם אין מה לבחור במסך.
        assert body["materials"] and body["detailed"] is False
        assert "margin_pct" not in body and "waste_tiers" not in body

    def test_a_public_user_cannot_read_the_price_table_or_the_dashboard(self, gated):
        client = self._public_client(gated)
        assert client.get("/api/prices").status_code == 403
        assert client.get("/api/tariff").status_code == 403
        assert client.get("/prices").status_code == 403
        # הדשבורד הוא מפת המערכת מבפנים: מה ריק בטבלה, מצב השער, כמה משתמשים.
        assert client.get("/api/dashboard").status_code == 403

    def test_a_public_user_lands_on_the_engine_and_not_on_dads_form(self, gated):
        """הכלל היה "אין לו quote:use → לטופס", וזה נכון רק לאבא.

        מאז `quote:total` מי שנרשם מהרחוב נפל באותו תנאי ונשלח ל-
        `/prices` — 403 מיד אחרי הרשמה מוצלחת. נמצא בבדיקה בדפדפן.
        """
        self._signup(gated)
        landing = gated.post(
            "/api/login", json={"username": "orach", "password": "sisma-arukah"}
        ).json()["next"]
        assert landing == "/"

    def test_the_public_screen_is_a_different_screen(self, gated):
        client = self._public_client(gated)
        public = client.get("/", headers={"accept": "text/html"}).text
        assert "פירוט ההצעה" not in public and "טבלת התמחור" not in public

    # ---- השער של הטבלה הריקה ----

    def test_while_the_table_is_empty_a_public_user_gets_no_quote_at_all(self, gated):
        """הוראת האב: מערכת שמחזירה 0 למבקר גרועה מאין הרשמה."""
        client = self._public_client(gated)
        assert tariff_store.STATE.is_ready is False
        blocked = client.post("/api/manual", json={"shape": "rect", "width_mm": 200, "height_mm": 100})
        assert blocked.status_code == 503
        assert "בהרצה" in blocked.json()["detail"]

    def test_but_an_internal_user_still_sees_the_zeros(self, gated):
        """אבא וינון חייבים להמשיך לעבוד על טבלה ריקה — זה כל הבידוד."""
        assert gated.post(
            "/api/login", json={"username": "ynon", "password": "sod-arok-1"}
        ).status_code == 200
        assert gated.post(
            "/api/manual", json={"shape": "rect", "width_mm": 200, "height_mm": 100}
        ).status_code == 200

    # ---- בידוד הגיאומטריות ----

    def test_one_user_cannot_price_another_users_drawing(self, gated):
        """המפתחות היו g1, g2, g3 — רצף, ולכן שרטוט של אחר במרחק ניחוש."""
        tariff_store.STATE.replace(TEST_TARIFF)
        assert gated.post(
            "/api/login", json={"username": "ynon", "password": "sod-arok-1"}
        ).status_code == 200
        mine = gated.post(
            "/api/manual", json={"shape": "rect", "width_mm": 200, "height_mm": 100}
        ).json()["geometry_id"]
        assert not mine.startswith("g"), "מפתח שאפשר לנחש הוא רשימת הקבצים של כל השאר"

        gated.post("/api/logout")
        self._signup(gated)
        stolen = gated.post(
            "/api/quote",
            json={
                "parts": [
                    {"geometry_id": mine, "material_key": "st37", "thickness_mm": 3.0, "quantity": 1}
                ]
            },
        )
        assert stolen.status_code == 410


class TestBasicAuthFallsThroughToTheTable:
    """שם משתמש ששווה ל-APP_USER לא נסגר מול הסביבה בלבד.

    הפער: לינון יש גם `APP_PASSWORD` בסביבה וגם שורה במסד. הגרסה
    הקודמת עצרה מול הסביבה והחזירה None — כלומר מסך הכניסה עבד (הוא
    קורא ישירות ל-`authenticate`) ו-`curl -u ynon:…` נכשל, עם אותה
    סיסמה בדיוק. תוקן 23.8.2026 באישור האב.
    """

    @pytest.fixture
    def both(self, monkeypatch):
        monkeypatch.setenv("APP_USER", "ynon")
        monkeypatch.setenv("APP_PASSWORD", "sisma-mehasviva")
        identity.create_user("ynon", "sisma-mehatavla", "ינון", {identity.CAP_QUOTE_USE})
        return TestClient(app)

    def test_the_environment_password_still_works(self, both):
        assert both.get("/api/config", auth=("ynon", "sisma-mehasviva")).status_code == 200

    def test_and_so_does_his_own_row_in_the_table(self, both):
        assert both.get("/api/config", auth=("ynon", "sisma-mehatavla")).status_code == 200

    def test_a_password_that_is_neither_is_still_refused(self, both):
        assert both.get("/api/config", auth=("ynon", "lo-nachon-bichlal")).status_code == 401
