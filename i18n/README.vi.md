<div align="center">

[English](../README.md) · [العربية](README.ar.md) · [Español](README.es.md) · [Français](README.fr.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Tiếng Việt](README.vi.md) · [中文 (简体)](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [Deutsch](README.de.md) · [Русский](README.ru.md)

[![LazyingArt banner](../figs/banner.png)](https://lazying.art)

# Astrill Lazy Router

**Chọn Direct hoặc Astrill theo từng dịch vụ, trang web, thiết bị và ứng dụng mà không thay thế applet Astrill gốc trên router.**

[![CI](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml/badge.svg)](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml)
[![License MIT](https://img.shields.io/badge/License-MIT-2F81F7)](../LICENSE)
[![Website](https://img.shields.io/badge/Website-lazying.art-111827)](https://lazying.art)

</div>

Dự án kết hợp ứng dụng Ubuntu GTK 4 gốc với một thành phần DD-WRT nhỏ.
Astrill vẫn quản lý đường hầm, máy chủ, DNS và các quy tắc gốc; dự án chỉ bổ
sung một lớp chính sách rõ ràng bên cạnh.

## Tính năng chính

- Định tuyến Direct hoặc Astrill theo dịch vụ, web, thiết bị, mạng IPv4, giao thức và cổng.
- Danh mục 261 hồ sơ với tìm kiếm và lọc theo quốc gia nhà cung cấp, danh mục và loại.
- Chọn hàng loạt kết quả đang hiển thị rồi áp dụng Suggested, Direct hoặc Astrill.
- Đồng bộ hai chiều cài đặt web, thiết bị, giao diện, DNS và kết nối.
- Kích hoạt A/B theo giao dịch, hoàn tác, giám sát và khôi phục Astrill nguyên bản.
- Cấp danh tính DHCP riêng cho ứng dụng Ubuntu bằng macvlan.

## Mô hình định tuyến

Router chỉ có một đường hầm Astrill nên chỉ có một endpoint VPN hoạt động.
Quốc gia của chính sách là ưu tiên cho đường hầm dùng chung, không phải kết nối
đồng thời bổ sung. Bộ lọc quốc gia nhà cung cấp trong Services chỉ lọc danh mục
và không tự thay đổi endpoint.

## Bắt đầu nhanh

```bash
git clone git@github.com:lachlanchen/astrill-lazy-router.git
cd astrill-lazy-router
./scripts/install-desktop.sh
astrill-lazy-gui
```

Cần Python 3.11, GTK 4, Libadwaita, router DD-WRT có Astrill đang hoạt động và
SSH root chỉ dùng khóa qua bí danh `astrill-router`. Hãy đọc
[hướng dẫn cài router](../docs/ROUTER_INSTALL.md) trước lần áp dụng đầu tiên.

## Bảo mật và tài liệu

Thành phần đồng hành không sửa tệp Astrill, dùng mark và bảng định tuyến riêng,
đồng thời đóng chính sách VPN khi `tun0` không khả dụng.

- [Kiến trúc](../docs/ARCHITECTURE.md)
- [Ứng dụng desktop](../docs/DESKTOP_APP.md)
- [Mô hình quy tắc](../docs/RULE_MODEL.md)
- [Bảo mật](../docs/SECURITY.md)
- [Kiểm thử](../docs/TESTING.md)

## Hỗ trợ

| Donate | PayPal | Stripe |
| --- | --- | --- |
| [![Donate](https://img.shields.io/badge/Donate-LazyingArt-0EA5E9?style=for-the-badge&logo=ko-fi&logoColor=white)](https://chat.lazying.art/donate) | [![PayPal](https://img.shields.io/badge/PayPal-RongzhouChen-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://paypal.me/RongzhouChen) | [![Stripe](https://img.shields.io/badge/Stripe-Donate-635BFF?style=for-the-badge&logo=stripe&logoColor=white)](https://buy.stripe.com/aFadR8gIaflgfQV6T4fw400) |

Phát hành theo [giấy phép MIT](../LICENSE). Đây là dự án độc lập, không liên
kết với Astrill và không cung cấp tài khoản, thông tin đăng nhập hoặc cách vượt
qua giới hạn kết nối của nhà cung cấp.
