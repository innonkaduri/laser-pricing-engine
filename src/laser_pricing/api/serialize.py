"""המרת אובייקטי המנוע ל-JSON לממשק.

נכתב ביד ולא ב-dataclasses.asdict: ה-Quote מחזיק בתוכו את כל תוצאת
הנסטינג, ופריסה אוטומטית שלו הייתה שולחת לדפדפן עשרות אלפי שורות
שאיש לא מציג. כאן נשלח בדיוק מה שהמסך מצייר.
"""

from __future__ import annotations

from ..domain.geometry import Contour, PartGeometry
from ..nesting.nester import PlateLayout
from ..pricing.engine import MaterialGroup, Quote, QuoteLine

MAX_PREVIEW_POINTS = 400


def contour_to_json(contour: Contour, is_hole: bool) -> dict:
    """נקודות המתאר, מדוללות אם צריך — לתצוגה בלבד, לא לחישוב.

    הדילול הוא של התצוגה ולעולם לא של המידות: השטח ואורך החיתוך
    מחושבים על המתאר המלא לפני שהגענו לכאן.
    """
    points = contour.points
    step = max(1, len(points) // MAX_PREVIEW_POINTS)
    thinned = points[::step] if step > 1 else points
    return {
        "points": [[round(x, 3), round(y, 3)] for x, y in thinned],
        "hole": is_hole,
        "closed": contour.closed,
        "layer": contour.layer,
    }


def geometry_to_json(geometry: PartGeometry) -> dict:
    holes = {id(c) for c in geometry.hole_contours}
    box = geometry.bbox
    return {
        "bbox": {
            "min_x": round(box.min_x, 3),
            "min_y": round(box.min_y, 3),
            "width_mm": round(box.width, 2),
            "height_mm": round(box.height, 2),
        },
        "net_area_mm2": round(geometry.net_area, 2),
        "gross_area_mm2": round(geometry.gross_area, 2),
        "cut_length_mm": round(geometry.cut_length, 2),
        "pierces": geometry.pierce_count,
        "holes": geometry.hole_count,
        "bodies": geometry.body_count,
        "contours": [contour_to_json(c, id(c) in holes) for c in geometry.contours]
        + [contour_to_json(c, False) for c in geometry.open_contours],
    }


def layout_to_json(layout: PlateLayout) -> dict:
    return {
        "index": layout.index,
        "plate": {
            "width_mm": layout.plate.width_mm,
            "height_mm": layout.plate.height_mm,
            "usable_width_mm": round(layout.plate.usable_width, 2),
            "usable_height_mm": round(layout.plate.usable_height, 2),
            "edge_margin_mm": layout.plate.edge_margin_mm,
        },
        "placements": [
            {
                "x": round(p.rect.x, 2),
                "y": round(p.rect.y, 2),
                "w": round(p.rect.w, 2),
                "h": round(p.rect.h, 2),
                "rotated": p.rotated,
                "item_id": p.item_id,
            }
            for p in layout.placements
        ],
        "remnants": [
            {
                "width_mm": round(r.width_mm, 1),
                "height_mm": round(r.height_mm, 1),
                "area_mm2": round(r.area_mm2, 1),
                "quality": round(r.quality, 3),
                "reusable_area_mm2": round(r.reusable_area_mm2, 1),
            }
            for r in layout.remnants
        ],
        "part_count": layout.part_count,
        "utilization_pct": round(layout.utilization * 100.0, 2),
    }


def group_to_json(group: MaterialGroup) -> dict:
    nesting = group.nesting
    return {
        "material_key": group.material_key,
        "material_name": group.material_name,
        "thickness_mm": group.thickness_mm,
        "part_names": group.part_names,
        "plates_used": group.plates_used,
        "utilization_pct": round(group.utilization_pct, 2),
        "raw_waste_pct": round(group.raw_waste_pct, 2),
        "effective_waste_pct": round(group.effective_waste_pct, 2),
        "tier": {
            "label": group.tier.label,
            "max_waste_pct": group.tier.max_waste_pct,
            "multiplier": group.tier.multiplier,
        },
        "reusable_area_mm2": round(nesting.reusable_area_mm2, 1),
        "effective_plates": round(nesting.effective_plates, 3),
        "billed_area_mm2": round(group.billed_area_mm2, 1),
        "consumed_area_mm2": round(group.consumed_area_mm2, 1),
        "plate_floor_applied": group.plate_floor_applied,
        "layouts": [layout_to_json(layout) for layout in nesting.layouts],
        "unplaced": [i.item_id for i in nesting.unplaced],
    }


def line_to_json(line: QuoteLine) -> dict:
    return {
        "part_name": line.part_name,
        "material_name": line.material_name,
        "thickness_mm": line.thickness_mm,
        "quantity": line.quantity,
        "unit_price": line.unit_price,
        "line_total": line.line_total,
        "material_cost": line.material_cost,
        "cutting_cost": line.cutting_cost,
        "piercing_cost": line.piercing_cost,
        "welding_cost": line.welding_cost,
        "setup_cost": line.setup_cost,
        "min_charge_applied": line.min_charge_applied,
        "net_area_mm2": line.net_area_mm2,
        "billed_area_mm2": line.billed_area_mm2,
        "cut_length_mm": line.cut_length_mm,
        "pierces": line.pierces,
        "weight_kg": line.weight_kg,
        "width_mm": line.width_mm,
        "height_mm": line.height_mm,
        "waste_pct": line.waste_pct,
        "waste_tier_label": line.waste_tier_label,
        "pieces": line.pieces,
        "weld_length_mm": line.weld_length_mm,
        "plate_floor_applied": line.plate_floor_applied,
    }


def quote_to_json(quote: Quote) -> dict:
    return {
        "lines": [line_to_json(line) for line in quote.lines],
        "groups": [group_to_json(g) for g in quote.groups],
        "rejected": [
            {
                "part_name": r.part_name,
                "reason": r.reason,
                "width_mm": round(r.width_mm, 2),
                "height_mm": round(r.height_mm, 2),
            }
            for r in quote.rejected
        ],
        "parts_subtotal": quote.parts_subtotal,
        "order_setup_fee": quote.order_setup_fee,
        "subtotal": quote.subtotal,
        "margin_amount": quote.margin_amount,
        "total_before_vat": quote.total_before_vat,
        "vat_amount": quote.vat_amount,
        "total": quote.total,
        "currency": quote.currency,
        "min_order_applied": quote.min_order_applied,
        "warnings": quote.warnings,
        "tariff_source": quote.tariff_source,
        "has_rejections": quote.has_rejections,
        "is_quotable": quote.is_quotable,
        "has_split_parts": quote.has_split_parts,
    }
