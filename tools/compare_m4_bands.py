"""Compares screening with two M4 band tables on one Space IFIC. Reads only local files.

    uv run python tools/compare_m4_bands.py /path/to/ific3083.mdb [--old OLD.csv] [--new NEW.csv] [--csv OUT.csv]
"""
import argparse
import csv
from pathlib import Path

from ific_guard.config import Settings
from ific_guard.screening.m4 import load_m4_conflict_bands
from ific_guard.screening.rules import FinlandNamedRule, M4NoDownlinkRule
from ific_guard.screening.space import MdbReader, SpaceIssue

ROOT = Path(__file__).resolve().parent.parent


def band_text(hit) -> str:
    return " ".join(f"{b['low_mhz']:g}-{b['high_mhz']:g}" for b in hit.details.get("bands", []))


def hits_with(issue: SpaceIssue, table: Path) -> dict:
    settings = Settings(_env_file=None)
    bands = load_m4_conflict_bands(table)
    rules = [FinlandNamedRule(settings), M4NoDownlinkRule(settings, bands)]
    return {(h.rule_id, h.family_id): h for rule in rules for h in rule.run(issue)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mdb", type=Path)
    parser.add_argument("--old", type=Path, default=ROOT / "generetated-files" / "m4_satellite_filter.csv")
    parser.add_argument("--new", type=Path, default=ROOT / "data" / "national" / "m4_bands.csv")
    parser.add_argument("--csv", type=Path)
    args = parser.parse_args()

    issue = SpaceIssue(MdbReader(args.mdb))
    old, new = hits_with(issue, args.old), hits_with(issue, args.new)
    rows = []
    for key in sorted(old.keys() | new.keys(), key=str):
        before, after = old.get(key), new.get(key)
        head = after or before
        change = "appears" if not before else "disappears" if not after else (
            "severity" if before.severity != after.severity else "bands" if band_text(before) != band_text(after) else "same"
        )
        rows.append({
            "change": change, "rule_id": key[0], "family_id": key[1], "adm": head.adm, "network": head.network,
            "severity_old": before.severity if before else "", "severity_new": after.severity if after else "",
            "bands_old": band_text(before) if before else "", "bands_new": band_text(after) if after else "",
        })
    print(f"{args.mdb.name}: old {len(old)} cases, new {len(new)} cases")
    for severity in ("high", "medium", "low"):
        print(f"  {severity}: old {sum(h.severity == severity for h in old.values())}, new {sum(h.severity == severity for h in new.values())}")
    for change in ("appears", "disappears", "severity", "bands", "same"):
        chosen = [r for r in rows if r["change"] == change]
        print(f"\n{change}: {len(chosen)}")
        if change != "same":
            for r in chosen:
                print(f"  {r['rule_id']} family {r['family_id']} {r['adm']} {r['network']} "
                      f"[{r['severity_old'] or '-'} -> {r['severity_new'] or '-'}]\n"
                      f"    old: {r['bands_old'] or '-'}\n    new: {r['bands_new'] or '-'}")
    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    main()
