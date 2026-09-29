from __future__ import annotations

import pytest

from aidmesh.analysis.privacy import privacy_report
from aidmesh.experiments.privacy_batch import _gateway_observations, _ledger_observations
from aidmesh.privacy.batching import (
    BatchPolicy,
    ReceiptIntent,
    batch_receipts,
    decrypt_audit_record,
)


def _intent(receipt_id: str, released_us: int, *, payload_bytes: int = 512) -> ReceiptIntent:
    return ReceiptIntent(
        receipt_id=receipt_id,
        released_us=released_us,
        deadline_us=40_000_000,
        payload_bytes=payload_bytes,
        copies=1,
        epoch=1,
        risk_cell="cell-a",
        category="food",
        path="fallback",
        fallback_tag=f"tag-{receipt_id}",
    )


def test_batching_pads_hides_and_encrypts_private_audit_detail() -> None:
    policy = BatchPolicy(
        slot_us=10_000_000,
        pad_bytes=2048,
        dummy_min_batch=4,
        hide_fields=frozenset({"risk_cell", "category", "path", "fallback_tag"}),
        shuffle_seed=7,
    )
    key = b"a" * 32
    result = batch_receipts([_intent("r-1", 1), _intent("r-2", 2)], policy=policy, audit_key=key)

    assert len(result.manifests) == 1
    assert result.manifests[0].actual_receipts == 2
    assert result.manifests[0].dummy_receipts == 2
    assert len(result.public_views) == 4
    assert {view.padded_size for view in result.public_views} == {2048}
    assert {view.risk_cell for view in result.public_views} == {"hidden"}
    assert len(result.audit_records) == 2
    recovered = decrypt_audit_record(result.audit_records[0], key)
    assert recovered["receipt"]["receipt_id"] in {"r-1", "r-2"}


def test_batching_reduces_ledger_observability_but_not_gateway_claim() -> None:
    policy = BatchPolicy(
        slot_us=10_000_000,
        pad_bytes=2048,
        dummy_min_batch=4,
        hide_fields=frozenset({"risk_cell", "category", "path", "fallback_tag"}),
        shuffle_seed=7,
    )
    intents = [_intent("r-1", 1), _intent("r-2", 2)]
    result = batch_receipts(intents, policy=policy, audit_key=b"a" * 32)

    ledger = privacy_report(_ledger_observations(result))
    gateway = privacy_report(_gateway_observations(intents, policy))

    assert ledger.min_anonymity_set == 4
    assert ledger.attacker_top1_success == pytest.approx(0.25)
    assert gateway.min_anonymity_set < ledger.min_anonymity_set


def test_batching_rejects_payload_larger_than_fixed_public_shape() -> None:
    policy = BatchPolicy(
        slot_us=10_000_000,
        pad_bytes=1024,
        dummy_min_batch=0,
        hide_fields=frozenset(),
        shuffle_seed=7,
    )

    with pytest.raises(ValueError, match="payload exceeds"):
        batch_receipts([_intent("r-1", 1, payload_bytes=1025)], policy=policy, audit_key=b"a" * 32)


def test_sparse_cover_emits_dummy_only_empty_slots() -> None:
    policy = BatchPolicy(
        slot_us=100,
        pad_bytes=2048,
        dummy_min_batch=4,
        hide_fields=frozenset({"risk_cell", "category", "path", "fallback_tag"}),
        shuffle_seed=7,
        gateway_bucket_us=100,
        cover_empty_slots=True,
        cover_boundary_slots=1,
    )
    result = batch_receipts(
        [_intent("r-1", 10), _intent("r-2", 310)],
        policy=policy,
        audit_key=b"a" * 32,
    )
    assert len(result.manifests) == 6  # boundary, observed/empty slots, boundary
    assert sum(manifest.cover_only for manifest in result.manifests) == 4
    assert all(manifest.public_receipts >= 4 for manifest in result.manifests)
    assert sum(manifest.dummy_receipts for manifest in result.manifests) > 0
