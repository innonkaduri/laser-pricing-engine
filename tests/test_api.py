"""בדיקות שכבת ה-API.

הדגש כאן אינו על "מחזיר 200" אלא על שלושת הכללים שהמערכת מבטיחה:
הטבלה היא מקור האמת היחיד, האילוץ הפיזי חוסם לפני כל חישוב כספי,
וכשלון תמיד מפורש ולעולם לא שקט.
"""

from __future__ import annotations

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
    before = (state.raw, state.tariff, state.origin, state.error)
    yield
    state.raw, state.tariff, state.origin, state.error = before


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
