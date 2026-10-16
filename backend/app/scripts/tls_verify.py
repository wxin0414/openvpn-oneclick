#!/usr/bin/env python3
"""
OpenVPN tls-verify hook: reject connections from disabled clients without revoking certificates.

Usage (server.conf):
  script-security 3
  tls-verify /root/ovpnManager/backend/app/scripts/tls_verify.py

Environment override:
  OVPNM_DB_PATH=/path/to/app.db   # optional, defaults to repo data/app.db
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path


DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "app.db"


def _load_db_path() -> Path:
    override = os.environ.get("OVPNM_DB_PATH")
    if override:
        return Path(override)
    return DEFAULT_DB_PATH


def main() -> int:
    cn = os.environ.get("common_name")
    if not cn:
        sys.stderr.write("tls-verify: missing common_name env\n")
        return 1

    db_path = _load_db_path()
    if not db_path.exists():
        sys.stderr.write(f"tls-verify: db not found at {db_path}\n")
        return 1

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write(f"tls-verify: cannot open db: {exc}\n")
        return 1

    try:
        cur = conn.execute("SELECT disabled FROM clients WHERE common_name = ?", (cn,))
        row = cur.fetchone()
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write(f"tls-verify: query failed: {exc}\n")
        return 1
    finally:
        conn.close()

    if row is None:
        sys.stderr.write(f"tls-verify: unknown client {cn}\n")
        return 1

    disabled = bool(row[0])
    if disabled:
        sys.stderr.write(f"tls-verify: client {cn} is disabled\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
