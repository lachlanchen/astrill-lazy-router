<div align="center">

[English](../README.md) · [العربية](README.ar.md) · [Español](README.es.md) · [Français](README.fr.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Tiếng Việt](README.vi.md) · [中文 (简体)](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [Deutsch](README.de.md) · [Русский](README.ru.md)

[![LazyingArt banner](../figs/banner.png)](https://lazying.art)

# Astrill Lazy Router

**無需取代路由器原生 Astrill 小程式，即可依服務、網站、裝置和應用程式選擇 Direct 或 Astrill。**

[![CI](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml/badge.svg)](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml)
[![License MIT](https://img.shields.io/badge/License-MIT-2F81F7)](../LICENSE)
[![Website](https://img.shields.io/badge/Website-lazying.art-111827)](https://lazying.art)

</div>

本專案由原生 Ubuntu GTK 4 控制應用程式和輕量 DD-WRT 伴侶外掛組成。
Astrill 繼續管理自己的隧道、伺服器、DNS 與原生規則；本專案只在旁邊增加
明確的策略層。

## 核心功能

- 依服務、網站、裝置、IPv4 網路、協定和連接埠選擇 Direct 或 Astrill。
- 261 個服務設定檔，可依供應商國家、分類和類型搜尋篩選。
- 批次選取目前篩選結果，統一套用 Suggested、Direct 或 Astrill。
- 雙向同步網站、裝置、介面、DNS、連線及進階 Astrill 設定。
- 交易式 A/B 套用、回復、看門狗修復及一鍵還原原生 Astrill。
- 使用 macvlan 為 Ubuntu 應用程式配置獨立 DHCP 身分。

## 路由模型

路由器只有一條 Astrill 隧道，因此同一時間只有一個 VPN 節點。策略國家只是
這條共享隧道的節點偏好，並非額外的同時連線。Services 中的供應商國家僅用於
篩選目錄，不會暗中切換 Astrill 節點。

## 快速開始

```bash
git clone git@github.com:lachlanchen/astrill-lazy-router.git
cd astrill-lazy-router
./scripts/install-desktop.sh
astrill-lazy-gui
```

需要 Python 3.11、GTK 4、Libadwaita、已正常執行 Astrill 的 DD-WRT
路由器，以及透過 `astrill-router` 別名進行的僅金鑰 root SSH。第一次套用前
請閱讀[路由器安裝與回復](../docs/ROUTER_INSTALL.md)。

## 安全與文件

伴侶外掛不會修改 Astrill 檔案，使用獨立的 mark 與路由表，並在 `tun0`
無法使用時對 VPN 策略採用 fail-closed。

- [架構](../docs/ARCHITECTURE.md)
- [桌面應用程式](../docs/DESKTOP_APP.md)
- [規則模型](../docs/RULE_MODEL.md)
- [安全邊界](../docs/SECURITY.md)
- [測試記錄](../docs/TESTING.md)

## 支持

| Donate | PayPal | Stripe |
| --- | --- | --- |
| [![Donate](https://img.shields.io/badge/Donate-LazyingArt-0EA5E9?style=for-the-badge&logo=ko-fi&logoColor=white)](https://chat.lazying.art/donate) | [![PayPal](https://img.shields.io/badge/PayPal-RongzhouChen-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://paypal.me/RongzhouChen) | [![Stripe](https://img.shields.io/badge/Stripe-Donate-635BFF?style=for-the-badge&logo=stripe&logoColor=white)](https://buy.stripe.com/aFadR8gIaflgfQV6T4fw400) |

本專案採用 [MIT 授權條款](../LICENSE)，與 Astrill 沒有隸屬或背書關係，也不
提供 Astrill 帳號、憑證或規避供應商連線數量限制的方法。
