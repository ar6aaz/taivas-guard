"""Admin commands. Run as: python -m ific_guard.cli <command>"""
import argparse
import json
import logging
from pathlib import Path

from .db import init_db


def main() -> None:
    parser = argparse.ArgumentParser(prog="ific_guard.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="Create the operational tables")
    commands.add_parser("sync", help="Run one sync pass against ITU now; can be called from a system crontab")
    add = commands.add_parser("import", help="Load a local IFIC .zip or .mdb instead of downloading it")
    add.add_argument("file", type=Path)
    add.add_argument("--domain", choices=["space", "terrestrial"], help="Default: from the file name")
    add.add_argument("--issue", type=int, help="Default: from the file name")
    export = commands.add_parser("export", help="Evaluate an imported issue and write ranked CSV/JSON with provenance")
    export.add_argument("--domain", choices=["space", "terrestrial"], required=True)
    export.add_argument("--issue", type=int, required=True)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--sha256", help="Select a specific imported revision instead of the latest")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    init_db()
    if args.command == "export":
        from .export import export_issue

        try:
            result = export_issue(args.domain, args.issue, args.out, sha256=args.sha256)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(result))
    elif args.command == "sync":
        from .sync.runner import run_sync

        print(json.dumps({"run_id": run_sync("manual")}))
    elif args.command == "import":
        from .sync.runner import ingest_file

        row, is_new = ingest_file(args.file, args.domain, args.issue, f"import:{args.file.name}")
        print(json.dumps({"artifact_id": row.id, "new": is_new, "sha256": row.sha256}))


if __name__ == "__main__":
    main()
