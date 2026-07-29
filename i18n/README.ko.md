<div align="center">

[English](../README.md) · [العربية](README.ar.md) · [Español](README.es.md) · [Français](README.fr.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Tiếng Việt](README.vi.md) · [中文 (简体)](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [Deutsch](README.de.md) · [Русский](README.ru.md)

[![LazyingArt banner](../figs/banner.png)](https://lazying.art)

# Astrill Lazy Router

**라우터의 기본 Astrill 애플릿을 교체하지 않고 서비스, 웹사이트, 기기, 앱별로 Direct 또는 Astrill을 선택합니다.**

[![CI](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml/badge.svg)](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml)
[![License MIT](https://img.shields.io/badge/License-MIT-2F81F7)](../LICENSE)
[![Website](https://img.shields.io/badge/Website-lazying.art-111827)](https://lazying.art)

</div>

GTK 4 기반 Ubuntu 네이티브 앱과 작은 DD-WRT 컴패니언으로 구성됩니다.
터널, 서버, DNS, 기본 규칙은 계속 Astrill이 관리하고, 이 프로젝트는 그 옆에
명시적인 정책 계층을 추가합니다.

## 주요 기능

- 서비스, 웹사이트, 기기, IPv4 네트워크, 프로토콜, 포트별 Direct/Astrill 라우팅.
- 261개 프로필 검색 및 제공자 국가, 카테고리, 유형 필터.
- 표시된 결과를 일괄 선택하고 Suggested, Direct, Astrill 모드 적용.
- 웹사이트, 기기, 인터페이스, DNS, 연결 설정의 양방향 동기화.
- 트랜잭션 A/B 적용, 롤백, 감시 복구, 기본 Astrill 상태로 복원.
- macvlan을 통한 Ubuntu 앱별 독립 DHCP ID.

## 라우팅 모델

라우터에는 Astrill 터널이 하나뿐이므로 활성 VPN 엔드포인트도 한 개입니다.
정책 국가는 공유 터널에 대한 선호이며 추가 동시 연결이 아닙니다. Services의
제공자 국가 필터는 카탈로그만 필터링하며 엔드포인트를 자동 변경하지 않습니다.

## 빠른 시작

```bash
git clone git@github.com:lachlanchen/astrill-lazy-router.git
cd astrill-lazy-router
./scripts/install-desktop.sh
astrill-lazy-gui
```

Python 3.11, GTK 4, Libadwaita, Astrill이 동작하는 DD-WRT 라우터,
`astrill-router` 별칭을 통한 키 전용 root SSH가 필요합니다. 처음 적용하기
전에 [라우터 설치 문서](../docs/ROUTER_INSTALL.md)를 확인하세요.

## 보안과 문서

컴패니언은 Astrill 파일을 수정하지 않고 별도의 마크와 라우팅 테이블을
사용하며, `tun0`이 중단되면 VPN 정책을 fail-closed 처리합니다.

- [아키텍처](../docs/ARCHITECTURE.md)
- [데스크톱 앱](../docs/DESKTOP_APP.md)
- [규칙 모델](../docs/RULE_MODEL.md)
- [보안](../docs/SECURITY.md)
- [테스트](../docs/TESTING.md)

## 후원

| Donate | PayPal | Stripe |
| --- | --- | --- |
| [![Donate](https://img.shields.io/badge/Donate-LazyingArt-0EA5E9?style=for-the-badge&logo=ko-fi&logoColor=white)](https://chat.lazying.art/donate) | [![PayPal](https://img.shields.io/badge/PayPal-RongzhouChen-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://paypal.me/RongzhouChen) | [![Stripe](https://img.shields.io/badge/Stripe-Donate-635BFF?style=for-the-badge&logo=stripe&logoColor=white)](https://buy.stripe.com/aFadR8gIaflgfQV6T4fw400) |

[MIT License](../LICENSE)로 배포됩니다. Astrill과 제휴하지 않은 독립 프로젝트이며,
계정, 인증 정보 또는 제공자의 연결 제한을 우회하는 방법을 제공하지 않습니다.
