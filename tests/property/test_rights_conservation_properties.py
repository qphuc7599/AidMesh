from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from aidmesh.common.errors import DuplicateError, ValidationError
from aidmesh.protocol.risk_escrow import EscrowRiskLedger

Operation = tuple[str, int, int, int, int]


@settings(max_examples=300, deadline=None)
@given(
    initial=st.lists(st.integers(min_value=1, max_value=8), min_size=3, max_size=3),
    operations=st.lists(
        st.tuples(
            st.sampled_from(
                [
                    "begin",
                    "accept",
                    "reclaim",
                    "expire",
                    "revoke",
                    "spend",
                    "replay_spend",
                ]
            ),
            st.integers(min_value=0, max_value=2),
            st.integers(min_value=0, max_value=2),
            st.integers(min_value=1, max_value=4),
            st.integers(min_value=0, max_value=20),
        ),
        min_size=1,
        max_size=80,
    ),
)
def test_escrow_rights_are_conserved_for_random_transition_sequences(
    initial: list[int],
    operations: list[Operation],
) -> None:
    sites = [f"s{index}" for index in range(3)]
    ledger = EscrowRiskLedger(dict(zip(sites, initial, strict=True)))
    transfer_ids: list[str] = []
    spend_ids: list[str] = []
    for op, left, right, amount, nonce in operations:
        try:
            if op == "begin":
                source = sites[left % len(sites)]
                destination = sites[right % len(sites)]
                if source == destination:
                    destination = sites[(right + 1) % len(sites)]
                transfer_id = f"t-{nonce}"
                ledger.begin_transfer(transfer_id, source, destination, amount)
                transfer_ids.append(transfer_id)
            elif op == "accept":
                transfer_id = transfer_ids[nonce % len(transfer_ids)] if transfer_ids else "x"
                ledger.accept_transfer(transfer_id)
            elif op == "reclaim":
                transfer_id = transfer_ids[nonce % len(transfer_ids)] if transfer_ids else "x"
                ledger.reclaim_transfer(transfer_id)
            elif op == "expire":
                transfer_id = transfer_ids[nonce % len(transfer_ids)] if transfer_ids else "x"
                ledger.terminate_transfer(transfer_id)
            elif op == "revoke":
                ledger.terminate_transfer(
                    transfer_ids[nonce % len(transfer_ids)] if transfer_ids else "x",
                    revoked=True,
                )
            elif op == "spend":
                spend_id = f"s-{nonce}"
                ledger.spend(spend_id, sites[left % len(sites)], amount)
                spend_ids.append(spend_id)
            else:
                spend_id = spend_ids[nonce % len(spend_ids)] if spend_ids else "x"
                ledger.spend(spend_id, sites[left % len(sites)], amount)
        except (DuplicateError, ValidationError):
            pass
        assert ledger.snapshot().conserved
