"""Publication figures for the protocol-faithful post-freeze upgrade evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def _tlc_counts(path: Path) -> tuple[int, int, int]:
    raw = path.read_bytes()
    text = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8")
    matches = re.findall(
        r"([\d,]+) states generated, ([\d,]+) distinct states found", text
    )
    depths = re.findall(r"depth of the complete state graph search is ([\d,]+)", text)
    if not matches or not depths:
        raise ValueError(f"TLC counts not found in {path}")
    generated, distinct = matches[-1]
    return int(generated.replace(",", "")), int(distinct.replace(",", "")), int(
        depths[-1].replace(",", "")
    )


def write_formal_refinement_figure(
    tlc_root: Path, refinement_summary: Path, output: Path
) -> Path:
    safe_cases = ("safe-quorum", "safe-rights", "safe-settlement")
    unsafe_cases = (
        "unsafe-quorum",
        "unsafe-rollback",
        "unsafe-conservation",
        "unsafe-settlement",
    )
    safe = {
        case: _tlc_counts(tlc_root / case / "tlc.log") for case in safe_cases
    }
    unsafe = {
        case: _tlc_counts(tlc_root / case / "tlc.log") for case in unsafe_cases
    }
    refinement = json.loads(refinement_summary.read_text(encoding="utf-8"))

    fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.3))
    labels = ["Quorum", "Rights", "Settlement"]
    distinct = [safe[case][1] for case in safe_cases]
    axes[0].bar(labels, distinct, color=("#4472c4", "#70ad47", "#ed7d31"))
    axes[0].set_yscale("log")
    axes[0].set_ylabel("Distinct states (log scale)")
    axes[0].set_title("Safe domains: no violation")
    axes[0].margins(y=0.22)
    for index, value in enumerate(distinct):
        axes[0].text(index, value * 1.15, f"{value:,}", ha="center", fontsize=8)

    negative_labels = ["Unsafe\nquorum", "Rollback", "Release\nwithout debit", "Double\ncredit"]
    depths = [unsafe[case][2] for case in unsafe_cases]
    axes[1].bar(negative_labels, depths, color="#c0504d")
    axes[1].set_ylabel("Counterexample depth")
    axes[1].set_title("Negative controls reproduced")
    axes[1].set_ylim(0, max(depths) * 1.22)
    for index, value in enumerate(depths):
        axes[1].text(index, value + 0.2, str(value), ha="center", fontsize=8)

    transition_labels = ["Escrow", "SQLite\nwitness", "Python +\nlocal EVM"]
    transitions = [
        refinement["escrow_risk_rights"]["transitions"],
        refinement["persistent_witness"]["transitions"],
        refinement["settlement"]["transitions"],
    ]
    axes[2].bar(transition_labels, transitions, color="#5b9bd5")
    axes[2].set_yscale("log")
    axes[2].set_ylabel("Refinement transitions (log scale)")
    axes[2].set_title("Oracle-to-code agreement")
    axes[2].margins(y=0.22)
    for index, value in enumerate(transitions):
        axes[2].text(index, value * 1.18, f"{value:,}", ha="center", fontsize=8)

    for axis in axes:
        axis.grid(axis="y", alpha=0.25)
        axis.tick_params(axis="x", labelsize=8)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)
    return output


def write_the_one_semantic_figure(
    coarse_csv: Path, fine_csv: Path, output: Path
) -> Path:
    frame = pd.concat([pd.read_csv(coarse_csv), pd.read_csv(fine_csv)], ignore_index=True)
    frame = frame[frame["buffer_bytes"] == frame["buffer_bytes"].max()].copy()
    frame = frame.sort_values("update_interval_seconds", ascending=False)
    labels = [f"{value:g}" for value in frame["update_interval_seconds"]]

    fig, axes = plt.subplots(1, 2, figsize=(10.3, 3.35))
    x = range(len(frame))
    axes[0].plot(x, frame["delivered"], marker="o", linewidth=1.8, label="The ONE")
    axes[0].axhline(50, color="#4472c4", linestyle="--", label="Slot-causal oracle")
    axes[0].axhline(51, color="#70ad47", linestyle=":", label="Intraslot-closure oracle")
    axes[0].set_xticks(list(x), labels)
    axes[0].set_xlabel("The ONE update interval (s; finer to the right)")
    axes[0].set_ylabel("Delivered messages / 85")
    axes[0].set_title("Scheduler convergence")
    axes[0].set_ylim(20, 55)
    axes[0].legend(frameon=False, fontsize=8, loc="lower right")
    axes[0].grid(alpha=0.25)

    bottoms = [0] * len(frame)
    stacks = (
        ("both_delivered", "Both deliver", "#70ad47"),
        ("aidmesh_only", "Slot-only", "#4472c4"),
        ("the_one_only", "The ONE-only", "#ed7d31"),
        ("neither", "Neither", "#bfbfbf"),
    )
    for field, label, color in stacks:
        values = frame[field].astype(int).tolist()
        axes[1].bar(labels, values, bottom=bottoms, label=label, color=color)
        bottoms = [left + right for left, right in zip(bottoms, values, strict=True)]
    axes[1].set_xlabel("The ONE update interval (s)")
    axes[1].set_ylabel("Message-level classification")
    axes[1].set_title("All 85 messages classified")
    axes[1].legend(frameon=False, fontsize=8, ncol=2, loc="upper center")
    axes[1].grid(axis="y", alpha=0.25)

    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)
    return output


def write_upgrade_figures(results: Path, output: Path) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    return {
        "formal_refinement": write_formal_refinement_figure(
            results / "formal" / "tlc-final",
            results / "formal" / "refinement" / "protocol_refinement_summary.json",
            output / "formal_refinement_upgrade.pdf",
        ),
        "the_one_semantics": write_the_one_semantic_figure(
            results / "the_one_message_level" / "the_one_discrepancy_sweep.csv",
            results / "the_one_fine_scheduler" / "the_one_discrepancy_sweep.csv",
            output / "the_one_semantic_resolution.pdf",
        ),
    }
