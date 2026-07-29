<div align="center">

[English](../README.md) · [العربية](README.ar.md) · [Español](README.es.md) · [Français](README.fr.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Tiếng Việt](README.vi.md) · [中文 (简体)](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [Deutsch](README.de.md) · [Русский](README.ru.md)

[![LazyingArt banner](../figs/banner.png)](https://lazying.art)

# Astrill Lazy Router

**Выбирайте Direct или Astrill для отдельных сервисов, сайтов, устройств и приложений, не заменяя штатный апплет Astrill на роутере.**

[![CI](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml/badge.svg)](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml)
[![License MIT](https://img.shields.io/badge/License-MIT-2F81F7)](../LICENSE)
[![Website](https://img.shields.io/badge/Website-lazying.art-111827)](https://lazying.art)

</div>

Проект объединяет нативное приложение Ubuntu на GTK 4 и небольшой компонент
для DD-WRT. Astrill по-прежнему управляет туннелем, сервером, DNS и штатными
правилами, а проект добавляет рядом явный слой политик.

## Основные возможности

- Маршрутизация Direct или Astrill по сервису, сайту, устройству, сети IPv4, протоколу и порту.
- Каталог из 261 профиля с поиском и фильтрами по стране провайдера, категории и типу.
- Пакетный выбор видимых результатов и режимы Suggested, Direct и Astrill.
- Двусторонняя синхронизация сайтов, устройств, интерфейсов, DNS и подключения.
- Транзакционное A/B-применение, откат, watchdog и восстановление штатного Astrill.
- Отдельная DHCP-идентичность для приложений Ubuntu через macvlan.

## Модель маршрутизации

На роутере работает один туннель Astrill, поэтому активен только один
VPN-узел. Страна в политике — предпочтение для общего туннеля, а не
дополнительное одновременное подключение. Страна провайдера в Services лишь
фильтрует каталог и не меняет VPN-узел автоматически.

## Быстрый запуск

```bash
git clone git@github.com:lachlanchen/astrill-lazy-router.git
cd astrill-lazy-router
./scripts/install-desktop.sh
astrill-lazy-gui
```

Требуются Python 3.11, GTK 4, Libadwaita, роутер DD-WRT с работающим Astrill и
root SSH только по ключу через псевдоним `astrill-router`. Перед первым
применением прочитайте [инструкцию по установке](../docs/ROUTER_INSTALL.md).

## Безопасность и документация

Компонент не изменяет файлы Astrill, использует отдельные метки и таблицы
маршрутизации и закрывает VPN-политики, когда `tun0` недоступен.

- [Архитектура](../docs/ARCHITECTURE.md)
- [Приложение для рабочего стола](../docs/DESKTOP_APP.md)
- [Модель правил](../docs/RULE_MODEL.md)
- [Безопасность](../docs/SECURITY.md)
- [Тестирование](../docs/TESTING.md)

## Поддержка

| Donate | PayPal | Stripe |
| --- | --- | --- |
| [![Donate](https://img.shields.io/badge/Donate-LazyingArt-0EA5E9?style=for-the-badge&logo=ko-fi&logoColor=white)](https://chat.lazying.art/donate) | [![PayPal](https://img.shields.io/badge/PayPal-RongzhouChen-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://paypal.me/RongzhouChen) | [![Stripe](https://img.shields.io/badge/Stripe-Donate-635BFF?style=for-the-badge&logo=stripe&logoColor=white)](https://buy.stripe.com/aFadR8gIaflgfQV6T4fw400) |

Проект распространяется по [лицензии MIT](../LICENSE), не связан с Astrill и
не предоставляет учётные записи, данные доступа или способ обхода ограничений
провайдера на число подключений.
