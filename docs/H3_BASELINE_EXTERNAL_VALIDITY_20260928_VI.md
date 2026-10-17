# Kết quả H3, baseline và external validity — 28-09-2026

Tài liệu này chốt ba rủi ro được ưu tiên trong lượt thực thi: H3
fairness/efficiency, tính trung thực của baseline và external validity trên nhiều
contact-trace family. Kiểm chứng lỗi phần cứng thực không thuộc phạm vi này.

## H3

Campaign hợp lệ dùng 640 seed mới, chín demand-skew × stock-sufficiency condition
trong mỗi seed, sáu phương pháp, tổng cộng 5.760 instance và 34.560 method row. Cỡ
mẫu, ngưỡng 3%, bootstrap một phía/hai phía và 50.000 resample được cố định trước khi
đọc outcome. Một execution 108 shard đã chạy trước thời điểm khai báo bị hủy toàn bộ,
không đọc outcome và không dùng trong phân tích hay manuscript.

Kết quả quyết định:

- mean seed-aggregate weighted-utility loss: 2,44768%;
- one-sided 95% upper bound: 2,56112%, nhỏ hơn margin 3%;
- two-sided 95% interval: [2,31259%; 2,58287%];
- minimum-service gain so với PF: 0,009029 [0,008629; 0,009441];
- so với equal-share: 0,079554 [0,078651; 0,080460];
- so với demand-proportional: 0,056776 [0,055767; 0,057802];
- so với utility-only: 0,235214 [0,232908; 0,237468];
- không có condition-level fairness loss trong bất kỳ contrast nào.

Endpoint gốc của H3 cũng đạt: fulfilled-unit loss so với utility-only bằng 0 với
one-sided upper bound bằng 0; hai fairness contrast gốc đều có lower bound dương.
Do đó cả contract gốc và criterion weighted-utility mạnh hơn đều được hỗ trợ.

## Baseline

- Matched protocol-state model: 1.080/1.080 cell đầy đủ, bốn method, safety pass.
- Matched routing component: 5.400 row, 1.350 paired run và 45 contrast; risk-priority
  hòa Binary Spray-and-Wait trên endpoint đã báo cáo nên không được dùng để chứng minh H4.
- Coconut-Online: 100 unique token, 0 duplicate credit, 36 field/threshold rejection;
  median issuance 57,499 ms và redemption 15,832 ms.
- Snappy connected component: 300 payment dương, 1.200 field mutation bị loại,
  $K=4,7,10$, wire 230--231 byte, median joint verification 298,92--317,23 ms.
  Literal equal-value Algorithm 4 vẫn debit 0; không có original contract để chạy
  differential validation, vì vậy không gắn nhãn full protocol.
- DPDRM và CSAR không có author implementation/reference profile đủ để replay đúng
  endpoint. Chúng chỉ được dùng làm related-work comparator, không thay bằng proxy rồi
  gắn tên gốc.

## External validity

Campaign hoàn tất 150/150 context-seed shard trên ba family và năm context:
SocioPatterns (InVS13, LH10, SFHH), UMass DieselNet và ENE5AI. Sau effective-cap
clipping có 12.240 matched policy row, 30 overlay seed mỗi context và 1.468.080
generated request. Tất cả structural/freeze/provenance check pass; risk-cap violation
bằng 0.

AdaTransfer trừ optimized static:

- ENE5AI: -0,04603 [-0,06429; -0,02540];
- SocioPatterns: -0,01003 [-0,01222; -0,00763];
- DieselNet: -0,04035 [-0,05283; -0,02788];
- family-balanced: -0,03214 [-0,03979; -0,02396].

AdaTransfer trừ periodic sync:

- ENE5AI: +0,25635 [0,23175; 0,28095];
- SocioPatterns: +0,13317 [0,13073; 0,13570];
- DieselNet: +0,05049 [0,03411; 0,06725];
- family-balanced: +0,14667 [0,13571; 0,15773].

Kết luận hợp lệ là comparator-dependent: AdaTransfer cải thiện so với periodic sync
trên cả ba family nhưng không vượt optimized static. Các interval resample 30 overlay
seed sau khi average context trong family; ba observed family là fixed và không tạo
population-wide inference cho mọi mobility dataset.

## Manuscript integration

`source/paper.tex` dùng Figure 1 của tác giả không đổi. Figure allocation được cập
nhật bằng hai endpoint H3; figure external validity dùng forest plot ba family. Paper
giữ H1--H5 và năm trụ cột gốc, đồng thời báo cả kết quả âm và ranh giới baseline để
tránh gắn nhãn hoặc suy diễn vượt evidence.
