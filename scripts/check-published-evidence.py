"""Check final published aggregate evidence; does not execute new experiments."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return json.loads((ROOT / 'results' / name).read_text(encoding='utf-8'))


def main():
    original = load('postreview_original_registry_20260910/POST_RUN_EVIDENCE.json')
    holdout = load('postreview_original_registry_20260910/holdout_coverage.json')
    h3 = load('h3_fixed_replication_20260928/summary.json')
    external = load('external_validity_three_family_20260928/fixed_analysis.json')
    settlement = load('geth_eip7825_atomic100_attackmatrix_final4_20260910/summary.json')
    state = load('geth_eip7825_atomic100_attackmatrix_final4_20260910/atomic_state_audit.json')
    attack = load('geth_eip7825_atomic100_attackmatrix_final4_20260910/attack_matrix.json')
    formal = load('postreview_formal_20260910/portfolio.json')
    crash = load('release_crash_matrix_20260926_py312/summary.json')
    protocol = load('matched_protocol_baselines_20260910/summary.json')
    controller = load('matched_receipt_controller_20260910/summary.json')
    t13 = load('t13_wang_seen_tag_matched_20260929/analysis/confirmatory_analysis_manifest.json')
    checks = {
        'original_reporting_integrity': original['all_required_integrity_and_reporting_checks_passed'],
        'disjoint_holdout_seeds': original['seed_overlap'] == 0,
        'holdout_coverage': holdout['all_checks_passed'] and holdout['completed_cells'] == 2650,
        'replay_drift_retained': original['replay']['historical_semantic_mismatches'] == 2500,
        'h3_sample_and_margin': h3['coverage']['seedClusters'] == 640
            and h3['coverage']['conditions'] == 5760
            and h3['utilityLoss']['oneSided95Upper'] < h3['utilityLoss']['margin'] == 0.03,
        'external_validity': external['allChecksPassed'] and external['rows'] == 12240,
        'atomic_credit': settlement['credits'] == [100, 50] and settlement['uniqueVouchers'] == 100,
        'settlement_replay_rejection': settlement['replayStatus'] == 0
            and settlement['incompleteFinalizeStatus'] == 0,
        'atomic_state': all(state['checks'].values()),
        'settlement_attack_controls': attack['allPassed'] and attack['negativeControls'] == 43,
        'formal_portfolio_coverage': formal['allObligationsCovered'],
        'process_crash_campaign': crash['all_boundary_trials_safe'] and crash['boundary_trials'] == 120,
        'matched_protocol_coverage': protocol['runs'] == 1080 and protocol['allSafetyRulesPassed'],
        'matched_controller_coverage': controller['runs'] == 1080
            and controller['matchedWorkloads'] == 360 and all(controller['matchingChecks'].values()),
        'seen_tag_analysis_coverage': t13['input_summary_count'] == 1450,
    }
    print(json.dumps({'status': 'PASS' if all(checks.values()) else 'FAIL',
                      'checks': checks, 'scope': 'recorded aggregates only; no rerun'}, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
