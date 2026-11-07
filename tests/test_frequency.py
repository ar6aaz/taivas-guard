import numpy as np
import pytest

from ific_guard.frequency import FrequencyIndex


def test_counts_match_brute_force_with_duplicates_and_edge_contacts():
    rng = np.random.default_rng(20261107)
    low = np.r_[rng.uniform(0, 1000, 2000), 100, 100, 120]
    high = np.r_[low[:-3] + rng.uniform(.001, 20, 2000), 110, 110, 130]
    query_low = np.r_[rng.uniform(0, 1000, 300), 110, 100, 90, 130]
    query_high = np.r_[query_low[:-4] + rng.uniform(.001, 40, 300), 120, 110, 100, 140]
    index = FrequencyIndex(low, high)
    for gap in (0, .5, 10):
        expected = [np.count_nonzero((low < hi) & (high > lo)) if not gap else
                    np.count_nonzero((low <= hi + gap) & (high >= lo - gap))
                    for lo, hi in zip(query_low, query_high)]
        np.testing.assert_array_equal(index.count(query_low, query_high, gap_mhz=gap), expected)


def test_empty_index_and_exact_adjacency():
    assert FrequencyIndex([], []).count([1], [2]).tolist() == [0]
    index = FrequencyIndex([100, 120], [110, 130])
    assert index.count([110], [120]).tolist() == [0]
    assert index.count([111], [119], gap_mhz=1).tolist() == [2]


@pytest.mark.parametrize("low,high", [([1], [1]), ([2], [1]), ([-1], [2]), ([float('nan')], [2]), ([1], [float('inf')]), ([1], []), ([[1]], [[2]])])
def test_invalid_ranges_fail_explicitly(low, high):
    with pytest.raises(ValueError):
        FrequencyIndex(low, high)


@pytest.mark.parametrize("gap", [-1, float('nan'), float('inf')])
def test_invalid_adjacency_cannot_silently_return_zero(gap):
    with pytest.raises(ValueError):
        FrequencyIndex([], []).count([], [], gap_mhz=gap)
