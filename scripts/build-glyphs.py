#!/usr/bin/env python3
"""גופן → טבלת מתארי חיתוך. **רץ ביד, לא בשרת.**

**למה בכלל צריך את זה:** גופן אינו מצייר אות אחת. הוא מצייר כמה
צורות שחופפות זו לזו, ומסתמך על מילוי **non-zero** של הרַסְטֶר כדי
שהן ייראו כאות אחת. במסך זה עובד; במכונת חיתוך זה אסון — כל מתאר
הוא מסלול, והמכונה הייתה חורצת קווים **בתוך** האות ומפילה חתיכות.
מה שצריך הוא **האיחוד** של הצורות, כלומר המתאר החיצוני האמיתי.

**למה בזמן בנייה:** הקופסה היא שתי ליבות המשותפות לחמישה פרויקטים
— הוראת האב. איחוד מצולעים מדויק בזמן בקשה, על כל הקלדה בעורך, היה
עיבוד כבד בדיוק במקום שאסור. כאן זה נעשה **פעם אחת**, התוצאה נכנסת
ל-git, והשרת רק מכפיל בקנה מידה.

**איך:** רסטר בדיוק גבוה עם מילוי non-zero → מעקב גבולות → פישוט
דאגלס-פויקר. זה מוותר על ייצוג העקום המדויק ומקבל מצולע, וזו אינה
פשרה: הפלט לחיתוך הוא **תמיד** מצולע, גם כשמתחילים מעקום. הסטייה
המוכרזת היא ‎0.02 מ"מ לאות בגובה 40 מ"מ — כחמישית מכֶּרֶף הלייזר.

    .venv/bin/python scripts/build-glyphs.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "assets" / "fonts"

SOURCES = {
    "assistant": "assistant-400.ttf",
    "assistant-bold": "assistant-700.ttf",
}

EM_RASTER = 2000
"""גובה הרסטר ביחידות פיקסל לכל em. **זה מה שקובע את הדיוק.**"""

SIMPLIFY_EM = 0.5
"""סף פישוט ביחידות גופן (upem=1000) — ‎0.02 מ"מ באות בגובה 40."""

CHARS = (
    "".join(chr(c) for c in range(0x5D0, 0x5EB))  # א–ת כולל סופיות
    + "׳״"
    + "".join(chr(c) for c in range(ord("A"), ord("Z") + 1))
    + "".join(chr(c) for c in range(ord("a"), ord("z") + 1))
    + "0123456789"
    + "-.,'\"()&/+:!?#@°%"
)


class _Flatten(BasePen):
    """עקומים → מקטעים. `BasePen` כבר הופך ריבועי לקובי."""

    def __init__(self, glyph_set, flatness):
        super().__init__(glyph_set)
        self.contours: list[list[tuple[float, float]]] = []
        self._cur: list[tuple[float, float]] = []
        self._flat = flatness

    def _moveTo(self, pt):
        self._flush()
        self._cur = [pt]

    def _lineTo(self, pt):
        self._cur.append(pt)

    def _curveToOne(self, p1, p2, p3):
        self._cur.extend(_cubic(self._cur[-1], p1, p2, p3, self._flat))

    def _closePath(self):
        self._flush()

    def _endPath(self):
        self._flush()

    def _flush(self):
        if len(self._cur) >= 3:
            self.contours.append(self._cur)
        self._cur = []


def _cubic(p0, p1, p2, p3, flat, depth=0):
    if depth >= 16 or _is_flat(p0, p1, p2, p3, flat):
        return [p3]
    m = lambda a, b: ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)  # noqa: E731
    a, b, c = m(p0, p1), m(p1, p2), m(p2, p3)
    d, e = m(a, b), m(b, c)
    f = m(d, e)
    return _cubic(p0, a, d, f, flat, depth + 1) + _cubic(f, e, c, p3, flat, depth + 1)


def _is_flat(p0, p1, p2, p3, flat):
    ux, uy = 3 * p1[0] - 2 * p0[0] - p3[0], 3 * p1[1] - 2 * p0[1] - p3[1]
    vx, vy = 3 * p2[0] - p0[0] - 2 * p3[0], 3 * p2[1] - p0[1] - 2 * p3[1]
    return max(ux * ux, vx * vx) + max(uy * uy, vy * vy) <= 16 * flat * flat


def rasterize(contours, res, pad=2):
    """מילוי non-zero — **בדיוק מה שהגופן התכוון אליו.**

    מילוי זוגי-אי-זוגי היה מבטל את החפיפות והופך אותן לחורים; זה
    בדיוק מה שראינו כשהאות ם יצאה עם חריצים שאינם בעיצוב שלה.
    """
    xs = [p[0] for c in contours for p in c]
    ys = [p[1] for c in contours for p in c]
    x0, y0 = min(xs) - pad / res, min(ys) - pad / res
    w = int((max(xs) - min(xs)) * res) + 2 * pad + 2
    h = int((max(ys) - min(ys)) * res) + 2 * pad + 2

    edges = []
    for ring in contours:
        for i in range(len(ring)):
            ax, ay = ring[i]
            bx, by = ring[(i + 1) % len(ring)]
            if ay != by:
                edges.append((ax, ay, bx, by))

    mask = [bytearray(w) for _ in range(h)]
    for row in range(h):
        y = y0 + (row + 0.5) / res
        hits = []
        for ax, ay, bx, by in edges:
            if (ay > y) != (by > y):
                t = (y - ay) / (by - ay)
                hits.append((ax + (bx - ax) * t, 1 if by > ay else -1))
        if not hits:
            continue
        hits.sort()
        wind = 0
        line = mask[row]
        for k in range(len(hits) - 1):
            wind += hits[k][1]
            if wind != 0:
                c0 = max(0, int((hits[k][0] - x0) * res - 0.5) + 1)
                c1 = min(w, int((hits[k + 1][0] - x0) * res - 0.5) + 1)
                for col in range(c0, c1):
                    line[col] = 1
    return mask, x0, y0, w, h


def trace(mask, x0, y0, w, h, res):
    """גבולות הרסטר → לולאות סגורות בקואורדינטות פינת-פיקסל.

    כל פאה של פיקסל מלא ששכנו ריק היא מקטע גבול מכוון; שרשור
    המקטעים נותן את הלולאות. הכיוון נבחר כך שמתאר חיצוני יוצא
    נגד כיוון השעון וחור עם כיוון השעון.
    """
    on = lambda r, c: 0 <= r < h and 0 <= c < w and mask[r][c]  # noqa: E731
    nxt: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for r in range(h):
        for c in range(w):
            if not mask[r][c]:
                continue
            if not on(r - 1, c):
                nxt.setdefault((c, r), []).append((c + 1, r))
            if not on(r, c + 1):
                nxt.setdefault((c + 1, r), []).append((c + 1, r + 1))
            if not on(r + 1, c):
                nxt.setdefault((c + 1, r + 1), []).append((c, r + 1))
            if not on(r, c - 1):
                nxt.setdefault((c, r + 1), []).append((c, r))

    loops = []
    while nxt:
        start = next(iter(nxt))
        loop = [start]
        cur = start
        while True:
            options = nxt.get(cur)
            if not options:
                break
            step = options.pop()
            if not options:
                del nxt[cur]
            cur = step
            if cur == start:
                break
            loop.append(cur)
        if len(loop) >= 4:
            loops.append([(x0 + x / res, y0 + y / res) for x, y in loop])
    return loops


def simplify(ring, tol):
    """דאגלס-פויקר על לולאה סגורה."""

    def dp(pts):
        if len(pts) < 3:
            return pts
        ax, ay = pts[0]
        bx, by = pts[-1]
        dx, dy = bx - ax, by - ay
        norm = (dx * dx + dy * dy) ** 0.5
        worst, idx = -1.0, 0
        for i in range(1, len(pts) - 1):
            px, py = pts[i]
            d = (
                abs(dy * px - dx * py + bx * ay - by * ax) / norm
                if norm
                else ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            )
            if d > worst:
                worst, idx = d, i
        if worst <= tol:
            return [pts[0], pts[-1]]
        return dp(pts[: idx + 1])[:-1] + dp(pts[idx:])

    if len(ring) < 4:
        return ring
    half = len(ring) // 2
    out = dp(ring[: half + 1])[:-1] + dp(ring[half:] + [ring[0]])[:-1]
    return out


def build(key: str, filename: str) -> dict:
    font = TTFont(FONT_DIR / filename, lazy=False)
    upem = font["head"].unitsPerEm
    cmap = font.getBestCmap()
    glyphs = font.getGlyphSet()
    widths = font["hmtx"].metrics
    res = EM_RASTER / upem

    out: dict[str, dict] = {}
    skipped = []
    for ch in CHARS:
        code = ord(ch)
        if code not in cmap:
            skipped.append(ch)
            continue
        name = cmap[code]
        pen = _Flatten(glyphs, 0.3)
        glyphs[name].draw(pen)
        entry: dict = {"advance": round(widths[name][0], 1), "contours": []}
        if pen.contours:
            mask, x0, y0, w, h = rasterize(pen.contours, res)
            rings = trace(mask, x0, y0, w, h, res)
            entry["contours"] = [
                [[round(x, 1), round(y, 1)] for x, y in simplify(r, SIMPLIFY_EM)]
                for r in rings
            ]
            entry["contours"] = [r for r in entry["contours"] if len(r) >= 3]
        out[ch] = entry

    space = cmap.get(ord(" "))
    return {
        "upem": upem,
        "cap_height": getattr(font["OS/2"], "sCapHeight", 0) or font["head"].yMax,
        "space": round(widths[space][0], 1) if space else upem * 0.3,
        "glyphs": out,
        "skipped": skipped,
    }


def main() -> int:
    table = {}
    for key, filename in SOURCES.items():
        data = build(key, filename)
        table[key] = data
        pts = sum(len(c) for g in data["glyphs"].values() for c in g["contours"])
        rings = sum(len(g["contours"]) for g in data["glyphs"].values())
        print(
            f"  {key}: {len(data['glyphs'])} תווים · {rings} מתארים · {pts} נקודות"
            + (f" · דילג על {' '.join(data['skipped'])}" if data["skipped"] else "")
        )
    target = FONT_DIR / "glyphs.json"
    target.write_text(
        json.dumps(table, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(f"  נכתב: {target.relative_to(ROOT)} · {target.stat().st_size:,} בתים")
    return 0


if __name__ == "__main__":
    sys.exit(main())
