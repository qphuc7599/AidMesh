"""Matched local-controller experiment for settlement traffic.

Every controller receives the same authenticated jobs, byte volume, copy budget,
arrival times, deadlines and service rate.  Controllers may inspect only fields
available when a packet reaches the local outbound queue.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class QueueJob:
    job_id: str
    created_s: float
    ready_s: float
    deadline_s: float
    wire_bytes: int
    packet_class: str

    def __post_init__(self) -> None:
        if self.packet_class not in {"receipt", "background"}:
            raise ValueError("unknown packet class")
        if self.wire_bytes <= 0 or self.created_s < 0 or self.ready_s < self.created_s:
            raise ValueError("invalid queue job")
        if self.deadline_s < self.created_s:
            raise ValueError("deadline precedes creation")


def schedule(jobs: list[QueueJob], *, policy: str, bandwidth_bps: int) -> list[dict]:
    if policy not in {"fifo", "edf", "receipt_priority"}:
        raise ValueError("unknown scheduling policy")
    if bandwidth_bps <= 0:
        raise ValueError("bandwidth must be positive")
    if policy == "fifo":

        def sort_key(job: QueueJob):
            return job.ready_s, job.job_id

    elif policy == "edf":

        def sort_key(job: QueueJob):
            return job.deadline_s, job.ready_s, job.job_id

    else:

        def sort_key(job: QueueJob):
            # Both class and deadline are authenticated local header fields.
            return (
                0 if job.packet_class == "receipt" else 1,
                job.deadline_s,
                job.ready_s,
                job.job_id,
            )

    future = sorted(jobs, key=lambda job: (job.ready_s, job.job_id))
    waiting: list[QueueJob] = []
    rows: list[dict] = []
    now = 0.0
    index = 0
    while index < len(future) or waiting:
        if not waiting and index < len(future):
            now = max(now, future[index].ready_s)
        while index < len(future) and future[index].ready_s <= now:
            waiting.append(future[index])
            index += 1
        job = min(waiting, key=sort_key)
        waiting.remove(job)
        started = now
        service_s = math.ceil(job.wire_bytes * 8 / bandwidth_bps * 1_000_000) / 1_000_000
        now = started + service_s
        row = asdict(job)
        row.update(
            policy=policy,
            start_s=started,
            finish_s=now,
            queue_delay_s=started - job.ready_s,
            end_to_end_delay_s=now - job.created_s,
            deadline_met=now <= job.deadline_s,
        )
        rows.append(row)
    return rows
