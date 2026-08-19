"""בדיקות שכבת ה-API.

הדגש כאן אינו על "מחזיר 200" אלא על שלושת הכללים שהמערכת מבטיחה:
הטבלה היא מקור האמת היחיד, האילוץ הפיזי חוסם לפני כל חישוב כספי,
וכשלון תמיד מפורש ולעולם לא שקט.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from laser_pricing.api import tariff_store
from laser_pricing.api.app import app

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
        assert body["warnings"] == []

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
