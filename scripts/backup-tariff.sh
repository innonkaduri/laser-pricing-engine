#!/usr/bin/env bash
# גיבוי טבלת המחירים החיה.
#
# הטבלה היא הקובץ היחיד במערכת שאי אפשר לשחזר מהקוד: היא עבודת ידיים
# של אבא של ינון. כל השאר נמצא ב-git.
#
# היעד נמצא **מחוץ** לעץ ה-git בכוונה. `git clean -fd` בתוך /opt/laser
# מוחק כל תיקייה לא-מנוטרת — כולל תיקיית גיבויים שתשב שם.
#
# שחזור:
#   systemctl stop laser-pricing
#   cp /var/backups/laser-tariff/tariff-<חותמת>.json /opt/laser/config/tariff.json
#   chown laser:laser /opt/laser/config/tariff.json
#   systemctl start laser-pricing

set -euo pipefail

SRC="${TARIFF_PATH:-/opt/laser/config/tariff.json}"
DEST="${BACKUP_DIR:-/var/backups/laser-tariff}"
KEEP="${KEEP:-60}"

# אבא עוד לא שמר. זה מצב תקין ולא שגיאה — טיימר שצועק כל שעה מאומן
# להתעלם ממנו, ואז גם הצעקה האמיתית לא תישמע.
if [[ ! -f "$SRC" ]]; then
  echo "אין עדיין טבלה חיה ב-$SRC — אין מה לגבות."
  exit 0
fi

# קובץ ריק או JSON פגום פירושו כתיבה שנקטעה. גיבוי כזה גרוע מכלום,
# כי הוא דוחף גיבוי תקין אל מחוץ לחלון השמירה.
if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$SRC" 2>/dev/null; then
  echo "שגיאה: $SRC אינו JSON תקין. לא מגבים." >&2
  exit 1
fi

mkdir -p "$DEST"
chmod 700 "$DEST"

NEWEST="$(find "$DEST" -maxdepth 1 -name 'tariff-*.json' -type f | sort | tail -1)"
if [[ -n "$NEWEST" ]] && cmp -s "$SRC" "$NEWEST"; then
  echo "ללא שינוי מאז $(basename "$NEWEST") — לא נוצר גיבוי."
  exit 0
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
TARGET="$DEST/tariff-$STAMP.json"
cp -p "$SRC" "$TARGET"
chmod 600 "$TARGET"
echo "נשמר גיבוי: $TARGET"

# שומרים רק גיבויים שנוצרו בפועל, כלומר גרסאות שונות זו מזו.
# ספירה מפורשת ולא `head -n -N`: הצורה ההיא היא הרחבה של GNU, ועל macOS
# היא נכשלת — כלומר הגיזום לא היה מתבצע והשגיאה הייתה נבלעת.
COUNT="$(find "$DEST" -maxdepth 1 -name 'tariff-*.json' -type f | wc -l | tr -d ' ')"
EXCESS=$(( COUNT - KEEP ))
if (( EXCESS > 0 )); then
  find "$DEST" -maxdepth 1 -name 'tariff-*.json' -type f | sort | head -n "$EXCESS" | while read -r old; do
    rm -f "$old"
    echo "נמחק גיבוי ישן: $(basename "$old")"
  done
fi
