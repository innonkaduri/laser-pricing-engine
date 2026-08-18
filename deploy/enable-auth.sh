#!/usr/bin/env bash
# מדליק את שער הסיסמה על ה-VPS.
#
# **אין כאן שינוי קוד.** ה-middleware `basic_auth` ב-
# src/laser_pricing/api/app.py כבר קיים ועובד; הוא מותנה בקיום
# APP_PASSWORD. בלי המשתנה הוא מוותר על השער בשקט, ולכן השרת פתוח.
# הקובץ הזה רק מספק את המשתנים ליחידת ה-systemd.
#
# EnvironmentFile ולא Environment= ביחידה: סיסמה בתוך unit file נקראת
# על ידי כל משתמש (`systemctl cat`), בעוד /etc/laser-pricing.env הוא 600 root.
#
# הרצה:  sudo bash enable-auth.sh
set -euo pipefail

ENV_FILE=/etc/laser-pricing.env

if [[ $EUID -ne 0 ]]; then
  echo "צריך root: sudo bash $0" >&2
  exit 1
fi

# מאתרים את היחידה במקום לנחש את שמה.
UNIT="$(systemctl list-units --type=service --all --no-legend \
        | awk '{print $1}' | grep -iE '^laser' | head -1)"
if [[ -z "$UNIT" ]]; then
  echo "לא נמצאה יחידת laser. הרץ: systemctl list-units | grep -i laser" >&2
  exit 1
fi
echo "יחידה: $UNIT"

umask 077
cat > "$ENV_FILE" <<'ENVEOF'
# שער המערכת. ינון בלבד.
# נשמר <APP_PASSWORD> בכוונה: ינון כבר מחזיק אותו, והוא מעולם לא נשלח
# בתעבורה אל השרת הזה — מה שהיה פרוץ הוא היעדר השער, לא הסיסמה.
APP_USER=ynon
APP_PASSWORD=<APP_PASSWORD>

# מפתח מוגבל לטופס המחירים בלבד: /prices ו-/api/prices.
# אינו פותח את המנוע, את ההצעות ואת עורך ה-JSON.
EDITOR_PASSWORD=<EDITOR_PASSWORD>
ENVEOF
chown root:root "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo "נכתב $ENV_FILE (600 root)"

# drop-in ולא עריכת היחידה עצמה — שורד עדכון של הקובץ המקורי.
DROPIN="/etc/systemd/system/${UNIT}.d"
mkdir -p "$DROPIN"
cat > "$DROPIN/10-auth.conf" <<EOF
[Service]
EnvironmentFile=$ENV_FILE
EOF
echo "נכתב $DROPIN/10-auth.conf"

systemctl daemon-reload
systemctl restart "$UNIT"
sleep 3
systemctl is-active "$UNIT" || { journalctl -u "$UNIT" -n 30 --no-pager; exit 1; }

echo
echo "=== המשתנים אכן הגיעו לתהליך ==="
# הראיה הישירה: מה שהתהליך עצמו רואה. שמות בלבד, בלי ערכים.
PID="$(systemctl show -p MainPID --value "$UNIT")"
if [[ -n "$PID" && "$PID" != "0" ]]; then
  tr '\0' '\n' < "/proc/$PID/environ" | grep -oE '^(APP_USER|APP_PASSWORD|EDITOR_PASSWORD)=' \
    | sed 's/=$//; s/^/  נטען: /' || echo "  אזהרה: לא נמצאו המשתנים בתהליך."
fi

echo
echo "השער פעיל רק אם האימות מהאינטרנט מחזיר 401. אמת מבחוץ."
