#!/usr/bin/env bash
# פריסה על הקופסה. מריצים כ-root: `bash /opt/laser/scripts/deploy.sh`
#
# הסקריפט הזה קיים כי שלוש פריסות רצופות נכתבו כטקסט חד-פעמי, ובכל אחת
# חזר אותו סוג באג. שלושת הלקחים שנצרבו בו, כולם מדיווח של האב מהשטח:
#
# 1. **משתמש השירות אינו בעלות הקוד.** `git merge` רץ כ-root, ולכן
#    הקבצים שייכים ל-root — ומי שגזר מהם בעלות יצר /var/lib/laser
#    בבעלות root וחסם את השירות מהקבצים של עצמו. מקור הסמכות היחיד
#    הוא `systemctl show -p User`.
#
# 2. **גלגול לאחור חייב להחזיר גם את git, לא רק את הקבצים.** גיבוי
#    שנלקח לפני המיזוג ומוחזר ב-`cp` אחריו משאיר את `git log` מדווח
#    על commit שאינו מה שרץ. פריסה שנכשלת וצועקת היא בסדר; פריסה
#    שמשאירה את git משקר שולחת את שנינו לחפש באג בקוד שאינו רץ.
#
# 3. **`sleep 3` קצר מדי.** השירות עלה בשנייה החמישית, הבדיקה רצה
#    בשלישית, והפריסה גלגלה לאחור קוד תקין.
#
# משתני סביבה: ROOT, UNIT, HEALTH_URL, WAIT_SECONDS, DRY_RUN, BACKUP_ROOT.

set -euo pipefail

ROOT="${ROOT:-/opt/laser}"
UNIT="${UNIT:-laser-pricing}"
HEALTH_URL="${HEALTH_URL:-https://laser.82.29.184.166.nip.io/health}"
WAIT_SECONDS="${WAIT_SECONDS:-30}"
DRY_RUN="${DRY_RUN:-0}"

GIT=(git -c "safe.directory=$ROOT")
cd "$ROOT"

# ---- משתמש השירות ----
SVC_USER="$(systemctl show "$UNIT" -p User --value || true)"
SVC_USER="${SVC_USER:-root}"   # יחידה בלי User= רצה כ-root
SVC_GROUP="$(id -gn "$SVC_USER")"
echo "השירות רץ כ: $SVC_USER:$SVC_GROUP"

BEFORE="$("${GIT[@]}" rev-parse HEAD)"
echo "נקודת חזרה: ${BEFORE:0:12}"

# ---- מה עומד להשתנות ----
"${GIT[@]}" fetch --quiet origin
TARGET="$("${GIT[@]}" rev-parse origin/main)"
if [[ "$BEFORE" == "$TARGET" ]]; then
  echo "אין מה למשוך — הקופסה כבר על ${TARGET:0:12}."
fi
echo
echo "=== מה משתנה ==="
"${GIT[@]}" --no-pager diff --stat HEAD origin/main || true
echo
echo "=== שינויים מקומיים (אמורים להיות ריקים) ==="
"${GIT[@]}" --no-pager status --short || true
echo

if [[ "$DRY_RUN" == "1" ]]; then
  echo "DRY_RUN — לא נגעתי בכלום."
  exit 0
fi

# ---- גיבוי ----
# הגיבוי הוא לקבצים שאינם במעקב git (טבלה, מסד, סביבה). את הקוד עצמו
# מחזירים ב-`git reset --hard` ולא מהעותק הזה — ראה לקח 2 למעלה.
BAK="${BACKUP_ROOT:-/root}/laser-deploy-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BAK"
[[ -d /var/lib/laser ]] && cp -a /var/lib/laser "$BAK/var-lib-laser"
[[ -f /etc/laser-pricing.env ]] && { cp -p /etc/laser-pricing.env "$BAK/"; chmod 600 "$BAK/laser-pricing.env"; }
echo "גיבוי הנתונים: $BAK"

# ---- אימות שהעץ אינו משקר ----
tree_matches_head() {
  [[ -z "$("${GIT[@]}" --no-pager diff --name-only HEAD)" ]]
}

wait_for_health() {
  local waited=0
  until curl -fsS --max-time 5 "$HEALTH_URL" > /tmp/health.out 2>/dev/null; do
    (( waited += 2 ))
    if (( waited >= WAIT_SECONDS )); then
      return 1
    fi
    sleep 2
  done
  echo "השירות ענה אחרי $waited שניות."
}

rollback() {
  echo "מגלגל לאחור אל ${BEFORE:0:12}." >&2
  "${GIT[@]}" reset --hard "$BEFORE"
  systemctl restart "$UNIT" || true
  if wait_for_health; then
    echo "השירות חזר על הגרסה הקודמת."
  else
    echo "השירות אינו עונה גם אחרי הגלגול. הנתונים ב-$BAK." >&2
    systemctl status "$UNIT" --no-pager -l | tail -20 >&2
  fi
  tree_matches_head || echo "אזהרה: עץ העבודה עדיין אינו תואם ל-HEAD." >&2
}

# ---- מיזוג ----
if ! "${GIT[@]}" merge --ff-only origin/main; then
  echo "המיזוג אינו fast-forward — מישהו כתב היסטוריה על הקופסה. לא נגעתי בכלום." >&2
  exit 3
fi

# ---- תחביר לפני הפעלה מחדש ----
if ! "$ROOT/.venv/bin/python3" -m compileall -q "$ROOT/src" > /dev/null; then
  echo "שגיאת תחביר בקוד החדש." >&2
  rollback
  exit 4
fi

systemctl restart "$UNIT"
if ! wait_for_health; then
  echo "השירות לא ענה תוך $WAIT_SECONDS שניות." >&2
  rollback
  exit 5
fi

python3 -m json.tool --no-ensure-ascii < /tmp/health.out

echo
echo "=== מה רץ בפועל ==="
echo "commit: $("${GIT[@]}" rev-parse --short HEAD)"
if tree_matches_head; then
  echo "עץ העבודה תואם ל-HEAD ✔"
else
  # זה בדיוק המצב שבו `git log` משקר על מה שרץ.
  echo "אזהרה: יש קבצים על הדיסק שאינם מה שב-HEAD:" >&2
  "${GIT[@]}" --no-pager diff --name-only HEAD >&2
fi
