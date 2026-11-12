# Changelog

Định dạng theo [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/); số phiên bản theo
[Semantic Versioning](https://semver.org/).

## [0.1.0] - chưa phát hành

### Thêm

- Lệnh `storenet site create | update | list` và `storenet --version`.
- Thư viện `StorenetClient` với `sites.create`, `sites.update`, `sites.list`.
- Tự ký HMAC-SHA256 theo hợp đồng Partner API.
- Gọi lại lỗi tạm thời với cùng `Idempotency-Key`; mã lỗi `not_deployed` cho cửa hàng chưa
  hỗ trợ thao tác.
