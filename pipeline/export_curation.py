"""Emit approved community submissions as curation.yaml entries.

Usage (review before committing):
    python -m pipeline.export_curation              # print to stdout
    python -m pipeline.export_curation >> curation.yaml

Run it periodically (e.g. after a moderation session) and commit the result,
so verified community contributions live in git rather than only in the
server-side SQLite database.
"""
from __future__ import annotations

import sys
from datetime import date

import yaml

from .config import DB_PATH
from .curation import approved_community_entries
from .db import get_connection, init_db


def main(db_path: str = DB_PATH, out=sys.stdout) -> int:
    conn = get_connection(db_path)
    init_db(conn)
    try:
        entries = approved_community_entries(conn)
    finally:
        conn.close()
    out.write(f"# Approved community submissions, exported {date.today().isoformat()}\n")
    if entries:
        yaml.safe_dump(entries, out, allow_unicode=True, sort_keys=False)
    else:
        out.write("# (none)\n")
    return len(entries)


if __name__ == "__main__":
    n = main()
    print(f"exported {n} approved community entries", file=sys.stderr)
