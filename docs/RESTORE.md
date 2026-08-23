# שחזור מנוע התמחור על קופסה ריקה

**נבדק בפועל ב-23.8.2026**, לא נכתב מהזיכרון: הורץ בסביבה זמנית על
הקופסה (`/tmp/restore-drill`, פורט 8095) בלי לגעת בשירות החי. מה
שכתוב כאן עבד; מה שנשבר בדרך רשום בסוף.

## מה יש ביד, ומה אין

| | איפה | מגובה? |
|---|---|---|
| הקוד | `github.com/innonkaduri/laser-pricing-engine` (**ציבורי**) | git |
| הטבלה + המשתמשים | `laser-<תאריך>.tar.gz` | `/opt/adi-backups` → המק של ינון, 60 יום |
| יחידת systemd, drop-in, nginx | `deploy/` בריפו | git |
| **הסודות** | `/etc/laser-pricing.env` | **לא. בכוונה.** |
| **תעודת TLS** | `/etc/letsencrypt/live/…` | **לא.** מנפיקים מחדש |

הריפו ציבורי, ולכן שלב 1 אינו דורש שום אישור. זה יתרון בשעת חירום.

## הנוהל

```bash
# 1. משתמש המערכת והתיקיות
useradd --system --home /opt/laser --shell /usr/sbin/nologin laser
install -d -o laser -g laser -m 755 /opt/laser
install -d -o laser -g laser -m 750 /var/lib/laser

# 2. הקוד
git clone https://github.com/innonkaduri/laser-pricing-engine.git /opt/laser
cd /opt/laser
python3 -m venv .venv                      # נבדק על Python 3.12.3
.venv/bin/pip install -r requirements.txt  # 27 חבילות
chown -R laser:laser /opt/laser

# 3. הנתונים, מהחבילה שעל המק של ינון
#    scp ~/backups/vps/laser-<תאריך>.tar.gz root@<קופסה>:/tmp/
tar xzf /tmp/laser-<תאריך>.tar.gz -C /tmp
cat /tmp/laser-backup/MANIFEST.txt          # ← קרא. מאיזה תאריך הטבלה?
cp -p /tmp/laser-backup/tariff.json /var/lib/laser/tariff.json
cp -p /tmp/laser-backup/users.db    /var/lib/laser/users.db
chown laser:laser /var/lib/laser/*
#    `cp -p` ולא `cp`: זמן השינוי הוא מה ש-/health מדווח כגיל הטבלה.

# 4. הסודות — נוצרים מחדש, לא משוחזרים
umask 077
cat > /etc/laser-pricing.env <<ENV
APP_USER=ynon
APP_PASSWORD=$(head -c 18 /dev/urandom | base64 | tr -d '=+/')
EDITOR_PASSWORD=$(head -c 18 /dev/urandom | base64 | tr -d '=+/')
SESSION_SECRET=$(head -c 32 /dev/urandom | base64 | tr -d '=+/')
TARIFF_PATH=/var/lib/laser/tariff.json
USERS_DB=/var/lib/laser/users.db
BACKUP_STATUS_FILE=/var/lib/laser/backup-status.json
ENV
chmod 600 /etc/laser-pricing.env

# 5. היחידות — מהריפו, לא ביד
install -m 644 deploy/laser-pricing.service       /etc/systemd/system/
install -m 644 deploy/laser-tariff-backup.service /etc/systemd/system/
install -m 644 deploy/laser-tariff-backup.timer   /etc/systemd/system/
install -D -m 644 deploy/10-auth.conf /etc/systemd/system/laser-pricing.service.d/10-auth.conf
systemctl daemon-reload
systemctl enable --now laser-pricing laser-tariff-backup.timer

# 6. TLS ו-nginx
install -m 644 deploy/nginx-laser.conf /etc/nginx/sites-available/laser
ln -sf /etc/nginx/sites-available/laser /etc/nginx/sites-enabled/laser
certbot certonly --nginx -d laser.82.29.184.166.nip.io   # התעודה לא מגובה
nginx -t && systemctl reload nginx
```

## אימות — מבחוץ, לא מהפלט של הפקודות

```bash
curl -s https://laser.82.29.184.166.nip.io/health | python3 -m json.tool
```

מה חייב להופיע:

- `commit` — זהה ל-HEAD שמשכת.
- `gate_on: true` — אם `false`, קובץ הסביבה או ה-drop-in לא נטענו.
- **`tariff_updated_at` ו-`tariff_age_days`** — התאריך של המחירים
  שנטענו. **זו השורה שקובעת אם שחזרת את הדבר הנכון.**
- `warnings` — "הטבלה חלקית" או "אם זה שחזור מגיבוי" הן אזהרות
  אמיתיות ולא רעש.

ואז: `/signup` ו-`/login` → 200 · `/`, `/api/config`, `/prices` → 401.

## מה שצריך להיעשות ידנית אחרי, ואיש לא יזכיר

1. **הסיסמאות של `aba` ו-`ynon` שרדו** (הגיבובים במסד), אבל
   `APP_PASSWORD` ו-`EDITOR_PASSWORD` **חדשים** — למסור לינון ולאבא.
2. **כל הסשנים מתו.** `SESSION_SECRET` חדש פוסל כל עוגייה קיימת.
3. **`SIGNUP_MODE` אינו בקובץ** ולכן ההרשמה חוזרת פתוחה. אם זה לא
   רצוי — להוסיף `SIGNUP_MODE=closed` לפני ההפעלה.
4. **לקרוא את `MANIFEST.txt`.** ראה למטה.

## מה נשבר בתרגיל

- **`10-auth.conf` היה קיים רק על הקופסה.** בלעדיו השירות עולה בלי
  שום משתנה סביבה — קורא את הטבלה מהנתיב הלא נכון ומחזיק
  `SESSION_SECRET` אקראי לכל הפעלה. **הוא עולה, והוא שגוי.** נוסף
  ל-`deploy/`. (הקובץ עצמו אינו סוד; הוא רק מפנה לקובץ הסודות.)
- **תעודת ה-TLS אינה מגובה בשום מקום.** צריך `certbot` והנפקה
  מחדש. `certbot 2.9.0` מותקן; התעודה הנוכחית בתוקף עד 14.11.2026.
- **הטבלה שבחבילה היא מ-18.8**, שורה מתומחרת אחת מתוך 22 — הניסוי,
  לא מחירי אבא. `/health` אומר את זה עכשיו במפורש:
  `"רק 1 מתוך 22 שורות מתומחרות — הטבלה חלקית"`.
  **שים לב מה *לא* תפס אותה:** בדיקת הגיל שתקה, כי הקובץ בן 5 ימים
  והסף הוא 14. מה שהציל היה בדיקת הכיסוי. שחזור בעוד שלושה שבועות
  יפיל את שתיהן.
- **תמחור מהטבלה המשוחזרת החזיר 0.0** על St37 3 מ"מ — כלומר השורה
  היחידה שמתומחרת בגיבוי אינה זו. המשמעות מדויקת: שחזור עיוור אינו
  מייצר "מחירים שגויים בכל מקום" אלא **אפס ברוב הצירופים ומחיר ניסוי
  באחד**. שתי הצורות שקטות באותה מידה בלי האזהרות.
