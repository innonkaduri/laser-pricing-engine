"""מי המשתמש, ומה מותר לו.

**זהו התפר.** כל הקוד שמעליו קורא לפונקציה אחת — `current_user` —
ואינו יודע מאיפה הגיעה הזהות. היום היא מגיעה מטבלה מקומית קטנה, ומחר
מטוקן שה-CRM מנפיק. החלפה כזאת נוגעת בקובץ הזה בלבד.

**מה שהמודול הזה במפורש אינו מחזיק:** לקוחות, הצעות כמסמך, ומספור.
הגבול שסוכם: כל דבר שיש עליו שם של לקוח שייך ל-CRM, והמנוע לא לומד
מי הלקוח. **הרשמה עצמית נכנסה 23.8.2026** (הכרעת ינון דרך האב), והיא
נשמרת בתוך הגבול הזה: שם משתמש, שם לתצוגה וסיסמה — ולא אימייל, לא
טלפון ולא שום דרך ליצור קשר. מי שנרשם כאן הוא מבקר שמתמחר לעצמו,
ולא לקוח שהמנוע מנהל.

**יכולות ולא תפקידים.** ארבעת התפקידים של ה-CRM (מזכיר, בונה הצעות,
מאשר, בעלים) ימופו אליהן כשהזהות תעבור לשם — ולא ישוכפלו לכאן, כי
תפקיד שמשוכפל בשני מקומות מתפצל בשקט.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
DB_PATH = Path(os.environ.get("USERS_DB", CONFIG_DIR / "users.db"))

CAP_PRICES_EDIT = "prices:edit"
"""מילוי טבלת המחירים ועריכת ה-JSON הגולמי. של אבא, ושל מי שמכייל."""

CAP_QUOTE_USE = "quote:use"
"""העלאה, תמחור, **ופירוק העלויות המלא**. פנימי בלבד."""

CAP_QUOTE_TOTAL = "quote:total"
"""העלאה, תמחור, וסכום סופי אחד. ברירת המחדל של מי שנרשם מהרחוב.

**זו לא אותה יכולת מוקטנת — זו הפרדה שנולדה ממדידה.** תשובת ההצעה
המלאה מחזירה `material_cost` לצד `billed_area_mm2` ו-`cutting_cost`
לצד `cut_length_mm`; חילוק אחד לכל שדה משחזר את מחיר הפלטה, את מחיר
החיתוך למטר ואת אחוז המרווח — כלומר *השימוש* במנוע חשף את טבלת
המחירים של אבא, גם בלי גישה לטופס. הכרעת ינון (23.8.2026): משתמש
ציבורי מקבל מספר אחד, והשדות האחרים **אינם נשלחים** — לא מוסתרים
במסך.
"""

QUOTE_CAPABILITIES = frozenset({CAP_QUOTE_USE, CAP_QUOTE_TOTAL})
"""מי רשאי להפעיל את המנוע בכלל. אחת מהן מספיקה."""

ALL_CAPABILITIES = frozenset({CAP_PRICES_EDIT, CAP_QUOTE_USE, CAP_QUOTE_TOTAL})

PUBLIC_SIGNUP_CAPABILITIES = frozenset({CAP_QUOTE_TOTAL})
"""מה שהרשמה עצמית מנפיקה. במקום אחד, כדי שלא ייפול לשם עוד משהו."""

USERNAME_MAX = 32
USERNAME_MIN = 3
PASSWORD_MIN = 8

SESSION_COOKIE = "laser_session"
SESSION_TTL_SECONDS = 12 * 3600

_ENV_SECRET = os.environ.get("SESSION_SECRET", "").strip()
SESSION_SECRET_IS_EPHEMERAL = not _ENV_SECRET
_SECRET = _ENV_SECRET.encode("utf-8") if _ENV_SECRET else secrets.token_bytes(32)
"""בלי SESSION_SECRET מהסביבה — מפתח אקראי לכל הפעלה.

הבחירה מכוונת: המערכת לא נפתחת בשקט (וזו הייתה התאונה של 18.8), אלא
הסשנים פשוט אינם שורדים הפעלה מחדש. `/health` מכריז על המצב הזה.
"""

# scrypt מהספרייה הסטנדרטית ולא bcrypt/argon2: אפס תלויות חדשות על
# קופסה עם שתי ליבות שמריצה עוד ארבעה פרויקטים.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


class UserError(Exception):
    """בקשה לא חוקית על משתמשים — שם תפוס, יכולת לא מוכרת וכדומה."""


@dataclass(frozen=True)
class User:
    username: str
    display_name: str
    capabilities: frozenset[str]
    source: str
    """מאיפה הגיעה הזהות: session / basic / editor-link / service."""

    def can(self, capability: str) -> bool:
        return capability in self.capabilities

    def can_any(self, capabilities: frozenset[str]) -> bool:
        return bool(self.capabilities & capabilities)

    @property
    def sees_cost_breakdown(self) -> bool:
        """האם מותר לו לראות **איך** התקבל המחיר, ולא רק כמה.

        זו השאלה היחידה שקובעת את צורת תשובת ההצעה, ולכן היא נשאלת
        כאן ולא בכל נתיב בנפרד. מי שאין לו — מקבל מספר אחד.
        """
        return CAP_QUOTE_USE in self.capabilities or CAP_PRICES_EDIT in self.capabilities


EDITOR_LINK_USER = User(
    username="editor-link",
    display_name="קישור טופס המחירים",
    capabilities=frozenset({CAP_PRICES_EDIT}),
    source="editor-link",
)
"""הקישור של אבא. מפתח מוגבל, לא אדם — ולכן אין לו שם משתמש אמיתי.

הוא נשאר בתוקף גם אחרי שנכנסת מערכת המשתמשים, כי אבא באמצע מילוי
הטבלה ואין שום סיבה להכניס אותו למסך כניסה עכשיו.
"""


# ---- סיסמאות ----


def hash_password(password: str) -> str:
    if not password:
        raise UserError("סיסמה ריקה.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, digest_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


# ---- הטבלה ----


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
               username     TEXT PRIMARY KEY,
               display_name TEXT NOT NULL,
               password_hash TEXT NOT NULL,
               capabilities TEXT NOT NULL,
               disabled     INTEGER NOT NULL DEFAULT 0,
               created_at   TEXT NOT NULL
           )"""
    )
    # **הגירה, כי המסד החי כבר קיים.** `CREATE TABLE IF NOT EXISTS`
    # אינו מוסיף עמודה לטבלה שכבר נוצרה, ולכן מסד שנולד לפני
    # הכניסה עם גוגל היה נשאר בלי העמודות ונופל בכל שאילתה עליהן.
    have = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    for column in ("google_sub", "email"):
        if column not in have:
            conn.execute(f"ALTER TABLE users ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS users_by_google ON users(google_sub)")
    return conn


def _row_to_user(row: sqlite3.Row, source: str) -> User:
    return User(
        username=row["username"],
        display_name=row["display_name"],
        capabilities=frozenset(json.loads(row["capabilities"])),
        source=source,
    )


def normalize_username(username: str) -> str:
    """שם משתמש חוקי, או `UserError` עם הסיבה.

    האכיפה יושבת כאן ולא במסך ההרשמה בכוונה: מאז שההרשמה ציבורית יש
    שני מסלולים לאותה טבלה (הטופס וה-CLI), וכלל שנאכף רק באחד מהם
    אינו כלל. `unicodedata` לא נדרש — האלפבית מצומצם לכוונה, כי שם
    משתמש נכנס לכתובות ולוגים.
    """
    username = username.strip().lower()
    if not username:
        raise UserError("שם משתמש ריק.")
    if not (USERNAME_MIN <= len(username) <= USERNAME_MAX):
        raise UserError(f"שם המשתמש חייב להיות באורך {USERNAME_MIN}–{USERNAME_MAX} תווים.")
    if not all(c.isascii() and (c.isalnum() or c in "._-") for c in username):
        raise UserError("שם משתמש יכול להכיל אותיות באנגלית, ספרות, נקודה, מקף וקו תחתון בלבד.")
    if not username[0].isalnum():
        raise UserError("שם משתמש חייב להתחיל באות או בספרה.")
    if username in RESERVED_USERNAMES:
        raise UserError("שם המשתמש הזה שמור.")
    return username


RESERVED_USERNAMES = frozenset(
    {"admin", "root", "crm", "editor-link", "system", "laser", "api", "support"}
)
"""שמות שהמערכת עצמה משתמשת בהם או שמתחזים לה.

`crm` ו-`editor-link` אינם שרירותיים: הם השמות שהמנוע מנפיק לטוקן
השירות ולקישור של אבא, ומשתמש רשום באותו שם היה קורא לבלבול בלוגים
בדיוק במקום שבו צריך לדעת מי עשה מה.
"""


GOOGLE_ONLY = "google"
"""ערך `password_hash` של חשבון שנפתח דרך גוגל. **אינו hash תקין**,
ולכן `verify_password` מחזיר עליו `False` תמיד — כלומר אי אפשר
להיכנס אליו בסיסמה, ואין כאן סיסמת ברירת מחדל שמישהו ינחש."""


def create_user(
    username: str,
    password: str,
    display_name: str = "",
    capabilities: set[str] | None = None,
    disabled: bool = False,
    google_sub: str = "",
    email: str = "",
) -> User:
    username = normalize_username(username)
    # חשבון גוגל נפתח בלי סיסמה בכוונה; כל השאר חייב אחת.
    if not google_sub and len(password) < PASSWORD_MIN:
        raise UserError(f"סיסמה קצרה מ-{PASSWORD_MIN} תווים. זו כתובת פומבית.")
    caps = set(capabilities or {CAP_QUOTE_USE})
    unknown = caps - ALL_CAPABILITIES
    if unknown:
        raise UserError(f"יכולת לא מוכרת: {', '.join(sorted(unknown))}")
    # שם לתצוגה נכנס לכותרת של כל מסך, ומאז ההרשמה הציבורית הוא מוקלד
    # בידי זרים. חיתוך אורך ושורה אחת — בלי לשפוט את התוכן.
    display = " ".join((display_name or username).split())[:60] or username

    with _connect() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise UserError(f"המשתמש {username} כבר קיים.")
        # **עמודות בשם ולא במיקום.** `INSERT INTO users VALUES (...)`
        # נשבר בשקט ברגע שנוספת עמודה — וזה בדיוק מה שקרה כאן
        # בהוספת `google_sub`. אותה מלכודת הפילה גם 23 בדיקות
        # כשנוסף שדה באמצע `FlatPanel`.
        conn.execute(
            """INSERT INTO users
                   (username, display_name, password_hash, capabilities,
                    disabled, created_at, google_sub, email)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                username,
                display,
                hash_password(password) if password else GOOGLE_ONLY,
                json.dumps(sorted(caps)),
                1 if disabled else 0,
                time.strftime("%Y-%m-%dT%H:%M:%S"),
                google_sub,
                email[:200],
            ),
        )
    return User(username, display, frozenset(caps), source="created")


def set_password(username: str, password: str) -> None:
    if len(password) < PASSWORD_MIN:
        raise UserError(f"סיסמה קצרה מ-{PASSWORD_MIN} תווים. זו כתובת פומבית.")
    with _connect() as conn:
        changed = conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (hash_password(password), username.strip().lower()),
        ).rowcount
    if not changed:
        raise UserError(f"אין משתמש בשם {username}.")


def set_display_name(username: str, display_name: str) -> str:
    """מחליף את שם התצוגה ומחזיר את מה שנשמר בפועל.

    אותו ניקוי בדיוק כמו ב-`create_user` — שורה אחת, עד 60 תווים —
    ובאותה פונקציה לא היה נכון: כלל שנאכף בהרשמה ולא בעריכה הוא כלל
    שכל משתמש יכול לעקוף בשתי לחיצות.
    """
    cleaned = " ".join((display_name or "").split())[:60]
    if not cleaned:
        raise UserError("שם לתצוגה אינו יכול להיות ריק.")
    with _connect() as conn:
        changed = conn.execute(
            "UPDATE users SET display_name = ? WHERE username = ?",
            (cleaned, username.strip().lower()),
        ).rowcount
    if not changed:
        raise UserError(f"אין משתמש בשם {username}.")
    return cleaned


def set_disabled(username: str, disabled: bool) -> None:
    with _connect() as conn:
        changed = conn.execute(
            "UPDATE users SET disabled = ? WHERE username = ?",
            (1 if disabled else 0, username.strip().lower()),
        ).rowcount
    if not changed:
        raise UserError(f"אין משתמש בשם {username}.")


def set_capabilities(username: str, capabilities: set[str]) -> None:
    unknown = set(capabilities) - ALL_CAPABILITIES
    if unknown:
        raise UserError(f"יכולת לא מוכרת: {', '.join(sorted(unknown))}")
    with _connect() as conn:
        changed = conn.execute(
            "UPDATE users SET capabilities = ? WHERE username = ?",
            (json.dumps(sorted(capabilities)), username.strip().lower()),
        ).rowcount
    if not changed:
        raise UserError(f"אין משתמש בשם {username}.")


def list_users() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
    return [
        {
            "username": r["username"],
            "display_name": r["display_name"],
            "capabilities": sorted(json.loads(r["capabilities"])),
            "disabled": bool(r["disabled"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def user_count() -> int:
    with _connect() as conn:
        return int(conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])


def load_user(username: str, source: str) -> User | None:
    """טוען מהטבלה בכל בקשה, ולכן ביטול משתמש נכנס לתוקף מיד."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? AND disabled = 0", (username.strip().lower(),)
        ).fetchone()
    return _row_to_user(row, source) if row else None


def authenticate(username: str, password: str) -> User | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? AND disabled = 0", (username.strip().lower(),)
        ).fetchone()
    if row is None:
        # השוואה מדומה כדי שזמן התשובה לא יסגיר אילו שמות קיימים.
        verify_password(password, hash_password("x"))
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return _row_to_user(row, source="password")


# ---- סשן ----


def issue_session(username: str, ttl_seconds: int = SESSION_TTL_SECONDS) -> str:
    payload = json.dumps(
        {"u": username, "exp": int(time.time()) + ttl_seconds}, separators=(",", ":")
    ).encode("utf-8")
    body = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = base64.urlsafe_b64encode(
        hmac.new(_SECRET, body, hashlib.sha256).digest()
    ).rstrip(b"=")
    return f"{body.decode()}.{signature.decode()}"


def read_session(token: str) -> str | None:
    """מחזיר את שם המשתמש מהעוגייה, או None אם היא פגומה, מזויפת או פגה."""
    try:
        body, signature = token.split(".", 1)
    except ValueError:
        return None
    expected = base64.urlsafe_b64encode(
        hmac.new(_SECRET, body.encode(), hashlib.sha256).digest()
    ).rstrip(b"=")
    if not hmac.compare_digest(signature.encode(), expected):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError):
        return None
    if int(data.get("exp", 0)) < time.time():
        return None
    username = data.get("u")
    return username if isinstance(username, str) else None


# ---- התפר עצמו ----


def current_user(request) -> User | None:
    """מי עומד מאחורי הבקשה הזאת, או None.

    שלושה מקורות, ובסדר הזה: עוגיית סשן (בני אדם בדפדפן), Basic auth
    (הסיסמה ההיסטורית של ינון וכלי שורת פקודה), וטוקן שירות של ה-CRM.
    כשהזהות תעבור ל-CRM — כאן משנים, ולא בשום מקום אחר.
    """
    token = request.cookies.get(SESSION_COOKIE, "")
    if token:
        username = read_session(token)
        if username:
            user = load_user(username, source="session")
            if user is not None:
                return user

    user = _from_basic_auth(request)
    if user is not None:
        return user

    return _from_service_token(request)


def _from_basic_auth(request) -> User | None:
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
        username, _, password = decoded.partition(":")
    except (ValueError, UnicodeDecodeError):
        return None

    # הסיסמה ההיסטורית מהסביבה. היא קדמה לטבלת המשתמשים, ינון וסקריפטים
    # משתמשים בה, והיא מקבלת את שתי היכולות.
    #
    # **סיסמה שגויה כאן אינה עוצרת, אלא ממשיכה לטבלה.** `APP_USER` הוא
    # "ynon", ולינון יש גם שורה משלו במסד עם סיסמה משלו. הגרסה הקודמת
    # החזירה כאן None וסגרה בפניו את Basic לנצח: מסך הכניסה עבד (הוא
    # קורא ישירות ל-`authenticate`) ו-`curl -u ynon:…` נכשל — אותה
    # סיסמה, שתי תשובות. נמצא ותוקן 23.8.2026 באישור האב.
    app_password = os.environ.get("APP_PASSWORD", "")
    app_user = os.environ.get("APP_USER", "ynon")
    if (
        app_password
        and hmac.compare_digest(username, app_user)
        and hmac.compare_digest(password, app_password)
    ):
        return User(app_user, app_user, ALL_CAPABILITIES, source="basic-env")

    if user_count() == 0:
        return None
    return authenticate(username, password)


def _from_service_token(request) -> User | None:
    """ה-CRM קורא למנוע כשירות, לא כאדם.

    הוא מקבל `quote:use` בלבד: הוא מתמחר, ולעולם לא עורך את הטבלה.
    """
    expected = os.environ.get("SERVICE_TOKEN", "").strip()
    supplied = request.headers.get("x-service-token", "")
    if not expected or not supplied:
        return None
    if not hmac.compare_digest(supplied, expected):
        return None
    return User("crm", "CRM ימיש", frozenset({CAP_QUOTE_USE}), source="service")


# ---- כניסה עם גוגל ----


def find_by_google(sub: str) -> User | None:
    """חשבון לפי מזהה גוגל. **לפי `sub` ולא לפי כתובת מייל.**

    כתובת מייל אפשר להעביר בין חשבונות ואפשר לשנות; `sub` הוא
    מזהה קבוע של חשבון גוגל ואינו מוחזר לעולם למישהו אחר. קישור
    לפי מייל היה מאפשר להשתלט על חשבון קיים על ידי רכישת דומיין
    ישן — וזה קרה כבר במערכות אחרות.
    """
    if not sub:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE google_sub = ? AND google_sub != ''", (sub,)
        ).fetchone()
    return _row_to_user(row, "session") if row else None


def username_from_email(email: str) -> str:
    """שם משתמש פנוי שנגזר מכתובת המייל.

    האלפבית של שמות המשתמש מצומצם בכוונה (הם נכנסים לכתובות
    וללוגים), וכתובת מייל יכולה להכיל תווים שאינם בו. לכן גוזרים,
    מנקים, ואם נשאר קצר מדי — משלימים.
    """
    local = (email or "").split("@")[0].lower()
    cleaned = "".join(c for c in local if c.isascii() and (c.isalnum() or c in "._-"))
    while cleaned and not cleaned[0].isalnum():
        cleaned = cleaned[1:]
    base = (cleaned or "user")[:USERNAME_MAX]
    while len(base) < USERNAME_MIN:
        base += "0"
    if base in RESERVED_USERNAMES:
        base = f"{base}1"[:USERNAME_MAX]

    with _connect() as conn:
        taken = {
            row[0]
            for row in conn.execute(
                "SELECT username FROM users WHERE username LIKE ?", (base[:20] + "%",)
            )
        }
    if base not in taken:
        return base
    # **סיומת מספרית ולא אקראית.** מי שנכנס פעם שנייה צריך לזהות
    # את השם שלו, ו-`yossi7` קריא יותר מ-`yossi-a91f`.
    for index in range(2, 500):
        candidate = f"{base[: USERNAME_MAX - len(str(index))]}{index}"
        if candidate not in taken:
            return candidate
    raise UserError("לא נמצא שם משתמש פנוי לכתובת הזאת.")


def link_google(username: str, sub: str, email: str) -> None:
    """מקשר חשבון קיים לגוגל. **לא דורס קישור קיים.**"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT google_sub FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            raise UserError(f"אין משתמש בשם {username}.")
        if row["google_sub"] and row["google_sub"] != sub:
            raise UserError("החשבון הזה כבר מקושר לחשבון גוגל אחר.")
        conn.execute(
            "UPDATE users SET google_sub = ?, email = COALESCE(NULLIF(email,''), ?) "
            "WHERE username = ?",
            (sub, email[:200], username),
        )
