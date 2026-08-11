"""נקודת הכניסה לשרת.

הפרויקט בנוי בפריסת src/, ולכן במקום להסתמך על PYTHONPATH שנקבע
בהגדרות הפריסה — משתנה שקל לשכוח ושנכשל רק בזמן ריצה — הנתיב נוסף כאן
במפורש. הרצה מקומית והרצה בענן משתמשות באותה פקודה בדיוק.

    uvicorn main:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from laser_pricing.api.app import app  # noqa: E402

__all__ = ["app"]
