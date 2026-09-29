"""Two-phase escrow transfers for conserved risk rights under delayed delivery."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from aidmesh.common.canonical import stable_hash
from aidmesh.common.crypto import MessageSigner, SignatureVerifier
from aidmesh.common.errors import DuplicateError, InvariantViolation, ValidationError
from aidmesh.protocol.witness import InMemoryWitnessStore, PersistOutcome, WitnessStateStore


class TransferStatus(StrEnum):
    IN_TRANSIT = "in_transit"
    ACCEPTED = "accepted"
    RECLAIMED = "reclaimed"
    EXPIRED = "expired"
    REVOKED = "revoked"


class TerminalDecision(StrEnum):
    ACCEPT = "accept"
    RECLAIM = "reclaim"


@dataclass(frozen=True)
class TerminalStatement:
    protocol_version: int
    domain_id: str
    epoch: int
    transfer_id: str
    source_site: str
    destination_site: str
    amount: int
    decision: TerminalDecision

    def __post_init__(self) -> None:
        if self.protocol_version != 1:
            raise ValidationError("unsupported terminal statement version")
        if not all(
            (
                self.domain_id,
                self.transfer_id,
                self.source_site,
                self.destination_site,
            )
        ):
            raise ValidationError("terminal statement fields must be non-empty")
        if self.epoch <= 0 or self.amount <= 0:
            raise ValidationError("terminal epoch and amount must be positive")

    @property
    def digest(self) -> str:
        return stable_hash(self, domain="aidmesh/terminal-statement/v1")


@dataclass(frozen=True)
class TerminalShare:
    signer_id: str
    statement_digest: str
    signature: str


@dataclass(frozen=True)
class TerminalCertificate:
    statement: TerminalStatement
    shares: tuple[TerminalShare, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "shares", tuple(sorted(self.shares, key=lambda x: x.signer_id)))


class TerminalWitness:
    """Persist one terminal decision per transfer before signing it."""

    def __init__(
        self, signer_id: str, signer: MessageSigner, *, store: WitnessStateStore | None = None
    ) -> None:
        if not signer_id:
            raise ValidationError("terminal witness id is required")
        self.signer_id = signer_id
        self._signer = signer
        self._store = store if store is not None else InMemoryWitnessStore()
        self._lock = threading.RLock()

    def sign(self, statement: TerminalStatement) -> TerminalShare:
        key = (statement.domain_id, statement.epoch, statement.transfer_id)
        with self._lock:
            outcome = self._store.persist_once(key, statement.digest)
            if outcome is PersistOutcome.CONFLICT:
                raise DuplicateError("terminal witness already signed a conflicting decision")
            signature = self._signer(self.signer_id, statement.digest)
            if not signature:
                raise ValidationError("terminal signer returned an empty signature")
            return TerminalShare(self.signer_id, statement.digest, signature)


def assemble_terminal_certificate(
    statement: TerminalStatement,
    shares: Iterable[TerminalShare],
    *,
    members: tuple[str, ...],
    threshold: int,
    verify_signature: SignatureVerifier,
) -> TerminalCertificate:
    certificate = TerminalCertificate(statement, tuple(shares))
    _verify_terminal_certificate(
        certificate,
        members=members,
        threshold=threshold,
        verify_signature=verify_signature,
    )
    return certificate


def _verify_terminal_certificate(
    certificate: TerminalCertificate,
    *,
    members: tuple[str, ...],
    threshold: int,
    verify_signature: SignatureVerifier,
) -> None:
    if not 1 <= threshold <= len(members):
        raise ValidationError("invalid terminal threshold")
    signer_ids = tuple(share.signer_id for share in certificate.shares)
    if len(signer_ids) < threshold:
        raise ValidationError("insufficient terminal certificate")
    if len(signer_ids) != len(set(signer_ids)):
        raise ValidationError("terminal certificate repeats a signer")
    allowed = set(members)
    for share in certificate.shares:
        if share.signer_id not in allowed:
            raise ValidationError("terminal certificate contains a foreign signer")
        if share.statement_digest != certificate.statement.digest:
            raise ValidationError("terminal share is bound to another statement")
        if not verify_signature(share.signer_id, share.statement_digest, share.signature):
            raise ValidationError("invalid terminal signature")


def terminal_safety_condition(*, committee_size: int, threshold: int, faults: int) -> bool:
    return 2 * threshold > committee_size + faults


def terminal_liveness_condition(*, committee_size: int, threshold: int, faults: int) -> bool:
    return threshold <= committee_size - faults


@dataclass(frozen=True)
class EscrowTransfer:
    transfer_id: str
    source_site: str
    destination_site: str
    amount: int
    status: TransferStatus


@dataclass(frozen=True)
class EscrowSnapshot:
    issued: int
    remaining: int
    in_transit: int
    spent: int
    expired: int
    revoked: int
    reclaimed: int
    balances: tuple[tuple[str, int], ...]

    @property
    def conserved(self) -> bool:
        return self.issued == (
            self.remaining + self.in_transit + self.spent + self.expired + self.revoked
        )


class EscrowRiskLedger:
    """Fail-closed risk-right lifecycle with delayed, reorder-safe transfer.

    ``begin_transfer`` removes rights from the source before a transfer message is
    emitted. ``accept_transfer`` credits the destination exactly once. A certified
    ``reclaim_transfer`` returns an unaccepted transfer to its source. Acceptance and
    reclaim are mutually exclusive terminal decisions. Expiry and revocation burn
    the escrowed rights; they never return to spendable state.
    """

    def __init__(
        self,
        initial_rights: dict[str, int],
        *,
        domain_id: str = "reference",
        epoch: int = 1,
        terminal_members: tuple[str, ...] = (),
        terminal_threshold: int = 0,
    ) -> None:
        if not initial_rights or any(not site for site in initial_rights):
            raise ValidationError("at least one non-empty site is required")
        invalid_right = any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in initial_rights.values()
        )
        if invalid_right:
            raise ValidationError("rights must be non-negative integers")
        if not domain_id or epoch <= 0:
            raise ValidationError("terminal domain and epoch are required")
        if terminal_members:
            if len(terminal_members) != len(set(terminal_members)):
                raise ValidationError("terminal witnesses must be distinct")
            if not 1 <= terminal_threshold <= len(terminal_members):
                raise ValidationError("invalid terminal threshold")
        elif terminal_threshold != 0:
            raise ValidationError("terminal threshold requires configured witnesses")
        self.domain_id = domain_id
        self.epoch = epoch
        self.terminal_members = tuple(sorted(terminal_members))
        self.terminal_threshold = terminal_threshold
        self._balances = dict(sorted(initial_rights.items()))
        self._issued = sum(initial_rights.values())
        self._spent = 0
        self._expired = 0
        self._revoked = 0
        self._reclaimed = 0
        self._transfers: dict[str, EscrowTransfer] = {}
        self._spend_ids: dict[str, tuple[str, int]] = {}
        self._lock = threading.RLock()

    def begin_transfer(
        self, transfer_id: str, source_site: str, destination_site: str, amount: int
    ) -> EscrowTransfer:
        if not transfer_id or source_site == destination_site or amount <= 0:
            raise ValidationError("invalid transfer")
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
                raise DuplicateError("transfer id reused with different fields")
            if self._balances[source_site] < amount:
                raise ValidationError("insufficient rights")
            self._balances[source_site] -= amount
            transfer = EscrowTransfer(
                transfer_id, source_site, destination_site, amount, TransferStatus.IN_TRANSIT
            )
            self._transfers[transfer_id] = transfer
            self._assert_conserved()
            return transfer

    def accept_transfer(self, transfer_id: str) -> EscrowTransfer:
        if self.terminal_members:
            raise ValidationError("certified ledger requires an ACCEPT certificate")
        return self._finalize_accept(transfer_id)

    def _finalize_accept(self, transfer_id: str) -> EscrowTransfer:
        with self._lock:
            transfer = self._require_transfer(transfer_id)
            if transfer.status is TransferStatus.ACCEPTED:
                return transfer
            if transfer.status is not TransferStatus.IN_TRANSIT:
                raise ValidationError("terminal transfer cannot be accepted")
            self._balances[transfer.destination_site] += transfer.amount
            accepted = EscrowTransfer(
                transfer.transfer_id,
                transfer.source_site,
                transfer.destination_site,
                transfer.amount,
                TransferStatus.ACCEPTED,
            )
            self._transfers[transfer_id] = accepted
            self._assert_conserved()
            return accepted

    def reclaim_transfer(self, transfer_id: str) -> EscrowTransfer:
        """Return one still-locked transfer to its source exactly once.

        The ledger records the terminal transition.  A distributed deployment
        authorizes it with a quorum certificate whose honest witnesses never sign
        both ACCEPT and RECLAIM for the same transfer identifier.
        """

        if self.terminal_members:
            raise ValidationError("certified ledger requires a RECLAIM certificate")
        return self._finalize_reclaim(transfer_id)

    def _finalize_reclaim(self, transfer_id: str) -> EscrowTransfer:
        with self._lock:
            transfer = self._require_transfer(transfer_id)
            if transfer.status is TransferStatus.RECLAIMED:
                return transfer
            if transfer.status is not TransferStatus.IN_TRANSIT:
                raise ValidationError("only in-transit rights may be reclaimed")
            self._balances[transfer.source_site] += transfer.amount
            self._reclaimed += transfer.amount
            reclaimed = EscrowTransfer(
                transfer.transfer_id,
                transfer.source_site,
                transfer.destination_site,
                transfer.amount,
                TransferStatus.RECLAIMED,
            )
            self._transfers[transfer_id] = reclaimed
            self._assert_conserved()
            return reclaimed

    def terminal_statement(
        self,
        transfer_id: str,
        decision: TerminalDecision,
    ) -> TerminalStatement:
        with self._lock:
            transfer = self._require_transfer(transfer_id)
            return TerminalStatement(
                protocol_version=1,
                domain_id=self.domain_id,
                epoch=self.epoch,
                transfer_id=transfer.transfer_id,
                source_site=transfer.source_site,
                destination_site=transfer.destination_site,
                amount=transfer.amount,
                decision=decision,
            )

    def _require_matching_certificate(
        self,
        certificate: TerminalCertificate,
        decision: TerminalDecision,
        verify_signature: SignatureVerifier,
    ) -> None:
        if not self.terminal_members:
            raise ValidationError("ledger has no certified terminal committee")
        expected = self.terminal_statement(certificate.statement.transfer_id, decision)
        if certificate.statement != expected:
            raise ValidationError("terminal certificate does not match the transfer")
        _verify_terminal_certificate(
            certificate,
            members=self.terminal_members,
            threshold=self.terminal_threshold,
            verify_signature=verify_signature,
        )

    def accept_certified(
        self,
        certificate: TerminalCertificate,
        verify_signature: SignatureVerifier,
    ) -> EscrowTransfer:
        self._require_matching_certificate(
            certificate,
            TerminalDecision.ACCEPT,
            verify_signature,
        )
        return self._finalize_accept(certificate.statement.transfer_id)

    def reclaim_certified(
        self,
        certificate: TerminalCertificate,
        verify_signature: SignatureVerifier,
    ) -> EscrowTransfer:
        self._require_matching_certificate(
            certificate,
            TerminalDecision.RECLAIM,
            verify_signature,
        )
        return self._finalize_reclaim(certificate.statement.transfer_id)

    def terminate_transfer(self, transfer_id: str, *, revoked: bool = False) -> EscrowTransfer:
        with self._lock:
            transfer = self._require_transfer(transfer_id)
            wanted = TransferStatus.REVOKED if revoked else TransferStatus.EXPIRED
            if transfer.status is wanted:
                return transfer
            if transfer.status is not TransferStatus.IN_TRANSIT:
                raise ValidationError("only in-transit rights may terminate")
            if revoked:
                self._revoked += transfer.amount
            else:
                self._expired += transfer.amount
            terminal = EscrowTransfer(
                transfer.transfer_id,
                transfer.source_site,
                transfer.destination_site,
                transfer.amount,
                wanted,
            )
            self._transfers[transfer_id] = terminal
            self._assert_conserved()
            return terminal

    def spend(self, spend_id: str, site_id: str, amount: int) -> None:
        if not spend_id or amount <= 0:
            raise ValidationError("invalid spend")
        with self._lock:
            self._require_site(site_id)
            existing = self._spend_ids.get(spend_id)
            if existing is not None:
                if existing == (site_id, amount):
                    return
                raise DuplicateError("spend id reused with different fields")
            if self._balances[site_id] < amount:
                raise ValidationError("insufficient spendable rights")
            self._balances[site_id] -= amount
            self._spent += amount
            self._spend_ids[spend_id] = (site_id, amount)
            self._assert_conserved()

    def snapshot(self) -> EscrowSnapshot:
        with self._lock:
            in_transit = sum(
                t.amount for t in self._transfers.values() if t.status is TransferStatus.IN_TRANSIT
            )
            return EscrowSnapshot(
                issued=self._issued,
                remaining=sum(self._balances.values()),
                in_transit=in_transit,
                spent=self._spent,
                expired=self._expired,
                revoked=self._revoked,
                reclaimed=self._reclaimed,
                balances=tuple(sorted(self._balances.items())),
            )

    def _require_site(self, site_id: str) -> None:
        if site_id not in self._balances:
            raise ValidationError("unknown site")

    def _require_transfer(self, transfer_id: str) -> EscrowTransfer:
        try:
            return self._transfers[transfer_id]
        except KeyError as error:
            raise ValidationError("transfer debit not observed") from error

    def _assert_conserved(self) -> None:
        if not self.snapshot().conserved:
            raise InvariantViolation("escrow risk rights are not conserved")
