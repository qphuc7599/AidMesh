from aidmesh.experiments.matched_receipt_queue import QueueJob, schedule


def test_receipt_priority_reorders_only_waiting_jobs_without_changing_work() -> None:
    jobs = [
        QueueJob("occupy", 0, 0, 100, 10, "background"),
        QueueJob("background", 0, 0, 100, 10, "background"),
        QueueJob("receipt", 0, 0, 20, 10, "receipt"),
    ]
    fifo = schedule(jobs, policy="fifo", bandwidth_bps=8)
    priority = schedule(jobs, policy="receipt_priority", bandwidth_bps=8)
    assert {row["job_id"] for row in fifo} == {row["job_id"] for row in priority}
    assert sum(row["wire_bytes"] for row in fifo) == sum(row["wire_bytes"] for row in priority)
    fifo_receipt = next(row for row in fifo if row["job_id"] == "receipt")
    priority_receipt = next(row for row in priority if row["job_id"] == "receipt")
    assert priority_receipt["finish_s"] < fifo_receipt["finish_s"]
