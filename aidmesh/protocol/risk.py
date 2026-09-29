"""Conserved, transferable fallback risk rights with atomic hash-chained state."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from aidmesh.common.canonical import stable_hash
from aidmesh.common.errors import DuplicateError, InvariantViolation, ValidationError

from .campaign import SafetyDomain


@dataclass(frozen=True)
class SiteStateToken:
    site_id: str
    nonce: int
    chain_hash: str


@dataclass(frozen=True)
class DebitRecord:
    sequence: int
    domain: SafetyDomain
    site_id: str
    debit_id: str
    nullifier: str
    amount: int
    balance_before: int
    balance_after: int
    site_nonce: int
    previous_chain_hash: str
    chain_hash: str


@dataclass(frozen=True)
class TransferRecord:
    sequence: int
    domain: SafetyDomain
    transfer_id: str
    source_site: str
    destination_site: str
    amount: int
    source_balance_before: int
    source_balance_after: int
    destination_balance_before: int
    destination_balance_after: int
    source_nonce: int
    destination_nonce: int
    source_previous_hash: str
    destination_previous_hash: str
    source_chain_hash: str
    destination_chain_hash: str


LedgerRecord = DebitRecord | TransferRecord


@dataclass(frozen=True)
class RiskLedgerSnapshot:
    domain: SafetyDomain
    initial_total: int
    balances: tuple[tuple[str, int], ...]
    debited_by_site: tuple[tuple[str, int], ...]
    state_tokens: tuple[SiteStateToken, ...]
    journal_hash: str

    @property
    def remaining_total(self) -> int:
        return sum(value for _, value in self.balances)

    @property
    def debited_total(self) -> int:
        return sum(value for _, value in self.debited_by_site)

    @property
    def is_conserved(self) -> bool:
        return self.remaining_total + self.debited_total == self.initial_total


class RiskRightsLedger:
    """Atomic state machine for a fixed campaign safety domain.

    Rights may move between pre-authorized sites but are never minted during an
    epoch.  A fallback release consumes rights.  Every balance mutation advances
    the affected site's nonce and hash chain; transfers advance both sites in one
    lock-protected transaction.
    """

    def __init__(self, domain: SafetyDomain, initial_rights: dict[str, int]) -> None:
        if not initial_rights:
            raise ValidationError("risk ledger needs at least one authorized site")
        if any(not site for site in initial_rights):
            raise ValidationError("site identifiers must be non-empty")
        if any(isinstance(value, bool) or value < 0 for value in initial_rights.values()):
            raise ValidationError("initial risk rights must be non-negative integers")
        if any(not isinstance(value, int) for value in initial_rights.values()):
            raise ValidationError("risk rights use integral value units")
        self.domain = domain
        self._initial = dict(sorted(initial_rights.items()))
        self._initial_total = sum(self._initial.values())
        self._balances = self._initial.copy()
        self._debited = {site: 0 for site in self._initial}
        self._nonces = {site: 0 for site in self._initial}
        self._heads = {
            site: self._genesis_hash(site, value) for site, value in self._initial.items()
        }
        self._debits: dict[str, DebitRecord] = {}
        self._transfers: dict[str, TransferRecord] = {}
        self._journal: list[LedgerRecord] = []
        self._lock = threading.RLock()

    def _genesis_hash(self, site: str, value: int) -> str:
        return stable_hash(
            {"domain": self.domain, "site_id": site, "initial_rights": value},
            domain="aidmesh/risk-rights/genesis/v1",
        )

    def _require_site(self, site_id: str) -> None:
        if site_id not in self._balances:
            raise ValidationError(f"site {site_id!r} is not authorized by this ledger")

    def state_token(self, site_id: str) -> SiteStateToken:
        with self._lock:
            self._require_site(site_id)
            return SiteStateToken(site_id, self._nonces[site_id], self._heads[site_id])

    def balance(self, site_id: str) -> int:
        with self._lock:
            self._require_site(site_id)
            return self._balances[site_id]

    def _check_token(self, expected: SiteStateToken) -> None:
        self._require_site(expected.site_id)
        actual = SiteStateToken(
            expected.site_id,
            self._nonces[expected.site_id],
            self._heads[expected.site_id],
        )
        if actual != expected:
            raise ValidationError("stale risk-right state token")

    def debit(
        self,
        *,
        site_id: str,
        debit_id: str,
        nullifier: str,
        amount: int,
        expected_state: SiteStateToken | None = None,
    ) -> DebitRecord:
        """Atomically consume rights before an inventory release.

        ``debit_id`` makes an exact retry idempotent.  Reusing it with different
        fields is rejected.  ``expected_state`` provides compare-and-set
        protection when a caller prepared work against a prior snapshot.
        """

        if not debit_id or not nullifier:
            raise ValidationError("debit_id and nullifier are required")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise ValidationError("debit amount must be a positive integer")
        with self._lock:
            self._require_site(site_id)
            existing = self._debits.get(debit_id)
            if existing is not None:
                if (
                    existing.site_id == site_id
                    and existing.nullifier == nullifier
                    and existing.amount == amount
                ):
                    return existing
                raise DuplicateError("debit_id was already used for another operation")
            if debit_id in self._transfers:
                raise DuplicateError("operation id was already used for a transfer")
            if expected_state is not None:
                if expected_state.site_id != site_id:
                    raise ValidationError("state token belongs to another site")
                self._check_token(expected_state)
            before = self._balances[site_id]
            if amount > before:
                raise ValidationError("insufficient fallback risk rights")
            after = before - amount
            nonce = self._nonces[site_id] + 1
            previous = self._heads[site_id]
            sequence = len(self._journal)
            fields = {
                "sequence": sequence,
                "domain": self.domain,
                "kind": "debit",
                "site_id": site_id,
                "debit_id": debit_id,
                "nullifier": nullifier,
                "amount": amount,
                "balance_before": before,
                "balance_after": after,
                "site_nonce": nonce,
                "previous_chain_hash": previous,
            }
            chain_hash = stable_hash(fields, domain="aidmesh/risk-rights/site-chain/v1")
            record = DebitRecord(
                sequence=sequence,
                domain=self.domain,
                site_id=site_id,
                debit_id=debit_id,
                nullifier=nullifier,
                amount=amount,
                balance_before=before,
                balance_after=after,
                site_nonce=nonce,
                previous_chain_hash=previous,
                chain_hash=chain_hash,
            )
            # The complete mutation is one critical section: no negative balance
            # or externally visible half-updated hash-chain state is possible.
            self._balances[site_id] = after
            self._debited[site_id] += amount
            self._nonces[site_id] = nonce
            self._heads[site_id] = chain_hash
            self._debits[debit_id] = record
            self._journal.append(record)
            self._assert_conserved_unlocked()
            return record

    def transfer(
        self,
        *,
        transfer_id: str,
        source_site: str,
        destination_site: str,
        amount: int,
        expected_source: SiteStateToken,
        expected_destination: SiteStateToken,
    ) -> TransferRecord:
        """Atomically transfer, advancing nonce/hash chains at both endpoints."""

        if not transfer_id:
            raise ValidationError("transfer_id is required")
        if source_site == destination_site:
            raise ValidationError("source and destination sites must differ")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise ValidationError("transfer amount must be a positive integer")
        with self._lock:
            self._require_site(source_site)
            self._require_site(destination_site)
            existing = self._transfers.get(transfer_id)
            if existing is not None:
                if (
                    existing.source_site == source_site
                    and existing.destination_site == destination_site
                    and existing.amount == amount
                ):
                    return existing
                raise DuplicateError("transfer_id was already used for another transfer")
            if transfer_id in self._debits:
                raise DuplicateError("operation id was already used for a debit")
            if expected_source.site_id != source_site:
                raise ValidationError("source token belongs to another site")
            if expected_destination.site_id != destination_site:
                raise ValidationError("destination token belongs to another site")
            self._check_token(expected_source)
            self._check_token(expected_destination)

            source_before = self._balances[source_site]
            destination_before = self._balances[destination_site]
            if amount > source_before:
                raise ValidationError("insufficient transferable risk rights")
            source_after = source_before - amount
            destination_after = destination_before + amount
            source_nonce = self._nonces[source_site] + 1
            destination_nonce = self._nonces[destination_site] + 1
            source_previous = self._heads[source_site]
            destination_previous = self._heads[destination_site]
            sequence = len(self._journal)
            common = {
                "sequence": sequence,
                "domain": self.domain,
                "kind": "transfer",
                "transfer_id": transfer_id,
                "source_site": source_site,
                "destination_site": destination_site,
                "amount": amount,
                "source_balance_before": source_before,
                "source_balance_after": source_after,
                "destination_balance_before": destination_before,
                "destination_balance_after": destination_after,
                "source_nonce": source_nonce,
                "destination_nonce": destination_nonce,
                "source_previous_hash": source_previous,
                "destination_previous_hash": destination_previous,
            }
            source_hash = stable_hash(
                {**common, "chain_role": "source"},
                domain="aidmesh/risk-rights/site-chain/v1",
            )
            destination_hash = stable_hash(
                {**common, "chain_role": "destination", "peer_hash": source_hash},
                domain="aidmesh/risk-rights/site-chain/v1",
            )
            record = TransferRecord(
                sequence=sequence,
                domain=self.domain,
                transfer_id=transfer_id,
                source_site=source_site,
                destination_site=destination_site,
                amount=amount,
                source_balance_before=source_before,
                source_balance_after=source_after,
                destination_balance_before=destination_before,
                destination_balance_after=destination_after,
                source_nonce=source_nonce,
                destination_nonce=destination_nonce,
                source_previous_hash=source_previous,
                destination_previous_hash=destination_previous,
                source_chain_hash=source_hash,
                destination_chain_hash=destination_hash,
            )
            self._balances[source_site] = source_after
            self._balances[destination_site] = destination_after
            self._nonces[source_site] = source_nonce
            self._nonces[destination_site] = destination_nonce
            self._heads[source_site] = source_hash
            self._heads[destination_site] = destination_hash
            self._transfers[transfer_id] = record
            self._journal.append(record)
            self._assert_conserved_unlocked()
            return record

    def _assert_conserved_unlocked(self) -> None:
        remaining = sum(self._balances.values())
        debited = sum(self._debited.values())
        if remaining + debited != self._initial_total:
            raise InvariantViolation("risk rights were minted or destroyed")
        if any(value < 0 for value in self._balances.values()):
            raise InvariantViolation("negative risk-right balance")

    def assert_conserved(self) -> None:
        with self._lock:
            self._assert_conserved_unlocked()

    @property
    def journal(self) -> tuple[LedgerRecord, ...]:
        with self._lock:
            return tuple(self._journal)

    def snapshot(self) -> RiskLedgerSnapshot:
        with self._lock:
            self._assert_conserved_unlocked()
            tokens = tuple(
                SiteStateToken(site, self._nonces[site], self._heads[site])
                for site in sorted(self._balances)
            )
            return RiskLedgerSnapshot(
                domain=self.domain,
                initial_total=self._initial_total,
                balances=tuple(sorted(self._balances.items())),
                debited_by_site=tuple(sorted(self._debited.items())),
                state_tokens=tokens,
                journal_hash=stable_hash(self._journal, domain="aidmesh/risk-rights/journal/v1"),
            )
