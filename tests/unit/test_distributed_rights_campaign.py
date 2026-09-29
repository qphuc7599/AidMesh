from __future__ import annotations

from aidmesh.experiments.distributed_rights_campaign import (
    THRESHOLD,
    TransferProposal,
    _accept,
    _certificate,
    _export,
    _init_site,
    _owned_tokens,
)


def test_separate_site_databases_transfer_one_unique_right(tmp_path) -> None:
    source = tmp_path / "site-a.sqlite"
    destination = tmp_path / "site-b.sqlite"
    _init_site(source, "site-a", ("right-1",))
    _init_site(destination, "site-b")
    proposal = TransferProposal(
        domain="campaign|epoch|cell",
        token_id="right-1",
        source="site-a",
        destination="site-b",
        transfer_id="transfer-1",
        parent_certificate="GENESIS",
    )
    assert _export(source, proposal) == "exported"
    certificate, pids = _certificate(proposal, tmp_path / "witnesses")
    assert len(certificate["shares"]) >= THRESHOLD
    assert len(set(pids)) == 4
    assert _accept(destination, certificate) == "accepted"
    assert _accept(destination, certificate) == "idempotent"
    assert _owned_tokens(source) == set()
    assert _owned_tokens(destination) == {"right-1"}

