# Phát hành

Ghi chú cho người phát hành. Tag `vX.Y.Z` (bản chính thức) hoặc `vX.Y.ZrcN` (bản thử) chạy
`.github/workflows/release.yml`.

## Secret `STORENET_DIST_DENY`

- Đặt trong Settings → Secrets and variables → Actions của repo: danh sách từ cấm, cách nhau bởi
  dấu phẩy (tên nội bộ không được lọt vào wheel/sdist).
- Giá trị chỉ nằm trong secret. Không ghi các từ này vào bất kỳ tệp, commit, issue hay log nào
  của repo.
- Job `build` chạy `tools/check_dist.py --require-deny` cho **mọi** tag, kể cả rc (rc cũng lên
  TestPyPI công khai). Secret rỗng hoặc chưa đặt thì job dừng với mã 2, không có gì được phát
  hành.
- `ci.yml` quét dist không cần secret, vì pull request từ fork không nhận được secret.

Kiểm tại máy trước khi tag:

```bash
rm -rf dist && python -m build
STORENET_DIST_DENY="<từ 1>,<từ 2>" python tools/check_dist.py --require-deny dist/*
```

## Chuỗi `PLACEHOLDER`

`pyproject.toml`, `LICENSE`, `SECURITY.md`, `README.md`, `CHANGELOG.md` còn chữ `PLACEHOLDER`
(tác giả, email báo lỗ hổng) cho tới khi chốt. Tag rc vẫn chạy; tag chính thức `vX.Y.Z` dừng ở
job `build` nếu một trong các tệp này còn `PLACEHOLDER`. Thay hết trước khi tag bản chính thức.
