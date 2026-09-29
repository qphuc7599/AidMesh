"""Site inventory reservation/release state machine."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from aidmesh.common.errors import DuplicateError, InvariantViolation, ValidationError


@dataclass(frozen=True)
class InventoryReservation:
    reservation_id: str
    category: str
    units: int


@dataclass(frozen=True)
class InventorySnapshot:
    on_hand: tuple[tuple[str, int], ...]
    reserved: tuple[tuple[str, int], ...]
    released: tuple[tuple[str, str, int], ...]

    def available(self, category: str) -> int:
        on_hand = dict(self.on_hand).get(category, 0)
        reserved = dict(self.reserved).get(category, 0)
        return on_hand - reserved


class SiteInventory:
    def __init__(self, stock: dict[str, int]) -> None:
        if not stock or any(not category for category in stock):
            raise ValidationError("inventory needs named categories")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in stock.values()
        ):
            raise ValidationError("inventory quantities must be non-negative integers")
        self._on_hand = dict(sorted(stock.items()))
        self._reserved = {category: 0 for category in stock}
        self._reservations: dict[str, InventoryReservation] = {}
        self._released: dict[str, InventoryReservation] = {}
        self._lock = threading.RLock()

    def reserve(self, *, reservation_id: str, category: str, units: int) -> InventoryReservation:
        if not reservation_id:
            raise ValidationError("reservation_id is required")
        if isinstance(units, bool) or not isinstance(units, int) or units <= 0:
            raise ValidationError("reservation units must be a positive integer")
        with self._lock:
            if category not in self._on_hand:
                raise ValidationError("unknown inventory category")
            existing = self._reservations.get(reservation_id)
            if existing is not None:
                if existing.category == category and existing.units == units:
                    return existing
                raise DuplicateError("reservation id reused with different inventory")
            if reservation_id in self._released:
                raise DuplicateError("inventory operation was already released")
            available = self._on_hand[category] - self._reserved[category]
            if units > available:
                raise ValidationError("insufficient site inventory")
            reservation = InventoryReservation(reservation_id, category, units)
            self._reservations[reservation_id] = reservation
            self._reserved[category] += units
            self._assert_valid_unlocked()
            return reservation

    def commit(self, reservation_id: str) -> InventoryReservation:
        with self._lock:
            already = self._released.get(reservation_id)
            if already is not None:
                return already
            reservation = self._reservations.pop(reservation_id, None)
            if reservation is None:
                raise ValidationError("unknown inventory reservation")
            self._reserved[reservation.category] -= reservation.units
            self._on_hand[reservation.category] -= reservation.units
            self._released[reservation_id] = reservation
            self._assert_valid_unlocked()
            return reservation

    def cancel(self, reservation_id: str) -> bool:
        with self._lock:
            reservation = self._reservations.pop(reservation_id, None)
            if reservation is None:
                return False
            self._reserved[reservation.category] -= reservation.units
            self._assert_valid_unlocked()
            return True

    def _assert_valid_unlocked(self) -> None:
        if any(value < 0 for value in self._on_hand.values()):
            raise InvariantViolation("negative physical inventory")
        for category in self._on_hand:
            if not 0 <= self._reserved[category] <= self._on_hand[category]:
                raise InvariantViolation("invalid inventory reservation accounting")

    def snapshot(self) -> InventorySnapshot:
        with self._lock:
            self._assert_valid_unlocked()
            return InventorySnapshot(
                on_hand=tuple(sorted(self._on_hand.items())),
                reserved=tuple(sorted(self._reserved.items())),
                released=tuple(
                    (record.reservation_id, record.category, record.units)
                    for record in sorted(
                        self._released.values(), key=lambda item: item.reservation_id
                    )
                ),
            )
