"""Measure cached imports and screening with deterministic IFIC-shaped tables.

This does not measure actual Access/MDB decoding. Both imports and screening
use the production AnalysisStore interface, with a fixture reader at its seam.
"""

import argparse
import csv
import json
import tempfile
import time
from pathlib import Path

import numpy as np

from ific_guard import AnalysisStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stations", type=int, default=100000)
    parser.add_argument("--groups", type=int, default=10000)
    args = parser.parse_args()
    if min(args.stations, args.groups) < 1:
        parser.error("Counts must be positive")
    rng = np.random.default_rng(20261107)
    with tempfile.TemporaryDirectory(prefix="ific-benchmark-") as folder:
        folder = Path(folder)
        source = folder / "fixture-source.mdb"
        source.write_text(f"Synthetic decoded-table fixture: groups={args.groups}, seed=20261107", encoding="utf-8")
        stations = folder / "stations.csv"
        with stations.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["station_id", "f_low_mhz", "f_high_mhz"])
            for i in range(args.stations):
                low = rng.uniform(30, 30000)
                writer.writerow([f"SYN-{i}", low, low + rng.uniform(.0125, 100)])
        lows = rng.uniform(30, 30000, args.groups)
        highs = lows + rng.uniform(.0125, 100, args.groups)

        def fixture_reader(path, domain):
            yield "notice", ({"ntc_id": i, "ntc_type": "N", "adm": "S"} for i in range(1, 101))
            yield "grp", ({"ntc_id": i % 100 + 1, "grp_id": i, "freq_min": float(lo), "freq_max": float(hi), "emi_rcp": "E"}
                          for i, (lo, hi) in enumerate(zip(lows, highs)))
            yield "provn", ({"grp_id": i, "adm": "FIN"} for i in range(0, args.groups, 500))
            yield "emiss", ({"grp_id": i, "pwr_ds_max": -50} for i in range(args.groups))

        def forbidden_reader(*args):
            raise RuntimeError("Cache hit decoded source again")

        with AnalysisStore(folder / "cache.duckdb") as store:
            national = store.import_stations(stations)
            issue = store.import_ific(source, domain="space", issue_no=3074, reader=fixture_reader, reader_version="benchmark-1")
            cached = store.import_ific(source, domain="space", issue_no=3074, reader=forbidden_reader, reader_version="benchmark-1")
            if not cached.cache_hit:
                raise RuntimeError("Expected a cache hit")
            start = time.perf_counter()
            candidates = list(store.screen(issue.source_key, national.source_key))
            screen_s = time.perf_counter() - start
            start = time.perf_counter()
            repeated = list(store.screen(issue.source_key, national.source_key))
            repeated_s = time.perf_counter() - start
            if candidates != repeated:
                raise RuntimeError("Repeated analysis changed results")
            print(json.dumps({"fixture": "synthetic decoded tables; excludes MDB decode time",
                              "station_rows": args.stations, "frequency_groups": args.groups,
                              "station_import_s": national.elapsed_s,
                              "ific_import_s": issue.elapsed_s, "cached_ific_import_s": cached.elapsed_s,
                              "screen_s_including_index_build": screen_s, "repeated_screen_s": repeated_s,
                              "candidate_count": len(candidates), "cache_hit": cached.cache_hit,
                              "identical_repeated_results": True}, indent=2))


if __name__ == "__main__":
    main()
