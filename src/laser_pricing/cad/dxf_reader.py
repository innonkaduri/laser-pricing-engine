"""קריאת DXF עם ezdxf — חילוץ מקומי מלא, בלי AI ובלי טוקנים.

זה הרעיון המרכזי: הקובץ עצמו לעולם לא נשלח למודל. ezdxf מחלץ כאן
גיאומטריה מדויקת (מידות, אורך חיתוך, ניקובים), והמודל — אם בכלל —
מקבל אחר כך רק את `Part.summary()`, תקציר של עשרות טוקנים.

האתגר האמיתי בקבצי DXF: מלבן אינו ישות אחת אלא ארבע ישויות LINE נפרדות.
לכן חייבים לשחזר מתארים סגורים מקטעים בודדים — לזה משמשים
`ezdxf.edgeminer` ו-`ezdxf.edgesmith`.
"""

from __future__ import annotations

import ezdxf
import ezdxf.edgeminer as em
import ezdxf.edgesmith as es
from ezdxf import path as ezpath
from ezdxf import recover, units
from ezdxf.entities import DXFEntity

from ..domain.geometry import Contour, PartGeometry, Point
from .base import CadExtraction, CadReadError

# ישויות שנחתכות בפועל. כל השאר (טקסט, מידות, נקודות עזר) אינו גיאומטריית חיתוך.
CUTTABLE_TYPES = frozenset(
    {"LINE", "ARC", "CIRCLE", "ELLIPSE", "LWPOLYLINE", "POLYLINE", "SPLINE"}
)

# שכבות שנהוג לשים בהן מידות והערות ולא מתאר לחיתוך.
NON_CUTTING_LAYER_HINTS = ("dim", "text", "note", "annot", "hatch", "מידות", "הערות")

DEFAULT_GAP_TOL_MM = 0.05
DEFAULT_SAGITTA_MM = 0.02

_UNIT_NAMES = {
    0: "unitless",
    1: "inches",
    2: "feet",
    4: "mm",
    5: "cm",
    6: "m",
}


def read_dxf(
    file_path: str,
    *,
    gap_tol_mm: float = DEFAULT_GAP_TOL_MM,
    sagitta_mm: float = DEFAULT_SAGITTA_MM,
    assume_units_mm: bool = True,
) -> CadExtraction:
    """קורא קובץ DXF ומחזיר גיאומטריה מוכנה לתמחור, ביחידות מ"מ.

    gap_tol_mm — פער מותר בין קצוות שנחשבים מחוברים. קבצים אמיתיים
    כמעט לעולם אינם סגורים מתמטית.
    sagitta_mm — דיוק שיטוח קשתות לקטעים. משפיע ישירות על אורך החיתוך.
    """
    doc, warnings = _load(file_path)
    scale, unit_name, unit_warnings = _resolve_scale(doc, assume_units_mm)
    warnings.extend(unit_warnings)

    entities, skipped, layers = _collect_entities(doc)
    if not entities:
        raise CadReadError("לא נמצאה גיאומטריית חיתוך בקובץ — אין מה לתמחר.")

    # הסובלנויות ניתנות ביחידות מ"מ, אבל העבודה כאן היא ביחידות הקובץ.
    native_gap_tol = gap_tol_mm / scale
    native_sagitta = sagitta_mm / scale

    closed_entities = [e for e in entities if es.is_closed_entity(e)]
    open_entities = [e for e in entities if not es.is_closed_entity(e)]

    contours: list[Contour] = []
    open_contours: list[Contour] = []

    # ישויות שכבר סגורות בפני עצמן (מעגל, פוליליין סגור) — ישירות למתאר.
    for entity in closed_entities:
        points = _flatten_entity(entity, native_sagitta)
        if len(points) >= 3:
            contours.append(Contour(_scaled(points, scale), closed=True, layer=_layer_of(entity)))

    # ישויות פתוחות — משחזרים מהן שרשראות. בקובץ תקין כל צומת מחבר בדיוק
    # שני קטעים, ולכן שרשרת פשוטה מזהה כל מתאר באופן חד-משמעי וזול.
    if open_entities:
        chain_contours, chain_open, chain_warnings = _chains_to_contours(
            open_entities, native_gap_tol, native_sagitta, scale
        )
        contours.extend(chain_contours)
        open_contours.extend(chain_open)
        warnings.extend(chain_warnings)

    if not contours:
        raise CadReadError(
            "לא הצלחתי לשחזר אף מתאר סגור מהקובץ. "
            "ייתכן שהמתארים פתוחים או שהפער בין הקצוות גדול מהסובלנות."
        )

    geometry = PartGeometry(contours=contours, open_contours=open_contours)

    if open_contours:
        # האזהרה הזאת קדמה להתנהגות: היא אמרה "תומחרו כסימון" בזמן
        # שהקוד ספר אותם כחיתוך. מ-20.8.2026 הקוד עושה מה שהיא אומרת.
        total_open = sum(c.length for c in open_contours)
        open_layers = sorted({c.layer for c in open_contours if c.layer})
        detail = f", שכבות: {', '.join(open_layers)}" if open_layers else ""
        warnings.append(
            f"נמצאו {len(open_contours)} קווים פתוחים באורך כולל "
            f"{total_open:.1f} מ\"מ{detail}. הם אינם מחויבים כחיתוך — קו פתוח "
            f"בשרטוט פח הוא לרוב קו כיפוף או סימון. אם זה חיתוך אמיתי, "
            f"סמן זאת בחלק."
        )

    return CadExtraction(
        geometry=geometry,
        source_name=file_path,
        units_detected=unit_name,
        scale_to_mm=scale,
        warnings=warnings,
        skipped_entities=skipped,
        layers_seen=sorted(layers),
    )


# ---------- שלבים פנימיים ----------


def _load(file_path: str):
    """טוען את הקובץ, עם נפילה חזרה למצב שחזור לקבצים פגומים."""
    warnings: list[str] = []
    try:
        return ezdxf.readfile(file_path), warnings
    except IOError as exc:
        raise CadReadError(f"לא ניתן לפתוח את הקובץ: {exc}") from exc
    except ezdxf.DXFStructureError:
        try:
            doc, auditor = recover.readfile(file_path)
        except Exception as exc:
            raise CadReadError(f"הקובץ פגום ולא ניתן לשחזור: {exc}") from exc
        warnings.append(
            f"הקובץ היה פגום ושוחזר אוטומטית ({len(auditor.errors)} שגיאות). "
            f"בדוק את המידות לפני אישור ההצעה."
        )
        return doc, warnings


def _resolve_scale(doc, assume_units_mm: bool) -> tuple[float, str, list[str]]:
    """קובע מקדם המרה למ"מ לפי כותרת $INSUNITS.

    זו נקודת כשל שקטה וקלאסית: קובץ באינצ'ים שנקרא כמ"מ נותן חלק קטן
    פי 25 — שעובר את בדיקת הפלטה ומתומחר בחסר.
    """
    warnings: list[str] = []
    raw = int(doc.units or 0)
    name = _UNIT_NAMES.get(raw, f"code {raw}")

    if raw == 0:
        if not assume_units_mm:
            raise CadReadError("הקובץ אינו מציין יחידות ($INSUNITS=0) — חובה לבחור יחידות ידנית.")
        warnings.append(
            'הקובץ אינו מציין יחידות ($INSUNITS=0). מניח מילימטרים. '
            'אם השרטוט באינצ\'ים כל המידות שגויות פי 25.4 — ודא מול הלקוח.'
        )
        return 1.0, "unitless (assumed mm)", warnings

    factor = units.conversion_factor(raw, units.MM)
    if abs(factor - 1.0) > 1e-9:
        warnings.append(f'הקובץ ביחידות {name}; כל המידות הומרו למ"מ (מקדם {factor:g}).')
    return factor, name, warnings


def _collect_entities(doc) -> tuple[list[DXFEntity], dict[str, int], set[str]]:
    """אוסף ישויות חיתוך ממרחב המודל, כולל פיצוץ בלוקים (INSERT)."""
    skipped: dict[str, int] = {}
    layers: set[str] = set()
    collected: list[DXFEntity] = []

    def consume(entity: DXFEntity, depth: int = 0) -> None:
        dxftype = entity.dxftype()

        if dxftype == "INSERT":
            # בלוק הוא הפניה — הגיאומטריה נמצאת בתוכו וחייבת פיצוץ.
            if depth > 8:
                skipped["INSERT (קינון עמוק מדי)"] = skipped.get("INSERT (קינון עמוק מדי)", 0) + 1
                return
            try:
                for virtual in entity.virtual_entities():
                    consume(virtual, depth + 1)
            except Exception:
                skipped["INSERT (פיצוץ נכשל)"] = skipped.get("INSERT (פיצוץ נכשל)", 0) + 1
            return

        layer = _layer_of(entity)
        layers.add(layer)

        if dxftype not in CUTTABLE_TYPES:
            skipped[dxftype] = skipped.get(dxftype, 0) + 1
            return

        if _is_non_cutting_layer(layer):
            skipped[f"{dxftype} בשכבה '{layer}'"] = skipped.get(f"{dxftype} בשכבה '{layer}'", 0) + 1
            return

        collected.append(entity)

    for entity in doc.modelspace():
        consume(entity)

    return collected, skipped, layers


def _layer_of(entity: DXFEntity) -> str:
    return str(getattr(entity.dxf, "layer", "") or "0")


def _is_non_cutting_layer(layer: str) -> bool:
    low = layer.lower()
    return any(hint in low for hint in NON_CUTTING_LAYER_HINTS)


def _flatten_entity(entity: DXFEntity, sagitta: float) -> list[Point]:
    """הופך ישות כלשהי לרשימת נקודות דו-ממדית, כולל קשתות וספליינים."""
    try:
        path_obj = ezpath.make_path(entity)
    except Exception:
        return []
    return [(v.x, v.y) for v in path_obj.flattening(distance=max(sagitta, 1e-9))]


def _scaled(points: list[Point], scale: float) -> list[Point]:
    if scale == 1.0:
        return points
    return [(x * scale, y * scale) for x, y in points]


def _chains_to_contours(
    open_entities: list[DXFEntity], gap_tol: float, sagitta: float, scale: float
) -> tuple[list[Contour], list[Contour], list[str]]:
    """משחזר מתארים משרשראות של קטעים נפרדים."""
    warnings: list[str] = []
    edges = list(es.edges_from_entities_2d(open_entities, gap_tol=gap_tol))
    if not edges:
        return [], [], warnings

    deposit = em.Deposit(edges, gap_tol=gap_tol)
    chains = em.find_all_simple_chains(deposit)

    closed_contours: list[Contour] = []
    open_result: list[Contour] = []

    for chain in chains:
        points = _chain_points(chain, sagitta)
        if len(points) < 2:
            continue
        scaled = _scaled(points, scale)
        layer = _layer_of(chain[0].payload) if chain[0].payload is not None else ""

        if _is_chain_closed(chain, gap_tol) and len(scaled) >= 3:
            closed_contours.append(Contour(scaled, closed=True, layer=layer))
        else:
            open_result.append(Contour(scaled, closed=False, layer=layer))

    return closed_contours, open_result, warnings


def _is_chain_closed(chain, gap_tol: float) -> bool:
    start = chain[0].end if chain[0].is_reverse else chain[0].start
    last = chain[-1]
    end = last.start if last.is_reverse else last.end
    return start.distance(end) <= max(gap_tol, 1e-9)


def _chain_points(chain, sagitta: float) -> list[Point]:
    """מרכיב נקודות משרשרת, תוך שמירת עקמומיות הקשתות.

    לא משתמשים כאן ב-chain_vertices: הוא מחזיר רק קצוות קטעים, וקשת
    הייתה הופכת למיתר — אורך החיתוך היה יוצא קטן מדי והמחיר בחסר.
    """
    points: list[Point] = []
    for edge in chain:
        entity = edge.payload
        segment = _flatten_entity(entity, sagitta) if entity is not None else []
        if not segment:
            segment = [(edge.start.x, edge.start.y), (edge.end.x, edge.end.y)]
        if edge.is_reverse:
            segment = list(reversed(segment))
        if points and _close_enough(points[-1], segment[0]):
            segment = segment[1:]
        points.extend(segment)
    return points


def _close_enough(a: Point, b: Point, tol: float = 1e-6) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol
