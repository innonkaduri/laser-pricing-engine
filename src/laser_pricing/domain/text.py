"""טקסט → מתארי חיתוך. **אותיות הן גיאומטריה, לא כיתוב.**

מכונת חיתוך אינה יודעת "לכתוב". היא יודעת ללכת לאורך מתאר. לכן שם
שנחתך בפח הוא **מצולע**, בדיוק כמו חור או חיתוך שהמשתמש גרר ביד,
והוא נכנס לאותו `cutouts` שכבר קיים — אותו מנוע, אותה טבלה, אותו
מחיר. אין כאן מסלול תמחור שני, ואסור שיהיה.

**המתארים באים מטבלה שנבנתה מראש** ולא מקובץ הגופן. גופן מצייר אות
בכמה צורות חופפות ונשען על מילוי non-zero של הרַסְטֶר; חיתוך שלהן
כמו שהן היה חורץ קווים **בתוך** האות. האיחוד נעשה פעם אחת ב-
`scripts/build-glyphs.py`, והתוצאה ב-`assets/fonts/glyphs.json`.
זה גם מה שמונע עיבוד כבד על קופסה שהיא שתי ליבות לחמישה פרויקטים.

**הבעיה שאי אפשר להתעלם ממנה, והיא פיזית ולא תוכנתית:** לאות כמו
**ם** או **ס** יש חלל סגור. אם חותכים את המתאר החיצוני **ואת**
הפנימי, החתיכה שבאמצע אינה מחוברת לכלום — היא **נופלת מהשולחן**
בזמן החיתוך. שלט שיוצא מהמכונה עם חור מרובע במקום ם הוא שלט פגום,
והלקוח מגלה את זה אחרי שהוא שילם.

**הפתרון הוא גשר**, וכך עושים בכל בית מלאכה לשילוט: משאירים פס
חומר צר שמחבר את האי לשאר הפח. כאן זה נעשה בגיאומטריה: הטבעת שבין
המתאר החיצוני לפנימי נחתכת **מינוס פס** ברוחב `bridge_mm`, והתוצאה
היא מצולע פשוט אחד. זה גם מה שהופך שני ניקובים לניקוב אחד, ולכן
המחיר יורד מעצמו — בלי שאף אחד יתקן מספר ביד.

**גובה האות נמדד לפי גובה הגוף של הגופן ולא לפי תיבת הטקסט.**
אילו קנה המידה היה נגזר מהתיבה בפועל, "יוסי" ו"קפץ" היו יוצאים
בגדלים שונים באותה הגדרה — כי לאות ץ יש זנב יורד ולאות י אין.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

GLYPH_TABLE = Path(__file__).resolve().parents[3] / "assets" / "fonts" / "glyphs.json"

DEFAULT_FONT = "assistant"
BRIDGE_MM = 1.5
"""רוחב הגשר שמחזיק אי. צר מדי נשבר ביד, רחב מדי נראה כמו טעות."""

MIN_HEIGHT_MM = 3.0
MAX_HEIGHT_MM = 2000.0
MAX_CHARS = 80
"""שם ארוך מזה אינו שלט אלא הדבקה, ומאות מתארים בבקשה אחת הם
בדיוק העיבוד הכבד שאסור על הקופסה הזאת."""


class TextError(ValueError):
    """טקסט שאי אפשר לחתוך. **תמיד מפורש** — ראה `CLAUDE.md`."""


@dataclass(frozen=True)
class TextOutline:
    contours: list[list[tuple[float, float]]]
    width_mm: float
    height_mm: float
    bridges: int
    bridged_chars: list[str]

    @property
    def pierces(self) -> int:
        return len(self.contours)


@lru_cache(maxsize=1)
def _table() -> dict:
    if not GLYPH_TABLE.exists():  # pragma: no cover - נבדק ב-_self_check
        raise TextError(
            f"טבלת המתארים חסרה: {GLYPH_TABLE}. "
            "יש להריץ scripts/build-glyphs.py ולקמט את התוצאה."
        )
    return json.loads(GLYPH_TABLE.read_text(encoding="utf-8"))


def fonts() -> list[str]:
    return sorted(_table())


# ── סדר ויזואלי ───────────────────────────────────────────────────


def _is_rtl(ch: str) -> bool:
    return "֐" <= ch <= "׿" or "؀" <= ch <= "ۿ"


def _is_strong_ltr(ch: str) -> bool:
    return ch.isalpha() and not _is_rtl(ch)


def visual_order(text: str) -> str:
    """סדר ויזואלי מסדר לוגי. **עברית אינה זקוקה לצורות מותנות.**

    בערבית צורת האות תלויה בשכנותיה; בעברית האותיות הסופיות הן
    תווים נפרדים בפני עצמם. לכן כל מה שנדרש כאן הוא **סדר**, וזו
    הסיבה שאין כאן מנוע עיצוב טקסט שלם אלא היפוך ריצות.

    ספרות נשארות משמאל לימין גם בתוך טקסט עברי — "ימיש 2026" ולא
    "ימיש 6202".
    """
    if not text:
        return text
    base_rtl = next((_is_rtl(c) for c in text if _is_rtl(c) or _is_strong_ltr(c)), False)
    if not base_rtl:
        return text

    runs: list[tuple[bool, list[str]]] = []
    for ch in text:
        rtl = _is_rtl(ch)
        neutral = not rtl and not _is_strong_ltr(ch) and not ch.isdigit()
        if runs and (neutral or runs[-1][0] == rtl):
            runs[-1][1].append(ch)
        else:
            runs.append((rtl, [ch]))

    out: list[str] = []
    for rtl, chars in reversed(runs):
        out.extend(reversed(chars) if rtl else chars)
    return "".join(out)


# ── גיאומטריה של מצולעים ──────────────────────────────────────────


def _area(ring) -> float:
    s = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        s += x1 * y2 - x2 * y1
    return s / 2


def _contains(ring, pt) -> bool:
    x, y = pt
    inside = False
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i - 1) % len(ring)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def _orient(ring, ccw: bool):
    return ring if (_area(ring) > 0) == ccw else ring[::-1]


def _along(ring, start: int, distance: float):
    """נקודה במרחק קשת נתון מקודקוד, **על הצלע ולא על הקודקוד**.

    זו הייתה טעות אמיתית ולא זהירות יתר: גרסה שהחזירה אינדקס בלבד
    "התקדמה" מקודקוד לקודקוד, וברִיק שצלעו 100 מ"מ בקשה להתקדם
    0.75 מ"מ דילגה על פינה שלמה. הגשר יצא ברוחב של רבע ריבוע,
    והמצולע חתך את עצמו.

    מוחזר גם הקודקוד ש**קודם לנקודה בכיוון ההליכה קדימה** — הוא
    זהה בשני הכיוונים, וזו הייתה הטעות השנייה.
    """
    n = len(ring)
    forward = distance >= 0
    left = abs(distance)
    i = start
    for _ in range(n):
        j = (i + 1) % n if forward else (i - 1) % n
        seg = math.dist(ring[i], ring[j])
        if seg >= left:
            t = (left / seg) if seg else 0.0
            pt = (
                ring[i][0] + (ring[j][0] - ring[i][0]) * t,
                ring[i][1] + (ring[j][1] - ring[i][1]) * t,
            )
            return pt, j
        left -= seg
        i = j
    return ring[start], start


def _walk(ring, a: int, b: int):
    n = len(ring)
    out = [ring[a]]
    i = a
    while i != b:
        i = (i + 1) % n
        out.append(ring[i])
    return out


def _segments_cross(p1, p2, p3, p4) -> bool:
    def side(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1, d2 = side(p3, p4, p1), side(p3, p4, p2)
    d3, d4 = side(p1, p2, p3), side(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def bridge(outer, inner, width: float):
    """טבעת + אי → מצולע פשוט אחד, עם פס חומר שמחזיק את האי.

    **הפס אינו קישוט ואינו הידור.** בלעדיו האי נחתך מכל צדדיו
    ונופל, והאות יוצאת כתם. הגשר נמתח במקום שבו הטבעת **הצרה
    ביותר** — שם הוא הקצר ביותר, ושם הוא הכי פחות נראה.
    """
    outer = _orient(list(outer), ccw=True)
    inner = _orient(list(inner), ccw=False)

    best = (float("inf"), 0, 0)
    for i, p in enumerate(outer):
        for j, q in enumerate(inner):
            d = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2
            if d < best[0]:
                best = (d, i, j)
    _, i, j = best

    half = width / 2
    pa1, ka1 = _along(outer, i, -half)
    pa2, ka2 = _along(outer, i, half)
    pb1, kb1 = _along(inner, j, half)
    pb2, kb2 = _along(inner, j, -half)
    if _segments_cross(pa1, pb1, pb2, pa2):
        (pb1, kb1), (pb2, kb2) = (pb2, kb2), (pb1, kb1)
    return [pa2] + _walk(outer, ka2, ka1) + [pa1, pb1] + _walk(inner, kb1, kb2) + [pb2]


# ── ההרכבה ────────────────────────────────────────────────────────


def outline(
    text: str,
    height_mm: float,
    *,
    font: str = DEFAULT_FONT,
    tracking_mm: float = 0.0,
    bridge_mm: float = BRIDGE_MM,
    hollow: bool = True,
) -> TextOutline:
    """טקסט → מתארים במ"מ, מיושרים לפינה שמאלית-תחתונה.

    `hollow=False` חותך צללית בלבד: החלל שבתוך ם אינו נחתך כלל.
    זו אפשרות לגיטימית לשלט מלא, והיא **אינה** ברירת המחדל, כי מי
    שמקליד שם מצפה לראות את האותיות שלו.
    """
    text = text.strip()
    if not text:
        raise TextError("אין טקסט לחתוך.")
    if len(text) > MAX_CHARS:
        raise TextError(f"עד {MAX_CHARS} תווים בשורה, התקבלו {len(text)}.")
    if not MIN_HEIGHT_MM <= height_mm <= MAX_HEIGHT_MM:
        raise TextError(
            f'גובה אות {height_mm} מ"מ מחוץ לתחום {MIN_HEIGHT_MM}–{MAX_HEIGHT_MM}.'
        )
    if bridge_mm <= 0:
        raise TextError('רוחב גשר חייב להיות חיובי.')

    table = _table()
    face = table.get(font)
    if face is None:
        raise TextError(f"גופן לא מוכר: {font!r}. הגופנים הקיימים: {', '.join(fonts())}")

    scale = height_mm / face["cap_height"]
    glyphs = face["glyphs"]
    missing = sorted({c for c in text if c != " " and c not in glyphs})
    if missing:
        raise TextError(
            "הגופן אינו מכיל את התווים: " + " ".join(missing) + ". אפשר להחליף גופן או תו."
        )

    rings: list[list[tuple[float, float]]] = []
    owners: list[str] = []
    cursor = 0.0
    for ch in visual_order(text):
        if ch == " ":
            cursor += face["space"] * scale + tracking_mm
            continue
        glyph = glyphs[ch]
        for ring in glyph["contours"]:
            rings.append([(x * scale + cursor, y * scale) for x, y in ring])
            owners.append(ch)
        cursor += glyph["advance"] * scale + tracking_mm

    if not rings:
        raise TextError("הטקסט אינו מייצר מתאר לחיתוך.")

    contours, bridges, bridged = _resolve(rings, owners, bridge_mm, hollow)

    xs = [x for ring in contours for x, _ in ring]
    ys = [y for ring in contours for _, y in ring]
    dx, dy = -min(xs), -min(ys)
    contours = [[(round(x + dx, 3), round(y + dy, 3)) for x, y in ring] for ring in contours]
    return TextOutline(
        contours=contours,
        width_mm=round(max(xs) - min(xs), 3),
        height_mm=round(max(ys) - min(ys), 3),
        bridges=bridges,
        bridged_chars=bridged,
    )


def _resolve(rings, owners, bridge_mm, hollow):
    """קובע מי חיצוני ומי אי, ומחבר את האיים.

    **הקינון נגזר מהגיאומטריה ולא מסדר המתארים בטבלה.** סדר המתארים
    הוא פרט של הגופן ושל תהליך הבנייה, ובנייה שסומכת עליו נשברת
    בגופן הבא שמישהו יוסיף.
    """
    depth = [
        sum(1 for k, other in enumerate(rings) if k != i and _contains(other, ring[0]))
        for i, ring in enumerate(rings)
    ]
    out: list[list[tuple[float, float]]] = []
    bridges = 0
    bridged: list[str] = []
    for i, ring in enumerate(rings):
        if depth[i] % 2 == 1:
            continue  # אי — מטופל דרך ההורה שלו
        if not hollow:
            out.append(ring)
            continue
        merged = ring
        for j, other in enumerate(rings):
            if depth[j] == depth[i] + 1 and _contains(ring, other[0]):
                merged = bridge(merged, other, bridge_mm)
                bridges += 1
                if owners[j] not in bridged:
                    bridged.append(owners[j])
        out.append(merged)
    return out, bridges, bridged


def _self_check() -> None:
    """**נכשל בעלייה ולא אצל המשתמש הראשון.**

    טבלה חסרה או פגומה היא תקלת פריסה, לא תקלת בקשה. אילו הבדיקה
    הייתה קורית בבקשה הראשונה, השירות היה עולה "בהצלחה" ומחזיר 500
    למי שהקליד שם.
    """
    for key in fonts():
        result = outline("ם", height_mm=20, font=key)
        if not result.contours or result.bridges != 1:
            raise TextError(f"הגופן {key} אינו מייצר אות תקינה עם גשר.")


_self_check()
