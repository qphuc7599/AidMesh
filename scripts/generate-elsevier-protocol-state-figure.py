"""Generate the corrected release and certified-transfer state-machine figure."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

matplotlib.use("Agg")
matplotlib.rcParams.update({"pdf.fonttype": 42, "ps.fonttype": 42})
import matplotlib.pyplot as plt  # noqa: E402


def _node(ax, x: float, y: float, text: str, *, color: str, width: float = 1.75) -> None:
    box = FancyBboxPatch(
        (x - width / 2, y - 0.22),
        width,
        0.44,
        boxstyle="round,pad=0.03,rounding_size=0.06",
        linewidth=1.5,
        edgecolor=color,
        facecolor="white",
    )
    ax.add_patch(box)
    ax.text(x, y, text, ha="center", va="center", fontsize=9, color=color, weight="bold")


def _arrow(
    ax,
    start: tuple[float, float],
    end: tuple[float, float],
    label: str,
    *,
    color: str,
    curve: float = 0.0,
    label_offset: tuple[float, float] = (0.0, 0.18),
) -> None:
    arrow = FancyArrowPatch(
        start,
        end,
        arrowstyle="-|>",
        mutation_scale=12,
        linewidth=1.3,
        color=color,
        connectionstyle=f"arc3,rad={curve}",
    )
    ax.add_patch(arrow)
    midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    ax.text(
        midpoint[0] + label_offset[0],
        midpoint[1] + label_offset[1],
        label,
        ha="center",
        va="center",
        fontsize=7.3,
        color="#303030",
    )


def _release_panel(ax) -> None:
    blue = "#155A85"
    orange = "#B85C12"
    _node(ax, 0.9, 2.5, "INIT", color=blue, width=1.15)
    _node(ax, 2.7, 2.5, "AUTHORIZED", color=blue, width=1.55)
    _node(ax, 5.0, 2.5, "RELEASE_INTENT", color=orange, width=2.0)
    _node(ax, 7.6, 3.1, "CLOSED", color="#26734D", width=1.35)
    _node(ax, 7.6, 1.65, "AMBIGUOUS", color="#9A3E30", width=1.65)
    _arrow(ax, (1.5, 2.5), (1.9, 2.5), "authorize", color=blue)
    _arrow(ax, (3.5, 2.5), (3.98, 2.5), "durable commit", color=blue)
    _arrow(
        ax,
        (6.0, 2.65),
        (6.9, 3.0),
        "ack + final receipt",
        color="#26734D",
        label_offset=(0.0, 0.20),
    )
    _arrow(
        ax,
        (6.0, 2.3),
        (6.78, 1.78),
        "crash / uncertain handoff",
        color="#9A3E30",
        label_offset=(0.05, -0.16),
    )
    ax.text(
        5.0,
        3.48,
        "One durable slot $\\kappa_{rel}=(D,e,n,s)$; path-specific intent stored inside\n"
        "Persist before invoking the irreversible actuator",
        ha="center",
        fontsize=8.0,
        color=orange,
        weight="bold",
    )
    ax.text(
        5.0,
        0.62,
        "After RELEASE_INTENT: retries, late callbacks, and signer-subset variants stutter;\n"
        "CLOSED and AMBIGUOUS have no automatic path back to handoff.",
        ha="center",
        fontsize=8.2,
        color="#303030",
    )
    ax.set_title("(a) Honest-site crash-safe physical release", fontsize=11, weight="bold")
    ax.set_xlim(0, 8.7)
    ax.set_ylim(0.2, 3.8)
    ax.axis("off")


def _terminal_panel(ax) -> None:
    orange = "#B85C12"
    green = "#26734D"
    red = "#9A3E30"
    purple = "#6C3FA0"
    _node(ax, 0.9, 2.5, "OWNED(source)", color=orange, width=1.75)
    _node(ax, 3.0, 2.5, "IN_TRANSIT / OPEN", color=purple, width=2.1)
    _node(ax, 5.6, 3.15, "ACCEPT_PREPARED", color=green, width=2.05)
    _node(ax, 7.9, 3.15, "ACCEPT", color=green, width=1.35)
    _node(ax, 5.6, 1.75, "RECLAIM_PREPARED", color=orange, width=2.15)
    _node(ax, 7.9, 1.75, "RECLAIM", color=orange, width=1.45)
    _node(ax, 5.6, 0.42, "LOCKED", color=red, width=1.35)
    _arrow(ax, (1.78, 2.5), (1.95, 2.5), "debit first", color=orange)
    _arrow(ax, (4.05, 2.7), (4.58, 3.0), "destination evidence", color=green)
    _arrow(ax, (6.63, 3.15), (7.2, 3.15), "q-share certificate", color=green)
    _arrow(ax, (4.05, 2.3), (4.5, 1.9), "expiry + no accept", color=orange)
    _arrow(ax, (6.68, 1.75), (7.17, 1.75), "q-share certificate", color=orange)
    _arrow(
        ax,
        (3.65, 2.25),
        (5.05, 0.64),
        "terminal quorum unavailable",
        color=red,
        label_offset=(0.30, -0.02),
    )
    ax.text(
        6.75,
        3.72,
        "Witness durable CAS: one terminal digest per $\\kappa_{term}=(D,e,id)$",
        ha="center",
        fontsize=8.3,
        color=purple,
        weight="bold",
    )
    ax.text(
        6.75,
        -0.20,
        (
            "Safety and honest-only progress require $(K+f)/2<q\\leq K-f$, feasible "
            "exactly when $K>3f$, plus eventual delivery."
        ),
        ha="center",
        fontsize=8.0,
        color="#303030",
    )
    ax.set_title(
        "(b) Conserved right transfer with certified terminal decision",
        fontsize=11,
        weight="bold",
    )
    ax.set_xlim(0, 8.7)
    ax.set_ylim(-0.42, 4.05)
    ax.axis("off")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "source/paper_overleaf/AidMesh_Phuc_Elsevier/figures/"
            "protocol_state_machines_v3.pdf"
        ),
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 1, figsize=(10.6, 7.2))
    _release_panel(axes[0])
    _terminal_panel(axes[1])
    fig.tight_layout(h_pad=1.0)
    fig.savefig(args.output, bbox_inches="tight")
    fig.savefig(args.output.with_suffix(".svg"), bbox_inches="tight")
    plt.close(fig)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
