"""Compare the real FrequencyIndex with the exploratory NumPy scan."""

import argparse
import json
import time

import numpy as np

from ific_guard.frequency import FrequencyIndex


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stations", type=int, default=100000)
    parser.add_argument("--groups", type=int, default=10000)
    args = parser.parse_args()
    if args.stations < 1 or args.groups < 1:
        parser.error("Counts must be positive")
    rng = np.random.default_rng(20261107)
    station_low = rng.uniform(30, 30000, args.stations)
    station_high = station_low + rng.uniform(0.0125, 100, args.stations)
    group_low = rng.uniform(30, 30000, args.groups)
    group_high = group_low + rng.uniform(0.0125, 100, args.groups)
    start = time.perf_counter()
    expected = np.array([int(((station_low < hi) & (station_high > lo)).sum()) for lo, hi in zip(group_low, group_high)])
    baseline_s = time.perf_counter() - start
    start = time.perf_counter()
    actual = FrequencyIndex(station_low, station_high).count(group_low, group_high)
    indexed_s = time.perf_counter() - start
    if not np.array_equal(actual, expected):
        raise RuntimeError("Optimized overlap counts differ from the reference")
    print(json.dumps({"stations": args.stations, "groups": args.groups, "identical_results": True,
                      "baseline_s": baseline_s, "indexed_s_including_sort": indexed_s,
                      "overlap_only_speedup": baseline_s / indexed_s}, indent=2))


if __name__ == "__main__":
    main()
