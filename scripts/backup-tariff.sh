#!/usr/bin/env bash
# גיבוי טבלת המחירים החיה.
#
# הטבלה היא הקובץ היחיד במערכת שאי אפשר לשחזר מהקוד: היא עבודת ידיים
# של אבא של ינון. כל השאר נמצא ב-git.
#
# היעד נמצא **מחוץ** לעץ ה-git בכוונה. `git clean -fd` בתוך /opt/laser
# מוחק כל תיקייה לא-מנוטרת — כולל תיקיית גיבויים שתשב שם.
#
# מאותה סיבה גם המקור עבר ל-/var/lib/laser (19.8.2026): קובץ שאי אפשר
# לשחזר אותו מהקוד לא צריך לשבת בתוך עץ עבודה שמושכים אליו, ממזגים בו
# ומנקים אותו. `git clean -fdx` מוחק גם קבצים ש-.gitignore מכסה.
#
# מגובה גם מסד המשתמשים (users.db). הוא קטן, אבל גם הוא לא ניתן לשחזור
# מהקוד — ובלעדיו אף אחד לא נכנס למערכת.
#
# **הגיבוי המקומי אינו מגן על אובדן הקופסה (23.8.2026).** הוא כותב
# ל-/var/backups/laser-tariff — כלומר לאותו דיסק שהוא מגבה. לכן הוא
# מניח גם עותק יומי ב-$OFFBOX_DIR (/opt/adi-backups), שממנו
# `scripts/vps-backup-pull.sh` על המק של ינון מושך ב-09:00 ושומר 60
# יום. הצינור הזה כבר קיים ומוציא adi · crm-db · sia-db · sia-files;
# הלייזר פשוט לא היה בו.
#
# **הצורה `laser-YYYY-MM-DD.tar.gz` אינה שרירותית.** שני הגיזומים
# בקצוות — `adi-backup` על הקופסה (14 יום) והמושך על המק (60 יום) —
# מתאימים אך ורק ל-`*.tar.gz` ו-`*.sql.gz`. קובץ בשם אחר היה נמשך
# ב-rsync ואז **לא מתגזם לעולם** בשני הצדדים.
#
# **מה שלא נכנס לחבילה, וזו החלטה:** /etc/laser-pricing.env. הוא מחזיק
# APP_PASSWORD, EDITOR_PASSWORD ו-SESSION_SECRET בטקסט גלוי, והוא היה
# יוצא מהקופסה אל לפטופ. הוא גם אינו נחוץ לשחזור — סיסמאות אפשר לקבוע
# מחדש, ואת הטבלה של אבא אי אפשר.
#
# שחזור:
#   systemctl stop laser-pricing
#   cp /var/backups/laser-tariff/tariff-<חותמת>.json /var/lib/laser/tariff.json
#   cp /var/backups/laser-tariff/users-<חותמת>.db     /var/lib/laser/users.db
#   chown laser:laser /var/lib/laser/tariff.json /var/lib/laser/users.db
#   systemctl start laser-pricing
#
# שחזור מהעותק שמחוץ לקופסה:
#   tar xzf ~/backups/vps/laser-<תאריך>.tar.gz -C /tmp
#   ואז אותם שני `cp` מ-/tmp/laser-backup/.

set -euo pipefail

SRC="${TARIFF_PATH:-/var/lib/laser/tariff.json}"
DEST="${BACKUP_DIR:-/var/backups/laser-tariff}"
OFFBOX_DIR="${OFFBOX_DIR:-/opt/adi-backups}"
KEEP="${KEEP:-60}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DAY="$(date +%Y-%m-%d)"
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
USERS_DB="${USERS_DB:-/var/lib/laser/users.db}"
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

# ---- העותק שיוצא מהקופסה ----
#
# הגיבוי המקומי מגן על האפליקציה: מחיקה בטעות, כתיבה שנקטעה, שורה
# שאבא הרס. הוא **אינו** מגן על אובדן הקופסה, כי הוא יושב על אותו
# דיסק. הצינור שכן מוציא נתונים כבר קיים — `adi-backup.timer` כותב
# ל-/opt/adi-backups, והמק של ינון מושך משם ב-09:00 ושומר 60 יום.
#
# החבילה נבנית מ**גיבויים** ולא מהקבצים החיים: הם כבר עברו כאן בדיקת
# JSON ובדיקת `PRAGMA integrity_check`, ו-users.db נלקח ב-`.backup`
# של sqlite ולא ב-`cp`. העתקת קובץ sqlite שנכתב באותו רגע נותנת מסד
# שנראה תקין עד הפעם הראשונה שקוראים ממנו — וזה בדיוק סוג הכישלון
# שגיבוי אמור למנוע.
#
# שם קבוע ליום, ולא חותמת לשנייה: הסקריפט רץ כל שעה, והמושך היומי
# צריך קובץ אחד לתאריך. כתיבה מחדש באותו יום היא רענון, לא כפילות.
offbox_copy() {
  local newest_tariff newest_users staging target
  newest_tariff="$(find "$DEST" -maxdepth 1 -name 'tariff-*.json' -type f | sort | tail -1)"
  newest_users="$(find "$DEST" -maxdepth 1 -name 'users-*.db' -type f | sort | tail -1)"

  if [[ -z "$newest_tariff" && -z "$newest_users" ]]; then
    echo "אין עדיין מה להוציא מהקופסה."
    return 0
  fi
  if [[ ! -d "$OFFBOX_DIR" ]]; then
    # לא יוצרים את התיקייה: היא שייכת לצינור של adi, ותיקייה שנוצרת
    # כאן בשקט פירושה שהשם השתנה שם ואיש לא ידע.
    echo "שגיאה: $OFFBOX_DIR אינה קיימת — העותק שמחוץ לקופסה לא נוצר." >&2
    return 1
  fi

  staging="$(mktemp -d)"
  # שם קבוע בתוך החבילה, כדי שהשחזור לא יצטרך לנחש חותמת.
  mkdir -p "$staging/laser-backup"
  [[ -n "$newest_tariff" ]] && cp -p "$newest_tariff" "$staging/laser-backup/tariff.json"
  [[ -n "$newest_users"  ]] && cp -p "$newest_users"  "$staging/laser-backup/users.db"
  printf 'laser-pricing\nמקור: %s\nנוצר: %s\ntariff: %s\nusers: %s\n' \
    "$(hostname)" "$(date +%Y-%m-%dT%H:%M:%S)" \
    "$(basename "${newest_tariff:-אין}")" "$(basename "${newest_users:-אין}")" \
    > "$staging/laser-backup/MANIFEST.txt"

  # כתיבה לקובץ זמני והחלפה אטומית: המושך רץ ב-09:00 ואין שום סיבה
  # שהוא יתפוס חבילה באמצע כתיבה.
  target="$OFFBOX_DIR/laser-$DAY.tar.gz"
  if tar czf "$target.partial" -C "$staging" laser-backup 2>/dev/null; then
    mv -f "$target.partial" "$target"
    chmod 600 "$target"
    echo "עותק מחוץ לקופסה: $target ($(stat -c %s "$target" 2>/dev/null || stat -f %z "$target") בתים)"
    rm -rf "$staging"
    return 0
  fi
  rm -f "$target.partial"
  rm -rf "$staging"
  echo "שגיאה: יצירת החבילה ל-$OFFBOX_DIR נכשלה." >&2
  return 1
}

offbox_copy || STATUS=1


# ---- דיווח לדשבורד ----
#
# תיקיית הגיבויים היא 700 root, והשירות רץ כמשתמש אחר — כלומר הוא אינו
# יכול לראות מתי גובה לאחרונה. במקום לפתוח את התיקייה (ובכך לחשוף את
# המחירים לכל מי שמריץ קוד בתהליך), הסקריפט מדווח החוצה קובץ מצב קטן
# שאין בו אלא חותמות זמן.
STATUS_FILE="${BACKUP_STATUS_FILE:-/var/lib/laser/backup-status.json}"
python3 - "$DEST" "$STATUS_FILE" "$STATUS" <<'PY' || echo "לא ניתן לכתוב את קובץ המצב." >&2
import json, os, pathlib, sys, time

dest, status_file, status = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), int(sys.argv[3])


def newest(pattern: str):
    files = sorted(dest.glob(pattern))
    if not files:
        return None, 0
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(files[-1].stat().st_mtime)), len(files)


tariff_at, tariff_count = newest("tariff-*.json")
users_at, users_count = newest("users-*.db")
payload = {
    "last_run": time.strftime("%Y-%m-%dT%H:%M:%S"),
    "ok": status == 0,
    "tariff": {"last_backup": tariff_at, "count": tariff_count},
    "users": {"last_backup": users_at, "count": users_count},
}
status_file.parent.mkdir(parents=True, exist_ok=True)
status_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
os.chmod(status_file, 0o644)  # חותמות זמן בלבד — השירות חייב לקרוא אותו
PY

exit "$STATUS"
