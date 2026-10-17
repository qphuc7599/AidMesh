# Nguồn gốc và giấy phép UMass DieselNet Spring 2005

Cập nhật: 2026-07-18. Đây là biên bản provenance phục vụ nghiên cứu, không phải tư vấn pháp lý.

## Nguồn chính thức đã dùng

- Kho dữ liệu: UMass Trace Repository, University of Massachusetts Amherst.
- Trang bộ sưu tập: https://traces.cs.umass.edu/docs/traces/network/
- Archive trực tiếp: https://skulddata.cs.umass.edu/traces/network/UMassDieselNet_Spring2005.tar.gz
- SHA-256 archive: `38187d26ae57548df9b47bd6a45f2176e871ef001fde7b934ce55fccc51e8619`.
- Tài liệu format: https://skulddata.cs.umass.edu/traces/network/diesel.txt
- SHA-256 tài liệu: `9bd4a21ca5011ade4de157bd307a3c467c028c55c7082cdfcb2dd7b91b0cd040`.
- Kho UMass tuyên bố dữ liệu được cung cấp theo CC BY 4.0 nếu không có ghi chú khác.
- Citation nguồn yêu cầu:
  John Burgess, Brian Gallagher, David Jensen, and Brian Neil Levine,
  “MaxProp: Routing for Vehicle-Based Disruption-Tolerant Networking,”
  IEEE INFOCOM, 2006, DOI `10.1109/INFOCOM.2006.228`.

## Nội dung và giới hạn diễn giải

Archive chứa log truyền byte thành công giữa các xe buýt DieselNet từ 25/01/2005 đến
22/05/2005. Có 47.442 dòng gốc và 30 bus ID. GPS và route trong log không được dùng vì
tài liệu nguồn nói các trường này không chính xác. Dữ liệu không chứa beneficiary,
điểm cứu trợ, nhu cầu hoặc hoạt động nhân đạo.

AidMesh tạo lớp đánh giá tổng hợp như sau:

1. sắp xếp cặp bus theo thứ tự từ điển và coi contact là hai chiều;
2. gộp hai bản ghi đối ứng cùng cặp/cùng giây bằng dung lượng lớn nhất;
3. primary dùng cửa sổ contact 1 giây và capacity bằng số byte quan sát được;
4. sensitivity dùng cửa sổ 5 và 10 giây, giữ nguyên tổng byte quan sát;
5. role site/relay/client được gán theo contact-duration centrality trên calibration prefix;
6. request, home site, service site, nullifier replay, TTL và cap đều là synthetic overlay.

Sau deduplication có 45.673 contact opportunities. Không được mô tả trace là field
deployment, humanitarian logistics, hoặc dữ liệu người nhận viện trợ.

## Quy tắc phát hành artifact

- Giữ attribution UMass Trace Repository, paper MaxProp, URL và CC BY 4.0.
- Ghi rõ mọi biến đổi và hash nguồn.
- Không trộn giấy phép dữ liệu vào giấy phép source code.
- Khi phát hành artifact, ưu tiên kèm script tải/convert, hash và URL; nếu kèm dữ liệu
  đã chuẩn hóa phải giữ notice CC BY 4.0 và transformation notice này.
