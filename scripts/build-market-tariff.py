#!/usr/bin/env python3
"""בניית טבלת המחירים מהערכת השוק של האב — **פעם אחת, 25.8.2026.**

הסקריפט כאן ולא בהיסטוריה כי הוא ההוכחה שהמספרים **נגזרו ולא הועתקו**.
הכרעת ינון: "תבדוק את המחירים בשוק ותמלא 10 אחוז מעל". האב אסף את
הטווחים (iron-laser.co.il · ironchampions.co.il · מרכז הברזל והפלדה),
לקח את אמצע הטווח והוסיף 10%, והורה במפורש: "אל תעתיק את המספרים שלי
בעיוור — אלומיניום ונירוסטה הן צפיפות אחרת ומחיר לק\"ג אחר לגמרי."

מחיר הפלטה מחושב מהצפיפות **שכבר בשורה**:
    4.5 מ"ר x (עובי/1000) x צפיפות x מחיר לק"ג
הבדיקה שהחישוב נכון: הוא מחזיר בדיוק את המספרים שהאב חישב בנפרד —
2מ"מ 310.86 מול 311, 3מ"מ 466.29 מול 466, 10מ"מ 1554.30 מול 1554.

    .venv/bin/python scripts/build-market-tariff.py   # כותב /tmp/tariff.json

**אינו כותב לטבלה החיה.** הקובץ נבדק, מגובה, ומותקן ביד.
"""

import json, sys
sys.path.insert(0, "src")

raw = json.load(open("config/tariff.example.json"))

PLATE_M2 = 3.0 * 1.5          # 3000x1500
ILS_PER_KG_STEEL = 4.40       # 4.00 אמצע שוק + 10% (האב, 25.8.2026)

# חיתוך למטר רץ, לפי טווחי עובי. המספרים של האב, כבר כוללים +10%.
CUT = {
    "st37":    [(3.0, 11.0), (6.0, 16.5), (10.0, 23.7), (20.0, 35.8)],
    "ss304":   [(3.0, 16.5), (6.0, 25.3), (10.0, 37.4), (20.0, 55.0)],
    "alu5083": [(3.0, 20.4), (6.0, 29.7), (10.0, 42.4), (15.0, 60.5)],
}
BEND_PER_M = 275.0            # 250 למטר + 10%. **וודאות נמוכה.**

# מחיר לק"ג יש לנו **רק לפלדה**. נירוסטה ואלומיניום נשארים 0 בכוונה.
KG_PRICE = {"st37": ILS_PER_KG_STEEL}


def cut_rate(key, t):
    for limit, rate in CUT.get(key, []):
        if t <= limit:
            return rate
    return 0.0


filled, held = [], []
for row in raw["rates"]:
    key, t, rho = row["material_key"], row["thickness_mm"], row["density_kg_m3"]
    kg = KG_PRICE.get(key)
    rate = cut_rate(key, t)

    # **שורה נכנסת רק אם אפשר למלא אותה שלמה.** שורה עם חיתוך בלי חומר
    # מייצרת מחיר שנראה נכון וחסר בו הרכיב הגדול ביותר — בדיוק צורת
    # הכשל שהאב הגדיר כמסוכנת: "נראה סביר ולכן יוצא ללקוח".
    if kg is None or rate == 0.0:
        held.append((key, t))
        continue

    mass = PLATE_M2 * (t / 1000.0) * rho
    row["plate_price"] = round(mass * kg, 2)
    row["cut_rate_per_m"] = rate
    row["bend_rate_per_m"] = BEND_PER_M
    # pierce_price נשאר 0 **וזו לא השמטה**: תעריף השוק הוא לפי מטר רץ
    # והוא כולל את הניקוב. חיוב נפרד היה חיוב כפול.
    filled.append((key, t, row["plate_price"], rate, mass))

raw["min_order_total"] = 350.0
raw["margin_pct"] = 0.0   # ה-10% כבר בתוך התעריפים. אחוז כאן = כפל.
raw["price_origin"] = (
    "הערכת שוק שנאספה בידי האב ב-25.8.2026, אמצע טווח +10% "
    "(iron-laser.co.il · ironchampions.co.il · מרכז הברזל והפלדה). "
    "אלה אינם המחירים של אבא ואינם מאושרים על ידו."
)
raw["price_low_confidence"] = ["bend_rate_per_m"]
raw["_README"] = [
    "המספרים כאן הם הערכת שוק ולא מחירים מאושרים — ראה price_origin.",
    "מחיר הפלטה נגזר: 4.5 מ\"ר x עובי x צפיפות השורה x 4.40 ש\"ח לק\"ג.",
    "נירוסטה, אלומיניום ופח מגולוון נשארו 0 כי אין מחיר לק\"ג לחומרים",
    "האלה. שורה חלקית מייצרת מחיר שנראה נכון וחסר בו החומר.",
    "pierce_price הוא 0 בכוונה: תעריף השוק למטר רץ כולל את הניקוב.",
]

json.dump(raw, open("/tmp/tariff.json", "w"), ensure_ascii=False, indent=2)

print(f"מולאו {len(filled)} שורות:")
print(f"  {'חומר':<9} {'עובי':>5} {'משקל פלטה':>11} {'מחיר פלטה':>10} {'חיתוך/מ׳':>9}")
for key, t, price, rate, mass in filled:
    print(f"  {key:<9} {t:>5} {mass:>9.1f}ק\"ג {price:>10.2f} {rate:>9.1f}")
print(f"\nנשארו 0 ({len(held)} שורות): " + ", ".join(f"{k} {t}" for k, t in held))

# אימות מול הקוד האמיתי
from laser_pricing.pricing.tariff import tariff_from_dict
t = tariff_from_dict(json.load(open("/tmp/tariff.json")), source="בדיקה")
print(f"\nהטבלה תקינה. price_origin באורך {len(t.price_origin)} תווים, "
      f"וודאות נמוכה: {t.price_low_confidence}")
