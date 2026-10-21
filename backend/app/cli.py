"""Command-line utilities: `python -m app.cli <command>`.

  serve                 start the API (honours PDFWS_HOST / PDFWS_BACKEND_PORT)
  backup                create and verify a backup
  verify-backup DIR     verify a backup directory against its manifest
  restore DIR --confirm RESTORE [--no-safety-backup]
  verify-hashes         re-hash every stored file against the database
  verify-audit          verify the audit-log hash chain
  diagnostics           print tool availability
  sweep                 run expiration policies now
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pdfws")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    sub.add_parser("backup")
    vb = sub.add_parser("verify-backup")
    vb.add_argument("dir")
    rs = sub.add_parser("restore")
    rs.add_argument("dir")
    rs.add_argument("--confirm", required=True)
    rs.add_argument("--no-safety-backup", action="store_true")
    sub.add_parser("verify-hashes")
    sub.add_parser("verify-audit")
    sub.add_parser("diagnostics")
    sub.add_parser("sweep")
    a = p.parse_args(argv)

    from app.core.config import get_settings

    s = get_settings()
    s.ensure_dirs()
    if a.cmd == "serve":
        import uvicorn

        uvicorn.run("app.main:app_factory", factory=True, host=s.host, port=s.backend_port, log_level="info",
                    proxy_headers=False, server_header=False)
        return 0
    if a.cmd == "diagnostics":
        from app.core.tools import report_dicts

        rows = report_dicts()
        for r in rows:
            mark = "OK " if r["available"] else "MISSING"
            print(f"[{mark}] {r['name']:<34} {r['version'] or '':<12} {r['detail'] or ''}")
            if not r["available"]:
                print(f"         install: {r['install_hint']}")
        return 0 if all(r["available"] for r in rows) else 2
    from app.services import handlers  # noqa: F401

    if a.cmd == "backup":
        from app.services.backup import create_backup

        res = create_backup(progress=lambda n, i, t: print(f"  {n} {i}/{t}", file=sys.stderr))
        print(json.dumps(res, indent=2))
        return 0 if res["verified"] else 1
    if a.cmd == "verify-backup":
        from app.services.backup import verify_backup_dir

        res = verify_backup_dir(_resolve_backup(a.dir))
        print(json.dumps(res, indent=2))
        return 0 if res["ok"] else 1
    if a.cmd == "restore":
        from app.services.backup import restore_backup

        res = restore_backup(_resolve_backup(a.dir), confirm=a.confirm, safety_backup=not a.no_safety_backup)
        print(json.dumps(res, indent=2, default=str))
        return 0 if res["integrity"]["ok"] else 1
    from app.db.session import session_scope

    if a.cmd == "verify-hashes":
        from app.services.storage import verify_hashes

        with session_scope() as db:
            res = verify_hashes(db)
        print(json.dumps(res, indent=2))
        return 0 if res["ok"] else 1
    if a.cmd == "verify-audit":
        from app.services.audit import verify_chain

        with session_scope() as db:
            res = verify_chain(db)
        print(json.dumps(res, indent=2))
        return 0 if res["intact"] else 1
    if a.cmd == "sweep":
        from app.services.lifecycle import sweep

        print(json.dumps(sweep(), indent=2))
        return 0
    return 1


def _resolve_backup(d: str) -> Path:
    from app.core.config import get_settings

    p = Path(d)
    if not p.is_absolute() and not p.exists():
        p = get_settings().backups_dir / d
    return p.resolve()


if __name__ == "__main__":
    raise SystemExit(main())
