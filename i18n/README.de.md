<div align="center">

[English](../README.md) · [العربية](README.ar.md) · [Español](README.es.md) · [Français](README.fr.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Tiếng Việt](README.vi.md) · [中文 (简体)](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [Deutsch](README.de.md) · [Русский](README.ru.md)

[![LazyingArt banner](../figs/banner.png)](https://lazying.art)

# Astrill Lazy Router

**Wähle Direct oder Astrill pro Dienst, Website, Gerät und Anwendung, ohne das native Astrill-Applet des Routers zu ersetzen.**

[![CI](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml/badge.svg)](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml)
[![License MIT](https://img.shields.io/badge/License-MIT-2F81F7)](../LICENSE)
[![Website](https://img.shields.io/badge/Website-lazying.art-111827)](https://lazying.art)

</div>

Das Projekt kombiniert eine native Ubuntu-Anwendung auf Basis von GTK 4 mit
einem kleinen DD-WRT-Begleiter. Astrill verwaltet weiterhin Tunnel, Server,
DNS und native Regeln; daneben kommt eine explizite Richtlinienschicht hinzu.

## Hauptfunktionen

- Direct-/Astrill-Routing nach Dienst, Website, Gerät, IPv4-Netz, Protokoll und Port.
- 261 Profile mit Suche sowie Filtern nach Anbieterland, Kategorie und Typ.
- Sichtbare Ergebnisse gesammelt auswählen und Suggested, Direct oder Astrill anwenden.
- Bidirektionale Synchronisierung von Websites, Geräten, Schnittstellen, DNS und Verbindung.
- Transaktionale A/B-Aktivierung, Rollback, Watchdog und Wiederherstellung des nativen Astrill.
- Eigene DHCP-Identität für Ubuntu-Anwendungen über macvlan.

## Routingmodell

Der Router besitzt nur einen Astrill-Tunnel und damit nur einen aktiven
VPN-Endpunkt. Das Land einer Richtlinie ist eine Präferenz für diesen
gemeinsamen Tunnel, keine zusätzliche gleichzeitige Verbindung. Das
Anbieterland in Services filtert nur den Katalog und ändert den Endpunkt nicht.

## Schnellstart

```bash
git clone git@github.com:lachlanchen/astrill-lazy-router.git
cd astrill-lazy-router
./scripts/install-desktop.sh
astrill-lazy-gui
```

Benötigt werden Python 3.11, GTK 4, Libadwaita, ein DD-WRT-Router mit
funktionierendem Astrill und schlüsselbasierter root-SSH-Zugriff über
`astrill-router`. Vor der ersten Anwendung bitte die
[Router-Installation](../docs/ROUTER_INSTALL.md) lesen.

## Sicherheit und Dokumentation

Der Begleiter verändert keine Astrill-Dateien, verwendet getrennte Marks und
Routingtabellen und schließt VPN-Richtlinien, wenn `tun0` nicht verfügbar ist.

- [Architektur](../docs/ARCHITECTURE.md)
- [Desktop-Anwendung](../docs/DESKTOP_APP.md)
- [Regelmodell](../docs/RULE_MODEL.md)
- [Sicherheit](../docs/SECURITY.md)
- [Tests](../docs/TESTING.md)

## Unterstützung

| Donate | PayPal | Stripe |
| --- | --- | --- |
| [![Donate](https://img.shields.io/badge/Donate-LazyingArt-0EA5E9?style=for-the-badge&logo=ko-fi&logoColor=white)](https://chat.lazying.art/donate) | [![PayPal](https://img.shields.io/badge/PayPal-RongzhouChen-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://paypal.me/RongzhouChen) | [![Stripe](https://img.shields.io/badge/Stripe-Donate-635BFF?style=for-the-badge&logo=stripe&logoColor=white)](https://buy.stripe.com/aFadR8gIaflgfQV6T4fw400) |

Veröffentlicht unter der [MIT-Lizenz](../LICENSE). Dieses unabhängige Projekt
ist nicht mit Astrill verbunden und bietet weder Konten oder Zugangsdaten noch
eine Möglichkeit, Verbindungsgrenzen des Anbieters zu umgehen.
