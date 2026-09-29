"""Honest distribution-site redemption state machine."""

from __future__ import annotations

import threading

from aidmesh.common.canonical import stable_hash
from aidmesh.common.crypto import (
    FallbackProofVerifier,
    MessageSigner,
    Proof,
    SignatureVerifier,
    VoucherProofVerifier,
)
from aidmesh.common.errors import DuplicateError, ValidationError

from .campaign import CampaignHeader
from .inventory import SiteInventory
from .model import (
    FallbackEvidence,
    QuorumCertificate,
    Receipt,
    RedemptionPath,
    Voucher,
    WitnessRequest,
    receipt_skeleton_hash_for,
    release_intent_id_for,
    witness_request_for,
)
from .quorum import verify_certificate
from .release import (
    InMemoryReleaseStore,
    ReleaseRecord,
    ReleaseSlotKey,
    ReleaseStateStore,
    ReleaseStatus,
)
from .risk import RiskRightsLedger, SiteStateToken


class Site:
    """A site that releases physical inventory only after path authorization."""

    def __init__(
        self,
        site_id: str,
        header: CampaignHeader,
        inventory: SiteInventory,
        voucher_verifier: VoucherProofVerifier,
        signer: MessageSigner,
        *,
        risk_ledger: RiskRightsLedger | None = None,
        release_store: ReleaseStateStore | None = None,
        clock_skew: int = 0,
    ) -> None:
        if site_id not in header.authorized_sites:
            raise ValidationError("site is not authorized by the campaign header")
        if clock_skew < 0:
            raise ValidationError("clock skew cannot be negative")
        if risk_ledger is not None and risk_ledger.domain != header.domain:
            raise ValidationError("risk ledger belongs to another safety domain")
        self.site_id = site_id
        self.header = header
        self.inventory = inventory
        self.risk_ledger = risk_ledger
        self.release_store = release_store or InMemoryReleaseStore()
        self._voucher_verifier = voucher_verifier
        self._signer = signer
        self._clock_skew = clock_skew
        self._redeemed: dict[tuple[str, int, str], Receipt] = {}
        self._fallback_tags: set[tuple[str, int, int, str, str, str]] = set()
        self._lock = threading.RLock()

    def _freshness_key(self, voucher: Voucher) -> tuple[str, int, str]:
        campaign, epoch = voucher.domain.nullifier_scope
        return campaign, epoch, voucher.nullifier

    def _release_slot_key(self, voucher: Voucher) -> ReleaseSlotKey:
        return ReleaseSlotKey(
            domain_id=voucher.domain.digest,
            epoch=voucher.domain.epoch,
            nullifier=voucher.nullifier,
            site_id=self.site_id,
        )

    def _validate_voucher(self, voucher: Voucher, tick: int) -> None:
        if voucher.domain != self.header.domain:
            raise ValidationError("voucher uses a stale or foreign safety domain")
        if voucher.risk_cell != self.header.risk_cell:
            raise ValidationError("voucher is authorized for another risk cell")
        if not self.header.is_active(tick, clock_skew=self._clock_skew):
            raise ValidationError("campaign header is not active at release time")
        if not self._voucher_verifier(voucher, self.header):
            raise ValidationError("external voucher proof verifier rejected the voucher")
        if self._freshness_key(voucher) in self._redeemed:
            raise DuplicateError("site already released inventory for this nullifier")

    def make_witness_request(self, voucher: Voucher, *, tick: int) -> WitnessRequest:
        with self._lock:
            self._validate_voucher(voucher, tick)
            request = witness_request_for(voucher, self.site_id)
            self._reject_irreversible_retry(self._release_slot_key(voucher))
            return request

    def _reject_irreversible_retry(self, slot_key: ReleaseSlotKey) -> None:
        record = self.release_store.read(slot_key)
        if record is not None and record.status in {
            ReleaseStatus.RELEASE_INTENT,
            ReleaseStatus.AMBIGUOUS,
            ReleaseStatus.CLOSED,
        }:
            raise DuplicateError(
                f"release slot is already {record.status}; automatic re-handoff is forbidden"
            )

    def _authorize_release(
        self,
        *,
        voucher: Voucher,
        path: RedemptionPath,
        authorization_hash: str,
        release_intent_id: str,
        receipt_skeleton_hash: str,
        reservation_id: str,
    ) -> ReleaseRecord:
        expected_intent = release_intent_id_for(voucher, self.site_id, path)
        expected_skeleton = receipt_skeleton_hash_for(
            voucher,
            self.site_id,
            path,
            expected_intent,
        )
        if release_intent_id != expected_intent:
            raise ValidationError("authorization names another release intent")
        if receipt_skeleton_hash != expected_skeleton:
            raise ValidationError("authorization names another receipt skeleton")
        slot_key = self._release_slot_key(voucher)
        self._reject_irreversible_retry(slot_key)
        return self.release_store.authorize(
            ReleaseRecord(
                release_intent_id=release_intent_id,
                domain_id=voucher.domain.digest,
                epoch=voucher.domain.epoch,
                nullifier=voucher.nullifier,
                site_id=self.site_id,
                authorization_hash=authorization_hash,
                receipt_skeleton_hash=receipt_skeleton_hash,
                reservation_id=reservation_id,
                status=ReleaseStatus.AUTHORIZED,
            )
        )

    def _reservation_id(
        self, voucher: Voucher, path: RedemptionPath, discriminator: str = ""
    ) -> str:
        return stable_hash(
            {
                "domain": voucher.domain,
                "site_id": self.site_id,
                "nullifier": voucher.nullifier,
                "path": path,
                "discriminator": discriminator,
            },
            domain="aidmesh/inventory-reservation/v1",
        )

    def redeem_quorum(
        self,
        voucher: Voucher,
        certificate: QuorumCertificate,
        *,
        tick: int,
        verify_witness_signature: SignatureVerifier,
    ) -> Receipt:
        with self._lock:
            self._validate_voucher(voucher, tick)
            request = witness_request_for(voucher, self.site_id)
            if not verify_certificate(
                request,
                certificate,
                self.header.committee,
                verify_witness_signature,
            ):
                raise ValidationError("invalid quorum certificate")
            reservation_id = self._reservation_id(voucher, RedemptionPath.QUORUM)
            release_record = self._authorize_release(
                voucher=voucher,
                path=RedemptionPath.QUORUM,
                authorization_hash=request.digest,
                release_intent_id=request.release_intent_id,
                receipt_skeleton_hash=request.receipt_skeleton_hash,
                reservation_id=reservation_id,
            )
            self.inventory.reserve(
                reservation_id=reservation_id,
                category=voucher.category,
                units=voucher.inventory_units,
            )
            intent_persisted = False
            try:
                # This is the durable point of no automatic retry.  From here on,
                # a crash is ambiguous because software cannot infer whether the
                # physical handoff occurred.
                self.release_store.persist_intent(release_record.slot_key)
                intent_persisted = True
                self.inventory.commit(reservation_id)
                receipt = Receipt(
                    voucher=voucher,
                    site_id=self.site_id,
                    path=RedemptionPath.QUORUM,
                    evidence=certificate,
                    released_at=tick,
                    release_intent_id=request.release_intent_id,
                    receipt_skeleton_hash=request.receipt_skeleton_hash,
                ).signed(self._signer)
                self.release_store.close(release_record.slot_key, receipt.receipt_id)
            except Exception:
                if intent_persisted:
                    current = self.release_store.read(release_record.slot_key)
                    if current is not None and current.status is ReleaseStatus.RELEASE_INTENT:
                        self.release_store.mark_ambiguous(release_record.slot_key)
                else:
                    self.inventory.cancel(reservation_id)
                raise
            self._redeemed[self._freshness_key(voucher)] = receipt
            return receipt

    def redeem_fallback(
        self,
        voucher: Voucher,
        *,
        fallback_window: int,
        link_tag: str,
        fallback_proof: Proof,
        tick: int,
        verify_fallback_proof: FallbackProofVerifier,
        expected_risk_state: SiteStateToken | None = None,
    ) -> Receipt:
        with self._lock:
            self._validate_voucher(voucher, tick)
            if self.risk_ledger is None:
                raise ValidationError("site has no fallback risk-right ledger")
            if voucher.category not in self.header.fallback_categories:
                raise ValidationError("fallback is disabled for this category")
            if fallback_window < 0 or not link_tag:
                raise ValidationError("invalid fallback window or link tag")
            if not verify_fallback_proof(voucher, fallback_window, link_tag, fallback_proof):
                raise ValidationError("external fallback proof verifier rejected the proof")

            campaign, epoch = voucher.domain.nullifier_scope
            tag_key = (
                campaign,
                epoch,
                fallback_window,
                voucher.risk_cell,
                voucher.category,
                link_tag,
            )
            if tag_key in self._fallback_tags:
                raise DuplicateError("fallback link tag was already used at this site")
            tag_hash = stable_hash(
                {"window": fallback_window, "tag": link_tag},
                domain="aidmesh/fallback-link-tag/v1",
            )
            reservation_id = self._reservation_id(voucher, RedemptionPath.FALLBACK, tag_hash)
            release_intent_id = release_intent_id_for(
                voucher,
                self.site_id,
                RedemptionPath.FALLBACK,
            )
            receipt_skeleton_hash = receipt_skeleton_hash_for(
                voucher,
                self.site_id,
                RedemptionPath.FALLBACK,
                release_intent_id,
            )
            self._reject_irreversible_retry(self._release_slot_key(voucher))
            self.inventory.reserve(
                reservation_id=reservation_id,
                category=voucher.category,
                units=voucher.inventory_units,
            )
            debit_id = stable_hash(
                {
                    "domain": voucher.domain,
                    "site_id": self.site_id,
                    "nullifier": voucher.nullifier,
                    "fallback_window": fallback_window,
                    "link_tag_hash": tag_hash,
                },
                domain="aidmesh/fallback-debit-id/v1",
            )
            intent_persisted = False
            try:
                # The debit commits before signing and before physical release.
                debit = self.risk_ledger.debit(
                    site_id=self.site_id,
                    debit_id=debit_id,
                    nullifier=voucher.nullifier,
                    amount=voucher.value,
                    expected_state=expected_risk_state,
                )
                evidence = FallbackEvidence.from_debit(
                    debit=debit,
                    fallback_window=fallback_window,
                    link_tag=link_tag,
                )
                authorization_hash = stable_hash(
                    {
                        "release_intent_id": release_intent_id,
                        "receipt_skeleton_hash": receipt_skeleton_hash,
                        "evidence": evidence,
                    },
                    domain="aidmesh/fallback-authorization/v1",
                )
                release_record = self._authorize_release(
                    voucher=voucher,
                    path=RedemptionPath.FALLBACK,
                    authorization_hash=authorization_hash,
                    release_intent_id=release_intent_id,
                    receipt_skeleton_hash=receipt_skeleton_hash,
                    reservation_id=reservation_id,
                )
                self.release_store.persist_intent(release_record.slot_key)
                intent_persisted = True
                self.inventory.commit(reservation_id)
                receipt = Receipt(
                    voucher=voucher,
                    site_id=self.site_id,
                    path=RedemptionPath.FALLBACK,
                    evidence=evidence,
                    released_at=tick,
                    release_intent_id=release_intent_id,
                    receipt_skeleton_hash=receipt_skeleton_hash,
                ).signed(self._signer)
                self.release_store.close(release_record.slot_key, receipt.receipt_id)
            except Exception:
                # Rights are deliberately not rolled back after a committed debit:
                # wasting capacity is safer than minting fallback exposure.
                if intent_persisted:
                    current = self.release_store.read(release_record.slot_key)
                    if current is not None and current.status is ReleaseStatus.RELEASE_INTENT:
                        self.release_store.mark_ambiguous(release_record.slot_key)
                else:
                    self.inventory.cancel(reservation_id)
                raise
            self._fallback_tags.add(tag_key)
            self._redeemed[self._freshness_key(voucher)] = receipt
            return receipt

    @property
    def receipts(self) -> tuple[Receipt, ...]:
        with self._lock:
            return tuple(self._redeemed[key] for key in sorted(self._redeemed))
