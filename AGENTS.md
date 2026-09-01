# AGENTS.md — מנוע תמחור לייזר

## What this is
מנוע תמחור לחיתוך לייזר: DXF או קלט ידני → גיאומטריה → פיצול → נסטינג →
בזבוז מדורג → רצפת פלטה → מחיר. **נפרד לחלוטין מה-CRM** (פרויקט
"CRM ימיש חדש") — אין ביניהם שום חיבור מלבד `SERVICE_TOKEN`. **חוּוט
ונבדק קצה-לקצה ב-1.9.2026** (`POST /api/manual` → `POST /api/quote`
עם `X-Service-Token`, מול הפרודקשן ב-curl): מקבל `quote:use` בלבד,
`/api/prices` נשאר 403 גם איתו. חילוץ ה-DXF הוא `ezdxf` ומקומי
לגמרי; שום קובץ של לקוח לא יוצא החוצה.

חי על VPS: **https://laser.82.29.184.166.nip.io** · יחידה `laser-pricing`
· קוד ב-`/opt/laser` · **הריפו ציבורי**.

## Commands
- Run: `.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000`
- Test: `.venv/bin/python -m pytest tests/ -q`
- פריסה: `ssh -i ~/.ssh/mangpro_vps root@82.29.184.166` ואז
  `bash /opt/laser/scripts/deploy.sh` (קודם `DRY_RUN=1`). דוחפים
  ל-GitHub לפני — הסקריפט מושך מ-`origin/main`.
- משתמשים: `sudo -u laser env USERS_DB=/var/lib/laser/users.db
  .venv/bin/python3 scripts/laser-user.py list`

## Conventions
- **הטבלה היא מקור האמת היחיד למחיר.** צירוף חומר+עובי שאינו בטבלה →
  422 מפורש. **לעולם לא להמציא מחיר "סביר"** — מנוע שממציא גרוע ממנוע
  שמחזיר 0. גם אם מבקשים שוב.
- כישלון תמיד מפורש, לעולם לא שקט. 0 שמוצג בלי אזהרה נראה כמו מנוע עובד.
- שלוש יכולות: `prices:edit` · `quote:use` (פירוק מלא) · `quote:total`
  (מחיר סופי אחד, הרשמה ציבורית). כל הזהות עוברת דרך
  `identity.current_user` — תפר אחד, כדי שמעבר ל-CRM יגע בקובץ אחד.
- שדה חדש בתשובת `/api/quote` או `/api/config` חייב לעבור את השאלה
  **"אפשר לחלק ולקבל ממנו מחיר מהטבלה?"** — אם כן, הוא לא יוצא לציבור.
- מסמכים ותגובות בעברית. תגובה מסבירה **למה**, לא מה.

## Gotchas
- **`git pull` מושך יחידות systemd ואינו מתקין אותן.** `deploy/*.service`
  ו-`deploy/*.timer` בריפו יכולים להיות נכונים בזמן שהמותקן ב-
  `/etc/systemd/system/` ישן. **זה כבר הפיל את הגיבוי:** ארבעה ימים
  (19–23.8.2026) שבהם `laser-tariff-backup.service` גיבה
  `/opt/laser/config/*` במקום `/var/lib/laser/*`, דיווח `ok:true` בכל
  שעה, והגיבוי היחיד של המשתמשים היה **מסד ריק**. אחרי כל שינוי ביחידה:
  `install -m 644 deploy/<unit> /etc/systemd/system/` + `daemon-reload` +
  **ריצה אחת שבודקים את הפלט שלה**, לא "היחידה הצליחה".
- **`systemctl show -p Environment` משקר.** הוא מציג את ה-`Environment=`
  המוטבע ומחביא את ה-`EnvironmentFile` שדורס אותו. מקור האמת:
  `tr '\0' '\n' < /proc/$(systemctl show <unit> -p MainPID --value)/environ`.
- **`sudo -u laser` אינו טוען את ה-`EnvironmentFile` בכלל.** כל הרצה של
  `scripts/laser-user.py` חייבת `USERS_DB=` מפורש, אחרת היא כותבת למסד
  שהתהליך אינו קורא ומדפיסה "נוצר". השורה "מסד המשתמשים:" שהסקריפט
  מדפיס היא זו שקובעת.
- **הקבצים שאי אפשר לשחזר יושבים ב-`/var/lib/laser/`** ולא בעץ ה-git:
  `tariff.json`, `users.db`, `backup-status.json`. `git clean -fdx` מוחק
  גם מה ש-`.gitignore` מכסה.
- **הריפו ציבורי.** כל קומיט הוא פרסום. לבדוק סודות בתוכן **ובהודעות**
  לפני כל `push`.
- **הקופסה היא שתי ליבות המשותפות לחמישה פרויקטים.** עיבוד כבד
  (OCCT/STEP) רק בשירות נפרד עם מגבלת cgroup — הוראת האב.

## Session protocol
- Start: read docs/STATUS.md
- End: update docs/STATUS.md + commit
- **אחרי כל פריסה: למדוד מבחוץ.** `/health` מחזיר את ה-commit שרץ
  בפועל. "הקוד נכתב" אינו "זה רץ", ו"היחידה הצליחה" אינו "זה עבד".
