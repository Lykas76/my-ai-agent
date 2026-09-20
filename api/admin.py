"""Trusted local administration. Never exposed through HTTP."""
import argparse
import sqlite3
from dataclasses import asdict
import json

from config import Settings
from memory.sqlite import SQLiteMemory


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local assistant security administration")
    parser.add_argument("--database", default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user")
    create.add_argument("--user-id", default=None, help="Optional explicit ID to adopt verified legacy sessions")
    for name in ("issue-token", "disable-user", "enable-user"):
        command = commands.add_parser(name)
        command.add_argument("--user-id", required=True)
    revoke = commands.add_parser("revoke-token")
    revoke.add_argument("--token-id", required=True)
    for name in ("grant", "revoke-permission"):
        command = commands.add_parser(name)
        command.add_argument("--user-id", required=True)
        command.add_argument("--tool", required=True)
        command.add_argument("--permission", choices=("read", "write", "sensitive"), required=True)
    args = parser.parse_args(argv)
    try:
        with SQLiteMemory(args.database or Settings.from_env().database_path) as memory:
            store = memory.security
            if args.command == "create-user":
                print(json.dumps(asdict(store.create_user(args.user_id))))
            elif args.command == "issue-token":
                issued = store.issue_token(args.user_id)
                # The only command that prints a raw credential. It cannot be retrieved later.
                print(json.dumps({"user_id": issued.user_id, "token_id": issued.token_id, "token": issued.token}))
            elif args.command in ("disable-user", "enable-user"):
                store.set_status(args.user_id, "disabled" if args.command == "disable-user" else "active")
                print("User status updated")
            elif args.command == "revoke-token":
                store.revoke_token(args.token_id)
                print("Token revoked")
            else:
                method = store.grant if args.command == "grant" else store.revoke_permission
                method(args.user_id, args.tool, args.permission)
                print("Permission updated")
    except (ValueError, sqlite3.Error, OSError):
        parser.exit(1, "Security administration failed; check local configuration and identifiers.\n")


if __name__ == "__main__":
    main()
