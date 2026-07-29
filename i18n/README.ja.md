<div align="center">

[English](../README.md) · [العربية](README.ar.md) · [Español](README.es.md) · [Français](README.fr.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Tiếng Việt](README.vi.md) · [中文 (简体)](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [Deutsch](README.de.md) · [Русский](README.ru.md)

[![LazyingArt banner](../figs/banner.png)](https://lazying.art)

# Astrill Lazy Router

**ルーター標準の Astrill アプレットを置き換えず、サービス、Web サイト、端末、アプリごとに Direct または Astrill を選択します。**

[![CI](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml/badge.svg)](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml)
[![License MIT](https://img.shields.io/badge/License-MIT-2F81F7)](../LICENSE)
[![Website](https://img.shields.io/badge/Website-lazying.art-111827)](https://lazying.art)

</div>

GTK 4 で作られた Ubuntu ネイティブアプリと、小さな DD-WRT
コンパニオンで構成されています。トンネル、サーバー、DNS、標準ルールは
Astrill が管理したまま、その横に明示的なポリシー層を追加します。

## 主な機能

- サービス、Web、端末、IPv4 ネットワーク、プロトコル、ポート単位の Direct/Astrill ルーティング。
- 261 プロファイルを、提供元の国・地域、カテゴリ、種類で検索・絞り込み。
- 表示中の結果を一括選択し、Suggested、Direct、Astrill をまとめて適用。
- Web サイト、端末、インターフェース、DNS、接続設定の双方向同期。
- トランザクション型 A/B 適用、ロールバック、監視、標準 Astrill への復元。
- macvlan による Ubuntu アプリ専用の DHCP ID。

## ルーティングモデル

ルーター上の Astrill トンネルは 1 本だけなので、同時に有効な VPN
エンドポイントも 1 つです。ポリシーの国・地域は共有トンネルへの希望であり、
追加の同時接続ではありません。Services の提供元国フィルターはカタログ用で、
エンドポイントを自動変更しません。

## クイックスタート

```bash
git clone git@github.com:lachlanchen/astrill-lazy-router.git
cd astrill-lazy-router
./scripts/install-desktop.sh
astrill-lazy-gui
```

Python 3.11、GTK 4、Libadwaita、Astrill が動作する DD-WRT ルーター、
および `astrill-router` への鍵認証 root SSH が必要です。初回適用前に
[ルーターの導入手順](../docs/ROUTER_INSTALL.md)を確認してください。

## セキュリティとドキュメント

コンパニオンは Astrill のファイルを変更せず、別のマークとルーティング
テーブルを使用し、`tun0` 停止時は VPN ポリシーをフェイルクローズします。

- [アーキテクチャ](../docs/ARCHITECTURE.md)
- [デスクトップアプリ](../docs/DESKTOP_APP.md)
- [ルールモデル](../docs/RULE_MODEL.md)
- [セキュリティ](../docs/SECURITY.md)
- [テスト](../docs/TESTING.md)

## サポート

| Donate | PayPal | Stripe |
| --- | --- | --- |
| [![Donate](https://img.shields.io/badge/Donate-LazyingArt-0EA5E9?style=for-the-badge&logo=ko-fi&logoColor=white)](https://chat.lazying.art/donate) | [![PayPal](https://img.shields.io/badge/PayPal-RongzhouChen-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://paypal.me/RongzhouChen) | [![Stripe](https://img.shields.io/badge/Stripe-Donate-635BFF?style=for-the-badge&logo=stripe&logoColor=white)](https://buy.stripe.com/aFadR8gIaflgfQV6T4fw400) |

[MIT License](../LICENSE) で公開されています。Astrill とは無関係の独立した
プロジェクトであり、アカウント、認証情報、接続数制限を回避する方法は提供しません。
