"""Exact weighted progressive filling and auditable quota issuance.

The proved AidMesh allocator operates independently inside each risk cell.  A
``PackingProblem`` therefore has explicit ``cell/group/category`` axes.  All
numeric inputs are converted to :class:`fractions.Fraction` before validation;
the filling path, bottleneck events, and rounding certificates consequently do
not depend on a floating-point tolerance or an external LP solver.

This module implements only the downward-closed packing model used by the
paper's weighted max-min theorem.  It deliberately does not accept signed or
cross-category constraints, because those belong to a separately labelled
general-LP experiment and do not inherit that theorem.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from threading import RLock
from types import MappingProxyType

type RationalLike = int | float | str | Decimal | Fraction


class AllocationInputError(ValueError):
    """Raised when an allocation problem violates the packing-model schema."""


class AllocationInvariantError(RuntimeError):
    """Raised when an internal allocation or audit invariant is violated."""


class QuotaExceededError(ValueError):
    """Raised when an issuance would exceed a committed integer entitlement."""


class DuplicateIssuanceError(ValueError):
    """Raised when an opaque issuance identifier is reused."""


def _identifier(value: object, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AllocationInputError(f"{path} must be a non-empty string")
    return value


def as_fraction(value: RationalLike, path: str = "value") -> Fraction:
    """Convert a finite decimal/rational input to an exact ``Fraction``.

    Floats are first converted through their shortest decimal representation,
    rather than through their binary expansion.  This makes configuration such
    as ``0.1`` behave as the decimal number a researcher wrote.
    """

    if isinstance(value, bool):
        raise AllocationInputError(f"{path} must be numeric, not bool")
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise AllocationInputError(f"{path} must be finite")
        return Fraction(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AllocationInputError(f"{path} must be finite")
        return Fraction(Decimal(str(value)))
    if isinstance(value, str):
        try:
            decimal_value = Decimal(value)
        except InvalidOperation as exc:
            raise AllocationInputError(f"{path} is not a decimal number") from exc
        if not decimal_value.is_finite():
            raise AllocationInputError(f"{path} must be finite")
        return Fraction(decimal_value)
    raise AllocationInputError(f"{path} must be int, float, Decimal, Fraction, or decimal string")


def _freeze(mapping: Mapping[str, Fraction]) -> Mapping[str, Fraction]:
    return MappingProxyType(dict(mapping))


def _freeze_int(mapping: Mapping[str, int]) -> Mapping[str, int]:
    return MappingProxyType(dict(mapping))


def _freeze_nested(
    mapping: Mapping[str, Mapping[str, Fraction]],
) -> Mapping[str, Mapping[str, Fraction]]:
    return MappingProxyType({key: _freeze(row) for key, row in mapping.items()})


def _freeze_nested_int(
    mapping: Mapping[str, Mapping[str, int]],
) -> Mapping[str, Mapping[str, int]]:
    return MappingProxyType({key: _freeze_int(row) for key, row in mapping.items()})


@dataclass(frozen=True, slots=True)
class PackingProblem:
    """Input for one cell's nonnegative packing-polytope allocation.

    ``demand[g][k]`` and ``stock[k]`` are amounts of category ``k``.
    ``weights[g]`` is the published positive priority weight, while
    ``conversion_factors[k]`` defines aggregate eligible demand
    ``d_g = sum_k a_k d_gk``.  For every positive-demand group, bundle direction
    ``directions[g]`` must be nonnegative and exactly satisfy
    ``sum_k a_k p_gk == 1`` after decimal inputs have been rationalized.

    ``units[k]`` is the amount carried by one quota index.  It may be rational,
    but rounded quota *counts* are always nonnegative integers.
    """

    cell: str
    demand: Mapping[str, Mapping[str, RationalLike]]
    stock: Mapping[str, RationalLike]
    weights: Mapping[str, RationalLike]
    conversion_factors: Mapping[str, RationalLike]
    directions: Mapping[str, Mapping[str, RationalLike]]
    units: Mapping[str, RationalLike] = field(default_factory=dict)

    def normalized(self) -> ValidatedPackingProblem:
        """Return a deeply immutable, exact, schema-validated representation."""

        return normalize_problem(self)


@dataclass(frozen=True, slots=True)
class ValidatedPackingProblem:
    """Exact immutable form of :class:`PackingProblem`."""

    cell: str
    groups: tuple[str, ...]
    categories: tuple[str, ...]
    demand: Mapping[str, Mapping[str, Fraction]]
    stock: Mapping[str, Fraction]
    weights: Mapping[str, Fraction]
    conversion_factors: Mapping[str, Fraction]
    directions: Mapping[str, Mapping[str, Fraction]]
    units: Mapping[str, Fraction]
    aggregate_demand: Mapping[str, Fraction]


def normalize_problem(problem: PackingProblem | ValidatedPackingProblem) -> ValidatedPackingProblem:
    """Validate a cell problem and canonicalize all axes and numeric values."""

    if isinstance(problem, ValidatedPackingProblem):
        return problem
    if not isinstance(problem, PackingProblem):
        raise AllocationInputError("problem must be a PackingProblem")

    cell = _identifier(problem.cell, "cell")
    if not isinstance(problem.stock, Mapping) or not problem.stock:
        raise AllocationInputError("stock must define at least one category")
    categories = tuple(sorted(_identifier(key, "stock category") for key in problem.stock))
    if len(set(categories)) != len(categories):
        raise AllocationInputError("stock contains duplicate category identifiers")

    stock: dict[str, Fraction] = {}
    conversions: dict[str, Fraction] = {}
    units: dict[str, Fraction] = {}
    for category in categories:
        amount = as_fraction(problem.stock[category], f"stock[{category!r}]")
        if amount < 0:
            raise AllocationInputError(f"stock[{category!r}] must be nonnegative")
        stock[category] = amount
        if category not in problem.conversion_factors:
            raise AllocationInputError(f"missing conversion factor for {category!r}")
        conversion = as_fraction(
            problem.conversion_factors[category],
            f"conversion_factors[{category!r}]",
        )
        if conversion <= 0:
            raise AllocationInputError(f"conversion_factors[{category!r}] must be positive")
        conversions[category] = conversion
        unit = as_fraction(problem.units.get(category, 1), f"units[{category!r}]")
        if unit <= 0:
            raise AllocationInputError(f"units[{category!r}] must be positive")
        units[category] = unit

    unknown_conversions = set(problem.conversion_factors) - set(categories)
    unknown_units = set(problem.units) - set(categories)
    if unknown_conversions:
        raise AllocationInputError(
            f"conversion factors contain unknown categories: {sorted(unknown_conversions)!r}"
        )
    if unknown_units:
        raise AllocationInputError(f"units contain unknown categories: {sorted(unknown_units)!r}")
    if not isinstance(problem.demand, Mapping) or not problem.demand:
        raise AllocationInputError("demand must define at least one group")
    groups = tuple(sorted(_identifier(key, "demand group") for key in problem.demand))
    if set(problem.weights) != set(groups):
        missing = sorted(set(groups) - set(problem.weights))
        extra = sorted(set(problem.weights) - set(groups))
        raise AllocationInputError(f"weights axis mismatch; missing={missing}, extra={extra}")
    if set(problem.directions) - set(groups):
        extra = sorted(set(problem.directions) - set(groups))
        raise AllocationInputError(f"directions contain unknown groups: {extra}")

    demand: dict[str, dict[str, Fraction]] = {}
    weights: dict[str, Fraction] = {}
    directions: dict[str, dict[str, Fraction]] = {}
    aggregate: dict[str, Fraction] = {}
    category_set = set(categories)
    for group in groups:
        row = problem.demand[group]
        if not isinstance(row, Mapping):
            raise AllocationInputError(f"demand[{group!r}] must be a mapping")
        unknown = set(row) - category_set
        if unknown:
            raise AllocationInputError(
                f"demand[{group!r}] contains unknown categories: {sorted(unknown)!r}"
            )
        demand_row: dict[str, Fraction] = {}
        for category in categories:
            value = as_fraction(row.get(category, 0), f"demand[{group!r}][{category!r}]")
            if value < 0:
                raise AllocationInputError(f"demand[{group!r}][{category!r}] must be nonnegative")
            demand_row[category] = value
        demand[group] = demand_row
        weight = as_fraction(problem.weights[group], f"weights[{group!r}]")
        if weight <= 0:
            raise AllocationInputError(f"weights[{group!r}] must be positive")
        weights[group] = weight
        aggregate[group] = sum((conversions[k] * demand_row[k] for k in categories), Fraction(0))

        direction_input = problem.directions.get(group, {})
        if not isinstance(direction_input, Mapping):
            raise AllocationInputError(f"directions[{group!r}] must be a mapping")
        unknown_direction = set(direction_input) - category_set
        if unknown_direction:
            raise AllocationInputError(
                f"directions[{group!r}] contains unknown categories: {sorted(unknown_direction)!r}"
            )
        direction_row: dict[str, Fraction] = {}
        for category in categories:
            value = as_fraction(
                direction_input.get(category, 0),
                f"directions[{group!r}][{category!r}]",
            )
            if value < 0:
                raise AllocationInputError(
                    f"directions[{group!r}][{category!r}] must be nonnegative"
                )
            direction_row[category] = value
        normalization = sum((conversions[k] * direction_row[k] for k in categories), Fraction(0))
        if aggregate[group] > 0 and normalization != 1:
            raise AllocationInputError(
                f"directions[{group!r}] must satisfy sum(a_k*p_gk) == 1; observed {normalization}"
            )
        if aggregate[group] == 0 and normalization not in (Fraction(0), Fraction(1)):
            raise AllocationInputError(
                f"zero-demand directions[{group!r}] must be zero or normalized"
            )
        directions[group] = direction_row

    return ValidatedPackingProblem(
        cell=cell,
        groups=groups,
        categories=categories,
        demand=_freeze_nested(demand),
        stock=_freeze(stock),
        weights=_freeze(weights),
        conversion_factors=_freeze(conversions),
        directions=_freeze_nested(directions),
        units=_freeze(units),
        aggregate_demand=_freeze(aggregate),
    )


@dataclass(frozen=True, slots=True)
class BottleneckEvent:
    """One exact water-level event in progressive filling."""

    level: Fraction
    increment: Fraction
    frozen_groups: tuple[str, ...]
    individual_rows: tuple[tuple[str, str], ...]
    stock_rows: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AllocationMatrix:
    """A feasible divisible allocation with its complete measurement context."""

    method: str
    cell: str
    groups: tuple[str, ...]
    categories: tuple[str, ...]
    amounts: Mapping[str, Mapping[str, Fraction]]
    demand: Mapping[str, Mapping[str, Fraction]]
    stock: Mapping[str, Fraction]
    weights: Mapping[str, Fraction]
    conversion_factors: Mapping[str, Fraction]
    units: Mapping[str, Fraction]

    def amount(self, group: str, category: str) -> Fraction:
        """Return the exact allocated amount at one matrix coordinate."""

        return self.amounts[group][category]

    def assert_feasible(self) -> None:
        """Raise ``AllocationInvariantError`` unless all packing caps hold."""

        if set(self.amounts) != set(self.groups):
            raise AllocationInvariantError("allocation group axis mismatch")
        for group in self.groups:
            if set(self.amounts[group]) != set(self.categories):
                raise AllocationInvariantError(
                    f"allocation category axis mismatch for group {group!r}"
                )
            for category in self.categories:
                amount = self.amounts[group][category]
                if amount < 0 or amount > self.demand[group][category]:
                    raise AllocationInvariantError(
                        f"demand cap violated at ({group!r}, {category!r})"
                    )
        for category in self.categories:
            used = sum((self.amounts[group][category] for group in self.groups), Fraction(0))
            if used > self.stock[category]:
                raise AllocationInvariantError(f"stock cap violated for {category!r}")


@dataclass(frozen=True, slots=True)
class DivisibleAllocation(AllocationMatrix):
    """Terminal weighted progressive-filling allocation."""

    ratios: Mapping[str, Fraction]
    coefficients: Mapping[str, Mapping[str, Fraction]]
    bottlenecks: tuple[BottleneckEvent, ...]


def _matrix_from_normalized(
    problem: ValidatedPackingProblem,
    amounts: Mapping[str, Mapping[str, Fraction]],
    method: str,
) -> AllocationMatrix:
    matrix = AllocationMatrix(
        method=method,
        cell=problem.cell,
        groups=problem.groups,
        categories=problem.categories,
        amounts=_freeze_nested(amounts),
        demand=problem.demand,
        stock=problem.stock,
        weights=problem.weights,
        conversion_factors=problem.conversion_factors,
        units=problem.units,
    )
    matrix.assert_feasible()
    return matrix


def progressive_fill(
    problem: PackingProblem | ValidatedPackingProblem,
) -> DivisibleAllocation:
    """Compute exact weighted max-min ratios on the packing polytope.

    Every active ratio increases at unit rate.  At an individual demand row,
    only that group freezes; at a saturated stock row, all active groups with a
    positive coefficient in the row freeze.  Simultaneous rows are processed as
    one event, which is important for deterministic leximin results.
    """

    normalized = normalize_problem(problem)
    coefficients: dict[str, dict[str, Fraction]] = {}
    ratios = {group: Fraction(0) for group in normalized.groups}
    for group in normalized.groups:
        coefficients[group] = {
            category: normalized.weights[group]
            * normalized.aggregate_demand[group]
            * normalized.directions[group][category]
            for category in normalized.categories
        }

    active = {group for group in normalized.groups if normalized.aggregate_demand[group] > 0}
    events: list[BottleneckEvent] = []
    while active:
        candidates: list[Fraction] = []
        for group in active:
            for category in normalized.categories:
                coefficient = coefficients[group][category]
                if coefficient > 0:
                    slack = normalized.demand[group][category] - coefficient * ratios[group]
                    if slack < 0:
                        raise AllocationInvariantError("negative individual-row slack")
                    candidates.append(slack / coefficient)

        for category in normalized.categories:
            active_rate = sum((coefficients[group][category] for group in active), Fraction(0))
            if active_rate == 0:
                continue
            used = sum(
                (coefficients[group][category] * ratios[group] for group in normalized.groups),
                Fraction(0),
            )
            slack = normalized.stock[category] - used
            if slack < 0:
                raise AllocationInvariantError("negative stock-row slack")
            candidates.append(slack / active_rate)

        if not candidates:
            raise AllocationInvariantError("active group has no positive packing coefficient")
        increment = min(candidates)
        if increment < 0:
            raise AllocationInvariantError("progressive filling selected a negative increment")
        for group in active:
            ratios[group] += increment

        individual_rows: list[tuple[str, str]] = []
        stock_rows: list[str] = []
        frozen: set[str] = set()
        for group in sorted(active):
            for category in normalized.categories:
                coefficient = coefficients[group][category]
                if (
                    coefficient > 0
                    and coefficient * ratios[group] == normalized.demand[group][category]
                ):
                    individual_rows.append((group, category))
                    frozen.add(group)
        for category in normalized.categories:
            used = sum(
                (coefficients[group][category] * ratios[group] for group in normalized.groups),
                Fraction(0),
            )
            if used == normalized.stock[category] and any(
                coefficients[group][category] > 0 for group in active
            ):
                stock_rows.append(category)
                frozen.update(group for group in active if coefficients[group][category] > 0)
        if not frozen:
            raise AllocationInvariantError("a water-level event froze no active group")
        events.append(
            BottleneckEvent(
                level=min(ratios[group] for group in frozen),
                increment=increment,
                frozen_groups=tuple(sorted(frozen)),
                individual_rows=tuple(individual_rows),
                stock_rows=tuple(stock_rows),
            )
        )
        active.difference_update(frozen)

    amounts = {
        group: {
            category: coefficients[group][category] * ratios[group]
            for category in normalized.categories
        }
        for group in normalized.groups
    }
    result = DivisibleAllocation(
        method="weighted-progressive-fill",
        cell=normalized.cell,
        groups=normalized.groups,
        categories=normalized.categories,
        amounts=_freeze_nested(amounts),
        demand=normalized.demand,
        stock=normalized.stock,
        weights=normalized.weights,
        conversion_factors=normalized.conversion_factors,
        units=normalized.units,
        ratios=_freeze(ratios),
        coefficients=_freeze_nested(coefficients),
        bottlenecks=tuple(events),
    )
    result.assert_feasible()
    for group in result.groups:
        aggregate_fulfilled = sum(
            (result.conversion_factors[k] * result.amounts[group][k] for k in result.categories),
            Fraction(0),
        )
        denominator = result.weights[group] * normalized.aggregate_demand[group]
        if denominator > 0 and aggregate_fulfilled / denominator != result.ratios[group]:
            raise AllocationInvariantError("reported service ratio is inconsistent")
    return result


@dataclass(frozen=True, slots=True)
class CampaignAllocation:
    """Cell-indexed collection of independent packing allocations."""

    cells: Mapping[str, DivisibleAllocation]

    def __getitem__(self, cell: str) -> DivisibleAllocation:
        return self.cells[cell]


def progressive_fill_cells(
    problems: Iterable[PackingProblem | ValidatedPackingProblem],
) -> CampaignAllocation:
    """Allocate multiple cells and reject ambiguous duplicate cell identifiers."""

    results: dict[str, DivisibleAllocation] = {}
    for problem in problems:
        result = progressive_fill(problem)
        if result.cell in results:
            raise AllocationInputError(f"duplicate cell {result.cell!r}")
        results[result.cell] = result
    if not results:
        raise AllocationInputError("at least one cell problem is required")
    return CampaignAllocation(MappingProxyType(dict(sorted(results.items()))))


@dataclass(frozen=True, slots=True)
class RoundingCategoryCertificate:
    """Exact accounting certificate for one category's integer rounding."""

    category: str
    floor_amount: Fraction
    floor_loss: Fraction
    residual_units_assigned: int
    allocated_amount: Fraction
    stock_remaining: Fraction


@dataclass(frozen=True, slots=True)
class IntegerAllocation(AllocationMatrix):
    """Feasible allocation represented by integer quota counts."""

    quotas: Mapping[str, Mapping[str, int]]
    seed_commitment: str
    certificates: Mapping[str, RoundingCategoryCertificate]

    def quota(self, group: str, category: str) -> int:
        """Return the number of voucher indices authorized at one coordinate."""

        return self.quotas[group][category]


def _seed_bytes(seed: str | bytes) -> bytes:
    if isinstance(seed, str):
        data = seed.encode("utf-8")
    elif isinstance(seed, bytes):
        data = seed
    else:
        raise AllocationInputError("rounding seed must be str or bytes")
    if not data:
        raise AllocationInputError("rounding seed must not be empty")
    return data


def _tie_digest(seed: bytes, cell: str, category: str, group: str) -> bytes:
    encoded = json.dumps([cell, category, group], ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(b"aidmesh-rounding-tie-v1\x00" + seed + b"\x00" + encoded).digest()


def round_allocation(
    allocation: AllocationMatrix,
    *,
    seed: str | bytes,
) -> IntegerAllocation:
    """Floor to quota units, then assign feasible largest remainders.

    At most one residual unit is considered per ``(group, category)`` coordinate,
    and only when the divisible amount had a positive fractional remainder.  A
    unit is added only if both the demand cap and category stock remain feasible.
    Remainders are ordered largest first; exact ties are rotated by a stable hash
    of the committed seed and the full cell/category/group coordinate.
    """

    if not isinstance(allocation, AllocationMatrix):
        raise AllocationInputError("allocation must be an AllocationMatrix")
    allocation.assert_feasible()
    seed_data = _seed_bytes(seed)
    quotas: dict[str, dict[str, int]] = {
        group: {category: 0 for category in allocation.categories} for group in allocation.groups
    }
    amounts: dict[str, dict[str, Fraction]] = {
        group: {category: Fraction(0) for category in allocation.categories}
        for group in allocation.groups
    }
    certificates: dict[str, RoundingCategoryCertificate] = {}

    for category in allocation.categories:
        unit = allocation.units[category]
        candidates: list[tuple[Fraction, bytes, str]] = []
        floor_amount = Fraction(0)
        floor_loss = Fraction(0)
        for group in allocation.groups:
            target = allocation.amounts[group][category]
            quota = target // unit
            base = quota * unit
            quotas[group][category] = quota
            amounts[group][category] = base
            floor_amount += base
            remainder = target - base
            floor_loss += remainder
            if remainder > 0 and base + unit <= allocation.demand[group][category]:
                candidates.append(
                    (remainder, _tie_digest(seed_data, allocation.cell, category, group), group)
                )

        remaining_stock = allocation.stock[category] - floor_amount
        if remaining_stock < 0:
            raise AllocationInvariantError("flooring unexpectedly exceeded stock")
        available_units = remaining_stock // unit
        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        residual_count = min(available_units, len(candidates))
        for _, _, group in candidates[:residual_count]:
            quotas[group][category] += 1
            amounts[group][category] += unit

        allocated_amount = sum(
            (amounts[group][category] for group in allocation.groups), Fraction(0)
        )
        stock_remaining = allocation.stock[category] - allocated_amount
        if allocated_amount + stock_remaining != allocation.stock[category]:
            raise AllocationInvariantError("rounding stock conservation failed")
        certificates[category] = RoundingCategoryCertificate(
            category=category,
            floor_amount=floor_amount,
            floor_loss=floor_loss,
            residual_units_assigned=residual_count,
            allocated_amount=allocated_amount,
            stock_remaining=stock_remaining,
        )

    commitment = hashlib.sha256(b"aidmesh-rounding-seed-v1\x00" + seed_data).hexdigest()
    result = IntegerAllocation(
        method=f"{allocation.method}+integer-rounding",
        cell=allocation.cell,
        groups=allocation.groups,
        categories=allocation.categories,
        amounts=_freeze_nested(amounts),
        demand=allocation.demand,
        stock=allocation.stock,
        weights=allocation.weights,
        conversion_factors=allocation.conversion_factors,
        units=allocation.units,
        quotas=_freeze_nested_int(quotas),
        seed_commitment=commitment,
        certificates=MappingProxyType(certificates),
    )
    result.assert_feasible()
    for group in result.groups:
        for category in result.categories:
            if result.amounts[group][category] != (
                result.quotas[group][category] * result.units[category]
            ):
                raise AllocationInvariantError("quota-to-amount conservation failed")
            error = abs(result.amounts[group][category] - allocation.amounts[group][category])
            if error >= result.units[category]:
                raise AllocationInvariantError("additive rounding bound failed")
    return result


@dataclass(frozen=True, slots=True)
class IssuanceRecord:
    """Append-only aggregate quota issuance record without beneficiary identity."""

    sequence: int
    issuance_id: str
    cell: str
    group: str
    quotas: Mapping[str, int]
    previous_digest: str
    record_digest: str


def _record_digest(
    sequence: int,
    issuance_id: str,
    cell: str,
    group: str,
    quotas: Mapping[str, int],
    previous_digest: str,
) -> str:
    payload = json.dumps(
        {
            "sequence": sequence,
            "issuance_id": issuance_id,
            "cell": cell,
            "group": group,
            "quotas": [[key, quotas[key]] for key in sorted(quotas)],
            "previous_digest": previous_digest,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(b"aidmesh-issuance-record-v1\x00" + payload).hexdigest()


class IssuanceLedger:
    """Thread-safe ledger enforcing aggregate integer entitlement conservation.

    The ledger stores only an opaque issuance identifier and aggregate
    ``cell/group/category`` counts.  It does not ask for a name, credential
    secret, or identity-linked transcript.  A multi-category issuance is
    checked in full before any counter changes, so rejected batches are atomic.
    """

    _GENESIS_DIGEST = hashlib.sha256(b"aidmesh-issuance-genesis-v1").hexdigest()

    def __init__(self, allocations: IntegerAllocation | Iterable[IntegerAllocation]) -> None:
        if isinstance(allocations, IntegerAllocation):
            items = (allocations,)
        else:
            items = tuple(allocations)
        if not items:
            raise AllocationInputError("issuance ledger needs at least one allocation")
        caps: dict[tuple[str, str, str], int] = {}
        units: dict[tuple[str, str], Fraction] = {}
        cells: set[str] = set()
        for allocation in items:
            if not isinstance(allocation, IntegerAllocation):
                raise AllocationInputError("ledger inputs must be IntegerAllocation objects")
            allocation.assert_feasible()
            if allocation.cell in cells:
                raise AllocationInputError(
                    f"duplicate integer allocation for cell {allocation.cell!r}"
                )
            cells.add(allocation.cell)
            for category in allocation.categories:
                units[(allocation.cell, category)] = allocation.units[category]
            for group in allocation.groups:
                for category in allocation.categories:
                    caps[(allocation.cell, group, category)] = allocation.quotas[group][category]
        self._caps = caps
        self._units = units
        self._issued = {key: 0 for key in caps}
        self._records: list[IssuanceRecord] = []
        self._issuance_ids: set[str] = set()
        self._lock = RLock()

    @property
    def records(self) -> tuple[IssuanceRecord, ...]:
        """Return an immutable snapshot of the append-only record sequence."""

        with self._lock:
            return tuple(self._records)

    def allocation_quota(self, cell: str, group: str, category: str) -> int:
        """Return the committed quota-count cap for one coordinate."""

        key = (cell, group, category)
        try:
            return self._caps[key]
        except KeyError as exc:
            raise AllocationInputError(f"unknown quota coordinate {key!r}") from exc

    def issued_quota(self, cell: str, group: str, category: str) -> int:
        """Return the quota count issued so far for one coordinate."""

        key = (cell, group, category)
        with self._lock:
            try:
                return self._issued[key]
            except KeyError as exc:
                raise AllocationInputError(f"unknown quota coordinate {key!r}") from exc

    def remaining_quota(self, cell: str, group: str, category: str) -> int:
        """Return the unissued integer quota count for one coordinate."""

        return self.allocation_quota(cell, group, category) - self.issued_quota(
            cell, group, category
        )

    def issue(
        self,
        *,
        issuance_id: str,
        cell: str,
        group: str,
        quotas: Mapping[str, int],
    ) -> IssuanceRecord:
        """Atomically append a multi-category issuance after conservation checks."""

        issuance_id = _identifier(issuance_id, "issuance_id")
        cell = _identifier(cell, "cell")
        group = _identifier(group, "group")
        if not isinstance(quotas, Mapping) or not quotas:
            raise AllocationInputError("quotas must contain at least one category")
        normalized: dict[str, int] = {}
        for category, count in quotas.items():
            category = _identifier(category, "quota category")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise AllocationInputError(
                    f"quota count for {category!r} must be a nonnegative integer"
                )
            if count:
                normalized[category] = count
        if not normalized:
            raise AllocationInputError("issuance must contain a positive quota count")

        with self._lock:
            if issuance_id in self._issuance_ids:
                raise DuplicateIssuanceError(f"issuance_id {issuance_id!r} already exists")
            for category, count in normalized.items():
                key = (cell, group, category)
                if key not in self._caps:
                    raise AllocationInputError(f"unknown quota coordinate {key!r}")
                proposed = self._issued[key] + count
                if proposed > self._caps[key]:
                    raise QuotaExceededError(
                        f"issuance would exceed {key!r}: {proposed} > {self._caps[key]}"
                    )

            sequence = len(self._records) + 1
            previous = self._records[-1].record_digest if self._records else self._GENESIS_DIGEST
            digest = _record_digest(sequence, issuance_id, cell, group, normalized, previous)
            record = IssuanceRecord(
                sequence=sequence,
                issuance_id=issuance_id,
                cell=cell,
                group=group,
                quotas=_freeze_int(normalized),
                previous_digest=previous,
                record_digest=digest,
            )
            for category, count in normalized.items():
                self._issued[(cell, group, category)] += count
            self._records.append(record)
            self._issuance_ids.add(issuance_id)
            self.assert_consistent()
            return record

    def issued_amount(self, cell: str, group: str, category: str) -> Fraction:
        """Return issued physical amount (quota count multiplied by unit)."""

        key = (cell, group, category)
        if (cell, category) not in self._units:
            raise AllocationInputError(f"unknown quota coordinate {key!r}")
        return self.issued_quota(cell, group, category) * self._units[(cell, category)]

    def assert_consistent(self) -> None:
        """Recompute the hash chain and all counters from records.

        This check is intentionally independent of the incrementally maintained
        counters.  It can therefore detect accidental mutation or ledger drift.
        """

        with self._lock:
            recomputed = {key: 0 for key in self._caps}
            seen: set[str] = set()
            previous = self._GENESIS_DIGEST
            for expected_sequence, record in enumerate(self._records, start=1):
                if record.sequence != expected_sequence:
                    raise AllocationInvariantError("issuance sequence is not contiguous")
                if record.issuance_id in seen:
                    raise AllocationInvariantError("duplicate issuance id in record history")
                if record.previous_digest != previous:
                    raise AllocationInvariantError("issuance hash-chain predecessor mismatch")
                expected_digest = _record_digest(
                    record.sequence,
                    record.issuance_id,
                    record.cell,
                    record.group,
                    record.quotas,
                    record.previous_digest,
                )
                if record.record_digest != expected_digest:
                    raise AllocationInvariantError("issuance record digest mismatch")
                for category, count in record.quotas.items():
                    key = (record.cell, record.group, category)
                    if key not in recomputed:
                        raise AllocationInvariantError("record uses an unknown quota coordinate")
                    recomputed[key] += count
                    if recomputed[key] > self._caps[key]:
                        raise AllocationInvariantError("record history exceeds allocation quota")
                seen.add(record.issuance_id)
                previous = record.record_digest
            if recomputed != self._issued:
                raise AllocationInvariantError("issuance counters differ from record history")
            if seen != self._issuance_ids:
                raise AllocationInvariantError("issuance id index differs from record history")


__all__ = [
    "AllocationInputError",
    "AllocationInvariantError",
    "AllocationMatrix",
    "BottleneckEvent",
    "CampaignAllocation",
    "DivisibleAllocation",
    "DuplicateIssuanceError",
    "IntegerAllocation",
    "IssuanceLedger",
    "IssuanceRecord",
    "PackingProblem",
    "QuotaExceededError",
    "RationalLike",
    "RoundingCategoryCertificate",
    "ValidatedPackingProblem",
    "as_fraction",
    "normalize_problem",
    "progressive_fill",
    "progressive_fill_cells",
    "round_allocation",
]
