# storenet

Thư viện Python và lệnh `storenet` cho StoreNet Partner API: tạo, sửa bằng AI và liệt kê mẫu
thiết kế web của cửa hàng, không phải tự ký HMAC.

- Python 3.10 trở lên, chỉ dùng thư viện chuẩn (không có dependency).
- Bản 0.1 gồm 3 thao tác trên mẫu thiết kế: `create`, `update`, `list`.

## Điều kiện

- Cửa hàng StoreNet dùng **gói Pro trở lên** và đã bật công cụ thiết kế shop nhanh. Gói thấp hơn
  nhận lỗi `403290`; công cụ đang tắt nhận `404310`.
- Một khoá Partner API có scope `shop_designs:write`, do quản trị cửa hàng tạo. Mẫu thiết kế
  thuộc về người tạo khoá và credit AI trừ vào ví của người đó.

## Cài đặt

```bash
pip install storenet
storenet --version
```

## Cấu hình

Gói đọc 3 biến môi trường. Thiếu biến nào, lệnh dừng với mã thoát 2 và nêu tên biến đó; gói
không có URL mặc định.

```bash
export STORENET_BASE_URL="https://shop.example.com"   # địa chỉ cửa hàng của bạn
export STORENET_KEY_ID="<key id>"
export STORENET_SECRET="<secret>"
```

Giữ `STORENET_SECRET` như mật khẩu: không commit, không dán vào issue. Gói không bao giờ in
secret ra màn hình hay đưa vào thông điệp lỗi.

## Dùng lệnh `storenet`

### Tạo mẫu

```bash
storenet site create "Tiệm bánh nhỏ ở phố cổ, tông kem ấm"
storenet site create --file mo_ta.txt
```

```text
Đã tạo: Tiệm Bánh Phố Cổ (bakery)
ID: 1b2c3d4e-....
Xem: https://shop.example.com/d/...
```

Một lượt tạo mất khoảng 15 giây. Mô tả dài từ 1 đến 600 ký tự.

### Sửa mẫu bằng AI

```bash
storenet site update 1b2c3d4e "Chuyển sang gam màu đỏ, tiêu đề to hơn"
```

```text
Đã đổi:
  - Màu — đổi bộ màu
  - Tiêu đề — đổi cỡ chữ
Không làm được:
  - Logo — đổi ảnh: chưa hỗ trợ
Credit: −1.5 (còn 98.5)
Xem: https://shop.example.com/d/...
```

- `ID` là ID đầy đủ hoặc vài ký tự đầu (thường 8 ký tự như trong `site list`). Tiền tố khớp
  nhiều mẫu thì lệnh liệt kê các ID trùng và thoát 2; không khớp mẫu nào thì thoát 1.
- Yêu cầu dài từ 1 đến 400 ký tự. Một lượt sửa có thể tới khoảng 110 giây.
- AI không áp được thay đổi nào thì lệnh in lý do, không trừ credit và thoát 1.
- Link xem không đổi sau khi sửa.

### Liệt kê mẫu

```bash
storenet site list
```

```text
ID        TÊN               NGÀNH   CẬP NHẬT          LINK
1b2c3d4e  Tiệm Bánh Phố Cổ  bakery  2026-11-01 15:30  https://shop.example.com/d/...
Tổng: 1 mẫu
```

Danh sách chỉ gồm các mẫu tạo qua Partner API bởi chủ khoá, mới cập nhật trước; lệnh tự lấy
hết các trang.

### Tuỳ chọn chung

- `--json`: in nguyên payload JSON của máy chủ (dùng cho script).
- `--idempotency-key KHOÁ` (`create`, `update`): đặt khoá chống trùng của riêng bạn. Gửi lại
  cùng khoá và cùng nội dung thì nhận lại đúng kết quả cũ, không tạo thêm, không trừ credit lần
  nữa.

### Mã thoát

| Mã | Ý nghĩa |
|---|---|
| 0 | Thành công |
| 1 | Máy chủ trả lỗi, AI không đổi gì, hoặc tiền tố ID không khớp mẫu nào |
| 2 | Sai cú pháp, thiếu biến môi trường, hoặc tiền tố ID khớp nhiều mẫu |

## Dùng làm thư viện

```python
from storenet import StorenetClient, StorenetError

client = StorenetClient.from_env()  # hoặc StorenetClient(base_url, key_id, secret)

site = client.sites.create("Cửa hàng thú cưng, tông xanh lá")
print(site.id, site.name, site.industry, site.url)

edit = client.sites.update(site.id, "Đổi sang tông cam")
if edit.applied:
    print(edit.changed, edit.credits_used, edit.credits_available)
else:
    print(edit.outcome, edit.refused, edit.unsupported)

for item in client.sites.list():
    print(item.id[:8], item.name, item.updated_at)
```

- `sites.update` trả `Edit` cả khi AI không đổi gì (`outcome` là `refused` hoặc `empty`); chỉ lỗi
  thật mới ném `StorenetError`.
- `StorenetClient(..., timeout=130, max_retries=2)`: timeout tính theo từng request.

## Gọi lại tự động

Mỗi lệnh `create` / `update` gửi một `Idempotency-Key` (tự sinh nếu bạn không truyền). Khi gặp
lỗi tạm thời, gói gọi lại tối đa `max_retries` lần (mặc định 2) với **cùng** khoá, chữ ký mới:

- `429`: chờ theo header `Retry-After` (tối đa 60 giây); không có header thì chờ 2 rồi 4 giây.
- `503310`, `503190`, `409171`, hết thời gian chờ, đứt kết nối: chờ 2 rồi 4 giây.
- `409310` (mẫu vừa bị sửa ở nơi khác) khi `update`: gọi lại đúng một lần trên bản mới nhất.

Các lỗi 4xx khác (ví dụ `403080` hết credit) không được gọi lại.

## Lỗi

Lỗi do máy chủ trả mang đúng mã của máy chủ trong `StorenetError.code` (ví dụ `"409310"`), kèm
`status`, `message`, `details`. Lỗi phía client dùng mã chữ:

| Mã | Khi nào |
|---|---|
| `missing_config` | Thiếu biến môi trường hoặc `base_url` không phải URL đầy đủ (`StorenetConfigError`) |
| `not_deployed` | Cửa hàng chưa lên bản máy chủ hỗ trợ thao tác này (thường gặp ở `list` / `update`); báo quản trị cửa hàng cập nhật |
| `validation_error` | Máy chủ từ chối dữ liệu gửi lên (422) |
| `timeout` | Quá thời gian chờ phản hồi |
| `network_error` | Không kết nối được hoặc kết nối bị đứt |
| `invalid_response` | Máy chủ trả nội dung không phải JSON hoặc sai định dạng |

## Bảo mật

Báo lỗ hổng theo hướng dẫn ở
[SECURITY.md](https://github.com/storenet-ai/storenet-python/blob/main/SECURITY.md), không mở
issue công khai.

## Giấy phép

MIT.
