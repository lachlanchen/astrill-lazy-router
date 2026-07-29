<div align="center">

[English](../README.md) · [العربية](README.ar.md) · [Español](README.es.md) · [Français](README.fr.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Tiếng Việt](README.vi.md) · [中文 (简体)](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [Deutsch](README.de.md) · [Русский](README.ru.md)

[![LazyingArt banner](../figs/banner.png)](https://lazying.art)

# Astrill Lazy Router

**Elige Direct o Astrill por servicio, sitio web, dispositivo y aplicación sin sustituir el applet nativo de Astrill del router.**

[![CI](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml/badge.svg)](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml)
[![License MIT](https://img.shields.io/badge/License-MIT-2F81F7)](../LICENSE)
[![Website](https://img.shields.io/badge/Website-lazying.art-111827)](https://lazying.art)

</div>

El proyecto combina una aplicación nativa de Ubuntu con GTK 4 y un pequeño
complemento para DD-WRT. Añade políticas junto a Astrill, mientras Astrill
sigue controlando su túnel, servidor, DNS y reglas nativas.

## Funciones principales

- Rutas Direct o Astrill por servicio, web, dispositivo, red IPv4, protocolo y puerto.
- Catálogo de 261 perfiles con búsqueda y filtros por país del proveedor, categoría y tipo.
- Selección por lotes del resultado visible con modos Suggested, Direct y Astrill.
- Sincronización bidireccional de webs, dispositivos, interfaces, DNS y conexión.
- Activación A/B transaccional, reversión, vigilancia y restauración de Astrill nativo.
- Identidad DHCP independiente para aplicaciones de Ubuntu mediante macvlan.

## Modelo de rutas

El router dispone de un solo túnel Astrill y, por tanto, de un único endpoint
VPN activo. El país de una política es una preferencia para ese túnel
compartido, no una conexión simultánea adicional. El país del proveedor en
Services solo filtra el catálogo y no cambia el endpoint.

## Inicio rápido

```bash
git clone git@github.com:lachlanchen/astrill-lazy-router.git
cd astrill-lazy-router
./scripts/install-desktop.sh
astrill-lazy-gui
```

Se requieren Python 3.11, GTK 4, Libadwaita, un router DD-WRT con Astrill
funcionando y SSH de root mediante clave con el alias `astrill-router`. Lee la
[instalación del router](../docs/ROUTER_INSTALL.md) antes de la primera aplicación.

## Seguridad y documentación

El complemento no modifica los archivos de Astrill, usa marcas y tablas
separadas y cierra las rutas VPN cuando `tun0` no está disponible.

- [Arquitectura](../docs/ARCHITECTURE.md)
- [Aplicación de escritorio](../docs/DESKTOP_APP.md)
- [Modelo de reglas](../docs/RULE_MODEL.md)
- [Seguridad](../docs/SECURITY.md)
- [Pruebas](../docs/TESTING.md)

## Apoyo

| Donate | PayPal | Stripe |
| --- | --- | --- |
| [![Donate](https://img.shields.io/badge/Donate-LazyingArt-0EA5E9?style=for-the-badge&logo=ko-fi&logoColor=white)](https://chat.lazying.art/donate) | [![PayPal](https://img.shields.io/badge/PayPal-RongzhouChen-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://paypal.me/RongzhouChen) | [![Stripe](https://img.shields.io/badge/Stripe-Donate-635BFF?style=for-the-badge&logo=stripe&logoColor=white)](https://buy.stripe.com/aFadR8gIaflgfQV6T4fw400) |

Publicado con licencia [MIT](../LICENSE). Es un proyecto independiente, no
afiliado a Astrill, y no proporciona cuentas, credenciales ni una forma de
eludir los límites de conexión del proveedor.
