# Evidence provenance

`artifacts/RELEASE_SCOPE.json` lists final campaign roots. Full data from those
roots are in the final-evidence ZIP; selected aggregate outputs are on `main`.
The historical-reference ZIP preserves supporting/predecessor experiments and
original simulator events. They are not all current-paper results. Dates,
profiles and recorded file bytes are preserved; never merge profiles or count
the same observation twice.

## Interpretation

- The replay and holdout each contain 2,650 cells. Replay reuses analyzed seeds
  and differs from 2,500 historical summaries after executable changes. It is
  not exact reproduction of the historical binary. Holdout seeds are disjoint.
- The September 10 freeze is prospective local prespecification, not independently
  timestamped public preregistration. Publication today cannot change that status.
- H3 has 640 seed clusters, with 5,760 nested conditions and 34,560 method rows.
- External-validity intervals concern overlay seeds over fixed observed contexts,
  not population-wide inference across arbitrary contact datasets.
- Seen-tag results count physical duplicate exposure at handoff, not only settled
  duplicate receipts. Receipt expiry can conceal physical release in the latter.
- The final staged batch uses the matched `attackmatrix_final4` settlement and
  value-admission inputs. Older final3/compact/full-call outputs are diagnostics.
- Formal obligation coverage is not proof of the compiler, OS, hardware or
  physical actuator. The portfolio contains safe results and unsafe controls.

## Explicit invalidations

Two invalidated local campaigns are excluded from distributed scientific outputs;
their names, file counts and sizes are recorded in `RELEASE_SCOPE.json`:

1. `h3_fixed_replication_20260928_invalidated_predeclaration`: 108 shards started
   before declaration and were stopped without outcome analysis. The final study
   contains the separate complete 640-seed execution.
2. `t13_wang_seen_tag_matched_20260929_invalidated_settlement_endpoint`: settlement
   exposure was not the intended physical-handoff endpoint. The complete final
   physical-exposure campaign replaced it before manuscript use.

Valid negative controls, nulls and unfavorable results remain included. Invalid
runs are not merged into final analyses or relabelled as valid evidence.

## Publication validation

The new verifier checks distributed file hashes and package completeness. These
checks do not rerun experiments or constitute independent replication. Historical
execution receipts remain evidence of their original executions. The publication
validation report lists what was actually checked during packaging.
