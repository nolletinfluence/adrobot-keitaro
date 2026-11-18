from dataclasses import replace
from itertools import permutations

import pytest

from app.allocation import Allocation, AllocationError, allocate_weights


@pytest.mark.parametrize("count", [1, 2, 3, 4, 7, 12, 33, 100, 101])
def test_equal_allocation_preserves_total_and_rounding_bound(count):
    offers = [Allocation(i, 0, False, False, i) for i in range(count)]
    weights = allocate_weights(offers)
    assert sum(weights.values()) == 100
    assert max(weights.values()) - min(weights.values()) <= 1
    assert offers == [Allocation(i, 0, False, False, i) for i in range(count)]


@pytest.mark.parametrize("pinned", [0, 1, 25, 99, 100])
def test_pinned_allocation_and_archive_are_invariant_under_input_order(pinned):
    offers = [
        Allocation(11, pinned, True, False, 0),
        Allocation(12, 0, False, False, 1),
        Allocation(13, 0, False, False, 2),
        Allocation(14, 100, True, True, 3),
    ]
    expected = allocate_weights(offers)
    for ordered in permutations(offers):
        assert allocate_weights(ordered) == expected
    assert expected[11] == pinned
    assert expected[14] == 0
    assert sum(expected.values()) == 100
    assert abs(expected[12] - expected[13]) <= 1


def test_restored_offer_participates_and_preserves_pinned_share():
    original = [
        Allocation(1, 25, True, False),
        Allocation(2, 75, False, False, 1),
        Allocation(3, 0, False, True, 2),
    ]
    assert allocate_weights(original) == {1: 25, 2: 75, 3: 0}
    restored = [*original[:2], replace(original[2], removed=False)]
    assert allocate_weights(restored) == {1: 25, 2: 38, 3: 37}


@pytest.mark.parametrize(
    "offers",
    [
        [],
        [Allocation(1, 0, False, True)],
        [Allocation(1, 20, True, False)],
        [Allocation(1, 60, True, False), Allocation(2, 60, True, False)],
        [Allocation(1, 50, False, False), Allocation(1, 50, False, False)],
        [Allocation(1, -1, True, False)],
    ],
)
def test_impossible_allocations_are_rejected_without_mutating_inputs(offers):
    snapshot = tuple(offers)
    with pytest.raises(AllocationError):
        allocate_weights(offers)
    assert tuple(offers) == snapshot
