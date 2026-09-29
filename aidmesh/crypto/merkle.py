"""Canonical indexed-entitlement Merkle commitments and membership proofs."""

from __future__ import annotations

from dataclasses import dataclass

from .encoding import domain_hash, fixed_bytes, text, u32, u64, vector


@dataclass(frozen=True, order=True)
class EntitlementLeaf:
    """The unique value authorized for one campaign/cell/group/category/index."""

    epoch: int
    cell: str
    group: str
    category: str
    index: int
    value: int

    def __post_init__(self) -> None:
        if self.epoch <= 0 or self.index <= 0 or self.value <= 0:
            raise ValueError("epoch, index and value must be positive")
        text(self.cell, "cell")
        text(self.group, "group")
        text(self.category, "category")
        u64(self.epoch)
        u64(self.index)
        u64(self.value)

    @property
    def key(self) -> tuple[int, str, str, str, int]:
        return (self.epoch, self.cell, self.group, self.category, self.index)

    def encode(self) -> bytes:
        return (
            b"AIDMESH/ENTITLEMENT-LEAF/V1\x00"
            + u64(self.epoch)
            + text(self.cell, "cell")
            + text(self.group, "group")
            + text(self.category, "category")
            + u64(self.index)
            + u64(self.value)
        )

    @property
    def digest(self) -> bytes:
        return domain_hash("entitlement-merkle-leaf-v1", self.encode())


@dataclass(frozen=True)
class MerkleProof:
    """A membership path bound to both leaf position and exact leaf count."""

    leaf_index: int
    leaf_count: int
    siblings: tuple[bytes, ...]

    def __post_init__(self) -> None:
        if self.leaf_count <= 0:
            raise ValueError("leaf_count must be positive")
        if self.leaf_index < 0 or self.leaf_index >= self.leaf_count:
            raise ValueError("leaf_index outside committed tree")
        for sibling in self.siblings:
            fixed_bytes(sibling, 32, "Merkle sibling")
        expected = (self.leaf_count - 1).bit_length()
        if len(self.siblings) != expected:
            raise ValueError(f"proof requires exactly {expected} siblings")

    def encode(self) -> bytes:
        return (
            b"AIDMESH/MERKLE-PROOF/V1\x00"
            + u32(self.leaf_index)
            + u32(self.leaf_count)
            + vector(self.siblings, "siblings")
        )


def _node(left: bytes, right: bytes) -> bytes:
    return domain_hash(
        "entitlement-merkle-node-v1",
        fixed_bytes(left, 32, "left"),
        fixed_bytes(right, 32, "right"),
    )


_PAD = domain_hash("entitlement-merkle-padding-leaf-v1")


def _root(top: bytes, leaf_count: int) -> bytes:
    return domain_hash("entitlement-merkle-root-v1", u32(leaf_count), top)


class EntitlementTree:
    """Deterministic sorted Merkle tree with explicit uniqueness checks.

    Leaves are sorted by their entitlement key.  Padding to a power of two uses
    a separately domain-separated constant and the published root binds the
    number of real leaves, preventing alternate-tree-shape interpretations.
    """

    def __init__(self, leaves: list[EntitlementLeaf] | tuple[EntitlementLeaf, ...]):
        if not leaves:
            raise ValueError("an entitlement tree cannot be empty")
        ordered = tuple(sorted(leaves, key=lambda leaf: leaf.key))
        keys = [leaf.key for leaf in ordered]
        if len(keys) != len(set(keys)):
            raise ValueError("entitlement keys must be unique; one index cannot have two values")
        if len(ordered) >= 1 << 32:
            raise ValueError("too many entitlement leaves")

        self._leaves = ordered
        width = 1 << (len(ordered) - 1).bit_length()
        layer = [leaf.digest for leaf in ordered] + [_PAD] * (width - len(ordered))
        self._layers: list[tuple[bytes, ...]] = [tuple(layer)]
        while len(layer) > 1:
            layer = [_node(layer[i], layer[i + 1]) for i in range(0, len(layer), 2)]
            self._layers.append(tuple(layer))
        self._root = _root(layer[0], len(ordered))
        self._positions = {leaf.key: i for i, leaf in enumerate(ordered)}

    @property
    def leaves(self) -> tuple[EntitlementLeaf, ...]:
        return self._leaves

    @property
    def root(self) -> bytes:
        return self._root

    def proof_for(self, leaf: EntitlementLeaf) -> MerkleProof:
        try:
            position = self._positions[leaf.key]
        except KeyError as exc:
            raise KeyError("entitlement key is not in this tree") from exc
        if self._leaves[position] != leaf:
            raise KeyError("entitlement key exists with a different committed value")

        siblings: list[bytes] = []
        cursor = position
        for layer in self._layers[:-1]:
            siblings.append(layer[cursor ^ 1])
            cursor //= 2
        return MerkleProof(position, len(self._leaves), tuple(siblings))


def verify_entitlement_proof(leaf: EntitlementLeaf, proof: MerkleProof, root: bytes) -> bool:
    try:
        fixed_bytes(root, 32, "root")
        current = leaf.digest
        cursor = proof.leaf_index
        for sibling in proof.siblings:
            current = _node(current, sibling) if cursor % 2 == 0 else _node(sibling, current)
            cursor //= 2
        return _root(current, proof.leaf_count) == root
    except (TypeError, ValueError):
        return False
