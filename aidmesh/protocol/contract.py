"""Deterministic contract reference model with first-valid-wins settlement."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from aidmesh.common.crypto import SignatureVerifier, VoucherProofVerifier
from aidmesh.common.errors import InvariantViolation, ValidationError

from .campaign import CampaignHeader, SafetyDomain
from .model import (
    FallbackEvidence,
    QuorumCertificate,
    Receipt,
    RedemptionPath,
    witness_request_for,
)
from .quorum import verify_certificate

FallbackEvidenceVerifier = Callable[[Receipt, CampaignHeader], bool]
HeaderVerifier = Callable[[CampaignHeader], bool]


@dataclass(frozen=True)
class ContractVerifierInterfaces:
    """External proof/signature interfaces; no cryptography is emulated here."""

    verify_voucher: VoucherProofVerifier
    verify_signature: SignatureVerifier
    verify_fallback_evidence: FallbackEvidenceVerifier
    verify_header: HeaderVerifier | None = None


class SettlementStatus(StrEnum):
    SETTLED = "settled"
    CONFLICT = "conflict"
    REJECTED = "rejected"


@dataclass(frozen=True)
class SettlementResult:
    sequence: int
    receipt_id: str
    status: SettlementStatus
    reason: str
    credited_value: int
    winning_receipt_id: str | None = None


class ContractModel:
    """Executable specification for ordered, atomic receipt settlement."""

    def __init__(self, verifiers: ContractVerifierInterfaces) -> None:
        self._verifiers = verifiers
        self._headers: dict[SafetyDomain, CampaignHeader] = {}
        self._latest_by_cell: dict[tuple[str, str], CampaignHeader] = {}
        self._escrow: dict[tuple[str, int], int] = {}
        self._settled: dict[tuple[str, int, str], str] = {}
        self._receipts: dict[str, Receipt] = {}
        self._credits: dict[tuple[str, int, str], int] = {}
        self._results: list[SettlementResult] = []
        self._lock = threading.RLock()

    def register_header(self, header: CampaignHeader, *, escrow: int = 0) -> None:
        if isinstance(escrow, bool) or not isinstance(escrow, int) or escrow < 0:
            raise ValidationError("escrow must be a non-negative integer")
        if self._verifiers.verify_header is not None and not self._verifiers.verify_header(header):
            raise ValidationError("external header verifier rejected the header")
        with self._lock:
            existing = self._headers.get(header.domain)
            if existing is not None:
                if existing.header_hash != header.header_hash:
                    raise ValidationError("conflicting header for one safety domain")
                if escrow:
                    raise ValidationError("cannot fund by replaying a registered header")
                return
            lineage = (header.domain.campaign_id, header.risk_cell)
            previous = self._latest_by_cell.get(lineage)
            if previous is not None:
                CampaignHeader.validate_successor(previous, header)
            self._headers[header.domain] = header
            self._latest_by_cell[lineage] = header
            escrow_key = (header.domain.campaign_id, header.domain.epoch)
            self._escrow[escrow_key] = self._escrow.get(escrow_key, 0) + escrow

    def fund(self, campaign_id: str, epoch: int, amount: int) -> None:
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise ValidationError("funding amount must be a positive integer")
        with self._lock:
            if not any(
                domain.campaign_id == campaign_id and domain.epoch == epoch
                for domain in self._headers
            ):
                raise ValidationError("cannot fund an unregistered campaign epoch")
            key = (campaign_id, epoch)
            self._escrow[key] = self._escrow.get(key, 0) + amount

    def _validate_receipt(self, receipt: Receipt) -> tuple[bool, str]:
        header = self._headers.get(receipt.voucher.domain)
        if header is None:
            return False, "UNREGISTERED_SAFETY_DOMAIN"
        voucher = receipt.voucher
        if voucher.risk_cell != header.risk_cell:
            return False, "WRONG_RISK_CELL"
        if receipt.site_id not in header.authorized_sites:
            return False, "UNAUTHORIZED_SITE"
        if not header.is_active(receipt.released_at):
            return False, "RELEASE_OUTSIDE_EPOCH"
        if not self._verifiers.verify_voucher(voucher, header):
            return False, "INVALID_VOUCHER_PROOF"
        if not receipt.site_signature or not self._verifiers.verify_signature(
            receipt.site_id, receipt.signing_digest, receipt.site_signature
        ):
            return False, "INVALID_SITE_SIGNATURE"

        if receipt.path is RedemptionPath.QUORUM:
            if not isinstance(receipt.evidence, QuorumCertificate):
                return False, "WRONG_PATH_EVIDENCE"
            request = witness_request_for(voucher, receipt.site_id)
            if not verify_certificate(
                request,
                receipt.evidence,
                header.committee,
                self._verifiers.verify_signature,
            ):
                return False, "INVALID_QUORUM_CERTIFICATE"
        elif receipt.path is RedemptionPath.FALLBACK:
            evidence = receipt.evidence
            if not isinstance(evidence, FallbackEvidence):
                return False, "WRONG_PATH_EVIDENCE"
            if voucher.category not in header.fallback_categories:
                return False, "FALLBACK_DISABLED"
            if (
                evidence.debit_amount != voucher.value
                or evidence.balance_before - evidence.debit_amount != evidence.balance_after
                or evidence.balance_after < 0
                or evidence.site_nonce < 1
                or not evidence.debit_id
                or not evidence.chain_hash
            ):
                return False, "INVALID_RISK_DEBIT"
            if not self._verifiers.verify_fallback_evidence(receipt, header):
                return False, "INVALID_FALLBACK_EVIDENCE"
        else:  # defensive against values constructed outside the enum
            return False, "UNKNOWN_PATH"
        return True, "VALID"

    def submit(self, receipt: Receipt) -> SettlementResult:
        """Validate first, then atomically apply first-valid-wins ordering."""

        valid, reason = self._validate_receipt(receipt)
        with self._lock:
            sequence = len(self._results)
            if not valid:
                result = SettlementResult(
                    sequence,
                    receipt.receipt_id,
                    SettlementStatus.REJECTED,
                    reason,
                    0,
                )
                self._results.append(result)
                return result

            campaign, epoch = receipt.voucher.domain.nullifier_scope
            freshness_key = (campaign, epoch, receipt.voucher.nullifier)
            winner = self._settled.get(freshness_key)
            if winner is not None:
                result = SettlementResult(
                    sequence,
                    receipt.receipt_id,
                    SettlementStatus.CONFLICT,
                    "NULLIFIER_ALREADY_SETTLED",
                    0,
                    winner,
                )
                self._results.append(result)
                return result
            escrow_key = (campaign, epoch)
            if self._escrow.get(escrow_key, 0) < receipt.voucher.value:
                result = SettlementResult(
                    sequence,
                    receipt.receipt_id,
                    SettlementStatus.REJECTED,
                    "INSUFFICIENT_ESCROW",
                    0,
                )
                self._results.append(result)
                return result

            # Membership insertion, escrow debit, and site credit are one atomic
            # critical section mirroring one deterministic contract transaction.
            self._settled[freshness_key] = receipt.receipt_id
            self._receipts[receipt.receipt_id] = receipt
            self._escrow[escrow_key] -= receipt.voucher.value
            credit_key = (campaign, epoch, receipt.site_id)
            self._credits[credit_key] = self._credits.get(credit_key, 0) + receipt.voucher.value
            result = SettlementResult(
                sequence,
                receipt.receipt_id,
                SettlementStatus.SETTLED,
                "FIRST_VALID_RECEIPT",
                receipt.voucher.value,
                receipt.receipt_id,
            )
            self._results.append(result)
            return result

    def submit_batch(self, receipts: tuple[Receipt, ...]) -> tuple[SettlementResult, ...]:
        return tuple(self.submit(receipt) for receipt in receipts)

    @property
    def results(self) -> tuple[SettlementResult, ...]:
        with self._lock:
            return tuple(self._results)

    @property
    def credits(self) -> dict[tuple[str, int, str], int]:
        with self._lock:
            return self._credits.copy()

    def winner(self, campaign_id: str, epoch: int, nullifier: str) -> str | None:
        with self._lock:
            return self._settled.get((campaign_id, epoch, nullifier))

    def assert_at_most_once(self) -> None:
        with self._lock:
            settled_results = [
                result for result in self._results if result.status is SettlementStatus.SETTLED
            ]
            ids = [result.receipt_id for result in settled_results]
            if len(ids) != len(set(ids)):
                raise InvariantViolation("one receipt was credited twice")
            credited_total = sum(result.credited_value for result in settled_results)
            if credited_total != sum(self._credits.values()):
                raise InvariantViolation("contract credit accounting diverged")
