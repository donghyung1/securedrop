"""No signup API. Create admins with a developer AWS profile and hidden input."""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from auth import password_hash
from config import Settings
from store import Store, now


def main(reset=False):
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("--profile")
    args = parser.parse_args()
    if not 1 <= len(args.username) <= 100:
        parser.error("username must contain 1-100 characters")
    store = Store(Settings(aws_profile=args.profile))
    existing = store.get("admins", {"username": args.username})
    if bool(existing) != reset:
        parser.error("Admin must already exist for reset; create requires a new username")
    password = getpass.getpass("Password: ")
    if not password or len(password) > 1024 or password != getpass.getpass("Confirm password: "):
        parser.error("Passwords must match and contain 1-1024 characters")
    store.table("admins").put_item(
        Item={
            "username": args.username,
            "password_hash": password_hash(password),
            "created_at": existing["created_at"] if existing else now().isoformat(),
            "failed_count": 0,
            "locked_until": 0,
        },
        ConditionExpression="attribute_exists(username)"
        if reset
        else "attribute_not_exists(username)",
    )
    # Existing sessions retain their documented eight-hour lifetime on password reset.
    print("Administrator password saved.")


if __name__ == "__main__":
    main()
