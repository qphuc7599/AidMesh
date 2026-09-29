"""Command-line entry point for validation, pilot simulation and analysis."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from aidmesh.analysis.pipeline import load_run_summaries, write_assets
from aidmesh.config import load_scenario
from aidmesh.experiments import (
    run_allocation_matrix,
    run_reference_crypto_benchmark,
    write_allocation_results,
    write_crypto_benchmark,
)
from aidmesh.manifest import host_report
from aidmesh.simulator import METHODS, run_experiment


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _simulate(args: argparse.Namespace) -> int:
    config = load_scenario(args.config)
    methods = METHODS if args.method == "all" else (args.method,)
    for method in methods:
        run_dir = args.output / config["scenario_id"] / method / f"seed-{config['seed']:04d}"
        result = run_experiment(
            config,
            method,
            raw_path=run_dir / "events.jsonl",
            software_commit=args.software_commit,
        )
        _write_json(run_dir / "summary.json", result.to_dict())
        print(
            f"{method}: completion={result.completion_rate:.3f} "
            f"exposure/cap={result.exposure_cap_ratio:.3f} "
            f"checksum={result.event_checksum[:12]}"
        )
    return 0


def _matrix(args: argparse.Namespace) -> int:
    template = load_scenario(args.config)
    methods = METHODS if args.method == "all" else (args.method,)
    for seed in range(args.seed_start, args.seed_start + args.seeds):
        config = dict(template)
        config["seed"] = seed
        for method in methods:
            run_dir = args.output / config["scenario_id"] / method / f"seed-{seed:04d}"
            summary_path = run_dir / "summary.json"
            if args.resume and summary_path.exists():
                continue
            result = run_experiment(
                config,
                method,
                raw_path=run_dir / "events.jsonl" if args.raw else None,
                software_commit=args.software_commit,
            )
            _write_json(summary_path, result.to_dict())
    print(f"completed {args.seeds * len(methods)} paired pilot runs")
    return 0


def _analyze(args: argparse.Namespace) -> int:
    frame = load_run_summaries(args.input)
    assets = write_assets(frame, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _allocation(args: argparse.Namespace) -> int:
    frame = run_allocation_matrix(
        range(args.seed_start, args.seed_start + args.seeds),
        groups=args.groups,
        categories=args.categories,
        demand_skew=args.demand_skew,
        stock_sufficiency=args.stock_sufficiency,
    )
    path = write_allocation_results(frame, args.output)
    print(path)
    return 0


def _crypto_benchmark(args: argparse.Namespace) -> int:
    frame = run_reference_crypto_benchmark(args.iterations, args.warmup)
    print(write_crypto_benchmark(frame, args.output))
    return 0


def _evm_benchmark(args: argparse.Namespace) -> int:
    from aidmesh.experiments.evm_benchmark import (
        run_local_evm_benchmark,
        write_evm_benchmark,
    )

    root = Path(__file__).resolve().parents[1]
    frame = run_local_evm_benchmark(root)
    print(write_evm_benchmark(frame, args.output))
    return 0


def _zk_benchmark(args: argparse.Namespace) -> int:
    from aidmesh.experiments.zk_benchmark import run_zk_benchmark, write_zk_benchmark

    root = Path(__file__).resolve().parents[1]
    frame = run_zk_benchmark(
        root,
        iterations=args.iterations,
        warmup=args.warmup,
        zkey=args.zkey,
        verification_key=args.verification_key,
        solidity_verifier=args.solidity_verifier,
    )
    print(write_zk_benchmark(frame, args.output))
    return 0


def _fault_campaign(args: argparse.Namespace) -> int:
    from aidmesh.experiments.fault_campaign import write_fault_campaign

    print(write_fault_campaign(args.output))
    return 0


def _dtn_benchmark(args: argparse.Namespace) -> int:
    from aidmesh.experiments.dtn_benchmark import run_dtn_matrix, write_dtn_results

    frame = run_dtn_matrix(
        seed_start=args.seed_start,
        seeds=args.seeds,
        bundle_count=args.bundles,
        sites=args.sites,
        relays=args.relays,
        horizon_hours=args.horizon_hours,
        partition_hours=args.partition_hours,
        ttl_hours=args.ttl_hours,
        buffer_bytes=args.buffer_bytes,
    )
    assets = write_dtn_results(frame, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _dtn_trace_benchmark(args: argparse.Namespace) -> int:
    from aidmesh.experiments.dtn_benchmark import run_dtn_trace_matrix, write_dtn_results

    frame = run_dtn_trace_matrix(
        contacts_path=args.contacts,
        bundles_path=args.bundles,
        buffer_bytes=args.buffer_bytes,
        copy_budget=args.copy_budget,
    )
    assets = write_dtn_results(frame, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _trace_import_sociopatterns(args: argparse.Namespace) -> int:
    from aidmesh.experiments.trace_preprocess import (
        TraceConversionConfig,
        write_sociopatterns_trace,
    )

    config = TraceConversionConfig(
        context=args.context,
        bytes_per_second=args.bytes_per_second,
        contact_step_seconds=args.contact_step_seconds,
        site_count=args.sites,
        relay_count=args.relays,
        bundles_per_client=args.bundles_per_client,
        ttl_hours=args.ttl_hours,
        bundle_size_bytes=args.bundle_size_bytes,
        collapse_contiguous=args.collapse_contiguous,
    )
    assets = write_sociopatterns_trace(args.input, config=config, output=args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _trace_import_dieselnet(args: argparse.Namespace) -> int:
    from aidmesh.experiments.trace_preprocess import (
        DieselNetConversionConfig,
        write_dieselnet_trace,
    )

    config = DieselNetConversionConfig(
        context=args.context,
        event_window_seconds=args.event_window_seconds,
        minimum_bytes_per_second=args.minimum_bytes_per_second,
        site_count=args.sites,
        relay_count=args.relays,
        bundles_per_client=args.bundles_per_client,
        ttl_hours=args.ttl_hours,
        bundle_size_bytes=args.bundle_size_bytes,
    )
    assets = write_dieselnet_trace(args.input, config=config, output=args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _independent_trace_campaign(args: argparse.Namespace) -> int:
    from aidmesh.experiments.independent_trace_campaign import (
        load_independent_trace_registry,
        run_independent_trace_campaign,
        write_independent_trace_campaign,
    )

    registry = load_independent_trace_registry(args.registry)
    frame, diagnostics, manifest = run_independent_trace_campaign(registry)
    assets = write_independent_trace_campaign(frame, diagnostics, manifest, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _dtn_oracle_check(args: argparse.Namespace) -> int:
    from aidmesh.experiments.dtn_crosscheck import (
        run_temporal_reachability_crosscheck,
        write_temporal_reachability_crosscheck,
    )

    frame, summary = run_temporal_reachability_crosscheck(args.contacts, args.bundles)
    assets = write_temporal_reachability_crosscheck(frame, summary, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    if not summary["all_delivery_agree"] or not summary["all_times_agree"]:
        raise AssertionError(f"temporal oracle disagreement: {summary}")
    return 0


def _supporting_temporal_matched(args: argparse.Namespace) -> int:
    from aidmesh.experiments.temporal_matched_core import (
        load_temporal_matched_registry,
        run_temporal_matched_core,
        write_temporal_matched_core,
    )

    registry = load_temporal_matched_registry(args.registry)
    frame, summary, manifest = run_temporal_matched_core(registry)
    assets = write_temporal_matched_core(frame, summary, manifest, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _supporting_baselines(args: argparse.Namespace) -> int:
    from aidmesh.experiments.supporting_baselines import (
        load_baseline_registry,
        run_baseline_pack,
        write_baseline_pack,
    )

    registry = load_baseline_registry(args.registry)
    frame, manifest = run_baseline_pack(registry)
    assets = write_baseline_pack(frame, manifest, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _supporting_privacy_pareto(args: argparse.Namespace) -> int:
    from aidmesh.experiments.privacy_pareto import (
        load_privacy_pareto_registry,
        run_privacy_pareto,
        write_privacy_pareto,
    )

    registry = load_privacy_pareto_registry(args.registry)
    frame, manifest = run_privacy_pareto(registry)
    assets = write_privacy_pareto(frame, manifest, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _supporting_device_benchmark(args: argparse.Namespace) -> int:
    from aidmesh.experiments.device_benchmark import (
        load_device_benchmark_registry,
        run_device_benchmark,
        write_device_benchmark,
    )

    registry = load_device_benchmark_registry(args.registry)
    frame, manifest = run_device_benchmark(registry)
    assets = write_device_benchmark(frame, manifest, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _multiprocess_fault_campaign(args: argparse.Namespace) -> int:
    from aidmesh.experiments.multiprocess_fault import write_multiprocess_fault_campaign

    path = write_multiprocess_fault_campaign(args.output)
    print(path)
    return 0


def _distributed_rights_campaign(args: argparse.Namespace) -> int:
    from aidmesh.experiments.distributed_rights_campaign import (
        run_distributed_rights_campaign,
    )

    result = run_distributed_rights_campaign(args.output, trials=args.trials)
    print(json.dumps(result, indent=2))
    return 0 if result["all_passed"] else 1


def _frontier_practicality(args: argparse.Namespace) -> int:
    from aidmesh.experiments.frontier_practicality import (
        evaluate_frontier_practicality,
    )

    result = evaluate_frontier_practicality(args.registry, args.output)
    print(json.dumps(result, indent=2))
    return 0


def _heldout_temporal_frontier(args: argparse.Namespace) -> int:
    from aidmesh.experiments.temporal_frontier import (
        run_heldout_temporal_frontier,
        write_heldout_temporal_frontier,
    )

    frame, manifest = run_heldout_temporal_frontier(
        args.trace_root,
        seeds=tuple(range(args.seed_start, args.seed_start + args.seeds)),
        buffer_bytes=args.buffer_bytes,
    )
    assets = write_heldout_temporal_frontier(frame, manifest, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _bow_temporal_campaign(args: argparse.Namespace) -> int:
    from aidmesh.experiments.bow_temporal import (
        ONLINE_POLICIES,
        write_bow_temporal_campaign,
    )

    policies = tuple(args.policy) if args.policy else ONLINE_POLICIES
    contexts = tuple(args.context) if args.context else ("invs13", "lh10", "sfhh")
    assets = write_bow_temporal_campaign(
        args.trace_root,
        args.output,
        contexts=contexts,
        seeds=tuple(range(args.seed_start, args.seed_start + args.seeds)),
        cap_fractions=tuple(args.cap_fraction or (0.10, 0.20, 0.40, 0.80)),
        quorum_availabilities=tuple(
            args.quorum_availability or (0.35, 0.65, 0.90)
        ),
        policies=policies,
        bootstrap_repetitions=args.bootstrap_repetitions,
    )
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _bow_factorial(args: argparse.Namespace) -> int:
    from aidmesh.experiments.bow_factorial import (
        FACTORIAL_POLICIES,
        all_factorial_cells,
        write_bow_factorial,
    )

    contexts = tuple(args.context) if args.context else ("invs13", "lh10", "sfhh")
    policies = tuple(args.policy) if args.policy else FACTORIAL_POLICIES
    cells = all_factorial_cells()
    if args.smoke_cells is not None:
        cells = cells[: args.smoke_cells]
    assets = write_bow_factorial(
        args.trace_root,
        args.output,
        contexts=contexts,
        seeds=tuple(range(args.seed_start, args.seed_start + args.seeds)),
        policies=policies,
        cells=cells,
    )
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _e2e_local_run(args: argparse.Namespace) -> int:
    from dataclasses import replace

    from aidmesh.experiments.e2e_campaign import (
        load_e2e_registry,
        run_local_e2e_campaign,
        write_e2e_checksums,
    )

    root = Path(__file__).resolve().parents[1]
    registry = load_e2e_registry(args.registry)
    if args.context:
        registry = replace(registry, contexts=tuple(args.context))
    if args.seed:
        registry = replace(registry, seeds=tuple(args.seed))
    manifest = run_local_e2e_campaign(
        root,
        registry=registry,
        trace_root=args.trace_root,
        output=args.output,
        smoke_vouchers=args.smoke_vouchers,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    print(write_e2e_checksums(args.output))
    return 0


def _e2e_local_analyze(args: argparse.Namespace) -> int:
    from aidmesh.experiments.e2e_campaign import (
        analyze_local_e2e_campaign,
        write_e2e_checksums,
    )

    assets = analyze_local_e2e_campaign(args.input, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    print(write_e2e_checksums(args.input))
    return 0


def _e2e_local_verify(args: argparse.Namespace) -> int:
    from aidmesh.experiments.e2e_campaign import verify_e2e_checksums

    result = verify_e2e_checksums(args.input)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "valid" else 1


def _the_one_discrepancy(args: argparse.Namespace) -> int:
    from aidmesh.experiments.the_one_crosscheck import run_the_one_discrepancy_sweep

    print(
        run_the_one_discrepancy_sweep(
            args.the_one_root,
            args.contacts,
            args.bundles,
            args.output,
            update_intervals=tuple(args.update_intervals),
            buffer_sizes=tuple(args.buffer_sizes),
        )
    )
    return 0


def _privacy_benchmark(args: argparse.Namespace) -> int:
    from aidmesh.experiments.privacy_benchmark import (
        run_privacy_analysis,
        write_privacy_results,
    )

    frame = run_privacy_analysis(args.input, bucket_us=args.bucket_us)
    assets = write_privacy_results(frame, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _privacy_sweep(args: argparse.Namespace) -> int:
    from aidmesh.experiments.privacy_benchmark import (
        run_privacy_sweep,
        write_privacy_sweep,
    )

    frame = run_privacy_sweep(args.input, bucket_hours=tuple(args.bucket_hours))
    assets = write_privacy_sweep(frame, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _privacy_defense(args: argparse.Namespace) -> int:
    from aidmesh.experiments.privacy_benchmark import (
        run_privacy_defense_sweep,
        write_privacy_defense_sweep,
    )

    frame = run_privacy_defense_sweep(args.input)
    assets = write_privacy_defense_sweep(frame, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _privacy_batch_run(args: argparse.Namespace) -> int:
    from aidmesh.experiments.privacy_batch import run_privacy_batch, write_privacy_batch_artifacts
    from aidmesh.privacy.batching import BatchPolicy

    audit_key = None
    if args.audit_key_env:
        key_hex = os.environ.get(args.audit_key_env)
        if key_hex is None:
            raise ValueError(f"missing audit-key environment variable: {args.audit_key_env}")
        try:
            audit_key = bytes.fromhex(key_hex)
        except ValueError as exc:
            raise ValueError("audit-key environment variable must be hexadecimal") from exc
    policy = BatchPolicy(
        slot_us=args.slot_hours * 3_600_000_000,
        pad_bytes=args.pad_bytes,
        dummy_min_batch=args.dummy_min_batch,
        hide_fields=frozenset(args.hide_fields),
        shuffle_seed=args.seed,
    )
    artifacts = run_privacy_batch(args.input, policy=policy, audit_key=audit_key)
    assets = write_privacy_batch_artifacts(artifacts, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    if audit_key is None:
        print(
            "warning: generated non-persisted audit key; output audit ciphertext is not recoverable"
        )
    return 0


def _testnet_deploy(args: argparse.Namespace) -> int:
    from aidmesh.experiments.testnet_deploy import run_testnet_deploy_from_env

    root = Path(__file__).resolve().parents[1]
    path = run_testnet_deploy_from_env(
        root,
        output=args.output,
        dry_run=args.dry_run,
        rpc_env=args.rpc_env,
        key_env=args.private_key_env,
        settlement_adapter=args.settlement_adapter,
    )
    print(path)
    return 0


def _sepolia_preflight(args: argparse.Namespace) -> int:
    from aidmesh.experiments.sepolia_campaign import preflight_sepolia

    rpc_url = os.environ.get(args.rpc_env)
    private_key = os.environ.get(args.private_key_env)
    if not rpc_url:
        raise ValueError(f"missing RPC environment variable: {args.rpc_env}")
    result = preflight_sepolia(rpc_url=rpc_url, private_key=private_key)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0


def _sepolia_parity(args: argparse.Namespace) -> int:
    from aidmesh.experiments.sepolia_campaign import run_sepolia_parity_from_env

    root = Path(__file__).resolve().parents[1]
    path = run_sepolia_parity_from_env(
        root,
        deployment_manifest=args.deployment,
        output=args.output,
        run_id=args.run_id,
        confirmations=args.confirmations,
        rpc_env=args.rpc_env,
        key_env=args.private_key_env,
    )
    print(path)
    return 0


def _sepolia_analyze(args: argparse.Namespace) -> int:
    from aidmesh.experiments.sepolia_campaign import analyze_sepolia_campaign

    print(
        analyze_sepolia_campaign(
            args.input,
            args.output,
            plan=args.plan,
            allow_partial=args.allow_partial,
        )
    )
    return 0


def _sepolia_plan_check(args: argparse.Namespace) -> int:
    from aidmesh.experiments.sepolia_campaign import check_sepolia_plan

    result = check_sepolia_plan(args.plan, args.checksum)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0


def _sepolia_plan_window(args: argparse.Namespace) -> int:
    from aidmesh.experiments.sepolia_campaign import (
        run_sepolia_plan_window_from_env,
    )

    root = Path(__file__).resolve().parents[1]
    reports = run_sepolia_plan_window_from_env(
        root,
        plan=args.plan,
        checksum=args.checksum,
        window=args.window,
        analysis_output=args.analysis_output,
        rpc_env=args.rpc_env,
        key_env=args.private_key_env,
    )
    for report in reports:
        print(report)
    return 0


def _confirmatory_freeze(args: argparse.Namespace) -> int:
    from aidmesh.experiments.confirmatory import load_confirmatory_registry, write_freeze_manifest

    root = Path(__file__).resolve().parents[1]
    registry = load_confirmatory_registry(args.registry, root=root)
    print(write_freeze_manifest(registry, args.output))
    return 0


def _confirmatory_run(args: argparse.Namespace) -> int:
    from aidmesh.experiments.confirmatory import (
        load_confirmatory_registry,
        run_confirmatory_registry,
    )

    root = Path(__file__).resolve().parents[1]
    registry = load_confirmatory_registry(args.registry, root=root)
    result = run_confirmatory_registry(
        registry,
        output=args.output,
        software_commit=args.software_commit,
        allow_draft=args.allow_draft,
        resume=args.resume,
        workers=args.workers,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


def _confirmatory_analyze(args: argparse.Namespace) -> int:
    from aidmesh.experiments.confirmatory import (
        analyze_confirmatory_registry,
        load_confirmatory_registry,
    )

    root = Path(__file__).resolve().parents[1]
    registry = load_confirmatory_registry(args.registry, root=root)
    assets = analyze_confirmatory_registry(
        registry,
        input_root=args.input,
        output=args.output,
        allow_draft=args.allow_draft,
    )
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _frontier_analyze(args: argparse.Namespace) -> int:
    from aidmesh.analysis.frontier import write_frontier_assets

    frame = load_run_summaries(args.input)
    assets = write_frontier_assets(frame, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _paper_assets(args: argparse.Namespace) -> int:
    from aidmesh.analysis.paper_assets import write_paper_assets

    assets = write_paper_assets(args.results, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _upgrade_paper_assets(args: argparse.Namespace) -> int:
    from aidmesh.analysis.upgrade_assets import write_upgrade_figures

    assets = write_upgrade_figures(args.results, args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _upgrade_evidence_manifest(args: argparse.Namespace) -> int:
    from aidmesh.experiments.upgrade_manifest import write_upgrade_evidence_manifest

    root = Path(__file__).resolve().parents[1]
    print(write_upgrade_evidence_manifest(root, args.output))
    return 0


def _fixback_evidence(args: argparse.Namespace) -> int:
    from aidmesh.experiments.fixback import write_fixback_evidence

    assets = write_fixback_evidence(
        args.output,
        calibration_seeds=args.calibration_seeds,
        bundles_per_seed=args.bundles_per_seed,
        witness_trials=args.witness_trials,
        rights_sequences=args.rights_sequences,
    )
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _formal_check(args: argparse.Namespace) -> int:
    from aidmesh.formal import write_formal_evidence

    assets = write_formal_evidence(args.output)
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _conformance_check(args: argparse.Namespace) -> int:
    from aidmesh.formal import write_risk_conformance

    print(write_risk_conformance(args.output))
    return 0


def _protocol_refinement(args: argparse.Namespace) -> int:
    from aidmesh.formal import write_protocol_refinement

    root = Path(__file__).resolve().parents[1]
    assets = write_protocol_refinement(
        args.output,
        root=root,
        escrow_traces=args.traces,
        escrow_steps=args.steps,
    )
    for name, path in assets.items():
        print(f"{name}: {path}")
    return 0


def _validate(args: argparse.Namespace) -> int:
    load_scenario(args.config)
    print("valid")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aidmesh")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate one scenario")
    validate.add_argument("config", type=Path)
    validate.set_defaults(func=_validate)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("config", type=Path)
    common.add_argument("--output", type=Path, default=Path("raw/pilot"))
    common.add_argument("--method", choices=("all", *METHODS), default="all")
    common.add_argument("--software-commit", default="workspace-unversioned")
    simulate = sub.add_parser("simulate", parents=[common], help="run one paired scenario")
    simulate.set_defaults(func=_simulate)
    matrix = sub.add_parser("matrix", parents=[common], help="run a seed matrix")
    matrix.add_argument("--seed-start", type=int, default=1000)
    matrix.add_argument("--seeds", type=int, default=10)
    matrix.add_argument("--raw", action="store_true", help="retain event logs for every run")
    matrix.add_argument(
        "--no-resume",
        action="store_false",
        dest="resume",
        help="recompute runs even when a summary already exists",
    )
    matrix.set_defaults(resume=True)
    matrix.set_defaults(func=_matrix)

    analyze = sub.add_parser("analyze", help="aggregate pilot summaries")
    analyze.add_argument("--input", type=Path, default=Path("raw/pilot"))
    analyze.add_argument("--output", type=Path, default=Path("results/pilot"))
    analyze.set_defaults(func=_analyze)

    allocation = sub.add_parser("allocation", help="run paired scarce-allocation pilot")
    allocation.add_argument("--output", type=Path, default=Path("results/pilot/allocation.csv"))
    allocation.add_argument("--seed-start", type=int, default=2000)
    allocation.add_argument("--seeds", type=int, default=40)
    allocation.add_argument("--groups", type=int, default=50)
    allocation.add_argument("--categories", type=int, default=5)
    allocation.add_argument("--demand-skew", type=float, default=1.0)
    allocation.add_argument("--stock-sufficiency", type=float, default=0.6)
    allocation.set_defaults(func=_allocation)

    crypto = sub.add_parser("crypto-bench", help="benchmark non-private P0 reference profile")
    crypto.add_argument("--iterations", type=int, default=1000)
    crypto.add_argument("--warmup", type=int, default=100)
    crypto.add_argument("--output", type=Path, default=Path("results/pilot/crypto_p0.csv"))
    crypto.set_defaults(func=_crypto_benchmark)

    evm = sub.add_parser("evm-bench", help="benchmark local P2 verifier-adapter contract")
    evm.add_argument("--output", type=Path, default=Path("results/pilot/evm_p2.csv"))
    evm.set_defaults(func=_evm_benchmark)

    zk = sub.add_parser("zk-bench", help="benchmark local P1 Groth16 circuit artifacts")
    zk.add_argument("--iterations", type=int, default=3)
    zk.add_argument("--warmup", type=int, default=1)
    zk.add_argument("--zkey", type=Path)
    zk.add_argument("--verification-key", type=Path)
    zk.add_argument("--solidity-verifier", type=Path)
    zk.add_argument("--output", type=Path, default=Path("results/pilot/zk_p1.csv"))
    zk.set_defaults(func=_zk_benchmark)

    dtn = sub.add_parser("dtn-bench", help="run temporal-contact DTN routing matrix")
    dtn.add_argument("--output", type=Path, default=Path("results/pilot/dtn"))
    dtn.add_argument("--seed-start", type=int, default=5000)
    dtn.add_argument("--seeds", type=int, default=20)
    dtn.add_argument("--bundles", type=int, default=240)
    dtn.add_argument("--sites", type=int, default=8)
    dtn.add_argument("--relays", type=int, default=3)
    dtn.add_argument("--horizon-hours", type=int, default=48)
    dtn.add_argument("--partition-hours", type=int, default=6)
    dtn.add_argument("--ttl-hours", type=int, default=36)
    dtn.add_argument("--buffer-bytes", type=int, default=96 * 1024)
    dtn.set_defaults(func=_dtn_benchmark)

    dtn_trace = sub.add_parser("dtn-trace-bench", help="run DTN policies on CSV traces")
    dtn_trace.add_argument("--contacts", type=Path, required=True)
    dtn_trace.add_argument("--bundles", type=Path, required=True)
    dtn_trace.add_argument("--output", type=Path, default=Path("results/pilot/dtn_trace"))
    dtn_trace.add_argument("--buffer-bytes", type=int, default=96 * 1024)
    dtn_trace.add_argument("--copy-budget", type=int, default=4)
    dtn_trace.set_defaults(func=_dtn_trace_benchmark)

    trace_import = sub.add_parser(
        "trace-import-sociopatterns",
        help="convert a public SocioPatterns t-i-j contact file with a recorded evaluation overlay",
    )
    trace_import.add_argument("--input", type=Path, required=True)
    trace_import.add_argument("--context", required=True)
    trace_import.add_argument("--output", type=Path, required=True)
    trace_import.add_argument("--bytes-per-second", type=int, default=256000)
    trace_import.add_argument("--contact-step-seconds", type=int, default=20)
    trace_import.add_argument("--sites", type=int, default=4)
    trace_import.add_argument("--relays", type=int, default=3)
    trace_import.add_argument("--bundles-per-client", type=int, default=2)
    trace_import.add_argument("--ttl-hours", type=int, default=24)
    trace_import.add_argument("--bundle-size-bytes", type=int, default=2048)
    trace_import.add_argument(
        "--collapse-contiguous",
        action="store_true",
        help="compact adjacent samples; disabled by default to preserve 20-second slot causality",
    )
    trace_import.set_defaults(func=_trace_import_sociopatterns)

    diesel_import = sub.add_parser(
        "trace-import-dieselnet",
        help="convert official UMass DieselNet transfer logs with recorded provenance",
    )
    diesel_import.add_argument("--input", type=Path, required=True)
    diesel_import.add_argument("--context", default="dieselnet05")
    diesel_import.add_argument("--output", type=Path, required=True)
    diesel_import.add_argument("--event-window-seconds", type=int, default=1)
    diesel_import.add_argument("--minimum-bytes-per-second", type=int, default=2048)
    diesel_import.add_argument("--sites", type=int, default=4)
    diesel_import.add_argument("--relays", type=int, default=3)
    diesel_import.add_argument("--bundles-per-client", type=int, default=2)
    diesel_import.add_argument("--ttl-hours", type=int, default=24)
    diesel_import.add_argument("--bundle-size-bytes", type=int, default=2048)
    diesel_import.set_defaults(func=_trace_import_dieselnet)

    family_campaign = sub.add_parser(
        "independent-trace-campaign",
        help="run family-aware held-out external-validity and sensitivity campaign",
    )
    family_campaign.add_argument(
        "--registry",
        type=Path,
        default=Path("configs/trace_families/registry.yaml"),
    )
    family_campaign.add_argument(
        "--output",
        type=Path,
        default=Path("results/confirmatory/independent_trace_family_20260718"),
    )
    family_campaign.set_defaults(func=_independent_trace_campaign)

    dtn_oracle = sub.add_parser(
        "dtn-oracle-check",
        help="cross-check unbounded Epidemic routing against an independent temporal oracle",
    )
    dtn_oracle.add_argument("--contacts", type=Path, required=True)
    dtn_oracle.add_argument("--bundles", type=Path, required=True)
    dtn_oracle.add_argument(
        "--output",
        type=Path,
        default=Path("results/pilot/dtn_oracle"),
    )
    dtn_oracle.set_defaults(func=_dtn_oracle_check)

    heldout = sub.add_parser(
        "heldout-temporal-frontier",
        help="run the core cap frontier directly on held-out temporal contact events",
    )
    heldout.add_argument(
        "--trace-root",
        type=Path,
        default=Path("external/sociopatterns/normalized_slots"),
    )
    heldout.add_argument(
        "--output",
        type=Path,
        default=Path("results/pilot/heldout_temporal_frontier"),
    )
    heldout.add_argument("--seed-start", type=int, default=7301)
    heldout.add_argument("--seeds", type=int, default=8)
    heldout.add_argument("--buffer-bytes", type=int, default=96 * 1024)
    heldout.set_defaults(func=_heldout_temporal_frontier)

    bow = sub.add_parser(
        "bow-temporal-campaign",
        help="run the shared-queue causal authorization frontier and sparse flow oracle",
    )
    bow.add_argument(
        "--trace-root",
        type=Path,
        default=Path("external/sociopatterns/normalized_slots"),
    )
    bow.add_argument(
        "--output",
        type=Path,
        default=Path("results/bow_20260822/temporal_primary"),
    )
    bow.add_argument("--context", action="append", choices=["invs13", "lh10", "sfhh"])
    bow.add_argument("--seed-start", type=int, default=8401)
    bow.add_argument("--seeds", type=int, default=8)
    bow.add_argument("--cap-fraction", type=float, action="append", default=None)
    bow.add_argument("--quorum-availability", type=float, action="append", default=None)
    bow.add_argument(
        "--policy",
        action="append",
        choices=[
            "quorum_only",
            "equal_static",
            "optimized_static",
            "robust_static",
            "rolling_relocation",
            "myopic_transfer",
            "adatransfer",
            "periodic_sync",
        ],
    )
    bow.add_argument("--bootstrap-repetitions", type=int, default=2000)
    bow.set_defaults(func=_bow_temporal_campaign)

    bow_factorial = sub.add_parser(
        "bow-factorial",
        help="run the complete 2^7 shared-queue policy mechanism design",
    )
    bow_factorial.add_argument(
        "--trace-root",
        type=Path,
        default=Path("external/sociopatterns/normalized_slots"),
    )
    bow_factorial.add_argument(
        "--output",
        type=Path,
        default=Path("results/bow_20260822/factorial"),
    )
    bow_factorial.add_argument(
        "--context", action="append", choices=["invs13", "lh10", "sfhh"]
    )
    bow_factorial.add_argument("--seed-start", type=int, default=8501)
    bow_factorial.add_argument("--seeds", type=int, default=4)
    bow_factorial.add_argument(
        "--policy",
        action="append",
        choices=[
            "optimized_static",
            "rolling_relocation",
            "myopic_transfer",
            "adatransfer",
        ],
    )
    bow_factorial.add_argument(
        "--smoke-cells",
        type=int,
        default=None,
        help="run only the first N registered cells; smoke output is not paper evidence",
    )
    bow_factorial.set_defaults(func=_bow_factorial)

    e2e = sub.add_parser(
        "e2e-local-run",
        help="run held-out contact -> real Groth16 -> protocol -> local-EVM integration",
    )
    e2e.add_argument("--registry", type=Path, default=Path("configs/e2e/registry.yaml"))
    e2e.add_argument(
        "--trace-root",
        type=Path,
        default=Path("external/sociopatterns/normalized_slots"),
    )
    e2e.add_argument("--output", type=Path, default=Path("results/e2e_local"))
    e2e.add_argument("--context", action="append", choices=["invs13", "lh10", "sfhh"])
    e2e.add_argument("--seed", action="append", type=int)
    e2e.add_argument(
        "--smoke-vouchers",
        type=int,
        default=None,
        help="restrict each context/seed to the first N logical vouchers",
    )
    e2e.set_defaults(func=_e2e_local_run)

    e2e_analyze = sub.add_parser(
        "e2e-local-analyze", help="analyze integrated local-EVM run summaries"
    )
    e2e_analyze.add_argument("--input", type=Path, default=Path("results/e2e_local"))
    e2e_analyze.add_argument(
        "--output", type=Path, default=Path("results/e2e_local/analysis")
    )
    e2e_analyze.set_defaults(func=_e2e_local_analyze)

    e2e_verify = sub.add_parser(
        "e2e-local-verify", help="verify SHA-256 integrity of a local-EVM campaign"
    )
    e2e_verify.add_argument("--input", type=Path, default=Path("results/e2e_local"))
    e2e_verify.set_defaults(func=_e2e_local_verify)

    one_crosscheck = sub.add_parser(
        "the-one-discrepancy",
        help="classify The ONE differences per message over scheduler/buffer controls",
    )
    one_crosscheck.add_argument("--the-one-root", type=Path, required=True)
    one_crosscheck.add_argument("--contacts", type=Path, required=True)
    one_crosscheck.add_argument("--bundles", type=Path, required=True)
    one_crosscheck.add_argument(
        "--output", type=Path, default=Path("results/pilot/the_one_discrepancy")
    )
    one_crosscheck.add_argument(
        "--update-intervals", type=float, nargs="+", default=[20.0, 5.0, 1.0]
    )
    one_crosscheck.add_argument(
        "--buffer-sizes", type=int, nargs="+", default=[96 * 1024, 1024 * 1024 * 1024]
    )
    one_crosscheck.set_defaults(func=_the_one_discrepancy)

    privacy = sub.add_parser("privacy-bench", help="analyze metadata leakage from event logs")
    privacy.add_argument("--input", type=Path, default=Path("raw/pilot"))
    privacy.add_argument("--output", type=Path, default=Path("results/pilot/privacy"))
    privacy.add_argument("--bucket-us", type=int, default=3_600_000_000)
    privacy.set_defaults(func=_privacy_benchmark)

    privacy_sweep = sub.add_parser(
        "privacy-sweep",
        help="analyze metadata leakage over multiple time-bucket sizes",
    )
    privacy_sweep.add_argument("--input", type=Path, default=Path("raw/pilot"))
    privacy_sweep.add_argument("--output", type=Path, default=Path("results/pilot/privacy_sweep"))
    privacy_sweep.add_argument("--bucket-hours", type=int, nargs="+", default=[1, 2, 4, 8])
    privacy_sweep.set_defaults(func=_privacy_sweep)

    privacy_defense = sub.add_parser(
        "privacy-defense",
        help="simulate metadata-hiding defense profiles over event logs",
    )
    privacy_defense.add_argument("--input", type=Path, default=Path("raw/pilot"))
    privacy_defense.add_argument(
        "--output", type=Path, default=Path("results/pilot/privacy_defense")
    )
    privacy_defense.set_defaults(func=_privacy_defense)

    privacy_batch = sub.add_parser(
        "privacy-batch-run",
        help="apply concrete fixed-shape batched public receipt release to raw event logs",
    )
    privacy_batch.add_argument("--input", type=Path, default=Path("raw/pilot"))
    privacy_batch.add_argument(
        "--output",
        type=Path,
        default=Path("results/pilot/privacy_batch"),
    )
    privacy_batch.add_argument("--slot-hours", type=int, default=8)
    privacy_batch.add_argument("--pad-bytes", type=int, default=2048)
    privacy_batch.add_argument("--dummy-min-batch", type=int, default=32)
    privacy_batch.add_argument(
        "--hide-fields",
        nargs="+",
        default=["risk_cell", "category", "path", "fallback_tag"],
        choices=["risk_cell", "category", "path", "fallback_tag"],
    )
    privacy_batch.add_argument("--seed", type=int, default=20_260_712)
    privacy_batch.add_argument(
        "--audit-key-env",
        default=None,
        help="optional environment variable containing a hex AES key for recoverable audit detail",
    )
    privacy_batch.set_defaults(func=_privacy_batch_run)

    testnet = sub.add_parser(
        "testnet-deploy",
        help="compile or deploy the complete verifier/gateway/settlement stack",
    )
    testnet.add_argument("--output", type=Path, default=Path("results/testnet/deployment.json"))
    testnet.add_argument("--rpc-env", default="SEPOLIA_RPC_URL")
    testnet.add_argument("--private-key-env", default="SEPOLIA_PRIVATE_KEY")
    testnet.add_argument(
        "--deploy",
        action="store_false",
        dest="dry_run",
        help="send transactions using RPC/private-key environment variables",
    )
    testnet.set_defaults(dry_run=True)
    testnet.set_defaults(settlement_adapter=None)
    testnet.set_defaults(func=_testnet_deploy)

    sepolia_preflight = sub.add_parser(
        "sepolia-preflight",
        help="validate Sepolia RPC, chain ID and disposable-wallet readiness",
    )
    sepolia_preflight.add_argument("--rpc-env", default="SEPOLIA_RPC_URL")
    sepolia_preflight.add_argument("--private-key-env", default="SEPOLIA_PRIVATE_KEY")
    sepolia_preflight.add_argument(
        "--output",
        type=Path,
        default=Path("results/testnet/sepolia_preflight.json"),
    )
    sepolia_preflight.set_defaults(func=_sepolia_preflight)

    sepolia_parity = sub.add_parser(
        "sepolia-parity",
        help="run representative domain-v2 proof/receipt/accounting parity on Sepolia",
    )
    sepolia_parity.add_argument("--rpc-env", default="SEPOLIA_RPC_URL")
    sepolia_parity.add_argument("--private-key-env", default="SEPOLIA_PRIVATE_KEY")
    sepolia_parity.add_argument(
        "--deployment",
        type=Path,
        default=Path("results/testnet/deployment_sepolia.json"),
    )
    sepolia_parity.add_argument(
        "--output",
        type=Path,
        default=Path("results/testnet/sepolia_parity"),
    )
    sepolia_parity.add_argument("--run-id", required=True)
    sepolia_parity.add_argument("--confirmations", type=int, default=3)
    sepolia_parity.set_defaults(func=_sepolia_parity)

    sepolia_analyze = sub.add_parser(
        "sepolia-analyze",
        help="aggregate all validated Sepolia parity reports without seed selection",
    )
    sepolia_analyze.add_argument(
        "--input",
        type=Path,
        default=Path("results/testnet/sepolia_parity"),
    )
    sepolia_analyze.add_argument(
        "--output",
        type=Path,
        default=Path("results/testnet/sepolia_analysis"),
    )
    sepolia_analyze.add_argument(
        "--plan",
        type=Path,
        default=None,
        help="locked confirmatory YAML; omit only for explicitly unregistered aggregates",
    )
    sepolia_analyze.add_argument(
        "--allow-partial",
        action="store_true",
        help="write progress summary when registered runs are still missing",
    )
    sepolia_analyze.set_defaults(func=_sepolia_analyze)

    sepolia_plan_check = sub.add_parser(
        "sepolia-plan-check",
        help="validate the locked registered Sepolia plan without RPC credentials",
    )
    sepolia_plan_check.add_argument(
        "--plan",
        type=Path,
        default=Path("configs/sepolia/campaign_plan.yaml"),
    )
    sepolia_plan_check.add_argument(
        "--checksum",
        type=Path,
        default=Path("configs/sepolia/campaign_plan.sha256"),
    )
    sepolia_plan_check.add_argument(
        "--output",
        type=Path,
        default=Path("results/testnet/sepolia_plan_check.json"),
    )
    sepolia_plan_check.set_defaults(func=_sepolia_plan_check)

    sepolia_plan_window = sub.add_parser(
        "sepolia-plan-window",
        help="run one three-repetition window from the locked Sepolia plan",
    )
    sepolia_plan_window.add_argument("--window", type=int, choices=[1, 2, 3], required=True)
    sepolia_plan_window.add_argument("--rpc-env", default="SEPOLIA_RPC_URL")
    sepolia_plan_window.add_argument(
        "--private-key-env", default="SEPOLIA_PRIVATE_KEY"
    )
    sepolia_plan_window.add_argument(
        "--plan",
        type=Path,
        default=Path("configs/sepolia/campaign_plan.yaml"),
    )
    sepolia_plan_window.add_argument(
        "--checksum",
        type=Path,
        default=Path("configs/sepolia/campaign_plan.sha256"),
    )
    sepolia_plan_window.add_argument(
        "--analysis-output",
        type=Path,
        default=Path("results/testnet/sepolia_analysis_registered"),
    )
    sepolia_plan_window.set_defaults(func=_sepolia_plan_window)

    confirm_freeze = sub.add_parser(
        "confirmatory-freeze",
        help="validate a registry and write a hash-addressed freeze manifest",
    )
    confirm_freeze.add_argument("--registry", type=Path, required=True)
    confirm_freeze.add_argument(
        "--output",
        type=Path,
        default=Path("results/confirmatory/freeze"),
    )
    confirm_freeze.set_defaults(func=_confirmatory_freeze)

    confirm_run = sub.add_parser(
        "confirmatory-run",
        help="run only paired scenarios authorized by a confirmatory registry",
    )
    confirm_run.add_argument("--registry", type=Path, required=True)
    confirm_run.add_argument("--output", type=Path, default=Path("raw/confirmatory"))
    confirm_run.add_argument("--software-commit", default="workspace-unversioned")
    confirm_run.add_argument(
        "--allow-draft",
        action="store_true",
        help="allow a DRAFT registry for smoke validation; never use its output in a paper",
    )
    confirm_run.add_argument(
        "--no-resume",
        action="store_false",
        dest="resume",
        help="recompute existing run directories",
    )
    confirm_run.add_argument(
        "--workers",
        type=int,
        default=1,
        help="process count for independent registered cells; results stay deterministic",
    )
    confirm_run.set_defaults(resume=True)
    confirm_run.set_defaults(func=_confirmatory_run)

    confirm_analyze = sub.add_parser(
        "confirmatory-analyze",
        help="compute only registered paired contrasts from confirmatory raw summaries",
    )
    confirm_analyze.add_argument("--registry", type=Path, required=True)
    confirm_analyze.add_argument("--input", type=Path, default=Path("raw/confirmatory"))
    confirm_analyze.add_argument(
        "--output",
        type=Path,
        default=Path("results/confirmatory/analysis"),
    )
    confirm_analyze.add_argument(
        "--allow-draft",
        action="store_true",
        help="allow analysis of DRAFT smoke results only",
    )
    confirm_analyze.set_defaults(func=_confirmatory_analyze)

    frontier = sub.add_parser(
        "frontier-analyze",
        help="compute registered cap-grid frontier hypervolumes and paired contrasts",
    )
    frontier.add_argument("--input", type=Path, default=Path("raw/confirmatory"))
    frontier.add_argument(
        "--output",
        type=Path,
        default=Path("results/confirmatory/frontier"),
    )
    frontier.set_defaults(func=_frontier_analyze)

    paper_assets = sub.add_parser(
        "paper-assets",
        help="generate auditable confirmatory paper tables, figures, and checksums",
    )
    paper_assets.add_argument(
        "--results",
        type=Path,
        default=Path("results/confirmatory"),
    )
    paper_assets.add_argument(
        "--output",
        type=Path,
        default=Path("results/confirmatory/paper_assets"),
    )
    paper_assets.set_defaults(func=_paper_assets)

    upgrade_assets = sub.add_parser(
        "upgrade-paper-assets",
        help="render formal refinement and cross-simulator semantic-resolution figures",
    )
    upgrade_assets.add_argument(
        "--results", type=Path, default=Path("results/upgrade_20260718")
    )
    upgrade_assets.add_argument(
        "--output", type=Path, default=Path("results/upgrade_20260718/paper_assets")
    )
    upgrade_assets.set_defaults(func=_upgrade_paper_assets)

    upgrade_manifest = sub.add_parser(
        "upgrade-evidence-manifest",
        help="validate and hash the protocol-faithful upgrade evidence",
    )
    upgrade_manifest.add_argument(
        "--output",
        type=Path,
        default=Path("results/upgrade_20260718/UPGRADE_EVIDENCE_MANIFEST.json"),
    )
    upgrade_manifest.set_defaults(func=_upgrade_evidence_manifest)

    fixback = sub.add_parser(
        "fixback-evidence",
        help="run post-freeze supporting fixback validation campaigns",
    )
    fixback.add_argument("--output", type=Path, default=Path("results/fixback"))
    fixback.add_argument("--calibration-seeds", type=int, default=5)
    fixback.add_argument("--bundles-per-seed", type=int, default=200)
    fixback.add_argument("--witness-trials", type=int, default=100)
    fixback.add_argument("--rights-sequences", type=int, default=10_000)
    fixback.set_defaults(func=_fixback_evidence)

    temporal_matched = sub.add_parser(
        "supporting-temporal-matched",
        help="run post-freeze temporal-matched core campaign",
    )
    temporal_matched.add_argument(
        "--registry",
        type=Path,
        default=Path("configs/supporting_2026Q3/temporal_matched_core/registry.yaml"),
    )
    temporal_matched.add_argument(
        "--output",
        type=Path,
        default=Path("results/supporting_2026Q3/temporal_matched_core"),
    )
    temporal_matched.set_defaults(func=_supporting_temporal_matched)

    baselines = sub.add_parser(
        "supporting-baselines",
        help="run post-freeze strong baseline pack and operating envelope",
    )
    baselines.add_argument(
        "--registry",
        type=Path,
        default=Path("configs/supporting_2026Q3/baselines/registry.yaml"),
    )
    baselines.add_argument(
        "--output",
        type=Path,
        default=Path("results/supporting_2026Q3/baselines"),
    )
    baselines.set_defaults(func=_supporting_baselines)

    privacy_pareto = sub.add_parser(
        "supporting-privacy-pareto",
        help="run post-freeze privacy Pareto sweep",
    )
    privacy_pareto.add_argument(
        "--registry",
        type=Path,
        default=Path("configs/supporting_2026Q3/privacy_pareto/registry.yaml"),
    )
    privacy_pareto.add_argument(
        "--output",
        type=Path,
        default=Path("results/supporting_2026Q3/privacy_pareto"),
    )
    privacy_pareto.set_defaults(func=_supporting_privacy_pareto)

    device_bench = sub.add_parser(
        "supporting-device-benchmark",
        help="run post-freeze device-class P1 proving benchmark",
    )
    device_bench.add_argument(
        "--registry",
        type=Path,
        default=Path("configs/supporting_2026Q3/device_benchmark/registry.yaml"),
    )
    device_bench.add_argument(
        "--output",
        type=Path,
        default=Path("results/supporting_2026Q3/device_benchmark"),
    )
    device_bench.set_defaults(func=_supporting_device_benchmark)

    multiproc_fault = sub.add_parser(
        "multiprocess-fault-campaign",
        help="run multi-process witness kill/restart fault injections",
    )
    multiproc_fault.add_argument(
        "--output",
        type=Path,
        default=Path("results/supporting_2026Q3/multiprocess_fault"),
    )
    multiproc_fault.set_defaults(func=_multiprocess_fault_campaign)

    distributed_rights = sub.add_parser(
        "distributed-rights-campaign",
        help="run no-shared-ledger multi-process risk-right transfer campaign",
    )
    distributed_rights.add_argument(
        "--output",
        type=Path,
        default=Path("results/supporting_2026Q3/distributed_rights"),
    )
    distributed_rights.add_argument("--trials", type=int, default=8)
    distributed_rights.set_defaults(func=_distributed_rights_campaign)

    practicality = sub.add_parser(
        "frontier-practicality",
        help="apply the labeled retrospective non-zero frontier margin",
    )
    practicality.add_argument(
        "--registry",
        type=Path,
        default=Path(
            "configs/supporting_2026Q3/frontier_practicality/registry.yaml"
        ),
    )
    practicality.add_argument(
        "--output",
        type=Path,
        default=Path("results/supporting_2026Q3/frontier_practicality"),
    )
    practicality.set_defaults(func=_frontier_practicality)

    formal = sub.add_parser(
        "formal-check",
        help="run bounded exhaustive safety checks and write negative-control evidence",
    )
    formal.add_argument("--output", type=Path, default=Path("results/pilot/formal"))
    formal.set_defaults(func=_formal_check)

    conformance = sub.add_parser(
        "conformance-check",
        help="replay a registered risk-right transcript against formal and Python states",
    )
    conformance.add_argument("--output", type=Path, default=Path("results/pilot/conformance"))
    conformance.set_defaults(func=_conformance_check)

    refinement = sub.add_parser(
        "protocol-refinement",
        help="replay TLA+-mapped traces against Python, SQLite and local Solidity/EVM",
    )
    refinement.add_argument(
        "--output", type=Path, default=Path("results/pilot/protocol_refinement")
    )
    refinement.add_argument("--traces", type=int, default=256)
    refinement.add_argument("--steps", type=int, default=64)
    refinement.set_defaults(func=_protocol_refinement)

    fault = sub.add_parser(
        "fault-campaign",
        help="run deterministic native crash/stale-state/epoch safety injections",
    )
    fault.add_argument("--output", type=Path, default=Path("results/pilot/fault_campaign"))
    fault.set_defaults(func=_fault_campaign)

    env = sub.add_parser("env-report", help="print reproducibility host metadata")
    env.set_defaults(func=lambda _args: print(json.dumps(host_report(), indent=2)) or 0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
