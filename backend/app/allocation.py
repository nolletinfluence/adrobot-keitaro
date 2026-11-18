from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Allocation:
    offer_id: int
    weight: int
    pinned: bool
    removed: bool
    position: int = 0


class AllocationError(ValueError):
    pass


def allocate_weights(offers: Sequence[Allocation]) -> dict[int, int]:
    if len({offer.offer_id for offer in offers}) != len(offers):
        raise AllocationError("An offer can only occur once in a stream")
    active = [offer for offer in offers if not offer.removed]
    if not active:
        raise AllocationError("A stream must have at least one active offer")
    if any(type(offer.weight) is not int or not 0 <= offer.weight <= 100 for offer in active):
        raise AllocationError("Offer weights must be integers between 0 and 100")
    pinned_total = sum(offer.weight for offer in active if offer.pinned)
    if pinned_total > 100:
        raise AllocationError("Pinned weights cannot exceed 100%")
    free = sorted(
        (offer for offer in active if not offer.pinned),
        key=lambda offer: (offer.position, offer.offer_id),
    )
    if not free and pinned_total != 100:
        raise AllocationError("When every offer is pinned, their weights must total 100%")
    weights = {
        offer.offer_id: offer.weight if offer.pinned and not offer.removed else 0
        for offer in offers
    }
    if free:
        quotient, remainder = divmod(100 - pinned_total, len(free))
        for index, offer in enumerate(free):
            weights[offer.offer_id] = quotient + (index < remainder)
    return weights
