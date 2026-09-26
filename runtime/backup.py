"""Explicit local SQLite snapshot containing private user data."""
import argparse
import sqlite3
from contextlib import closing
from pathlib import Path


def backup(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file() or destination.exists() or source == destination:
        raise ValueError("Existing source and new destination required")

    destination.parent.mkdir(parents=True, exist_ok=True)

    with destination.open("xb"):
        pass

    source_uri = source.as_uri() + "?mode=ro"

    with closing(sqlite3.connect(source_uri, uri=True)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
            dst.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()

    backup(args.source, args.destination)
    print("Backup complete; protect this file as private data.")


if __name__ == "__main__":
    main()