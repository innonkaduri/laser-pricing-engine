"""תרגום בין טבלת התמחור לטופס שאפשר למלא בלי לדעת מה זה JSON.

המסך של אבא של ינון מציג שדות בעברית עם מספרים, ולא מבנה נתונים. שני
הכיוונים כאן, וכל אחד מהם שומר על כלל אחד:

`to_form` — מוציא **רק** את מה שאבא אמור למלא: מחירים. מדרגות הבזבוז,
מידות הפלטה ומדיניות השאריות אינן מופיעות בטופס, כי הן כיול של ינון
ולא מחיר. מי שלא רואה שדה לא יכול לשבור אותו בטעות.

`apply_form` — מזריק את המספרים בחזרה לתוך **עותק** של הטבלה המקורית,
ולא בונה טבלה חדשה. זה מכוון: כל מה שלא הופיע בטופס חייב לחזור בדיוק
כפי שהיה. טופס שבונה טבלה מאפס היה מוחק בשקט כל שדה שלא ידע עליו.
"""

from __future__ import annotations

import copy

MONEY_FIELDS = (
    ("plate_price", "מחיר פלטה שלמה"),
    ("cut_rate_per_m", 'מחיר חיתוך למטר'),
    ("pierce_price", "מחיר ניקוב"),
    ("min_charge_per_part", "מינימום לחלק"),
    ("weld_rate_per_m", "מחיר ריתוך למטר"),
    ("bend_price", "מחיר כיפוף אחד"),
    ("bend_rate_per_m", "תוספת כיפוף למטר"),
)

GENERAL_FIELDS = (
    ("vat_pct", 'מע"מ באחוזים'),
    ("margin_pct", "רווח באחוזים"),
    ("setup_fee_per_order", "דמי הקמה להזמנה"),
    ("setup_fee_per_part", "דמי הקמה לחלק"),
    ("min_order_total", "מינימום הזמנה"),
)

DEFAULT_DENSITY = 7850.0


def to_form(raw: dict) -> dict:
    """מבנה שטוח וידידותי: חומר → רשימת עוביים → מחירים."""
    materials: dict[str, dict] = {}
    for row in raw.get("rates", []):
        key = row.get("material_key", "")
        entry = materials.setdefault(
            key,
            {"key": key, "name": row.get("material_name", key), "rows": []},
        )
        entry["rows"].append(
            {
                "thickness_mm": _num(row.get("thickness_mm")),
                **{field: _num(row.get(field)) for field, _ in MONEY_FIELDS},
            }
        )

    for entry in materials.values():
        entry["rows"].sort(key=lambda r: r["thickness_mm"])

    return {
        "currency": raw.get("currency", "ILS"),
        "general": {field: _num(raw.get(field)) for field, _ in GENERAL_FIELDS},
        "materials": sorted(materials.values(), key=lambda m: m["name"]),
        "labels": {
            "money": [{"field": f, "label": lbl} for f, lbl in MONEY_FIELDS],
            "general": [{"field": f, "label": lbl} for f, lbl in GENERAL_FIELDS],
        },
    }


def apply_form(raw: dict, form: dict) -> dict:
    """מחזיר עותק של הטבלה עם המספרים מהטופס.

    שורה שהטופס מזכיר ואינה קיימת בטבלה נוצרת — כדי שאפשר יהיה להוסיף
    עובי חדש בלי לגעת ב-JSON. שורה שהטופס לא מזכיר נשארת כמות שהיא.

    שורה עם `"remove": true` נמחקת. זו הדרך היחידה למחוק דרך הטופס,
    והיא מפורשת בכוונה: 22 השורות בתבנית הן ניחוש, ואבא לא צריך למלא
    154 תאים בשביל עוביים שהוא לא מוכר — אבל "לא הזכרת אותה" עדיין
    לא אומר "מחק אותה", אחרת טופס חלקי היה מוחק חצי טבלה בשקט.
    """
    updated = copy.deepcopy(raw)
    updated.setdefault("rates", [])

    index = {
        (row.get("material_key"), _round_thickness(row.get("thickness_mm"))): row
        for row in updated["rates"]
    }

    for material in form.get("materials", []):
        key = str(material.get("key", "")).strip()
        if not key:
            continue
        name = str(material.get("name", "") or key).strip()
        for row in material.get("rows", []):
            thickness = _num(row.get("thickness_mm"))
            if thickness <= 0:
                continue
            if row.get("remove"):
                target = index.pop((key, _round_thickness(thickness)), None)
                if target is not None:
                    updated["rates"].remove(target)
                continue

            target = index.get((key, _round_thickness(thickness)))
            if target is None:
                target = {
                    "material_key": key,
                    "material_name": name,
                    "thickness_mm": thickness,
                    "density_kg_m3": DEFAULT_DENSITY,
                }
                updated["rates"].append(target)
                index[(key, _round_thickness(thickness))] = target
            target["material_name"] = name
            for field, _ in MONEY_FIELDS:
                if field in row:
                    target[field] = _num(row.get(field))

    general = form.get("general", {})
    for field, _ in GENERAL_FIELDS:
        if field in general:
            updated[field] = _num(general.get(field))

    return updated


def _num(value) -> float:
    """כל קלט מטופס הוא מחרוזת. שדה ריק הוא 0, לא שגיאה."""
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"ערך לא מספרי: {value!r}") from exc


def _round_thickness(value) -> float:
    return round(_num(value), 3)
