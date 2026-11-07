"""Builds data/national/m4_bands.csv from Traficom's Frequency Allocation Table.

    uv run python tools/build_m4_bands.py            # newest saved snapshot, no network
    uv run python tools/build_m4_bands.py --snapshot data/national/taajuusjakotaulukko_2026-11-07.json
    uv run python tools/build_m4_bands.py --fetch    # one GET to Traficom, saves a dated snapshot, then builds

Writes m4_bands.csv (the table), m4_classification_log.csv (service text it could not resolve)
and m4_bands.meta.json (what it was built from). Only --fetch touches the network.

m4_table_pages uses the printed "N (230)" numbers (PDF page = N + 9) and is carried over from
generetated-files/m4_satellite_filter.csv at table-band level. The row-level citation is api_row_ids.
"""
import argparse
import csv
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ific_guard.screening.m4 import BAND_COLUMNS, LOG_COLUMNS, build_band_table

API_URL = "https://opendata.traficom.fi/api/v13/Taajuusjakotaulukko"
USER_AGENT = "Mozilla/5.0 (compatible)"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "national"
OLD_TABLE = ROOT / "generetated-files" / "m4_satellite_filter.csv"
SNAPSHOT_GLOB = "taajuusjakotaulukko_*.json"


def fetch(out_dir: Path) -> Path:
    now = datetime.now(timezone.utc)
    response = httpx.get(API_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=300, follow_redirects=True)
    response.raise_for_status()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"taajuusjakotaulukko_{now.date().isoformat()}.json"
    path.write_bytes(response.content)
    meta = {
        "url": API_URL,
        "final_url": str(response.url),
        "fetched_at_utc": now.isoformat(timespec="seconds"),
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "response_headers": dict(response.headers),
        "user_agent": USER_AGENT,
        "bytes": len(response.content),
        "sha256": hashlib.sha256(response.content).hexdigest(),
    }
    path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return path


def read_snapshot(path: Path) -> tuple[list[dict], str]:
    raw = path.read_bytes()
    sha256 = hashlib.sha256(raw).hexdigest()
    meta_path = path.with_suffix(".meta.json")
    if meta_path.exists() and json.loads(meta_path.read_text(encoding="utf-8"))["sha256"] != sha256:
        raise SystemExit(f"{path} does not match the checksum in {meta_path}")
    return json.loads(raw)["value"], sha256


def page_index(path: Path = OLD_TABLE) -> list[tuple[float, float, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return [(float(r["band_low_mhz"]), float(r["band_high_mhz"]), r["m4_table_pages"]) for r in csv.DictReader(f)]


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def build(snapshot: Path, out_dir: Path) -> dict:
    rows, sha256 = read_snapshot(snapshot)
    bands, log = build_band_table(rows, page_index(), snapshot.name)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "m4_bands.csv", BAND_COLUMNS, bands)
    write_csv(out_dir / "m4_classification_log.csv", LOG_COLUMNS, log)
    categories: dict[str, int] = {}
    for entry in log:
        categories[entry["category"]] = categories.get(entry["category"], 0) + 1
    summary = {
        "snapshot": snapshot.name,
        "snapshot_sha256": sha256,
        "source_rows": len(rows),
        "bands": len(bands),
        "bands_with_no_space_to_earth_and_terrestrial_or_passive": sum(
            1 for b in bands
            if not b["space_down_allocated"] and (b["terrestrial_primary"] or b["passive_or_radio_astronomy"])
        ),
        "bands_where_direction_is_unresolved": sum(1 for b in bands if b["space_down_unresolved"]),
        "log_entries": categories,
    }
    (out_dir / "m4_bands.meta.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def newest_snapshot(out_dir: Path) -> Path:
    found = sorted(p for p in out_dir.glob(SNAPSHOT_GLOB) if re.search(r"_\d{4}-\d{2}-\d{2}\.json$", p.name))
    if not found:
        raise SystemExit(f"No snapshot in {out_dir}. Run with --fetch once.")
    return found[-1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fetch", action="store_true", help="download a new snapshot first")
    parser.add_argument("--snapshot", type=Path, help="snapshot to build from (default: newest saved)")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    snapshot = fetch(args.out) if args.fetch else args.snapshot or newest_snapshot(args.out)
    print(json.dumps(build(snapshot, args.out), indent=2))


if __name__ == "__main__":
    main()
