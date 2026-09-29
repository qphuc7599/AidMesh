"""Generate supporting 2026Q3 figures for the manuscript."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def _load_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def generate_supporting_figures(root: Path, output: Path) -> dict[str, Path]:
    output.mkdir(parents=True, exist_ok=True)
    assets: dict[str, Path] = {}

    temporal = _load_csv(root / "results/supporting_2026Q3/temporal_matched_core/temporal_matched_summary.csv")
    heldout = _load_csv(root / "results/supporting_2026Q3/temporal_matched_heldout/temporal_matched_summary.csv")
    baselines = _load_csv(root / "results/supporting_2026Q3/baselines/baseline_summary.csv")
    e2e_scale = _load_csv(root / "results/supporting_2026Q3/e2e_scale/analysis/e2e_paired_hierarchical_bootstrap.csv")
    e2e_diesel = _load_csv(
        root / "results/supporting_2026Q3/e2e_dieselnet_scale/analysis/e2e_paired_hierarchical_bootstrap.csv"
    )
    privacy = _load_csv(root / "results/supporting_2026Q3/privacy_pareto/privacy_pareto.csv")

    fig, axes = plt.subplots(2, 2, figsize=(10.5, 7.5))

    core = temporal[temporal["scenario_id"] == "CONF_CORE_W08_P24"]
    axes[0, 0].bar(
        core["method"],
        core["abstract_rmst_mean"],
        alpha=0.55,
        label="abstract RMST (h)",
    )
    axes[0, 0].bar(
        core["method"],
        core["temporal_rmst_mean"],
        alpha=0.85,
        label="temporal RMST (h)",
    )
    axes[0, 0].set_title("Temporal-matched core (synthetic contacts)")
    axes[0, 0].tick_params(axis="x", rotation=20)
    axes[0, 0].legend(fontsize=7)

    core_bl = baselines[baselines["scenario_id"] == "CONF_CORE_W08_P24"].sort_values("completion_mean")
    axes[0, 1].barh(core_bl["method"], core_bl["completion_mean"], color="steelblue")
    axes[0, 1].set_xlim(0.7, 1.0)
    axes[0, 1].set_title("Baseline pack: core completion (20 seeds)")
    axes[0, 1].set_xlabel("Completion rate")

    fb = e2e_scale[e2e_scale["witness_regime"] == "fallback"]
    metrics = fb[fb["metric"].isin(["completion_rate", "receipt_delivery_rate"])]
    x = range(len(metrics))
    axes[1, 0].bar(
        x,
        metrics["estimate"],
        yerr=[
            metrics["estimate"] - metrics["ci95_low"],
            metrics["ci95_high"] - metrics["estimate"],
        ],
        capsize=4,
        color=["#2a6f97", "#61a5c2"],
    )
    axes[1, 0].axhline(0.0, color="black", linewidth=0.8)
    axes[1, 0].set_xticks(list(x), [m.replace("_", "\n") for m in metrics["metric"]], fontsize=8)
    axes[1, 0].set_title("E2E scale (24 vouchers): transferable − static")
    axes[1, 0].set_ylabel("Paired effect")

    priv = (
        privacy[privacy["attacker"] == "baseline"]
        .groupby(["slot_width_h", "vantage"], as_index=False)["min_anonymity_set"]
        .median()
    )
    for vantage, group in priv.groupby("vantage"):
        axes[1, 1].plot(group["slot_width_h"], group["min_anonymity_set"], marker="o", label=vantage)
    axes[1, 1].set_xlabel("Slot width (h)")
    axes[1, 1].set_ylabel("Median min anonymity")
    axes[1, 1].set_title("Privacy Pareto (supporting)")
    axes[1, 1].legend(fontsize=7)

    fig.tight_layout()
    overview = output / "supporting_2026Q3_overview.pdf"
    fig.savefig(overview, bbox_inches="tight")
    plt.close(fig)
    assets["overview"] = overview

    # E2E scale vs frozen comparison bar
    fig2, ax2 = plt.subplots(figsize=(6.5, 4))
    rows = [
        ("Frozen E2E (12 vouchers)", 0.0283, 0.0, 0.0817),
        ("Scale E2E (24 vouchers)", 0.0202, 0.0, 0.0512),
        ("DieselNet scale (12 vouchers)", 0.0398, 0.0243, 0.0536),
    ]
    labels, est, lo, hi = zip(*rows, strict=True)
    err_low = [e - l for e, l in zip(est, lo, strict=True)]
    err_high = [h - e for e, h in zip(est, hi, strict=True)]
    ax2.bar(labels, est, yerr=[err_low, err_high], capsize=4, color=["#6c757d", "#198754", "#fd7e14"])
    ax2.axhline(0.0, color="black", linewidth=0.8)
    ax2.set_ylabel("Fallback completion: transferable − static")
    ax2.set_title("E2E fallback completion contrasts (supporting)")
    ax2.tick_params(axis="x", rotation=15)
    fig2.tight_layout()
    e2e_contrast = output / "e2e_scale_fallback_contrast.pdf"
    fig2.savefig(e2e_contrast, bbox_inches="tight")
    plt.close(fig2)
    assets["e2e_contrast"] = e2e_contrast

    held_core = heldout[heldout["scenario_id"] == "CONF_CORE_W08_P24"]
    fig3, ax3 = plt.subplots(figsize=(6, 4))
    ax3.bar(held_core["method"], held_core["temporal_rmst_mean"], color="#e76f51", alpha=0.85)
    ax3.set_title("Held-out InVS13 temporal RMST (core scenario)")
    ax3.set_ylabel("RMST (h)")
    ax3.tick_params(axis="x", rotation=20)
    fig3.tight_layout()
    heldout_fig = output / "temporal_matched_heldout_rmst.pdf"
    fig3.savefig(heldout_fig, bbox_inches="tight")
    plt.close(fig3)
    assets["heldout_rmst"] = heldout_fig

    return assets


def write_supporting_figures(root: Path | None = None, output: Path | None = None) -> dict[str, Path]:
    root = root or Path(__file__).resolve().parents[2]
    output = output or root / "results/supporting_2026Q3/paper_assets"
    return generate_supporting_figures(root, output)
