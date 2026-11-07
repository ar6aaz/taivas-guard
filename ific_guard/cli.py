"""Local, deterministic IFIC import and screening commands."""

import argparse
import csv
import json
import logging
import sys
import time
from dataclasses import asdict, fields
from pathlib import Path

import duckdb

from .store import AnalysisStore, Candidate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=".cache/ific.duckdb")
    parser.add_argument("--threads", type=int)
    parser.add_argument("--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("import-ific", help="Cache selected tables from an extracted .mdb")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--domain", choices=("space", "terrestrial"), required=True)
    ingest.add_argument("--issue", type=int, required=True)
    stations = commands.add_parser("import-stations", help="Cache national receiver assignments")
    stations.add_argument("path", type=Path)
    stations.add_argument("--sheet", default="Frequencies of interest")
    screen = commands.add_parser("screen", help="Write conservative candidate assignments as CSV")
    screen.add_argument("--source", required=True)
    screen.add_argument("--stations", required=True)
    screen.add_argument("--gap-mhz", type=float, default=0)
    screen.add_argument("--max-distance-km", type=float, help="Optional terrestrial radius prefilter; a modelling choice, not a protection limit")
    screen.add_argument("--output", type=Path, required=True)
    matches = commands.add_parser("matches", help="Retrieve station IDs for one candidate")
    matches.add_argument("--source", required=True)
    matches.add_argument("--stations", required=True)
    matches.add_argument("--assignment", required=True)
    matches.add_argument("--gap-mhz", type=float, default=0)
    matches.add_argument("--output", type=Path, required=True)
    diff = commands.add_parser("changes", help="Compare assignment fingerprints across two issues")
    diff.add_argument("--previous", required=True)
    diff.add_argument("--current", required=True)
    diff.add_argument("--output", type=Path, required=True)
    diff.add_argument("--complete-snapshots", action="store_true", help="Infer withdrawals from absence only for complete cumulative snapshots")
    export = commands.add_parser("export-parquet", help="Export typed assignments without reopening MDB")
    export.add_argument("--source", required=True)
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    start = time.perf_counter()
    try:
        with AnalysisStore(args.db, threads=args.threads, read_only=args.command not in {"import-ific", "import-stations"}) as store:
            if args.command == "import-ific":
                result = asdict(store.import_ific(args.path, domain=args.domain, issue_no=args.issue))
            elif args.command == "import-stations":
                result = asdict(store.import_stations(args.path, sheet_name=args.sheet))
                result["station_stats"] = store.station_stats(result["source_key"])
            elif args.command == "screen":
                names = [field.name for field in fields(Candidate)]
                candidates = (asdict(c) for c in store.screen(args.source, args.stations, gap_mhz=args.gap_mhz, max_terrestrial_distance_km=args.max_distance_km))
                count = _write_csv(args.output, names, candidates)
                result = {"candidate_count": count, "station_stats": store.station_stats(args.stations), "output": str(args.output)}
            elif args.command == "matches":
                names = ("source_row", "station_id", "f_low_mhz", "f_high_mhz")
                rows = (dict(zip(names, row)) for row in store.matches(args.source, args.stations, args.assignment, gap_mhz=args.gap_mhz))
                result = {"matched_assignment_count": _write_csv(args.output, names, rows), "output": str(args.output)}
            elif args.command == "changes":
                names = ("assignment_id", "change_type")
                rows = (dict(zip(names, row)) for row in store.changes(args.previous, args.current, complete_snapshots=args.complete_snapshots))
                result = {"changed_assignment_count": _write_csv(args.output, names, rows), "output": str(args.output)}
            else:
                store.export_assignments(args.source, args.output)
                result = {"output": str(args.output)}
        result["elapsed_s"] = round(time.perf_counter() - start, 6)
        print(json.dumps(result, sort_keys=True))
    except (ValueError, RuntimeError, OSError, duckdb.Error) as exc:
        print(f"ific-guard: {exc}", file=sys.stderr)
        return 1
    return 0


def _write_csv(path, names, rows):
    # Publish only a complete result; leave an existing result intact on errors.
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", suffix=".tmp", dir=path.parent, delete=False)
    temporary = Path(handle.name)
    count = 0
    try:
        with handle:
            writer = csv.DictWriter(handle, fieldnames=names)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
                count += 1
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return count
