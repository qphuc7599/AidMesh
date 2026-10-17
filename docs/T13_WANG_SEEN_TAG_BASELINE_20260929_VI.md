# T13 — matched Wang-style seen-tag baseline (29/09/2026)

## Phạm vi

T13 bổ sung một baseline thực thi cho mô tả multi-site trong Wang et al. Paper nguồn cho
phép các điểm phân phối đồng bộ danh sách seen tags qua Internet hoặc bản sao số được vận
chuyển, nhưng không quy định transport, chu kỳ đồng bộ hoặc giao thức xác nhận. Vì vậy đây là
reconstruction do nhóm AidMesh xây dựng, không phải code hoặc implementation gốc của Wang.
Thay đổi này chỉ thuộc evaluation; không sửa formal model hoặc các claim H1--H5 hiện có.

## Semantics đã cố định trước khi chạy

- Mỗi site giữ một tập tag cục bộ và phát hàng ngay nếu tag chưa có trong tập đó.
- Trong partition không có trao đổi seen tags giữa các site.
- Sau khi connectivity trở lại, mọi site hợp nhất toàn bộ tập tag tại các epoch cố định;
  merge hoàn tất trước request tiếp theo.
- Release trước merge là không thể đảo ngược.
- Profile 6 h là primary vì khớp chu kỳ periodic authorization trong manuscript; 1 h và
  24 h là sensitivity.
- Mỗi tag dài 32 B. Payload chỉ là lower bound, không gồm framing, chữ ký, ACK và audit record.
- Endpoint an toàn là physical_duplicate_exposure: duplicate value đã được phát tại handoff,
  không phụ thuộc receipt về sau có settle hay hết hạn.

Registry đã đóng trước execution:
configs/postreview_20260929/t13_wang_seen_tag_matched_physical_v2.yaml.
Registry ID: T13_WANG_SEENTAG_PHYSICAL_20260929.

## Thiết kế

- 9 scenarios: core; witness availability 0.6/0.7/0.9; partition 6/48/60 h;
  adversarial fraction 0.05/0.30.
- 5 methods: AidMesh transferable rights, FirstSeen-DTN và Wang-style seen-tag 1/6/24 h.
- Core: 50 paired seeds; mỗi sensitivity: 30 paired seeds.
- Tổng: 290 scenario--seed pairs và 1.450 scenario--method--seed cells.
- Seeds bắt đầu từ 121000 và không giao với các registry reference/holdout khác.
- 21 endpoint contrasts; 20.000 BCa bootstrap và 20.000 paired sign-flip permutations;
  Holm correction.

## Kết quả execution và kiểm tra

- 1.450/1.450 cells hoàn tất; 0 failed cells.
- Thời gian wall: 412,355 s; tổng cell execution: 369,551 s; 8 workers.
- 0 risk-conservation failures và 0 safe-quorum conflicts.
- Đủ năm methods trong mọi scenario; không trùng scenario--method--seed.
- Completion của ba chu kỳ seen-tag giống nhau như semantics dự đoán.
- Physical duplicate exposure tăng đơn điệu theo chu kỳ đồng bộ.
- Khi partition 60 h vượt request horizon, mọi chu kỳ đồng bộ trùng FirstSeen-DTN.
- Full unit suite: 256 passed, 1 cảnh báo deprecation từ dependency websockets.

Raw data và analysis nằm trong
results/t13_wang_seen_tag_matched_20260929/.

## Primary core

| Contrast | Endpoint | Mean difference | BCa 95% CI | Holm-adjusted p |
|---|---:|---:|---:|---:|
| AidMesh − Wang 6 h | Completion | -0,0402 | [-0,0424; -0,03806] | 0,00105 |
| AidMesh − Wang 6 h | Physical duplicate exposure | -91,26 | [-95,82; -86,22] | 0,00105 |
| Wang 6 h − FirstSeen-DTN | Physical duplicate exposure | -13,34 | [-14,98; -11,82] | 0,00105 |
| Wang 1 h − Wang 6 h | Physical duplicate exposure | -63,24 | [-65,94; -60,18] | 0,00105 |
| Wang 24 h − Wang 6 h | Physical duplicate exposure | +10,14 | [8,88; 11,46] | 0,00105 |

Core completion của FirstSeen-DTN và cả ba Wang-style profiles là 1,0. AidMesh đạt
0,9598. Mean exposure lần lượt là 199,90 (AidMesh), 227,92 (Wang 1 h), 291,16
(Wang 6 h), 301,30 (Wang 24 h), và 304,50 (FirstSeen-DTN). Wang 6 h truyền trung
bình tối thiểu 656.783 B tag payload.

AidMesh giảm exposure so với Wang 6 h trong 8/9 scenarios. Điểm đảo chiều duy nhất là
witness availability 0,9: +2,97 [0,40; 5,47], trong khi completion gần bằng nhau
(-0,0006 [-0,0010; -0,0003]). Kết quả này xác nhận baseline seen-tag thu hẹp cửa sổ
duplicate so với FirstSeen-DTN nhưng không triệt tiêu cửa sổ trước merge. AidMesh dùng
conserved rights để giảm rủi ro mạnh hơn, đổi lại completion thấp hơn khi Wang baseline
phát ngay mọi tag chưa thấy cục bộ.

## Run bị loại

Run đầu tiên dùng endpoint cũ duplicate_exposure, chỉ đếm duplicate receipts đã settle.
Ở partition 60 h, releases vật lý xảy ra nhưng receipt hết hạn, làm endpoint trả 0 sai với
câu hỏi T13. Run đó bị loại trước khi dùng cho manuscript và được giữ riêng tại
results/t13_wang_seen_tag_matched_20260929_invalidated_settlement_endpoint/.
Không số liệu nào từ run bị loại xuất hiện trong paper.

## Manuscript

overleaf/paper.tex đã được cập nhật ở simulator parameters, comparator semantics,
experimental campaign, evidence units, output analysis, model validation, H1 results và
limitations. Paper báo cáo đủ 18 AidMesh--Wang 6-h scenario endpoints cùng ba contrast
frequency/FirstSeen tại core. Bản paper không chứa local path, registry hash hoặc mô tả
như thể baseline là implementation gốc của Wang.
