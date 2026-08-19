#!/usr/bin/env bash
# גיבוי טבלת המחירים החיה.
#
# הטבלה היא הקובץ היחיד במערכת שאי אפשר לשחזר מהקוד: היא עבודת ידיים
# של אבא של ינון. כל השאר נמצא ב-git.
#
# היעד נמצא **מחוץ** לעץ ה-git בכוונה. `git clean -fd` בתוך /opt/laser
# מוחק כל תיקייה לא-מנוטרת — כולל תיקיית גיבויים שתשב שם.
#
# מגובה גם מסד המשתמשים (users.db). הוא קטן, אבל גם הוא לא ניתן לשחזור
# מהקוד — ובלעדיו אף אחד לא נכנס למערכת.
#
# שחזור:
#   systemctl stop laser-pricing
#   cp /var/backups/laser-tariff/tariff-<חותמת>.json /opt/laser/config/tariff.json
#   cp /var/backups/laser-tariff/users-<חותמת>.db     /opt/laser/config/users.db
#   chown laser:laser /opt/laser/config/tariff.json /opt/laser/config/users.db
#   systemctl start laser-pricing

set -euo pipefail

SRC="${TARIFF_PATH:-/opt/laser/config/tariff.json}"
DEST="${BACKUP_DIR:-/var/backups/laser-tariff}"
KEEP="${KEEP:-60}"
STAMP="$(date +%Y%m%d-%H%M%S)"
STATUS=0

mkdir -p "$DEST"
chmod 700 "$DEST"

# שומרים רק גיבויים שנוצרו בפועל, כלומר גרסאות שונות זו מזו.
# ספירה מפורשת ולא `head -n -N`: הצורה ההיא היא הרחבה של GNU, ועל macOS
# היא נכשלת — כלומר הגיזום לא היה מתבצע והשגיאה הייתה נבלעת.
prune() {
  local pattern="$1"
  local count excess
  count="$(find "$DEST" -maxdepth 1 -name "$pattern" -type f | wc -l | tr -d ' ')"
  excess=$(( count - KEEP ))
  if (( excess > 0 )); then
    find "$DEST" -maxdepth 1 -name "$pattern" -type f | sort | head -n "$excess" | while read -r old; do
      rm -f "$old"
      echo "נמחק גיבוי ישן: $(basename "$old")"
    done
  fi
}


# ---- טבלת המחירים ----
#
# **שני הגיבויים בלתי תלויים.** קודם היה כאן `exit 0` כשהטבלה עוד לא
# קיימת — ומכיוון שאבא עדיין לא הזין מחיר, הוא היה מדלג גם על מסד
# המשתמשים בכל שעה מחדש. אין תלות בין השניים, ולכן גם לא בזרימה.
backup_tariff() {
  # אבא עוד לא שמר. זה מצב תקין ולא שגיאה — טיימר שצועק כל שעה מאומן
  # להתעלם ממנו, ואז גם הצעקה האמיתית לא תישמע.
  if [[ ! -f "$SRC" ]]; then
    echo "אין עדיין טבלה חיה ב-$SRC — אין מה לגבות."
    return 0
  fi

  # קובץ ריק או JSON פגום פירושו כתיבה שנקטעה. גיבוי כזה גרוע מכלום,
  # כי הוא דוחף גיבוי תקין אל מחוץ לחלון השמירה.
  if ! python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$SRC" 2>/dev/null; then
    echo "שגיאה: $SRC אינו JSON תקין. לא מגבים." >&2
    return 1
  fi

  local newest target
  newest="$(find "$DEST" -maxdepth 1 -name 'tariff-*.json' -type f | sort | tail -1)"
  if [[ -n "$newest" ]] && cmp -s "$SRC" "$newest"; then
    echo "ללא שינוי מאז $(basename "$newest") — לא נוצר גיבוי."
    return 0
  fi

  target="$DEST/tariff-$STAMP.json"
  cp -p "$SRC" "$target"
  chmod 600 "$target"
  echo "נשמר גיבוי: $target"
  prune 'tariff-*.json'
}

backup_tariff || STATUS=1


# ---- מסד המשתמשים ----
#
# בלעדיו אף אחד לא נכנס למערכת, והוא אינו ניתן לשחזור מהקוד — בדיוק
# כמו הטבלה. `.backup` של sqlite ולא `cp`: העתקה של קובץ שנכתב באותו
# רגע נותנת מסד פגום שנראה תקין עד הפעם הראשונה שקוראים ממנו.
USERS_DB="${USERS_DB:-/opt/laser/config/users.db}"
if [[ ! -f "$USERS_DB" ]]; then
  echo "אין עדיין מסד משתמשים ב-$USERS_DB."
else
  USERS_TARGET="$DEST/users-$STAMP.db"
  NEWEST_DB="$(find "$DEST" -maxdepth 1 -name 'users-*.db' -type f | sort | tail -1)"
  # ההשוואה היא על **תוכן הטבלה** ולא על בתים. `cmp` על שני קבצי sqlite
  # החזיר "שונה" גם כשאיש לא נגע במשתמשים — ואז כל שעה יוצרת עותק,
  # והגיזום דוחף החוצה גרסאות אמיתיות. נתפס בהרצה, לא בקריאה.
  if VERDICT="$(python3 - "$USERS_DB" "$USERS_TARGET" "${NEWEST_DB:-}" <<'PY'
import hashlib, sqlite3, sys

src, dest, newest = sys.argv[1], sys.argv[2], sys.argv[3]

COLUMNS = "username, display_name, password_hash, capabilities, disabled, created_at"


def content_digest(path: str) -> str:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        rows = conn.execute(f"SELECT {COLUMNS} FROM users ORDER BY username").fetchall()
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as conn:
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        sys.exit("מסד המשתמשים פגום — לא מגבים.")
    # .backup ולא cp: העתקת קובץ שנכתב באותו רגע נותנת מסד פגום
    # שנראה תקין עד הפעם הראשונה שקוראים ממנו.
    with sqlite3.connect(dest) as out:
        conn.backup(out)

print("SAME" if newest and content_digest(dest) == content_digest(newest) else "NEW")
PY
)"; then
    if [[ "$VERDICT" == "SAME" ]]; then
      rm -f "$USERS_TARGET"
      echo "מסד המשתמשים ללא שינוי — לא נוצר גיבוי."
    else
      chmod 600 "$USERS_TARGET"
      echo "נשמר גיבוי משתמשים: $USERS_TARGET"
      prune 'users-*.db'
    fi
  else
    rm -f "$USERS_TARGET"
    echo "שגיאה בגיבוי מסד המשתמשים." >&2
    STATUS=1
  fi
fi

exit "$STATUS"
