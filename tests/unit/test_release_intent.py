from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aidmesh.common.canonical import stable_hash
from aidmesh.common.errors import DuplicateError, ValidationError
from aidmesh.protocol.campaign import CampaignHeader, CommitteeConfig, SafetyDomain
from aidmesh.protocol.inventory import SiteInventory
from aidmesh.protocol.model import Voucher, witness_request_for
from aidmesh.protocol.quorum import assemble_certificate
from aidmesh.protocol.release import (
    InMemoryReleaseStore,
    ReleaseRecord,
    ReleaseSlotKey,
    ReleaseStatus,
    SQLiteReleaseStore,
)
from aidmesh.protocol.risk import RiskRightsLedger
from aidmesh.protocol.site import Site
from aidmesh.protocol.witness import InMemoryWitnessStore, Witness


def _signature(signer_id: str, digest: str) -> str:
    return stable_hash({"signer": signer_id, "digest": digest}, domain="test/signature")


def _verify(signer_id: str, digest: str, signature: str) -> bool:
    return signature == _signature(signer_id, digest)


def _header() -> CampaignHeader:
    committee = CommitteeConfig("committee", ("w0", "w1", "w2", "w3"), 3, 1)
    return CampaignHeader(
        domain=SafetyDomain("campaign", 1, 1, committee.committee_id),
        risk_cell="z1",
        committee=committee,
        authorized_sites=("s1",),
        fallback_categories=("food",),
        initial_risk_rights=(("s1", 4),),
        start_tick=0,
        end_tick=100,
        verifier_id="reference",
        policy_hash="policy",
    )


def _voucher(header: CampaignHeader) -> Voucher:
    return Voucher(header.domain, "z1", "food", 1, 1, "n1", "c1", b"proof")


def _slot_key(header: CampaignHeader, voucher: Voucher) -> ReleaseSlotKey:
    return ReleaseSlotKey(header.domain.digest, header.domain.epoch, voucher.nullifier, "s1")


def _certificate(header: CampaignHeader, voucher: Voucher, signer_ids: tuple[str, ...]):
    request = witness_request_for(voucher, "s1")
    witnesses = {
        witness_id: Witness(
            witness_id,
            header,
            InMemoryWitnessStore(),
            _signature,
        )
        for witness_id in header.committee.members
    }
    shares = [witnesses[witness_id].sign(request) for witness_id in signer_ids]
    return request, assemble_certificate(request, shares, header.committee, _verify)


def test_same_digest_certificate_variant_cannot_repeat_handoff() -> None:
    header = _header()
    voucher = _voucher(header)
    _, first_certificate = _certificate(header, voucher, ("w0", "w1", "w2"))
    _, second_certificate = _certificate(header, voucher, ("w0", "w1", "w3"))
    release_store = InMemoryReleaseStore()
    site = Site(
        "s1",
        header,
        SiteInventory({"food": 2}),
        lambda _voucher, _header: True,
        _signature,
        release_store=release_store,
    )

    site.redeem_quorum(
        voucher,
        first_certificate,
        tick=1,
        verify_witness_signature=_verify,
    )
    assert release_store.read(_slot_key(header, voucher)).status is ReleaseStatus.CLOSED
    with pytest.raises(DuplicateError):
        site.redeem_quorum(
            voucher,
            second_certificate,
            tick=2,
            verify_witness_signature=_verify,
        )
    assert site.inventory.snapshot().on_hand == (("food", 1),)


def test_post_intent_failure_becomes_ambiguous_and_never_retries() -> None:
    header = _header()
    voucher = _voucher(header)
    request, certificate = _certificate(header, voucher, ("w0", "w1", "w2"))
    release_store = InMemoryReleaseStore()

    def failing_signer(_signer: str, _digest: str) -> str:
        raise RuntimeError("crash after physical handoff")

    site = Site(
        "s1",
        header,
        SiteInventory({"food": 2}),
        lambda _voucher, _header: True,
        failing_signer,
        release_store=release_store,
    )
    with pytest.raises(RuntimeError):
        site.redeem_quorum(
            voucher,
            certificate,
            tick=1,
            verify_witness_signature=_verify,
        )
    assert release_store.read(_slot_key(header, voucher)).status is ReleaseStatus.AMBIGUOUS
    assert site.inventory.snapshot().on_hand == (("food", 1),)
    with pytest.raises(DuplicateError):
        site.redeem_quorum(
            voucher,
            certificate,
            tick=2,
            verify_witness_signature=_verify,
        )


def test_failure_before_intent_commit_is_safe_to_retry() -> None:
    header = _header()
    voucher = _voucher(header)
    request, certificate = _certificate(header, voucher, ("w0", "w1", "w2"))
    attempts = 0

    def fail_once(_record: ReleaseRecord) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("crash before durable intent")

    release_store = InMemoryReleaseStore(before_intent_commit=fail_once)
    site = Site(
        "s1",
        header,
        SiteInventory({"food": 2}),
        lambda _voucher, _header: True,
        _signature,
        release_store=release_store,
    )
    with pytest.raises(RuntimeError):
        site.redeem_quorum(
            voucher,
            certificate,
            tick=1,
            verify_witness_signature=_verify,
        )
    assert release_store.read(_slot_key(header, voucher)).status is ReleaseStatus.AUTHORIZED
    assert site.inventory.snapshot().on_hand == (("food", 2),)

    site.redeem_quorum(
        voucher,
        certificate,
        tick=2,
        verify_witness_signature=_verify,
    )
    assert release_store.read(_slot_key(header, voucher)).status is ReleaseStatus.CLOSED


def test_sqlite_release_store_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "release.sqlite3"
    record = ReleaseRecord(
        release_intent_id="intent",
        domain_id="domain",
        epoch=1,
        nullifier="nullifier",
        site_id="site",
        authorization_hash="authorization",
        receipt_skeleton_hash="skeleton",
        reservation_id="reservation",
        status=ReleaseStatus.AUTHORIZED,
    )
    first = SQLiteReleaseStore(path)
    first.authorize(record)
    first.persist_intent(record.slot_key)
    first.close_store()

    second = SQLiteReleaseStore(path)
    assert second.read(record.slot_key).status is ReleaseStatus.RELEASE_INTENT
    second.mark_ambiguous(record.slot_key)
    second.close_store()

    third = SQLiteReleaseStore(path)
    assert third.read(record.slot_key).status is ReleaseStatus.AMBIGUOUS
    with pytest.raises(ValidationError):
        third.close(record.slot_key, "receipt")
    third.close_store()


@pytest.mark.parametrize(
    ("first_path", "second_path"),
    (("quorum", "fallback"), ("fallback", "quorum")),
)
def test_path_switch_after_restart_hits_the_same_release_slot(
    first_path: str,
    second_path: str,
) -> None:
    header = _header()
    voucher = _voucher(header)
    _, certificate = _certificate(header, voucher, ("w0", "w1", "w2"))
    release_store = InMemoryReleaseStore()
    ledger = RiskRightsLedger(header.domain, header.risk_rights)

    first_site = Site(
        "s1",
        header,
        SiteInventory({"food": 2}),
        lambda _voucher, _header: True,
        _signature,
        risk_ledger=ledger,
        release_store=release_store,
    )
    if first_path == "quorum":
        first_site.redeem_quorum(
            voucher,
            certificate,
            tick=1,
            verify_witness_signature=_verify,
        )
    else:
        first_site.redeem_fallback(
            voucher,
            fallback_window=1,
            link_tag="tag-first",
            fallback_proof=b"proof",
            tick=1,
            verify_fallback_proof=lambda *_args: True,
        )

    restarted_site = Site(
        "s1",
        header,
        SiteInventory({"food": 2}),
        lambda _voucher, _header: True,
        _signature,
        risk_ledger=ledger,
        release_store=release_store,
    )
    with pytest.raises(DuplicateError, match="release slot"):
        if second_path == "quorum":
            restarted_site.redeem_quorum(
                voucher,
                certificate,
                tick=2,
                verify_witness_signature=_verify,
            )
        else:
            restarted_site.redeem_fallback(
                voucher,
                fallback_window=2,
                link_tag="tag-second",
                fallback_proof=b"proof",
                tick=2,
                verify_fallback_proof=lambda *_args: True,
            )

    assert release_store.read(_slot_key(header, voucher)).status is ReleaseStatus.CLOSED
    assert restarted_site.inventory.snapshot().on_hand == (("food", 2),)


def test_sqlite_composite_key_rejects_cross_path_operation(tmp_path: Path) -> None:
    store = SQLiteReleaseStore(tmp_path / "release-slot.sqlite3")
    first = ReleaseRecord(
        release_intent_id="quorum-intent",
        domain_id="domain",
        epoch=3,
        nullifier="nullifier",
        site_id="site",
        authorization_hash="quorum-authorization",
        receipt_skeleton_hash="quorum-skeleton",
        reservation_id="quorum-reservation",
        status=ReleaseStatus.AUTHORIZED,
    )
    fallback = ReleaseRecord(
        release_intent_id="fallback-intent",
        domain_id="domain",
        epoch=3,
        nullifier="nullifier",
        site_id="site",
        authorization_hash="fallback-authorization",
        receipt_skeleton_hash="fallback-skeleton",
        reservation_id="fallback-reservation",
        status=ReleaseStatus.AUTHORIZED,
    )
    store.authorize(first)
    with pytest.raises(DuplicateError, match="release slot"):
        store.authorize(fallback)
    store.close_store()


def test_nonempty_legacy_release_table_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "legacy-release.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE release_state (release_intent_id TEXT PRIMARY KEY)"
    )
    connection.execute("INSERT INTO release_state VALUES ('legacy-intent')")
    connection.commit()
    connection.close()

    with pytest.raises(ValidationError, match="explicit epoch-aware migration"):
        SQLiteReleaseStore(path)
