# Mô hình hợp thành và quan hệ mô phỏng

## 1. Biên chứng minh

`formal/AidMeshIntegrated.tla` nối các thành phần thành một đường chạy:

```text
persistent witness/quorum
               hoặc conserved risk-right debit
                            ↓
domain-bound proof → authorization → physical release
                                      ↓
epoch-bound receipt → gateway acceptance → first-valid-wins settlement
```

Mô hình protocol-faithful không giả định fallback loại bỏ duplicate physical release.
Cùng `(epoch, nullifier)` có thể đi qua cả quorum lẫn fallback: quorum cung cấp tối đa
một release, còn mỗi release fallback phải được ánh xạ tới một right đã spend riêng.
Với hai right, mixed path có capacity tối đa ba release nhưng settlement vẫn chỉ credit
một lần. Epoch handoff cho phép nhiều release đã được quy về một settlement winner
thay vì yêu cầu số release bằng số credit.

Đây là điểm khác quan trọng so với phiên bản mô hình đầu ngày 2026-07-24, vốn vô tình
đặt `releases[e][n] <= 1` cho cả fallback. Phiên bản đó được giữ như evidence lịch sử
nhưng không còn là canonical.

## 2. Kết quả TLC canonical

Hai config safe đều dùng `K=4, q=3, f=1`. Config mixed-path có hai epoch, hai site,
một digest, một nullifier và **hai risk-right token** để thực sự duyệt nhánh
duplicate-within-cap. Config conflict có hai digest và thu nhỏ còn một epoch/site/right
để `NoConflictingCertificates` không bị đúng một cách vacuous. Không được dùng config
một digest làm bằng chứng duyệt conflict.

- 2.139.238 states generated;
- 282.334 distinct states;
- complete graph depth 30;
- không có violation cho type, quorum conflict, quorum at-most-once release,
  fallback conservation, authorization-before-release, settlement-after-release,
  epoch binding, at-most-once credit hoặc simulation invariant.

Ba negative control đều sinh đúng counterexample:

1. `UnsafeBypass`: settlement không có release;
2. `UnsafeConservation`: fallback release không consume right;
3. `UnsafeSettlement`: cùng nullifier được credit lần hai.

Log canonical:

`results/extension_20260724/formal/tlc_integrated_protocol_faithful/`

TLC duyệt hết không gian hữu hạn trong `.cfg`; đây không phải chứng minh vô hạn.

## 3. Quan hệ mô phỏng explicit

Với mỗi event identity, hàm trừu tượng hóa đọc event log theo sequence:

| Trạng thái/action trừu tượng | Sự kiện code cụ thể |
|---|---|
| `VerifyDomainBoundProof` | `groth16-local-evm / verifyProof / verified` |
| `AuthorizeQuorum` | `quorum / assemble-certificate / authorized` |
| `AuthorizeFallback` | `risk-right / spend / authorized` |
| `Release` | `inventory / release / committed` |
| `GatewayAccept; Settle` | `local-evm / verifyAndSettle / settled` |

Quan hệ `R(s_TLA, trace_impl)` yêu cầu:

1. `released ⇒ proofOK ∧ authorized`;
2. `settled ⇒ released ∧ proofOK`;
3. proof và authorization xuất hiện trước release;
4. release xuất hiện trước settlement;
5. với cùng circuit nullifier, fallback release không vượt số right debit, quorum
   release không vượt một, và settlement credit không vượt một;
6. chuỗi stage phải đơn điệu:
   `proof → authorization → release → settlement`;
7. mọi event được ánh xạ theo đúng action name; event không liên quan là stuttering
   step đối với abstraction.

Viết tường minh, với prefix `τ_i` và identity `x=(epoch, nullifier)`:

```text
α(τ_i)(x) = (P_i, Q_i, F_i, R_i^Q, R_i^F, S_i)
```

trong đó `P` là proof đã verify, `Q` là quorum authorization, `F` là số fallback
debit ID phân biệt, `R^Q/R^F` là release theo từng path và `S` là settlement credit.
Mỗi concrete step phải thỏa:

```text
α(τ_{i+1}) ∈ Post_{Verify, AuthorizeQ, AuthorizeF,
                   Release, GatewayAccept, Settle, Stutter}^{≤2}(α(τ_i))
```

Giới hạn hai action chỉ dành cho event adapter `verifyAndSettle` gộp
`GatewayAccept;Settle`. Debit ID không được tin trực tiếp từ log: checker tái tính
`stable_hash(event_id, domain="aidmesh/e2e/spend/v1")`, từ chối ID lặp hoặc đi qua
hai nullifier, rồi mới kiểm `R^F≤F`, `R^Q≤1`, `S≤1`. Đây là projected simulation
relation tường minh; arithmetic nội bộ của Groth16 và EVM microstep không log nằm
ngoài abstraction.

`python -m aidmesh.formal.integrated_refinement` áp quan hệ này lên **toàn bộ**
`events.jsonl` bất biến của full P1-v2-tagged campaign. Output gồm một hàng cho mỗi
event identity và summary hash:

```text
results/extension_20260724/formal/integrated_trace_refinement/
  integrated_simulation_relation.csv
  integrated_refinement_summary.json
```

Trace checker là bounded conformance evidence. Nó không chứng minh mọi execution có
thể có của Python/Solidity.

## 4. Component refinement bổ sung

Harness độc lập còn kiểm:

- 16.384 escrow transitions so với oracle;
- 2.048 durable witness transitions;
- 243 close/reopen cycles;
- crash-before-commit fail-closed;
- settlement transcript khớp giữa oracle, Python và local EVM.

Các kết quả này kiểm sâu state equality; integrated trace relation kiểm ordering và
composition xuyên lớp. Hai loại evidence bổ sung nhau, không thay thế nhau.

## 5. Không được suy diễn

- TLC chỉ bao phủ constants và state bound đã công bố.
- Trace refinement chỉ bao phủ campaign đã chạy.
- Local Py-EVM không chứng minh public-chain finality.
- Groth16 phụ thuộc giả định hệ mật và SRS đã nêu.
- Development zkey không chứng minh ceremony production độc lập.
- Không có independent formal/security review cho tới khi reviewer ngoài ký báo cáo.
