"""טבלת התמחור — מקור האמת היחיד למחירים.

הכלל שמעצב את כל המודול: **המנוע לא ממציא מחיר.** כל מספר כספי כאן
מגיע מינון. צירוף חומר+עובי שאינו בטבלה גורם לשגיאה מפורשת ולא לניחוש,
ולא לנפילה חזרה ל"ברירת מחדל סבירה" — ברירת מחדל סבירה היא בדיוק
הדרך שבה מנוע מתחיל להמציא מחירים בלי שאף אחד שם לב.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..nesting.nester import RemnantPolicy
from ..nesting.plate import Plate


class MissingTariffError(Exception):
    """אין רשומת תמחור לצירוף המבוקש. אין מחיר — ולא ננחש אחד."""


class InvalidTariffError(Exception):
    """הטבלה עצמה לא תקינה."""


@dataclass(frozen=True)
class MaterialRate:
    """שורה אחת בטבלה של ינון: חומר + עובי + המחירים שלהם."""

    material_key: str
    material_name: str
    thickness_mm: float
    plate_price: float
    cut_rate_per_m: float
    pierce_price: float = 0.0
    min_charge_per_part: float = 0.0
    density_kg_m3: float = 7850.0  # פלדה. משמש להצגת משקל בלבד, לא לתמחור.
    weld_rate_per_m: float = 0.0
    """מחיר ריתוך למטר תפר, לחלק שגדול מפלטה ומיוצר בכמה חתיכות.

    נשאר 0 עד שינון יזין אותו — ואז המנוע מדווח על כך במפורש במקום
    להלחים בחינם בשקט.
    """

    def __post_init__(self) -> None:
        if self.thickness_mm <= 0:
            raise InvalidTariffError(f"עובי לא חוקי ב-{self.material_key}: {self.thickness_mm}")
        for label, value in (
            ("מחיר פלטה", self.plate_price),
            ("מחיר חיתוך למטר", self.cut_rate_per_m),
            ("מחיר ניקוב", self.pierce_price),
            ("מינימום לחלק", self.min_charge_per_part),
            ("מחיר ריתוך למטר", self.weld_rate_per_m),
        ):
            if value < 0:
                raise InvalidTariffError(f"{label} שלילי ב-{self.material_key} {self.thickness_mm}")

    @property
    def key(self) -> tuple[str, float]:
        return (self.material_key, round(self.thickness_mm, 3))


@dataclass(frozen=True)
class WasteTier:
    """מדרגת בזבוז — הלב של התמחור הלא-בינארי.

    multiplier: על כמה משטח החלק נטו הלקוח מחויב. 1.25 = הלקוח משלם על
    125% משטח החלק, כלומר סופג 25% בזבוז. ינון קובע את המספרים.
    """

    max_waste_pct: float
    multiplier: float
    label: str = ""

    def __post_init__(self) -> None:
        if not 0 <= self.max_waste_pct <= 100:
            raise InvalidTariffError(f"אחוז בזבוז חייב להיות בין 0 ל-100, התקבל {self.max_waste_pct}")
        if self.multiplier < 1.0:
            raise InvalidTariffError(
                f"מכפיל בזבוז {self.multiplier} קטן מ-1 — משמעותו חיוב על פחות משטח החלק עצמו."
            )


@dataclass
class Tariff:
    """הטבלה המלאה."""

    rates: dict[tuple[str, float], MaterialRate] = field(default_factory=dict)
    waste_tiers: list[WasteTier] = field(default_factory=list)
    plate: Plate = field(default_factory=Plate)
    remnant_policy: RemnantPolicy = field(default_factory=RemnantPolicy)
    part_gap_mm: float = 5.0
    setup_fee_per_order: float = 0.0
    setup_fee_per_part: float = 0.0
    min_order_total: float = 0.0
    margin_pct: float = 0.0
    vat_pct: float = 18.0
    currency: str = "ILS"
    max_pieces_per_part: int = 0
    """תקרת חתיכות לחלק מפוצל. 0 = ללא הגבלה.

    ינון אישר פיצול לשתי פלטות. חלק שדורש יותר מזה עדיין מתומחר, אבל
    מקבל אזהרה מפורשת. מי שרוצה לחסום לגמרי מציב כאן 2.
    """
    source: str = "<inline>"

    def __post_init__(self) -> None:
        if not self.waste_tiers:
            raise InvalidTariffError("חובה להגדיר לפחות מדרגת בזבוז אחת.")
        self.waste_tiers = sorted(self.waste_tiers, key=lambda t: t.max_waste_pct)
        if self.waste_tiers[-1].max_waste_pct < 100:
            raise InvalidTariffError(
                "המדרגה האחרונה חייבת לכסות עד 100% בזבוז, אחרת יש עבודות בלי מחיר."
            )
        for pct in (self.margin_pct, self.vat_pct):
            if pct < 0:
                raise InvalidTariffError("אחוז מרווח או מע\"מ לא יכול להיות שלילי.")

    # ---- חיפוש ----

    def rate_for(self, material_key: str, thickness_mm: float) -> MaterialRate:
        """מחזיר את שורת התמחור, או נכשל במפורש. אין ניחוש ואין אינטרפולציה."""
        rate = self.rates.get((material_key, round(thickness_mm, 3)))
        if rate is None:
            available = sorted({t for m, t in self.rates if m == material_key})
            if available:
                raise MissingTariffError(
                    f'לחומר "{material_key}" אין מחיר לעובי {thickness_mm} מ"מ. '
                    f'עוביים שקיימים בטבלה: {", ".join(str(t) for t in available)}. '
                    f"הוסף שורה לטבלה — לא ננחש מחיר."
                )
            raise MissingTariffError(
                f'החומר "{material_key}" אינו קיים בטבלת התמחור. '
                f'חומרים קיימים: {", ".join(sorted({m for m, _ in self.rates})) or "אין"}.'
            )
        return rate

    def waste_tier_for(self, waste_pct: float) -> WasteTier:
        """המדרגה הראשונה שמכסה את אחוז הבזבוז שחושב."""
        for tier in self.waste_tiers:
            if waste_pct <= tier.max_waste_pct:
                return tier
        return self.waste_tiers[-1]

    def price_per_m2(self, rate: MaterialRate) -> float:
        """נגזר ממחיר הפלטה — ינון מזין איך שהוא קונה, לא מחיר למ"ר."""
        plate_area_m2 = self.plate.area_mm2 / 1_000_000.0
        return rate.plate_price / plate_area_m2

    @property
    def materials(self) -> list[dict[str, object]]:
        """מה שהממשק מציג בבוחר החומר והעובי."""
        grouped: dict[str, dict[str, object]] = {}
        for rate in self.rates.values():
            entry = grouped.setdefault(
                rate.material_key,
                {"key": rate.material_key, "name": rate.material_name, "thicknesses": []},
            )
            entry["thicknesses"].append(rate.thickness_mm)  # type: ignore[union-attr]
        for entry in grouped.values():
            entry["thicknesses"] = sorted(entry["thicknesses"])  # type: ignore[arg-type]
        return sorted(grouped.values(), key=lambda e: str(e["name"]))


# ---- טעינה מקובץ ----


def load_tariff(path: str | Path) -> Tariff:
    """טוען טבלת תמחור מקובץ JSON שינון עורך."""
    p = Path(path)
    if not p.exists():
        raise InvalidTariffError(f"קובץ טבלת התמחור לא נמצא: {p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InvalidTariffError(f"טבלת התמחור אינה JSON תקין: {exc}") from exc
    return tariff_from_dict(raw, source=str(p))


def tariff_from_dict(raw: dict, source: str = "<dict>") -> Tariff:
    if "rates" not in raw:
        raise InvalidTariffError('הטבלה חייבת לכלול "rates".')

    rates: dict[tuple[str, float], MaterialRate] = {}
    for row in raw["rates"]:
        try:
            rate = MaterialRate(**row)
        except TypeError as exc:
            raise InvalidTariffError(f"שורת תמחור לא תקינה: {row} ({exc})") from exc
        if rate.key in rates:
            raise InvalidTariffError(
                f"שורה כפולה לחומר {rate.material_key} בעובי {rate.thickness_mm}."
            )
        rates[rate.key] = rate

    tiers = [WasteTier(**t) for t in raw.get("waste_tiers", [])]
    plate = Plate(**raw["plate"]) if "plate" in raw else Plate()
    policy = RemnantPolicy(**raw["remnant_policy"]) if "remnant_policy" in raw else RemnantPolicy()

    known = {
        "part_gap_mm",
        "setup_fee_per_order",
        "setup_fee_per_part",
        "min_order_total",
        "margin_pct",
        "vat_pct",
        "currency",
        "max_pieces_per_part",
    }
    extras = {k: v for k, v in raw.items() if k in known}

    return Tariff(
        rates=rates, waste_tiers=tiers, plate=plate, remnant_policy=policy, source=source, **extras
    )
