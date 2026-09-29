"""Counter-based deterministic random streams with stable stream splitting."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import MutableSequence, Sequence
from dataclasses import dataclass
from typing import TypeVar

from .canonical import canonical_bytes

T = TypeVar("T")


@dataclass(frozen=True)
class StreamSnapshot:
    name: str
    counter: int


class DeterministicStream:
    """A reproducible SHA-256 counter stream.

    It is a simulation RNG, not a cryptographic key generator.  The algorithm
    is local and explicit so replay does not depend on Python's ``random``
    implementation or hash randomization.
    """

    def __init__(self, key: bytes, name: str) -> None:
        if not name:
            raise ValueError("stream name must be non-empty")
        self._key = key
        self.name = name
        self._counter = 0
        self._lock = threading.Lock()

    @property
    def counter(self) -> int:
        with self._lock:
            return self._counter

    def _block(self) -> bytes:
        with self._lock:
            counter = self._counter
            self._counter += 1
        hasher = hashlib.sha256()
        hasher.update(b"aidmesh/simulation-rng/v1\x00")
        hasher.update(len(self._key).to_bytes(2, "big"))
        hasher.update(self._key)
        hasher.update(counter.to_bytes(16, "big"))
        return hasher.digest()

    def bits(self, width: int) -> int:
        if width < 0:
            raise ValueError("width cannot be negative")
        if width == 0:
            return 0
        needed = (width + 7) // 8
        data = bytearray()
        while len(data) < needed:
            data.extend(self._block())
        value = int.from_bytes(data[:needed], "big")
        return value >> (needed * 8 - width)

    def random(self) -> float:
        """Return an exactly specified 53-bit fraction in ``[0, 1)``."""

        return self.bits(53) / (1 << 53)

    def randbelow(self, stop: int) -> int:
        if stop <= 0:
            raise ValueError("stop must be positive")
        width = stop.bit_length()
        while True:
            candidate = self.bits(width)
            if candidate < stop:
                return candidate

    def randint(self, start: int, stop: int) -> int:
        if stop < start:
            raise ValueError("empty integer range")
        return start + self.randbelow(stop - start + 1)

    def uniform(self, start: float, stop: float) -> float:
        return start + (stop - start) * self.random()

    def choice(self, values: Sequence[T]) -> T:
        if not values:
            raise IndexError("cannot choose from an empty sequence")
        return values[self.randbelow(len(values))]

    def shuffle(self, values: MutableSequence[T]) -> None:
        for index in range(len(values) - 1, 0, -1):
            other = self.randbelow(index + 1)
            values[index], values[other] = values[other], values[index]

    def snapshot(self) -> StreamSnapshot:
        return StreamSnapshot(self.name, self.counter)


class SplitRNG:
    """Derive independent named streams from one scenario seed."""

    def __init__(self, master_seed: int | str | bytes) -> None:
        seed_bytes = canonical_bytes(
            master_seed if not isinstance(master_seed, bytes) else {"seed": master_seed}
        )
        self._root = hashlib.sha256(b"aidmesh/simulation-master/v1\x00" + seed_bytes).digest()
        self._streams: dict[str, DeterministicStream] = {}
        self._lock = threading.Lock()

    def stream(self, name: str) -> DeterministicStream:
        if not name or name.strip() != name:
            raise ValueError("stream name must be non-empty and normalized")
        with self._lock:
            stream = self._streams.get(name)
            if stream is None:
                key = hashlib.sha256(
                    b"aidmesh/simulation-split/v1\x00" + self._root + name.encode("utf-8")
                ).digest()
                stream = DeterministicStream(key, name)
                self._streams[name] = stream
            return stream

    def manifest(self) -> tuple[StreamSnapshot, ...]:
        with self._lock:
            streams = tuple(self._streams[name] for name in sorted(self._streams))
        return tuple(stream.snapshot() for stream in streams)
