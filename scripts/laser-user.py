#!/usr/bin/env python3
"""ניהול המשתמשים של מנוע התמחור — משורת הפקודה בלבד.

**אין מסך ניהול משתמשים, וזו החלטה ולא חוסר.** ארבעה משתמשים פנימיים,
בלי הרשמה עצמית ובלי אזור לקוחות (הלקוח מקבל הצעה מאושרת מה-CRM ואינו
נכנס לשום מסך). מסך ניהול היה משטח תקיפה שלם עבור פעולה שקורית
פעם ברבעון.

**על השרת מריצים עם הפייתון של הסביבה הווירטואלית**, לא עם זה של
המערכת — `python3` המערכתי אינו רואה את התלויות ונופל על
`ModuleNotFoundError: No module named 'fastapi'`:

  cd /opt/laser
  sudo -u laser .venv/bin/python3 scripts/laser-user.py list
  sudo -u laser .venv/bin/python3 scripts/laser-user.py add itai --caps quote:use
  sudo -u laser .venv/bin/python3 scripts/laser-user.py add aba  --caps prices:edit
  sudo -u laser .venv/bin/python3 scripts/laser-user.py passwd itai
  sudo -u laser .venv/bin/python3 scripts/laser-user.py disable itai

הסקריפט מוסיף את `src/` ל-`sys.path` בעצמו, ולכן אין צורך ב-PYTHONPATH.

הסיסמה לעולם אינה ארגומנט בשורת הפקודה: היא נשארת בהיסטוריית המעטפת
ונראית ב-`ps`. הסקריפט מבקש אותה, או קורא אותה מ-LASER_USER_PASSWORD.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from laser_pricing.api import identity  # noqa: E402


def _read_password(username: str) -> str:
    from_env = os.environ.get("LASER_USER_PASSWORD", "")
    if from_env:
        return from_env
    first = getpass.getpass(f"סיסמה חדשה ל-{username}: ")
    if first != getpass.getpass("שוב: "):
        sys.exit("הסיסמאות אינן זהות.")
    if len(first) < 8:
        sys.exit("סיסמה קצרה מ-8 תווים. זו כתובת פומבית — אל תקצר כאן.")
    return first


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="משתמש חדש")
    add.add_argument("username")
    add.add_argument("--name", default="", help="שם לתצוגה")
    add.add_argument(
        "--caps",
        default=identity.CAP_QUOTE_USE,
        help=f"יכולות מופרדות בפסיק. קיימות: {', '.join(sorted(identity.ALL_CAPABILITIES))}",
    )

    sub.add_parser("list", help="מי קיים ומה מותר לו")

    passwd = sub.add_parser("passwd", help="החלפת סיסמה")
    passwd.add_argument("username")

    caps = sub.add_parser("caps", help="עדכון יכולות")
    caps.add_argument("username")
    caps.add_argument("--caps", required=True)

    for name, flag in (("disable", True), ("enable", False)):
        p = sub.add_parser(name, help="חסימה/שחרור של משתמש")
        p.add_argument("username")
        p.set_defaults(disabled=flag)

    args = parser.parse_args()
    print(f"מסד המשתמשים: {identity.DB_PATH}")

    try:
        if args.command == "add":
            wanted = {c.strip() for c in args.caps.split(",") if c.strip()}
            user = identity.create_user(
                args.username, _read_password(args.username), args.name, wanted
            )
            print(f"נוצר: {user.username} ({', '.join(sorted(user.capabilities))})")

        elif args.command == "list":
            users = identity.list_users()
            if not users:
                print("אין עדיין משתמשים.")
            for u in users:
                mark = "חסום" if u["disabled"] else "פעיל"
                print(f"  {u['username']:<12} {mark:<5} {', '.join(u['capabilities'])}")

        elif args.command == "passwd":
            identity.set_password(args.username, _read_password(args.username))
            print(f"הסיסמה של {args.username} הוחלפה.")

        elif args.command == "caps":
            wanted = {c.strip() for c in args.caps.split(",") if c.strip()}
            identity.set_capabilities(args.username, wanted)
            print(f"היכולות של {args.username}: {', '.join(sorted(wanted))}")

        else:
            identity.set_disabled(args.username, args.disabled)
            print(f"{args.username}: {'נחסם' if args.disabled else 'שוחרר'}")

    except identity.UserError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
