"""Exact overlap counts without a group-by-station comparison matrix."""

import numpy as np


def bounds(low_mhz, high_mhz):
    low = np.asarray(low_mhz, dtype=np.float64)
    high = np.asarray(high_mhz, dtype=np.float64)
    if low.ndim != 1 or high.shape != low.shape:
        raise ValueError("Frequency bounds must be equal-length one-dimensional arrays")
    if not (np.isfinite(low).all() and np.isfinite(high).all()):
        raise ValueError("Frequency bounds must be finite")
    if (low < 0).any() or (high <= low).any():
        raise ValueError("Each frequency range must satisfy 0 <= low < high")
    return low, high


class FrequencyIndex:
    """Index receiver assignments once; query batches in O(G log S).

    Counts are assignments, not distinct physical stations. At zero gap,
    touching edges do not overlap, matching the exploratory script. A positive
    gap includes assignments at exactly that distance. Retrieve identities
    separately through AnalysisStore.matches(), without expanding all pairs.
    """

    def __init__(self, low_mhz, high_mhz):
        low, high = bounds(low_mhz, high_mhz)
        self._low = np.sort(low)
        self._high = np.sort(high)

    def count(self, low_mhz, high_mhz, *, gap_mhz=0.0):
        low, high = bounds(low_mhz, high_mhz)
        if not np.isfinite(gap_mhz) or gap_mhz < 0:
            raise ValueError("gap_mhz must be finite and nonnegative")
        low_side, high_side = ("left", "right") if gap_mhz == 0 else ("right", "left")
        return (
            np.searchsorted(self._low, high + gap_mhz, side=low_side)
            - np.searchsorted(self._high, low - gap_mhz, side=high_side)
        )
