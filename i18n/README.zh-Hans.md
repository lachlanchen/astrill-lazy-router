<div align="center">

[English](../README.md) · [العربية](README.ar.md) · [Español](README.es.md) · [Français](README.fr.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Tiếng Việt](README.vi.md) · [中文 (简体)](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [Deutsch](README.de.md) · [Русский](README.ru.md)

[![LazyingArt banner](../figs/banner.png)](https://lazying.art)

# Astrill Lazy Router

**无需替换路由器原生 Astrill 小程序，即可按服务、网站、设备和应用选择 Direct 或 Astrill。**

[![CI](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml/badge.svg)](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml)
[![License MIT](https://img.shields.io/badge/License-MIT-2F81F7)](../LICENSE)
[![Website](https://img.shields.io/badge/Website-lazying.art-111827)](https://lazying.art)

</div>

本项目由原生 Ubuntu GTK 4 控制应用和轻量 DD-WRT 伴侣插件组成。Astrill
继续管理自己的隧道、服务器、DNS 与原生规则；本项目只在其旁边增加明确的
策略层。

## 核心功能

- 按服务、网站、设备、IPv4 网络、协议和端口选择 Direct 或 Astrill。
- 261 个服务档案，可按提供商国家、分类和类型搜索筛选。
- 批量选择当前筛选结果，并统一应用 Suggested、Direct 或 Astrill。
- 双向同步网站、设备、接口、DNS、连接及高级 Astrill 设置。
- 事务式 A/B 应用、回滚、看门狗恢复及一键还原原生 Astrill。
- 使用 macvlan 为 Ubuntu 应用分配独立 DHCP 身份。

## 路由模型

路由器只有一条 Astrill 隧道，因此同一时刻只有一个 VPN 节点。策略国家只是
这条共享隧道的节点偏好，并不是额外的并发连接。Services 中的提供商国家
仅用于筛选目录，不会暗中切换 Astrill 节点。

## 快速开始

```bash
git clone git@github.com:lachlanchen/astrill-lazy-router.git
cd astrill-lazy-router
./scripts/install-desktop.sh
astrill-lazy-gui
```

需要 Python 3.11、GTK 4、Libadwaita、已正常运行 Astrill 的 DD-WRT
路由器，以及通过 `astrill-router` 别名进行的仅密钥 root SSH。首次应用前
请阅读[路由器安装与回滚](../docs/ROUTER_INSTALL.md)。

## 安全与文档

伴侣插件不会修改 Astrill 文件，使用独立的 mark 与路由表，并在 `tun0`
不可用时对 VPN 策略执行 fail-closed。

- [架构](../docs/ARCHITECTURE.md)
- [桌面应用](../docs/DESKTOP_APP.md)
- [规则模型](../docs/RULE_MODEL.md)
- [安全边界](../docs/SECURITY.md)
- [测试记录](../docs/TESTING.md)

## 支持

| Donate | PayPal | Stripe |
| --- | --- | --- |
| [![Donate](https://img.shields.io/badge/Donate-LazyingArt-0EA5E9?style=for-the-badge&logo=ko-fi&logoColor=white)](https://chat.lazying.art/donate) | [![PayPal](https://img.shields.io/badge/PayPal-RongzhouChen-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://paypal.me/RongzhouChen) | [![Stripe](https://img.shields.io/badge/Stripe-Donate-635BFF?style=for-the-badge&logo=stripe&logoColor=white)](https://buy.stripe.com/aFadR8gIaflgfQV6T4fw400) |

本项目采用 [MIT 许可证](../LICENSE)，与 Astrill 无隶属或背书关系，也不提供
Astrill 账号、凭据或绕过服务商连接数量限制的方法。
