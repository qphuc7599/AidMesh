# Báo cáo replay và holdout registry gốc AidMesh

Ngày chạy: 10-09-2026.

## Thiết kế và tính toàn vẹn

Registry gốc `CONFIRMATORY_Q1_20260712` chứa 21 scenario và 2.650 cell. Trước khi
chạy, cấu hình replay, cấu hình holdout, analysis code, executable inputs và seed ranges
được ghi vào `PRE_RUN_FREEZE.json`. Holdout dùng seed 95.000 trở lên và không giao với
seed gốc. Đây là prospective local specification; không phải external preregistration.

| Campaign | Hoàn tất | Lỗi/thiếu/thừa | Safety violation | Thời gian | Raw logical | Gzip |
|---|---:|---:|---:|---:|---:|---:|
| Current-source replay | 2.650/2.650 | 0/0/0 | 0 | 549,55 s | 8.540.916.958 B | 562.620.811 B |
| Fresh seed holdout | 2.650/2.650 | 0/0/0 | 0 | 559,50 s | 8.545.898.202 B | 562.515.849 B |

Mỗi campaign sinh 18 registered contrasts. BCa 95% intervals dùng paired seed và
20.000 resamples; multiplicity dùng Holm trong endpoint family. Mọi checksum,
decompression, risk conservation, honest-site fallback cap và safe-certificate check đều
pass.

## H1: operating envelope

| Scenario | Replay Δ | Holdout Δ [BCa 95% CI] | Quyết định |
|---|---:|---:|---|
| Core, partition 24 h | +0,21382 | +0,21306 [0,20788; 0,21804] | Đạt margin và Holm |
| Witness availability 0,6 | -0,15263 | -0,15017 [-0,15847; -0,14260] | Âm |
| Witness availability 0,7 | +0,05593 | +0,05693 [0,05043; 0,06270] | Holdout vừa qua margin +0,05 |
| Witness availability 0,9 | +0,25953 | +0,25293 [0,24840; 0,25770] | Dương |
| Partition 6 h | -0,04313 | -0,04267 [-0,04557; -0,03950] | Âm |
| Partition 48 h | +0,72973 | +0,72910 [0,72330; 0,73390] | Dương |
| Partition 60 h | +0,95897 | +0,95790 [0,95540; 0,96070] | Dương/boundary |
| Adversarial rate 0,05 | +0,24753 | +0,24783 [0,24230; 0,25340] | Dương |
| Adversarial rate 0,30 | +0,18130 | +0,19087 [0,18510; 0,19700] | Dương |

Core non-home completion của holdout là +0,10748 [0,10544; 0,10952]. Registered
restricted-mean settlement-time endpoint không đạt: AidMesh minus Online-Only là
+13,24475 giờ [12,95050; 13,54172] dưới alternative định trước “less”, Holm-adjusted
`p=1`. FirstSeen-DTN hoàn thành cao hơn AidMesh 0,04038 trong core cell nhưng làm tăng
duplicate exposure 100,78 value unit; đây là unsafe availability control.

## Safety--availability frontier

| Quorum family | Holdout hypervolume Δ [BCa 95% CI] | Holm p |
|---|---:|---:|
| K4/q3 | +0,0048656 [0,0036707; 0,0060080] | 0,00014999 |
| K7/q5 | +0,0035342 [0,0025661; 0,0047552] | 0,00014999 |
| K10/q7 | +0,00372845 [0,0020295; 0,0052518] | 0,00014999 |

Transferable placement cải thiện completion--exposure hypervolume ở cả ba family.
Riêng completion trên sáu tight-cap cell là null: +0,0000467
[-0,0004467; 0,0005067], Holm-adjusted `p=1`; exposure-cap ratio giảm 0,0329
[-0,03793; -0,0278]. Claim đúng là frontier improvement, không phải completion
superiority ở mọi cap.

## Replay boundary

Replay dùng đúng seed và design gốc nhưng chạy executable hậu review. So với historical
summaries, 150/2.650 cell khớp và 2.500/2.650 cell khác; toàn bộ 150 cell của scenario
partition 60 h khớp. Vì vậy replay chứng minh current-source execution trên original
design, không chứng minh exact binary reproduction. Replay và fresh holdout có effect
size gần nhau, hỗ trợ seed stability cho phiên bản hậu review.

Nguồn quyết định: `PRE_RUN_FREEZE.json`, `POST_RUN_EVIDENCE.json`,
`replay_coverage.json`, `holdout_coverage.json`, hai thư mục `*_analysis` và hai thư mục
`*_frontier` trong `results/postreview_original_registry_20260910/`.
