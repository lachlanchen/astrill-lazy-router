<div align="center">

[English](../README.md) · [العربية](README.ar.md) · [Español](README.es.md) · [Français](README.fr.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Tiếng Việt](README.vi.md) · [中文 (简体)](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [Deutsch](README.de.md) · [Русский](README.ru.md)

[![LazyingArt banner](../figs/banner.png)](https://lazying.art)

# Astrill Lazy Router

**Choisissez Direct ou Astrill par service, site, appareil et application sans remplacer l'applet Astrill native du routeur.**

[![CI](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml/badge.svg)](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml)
[![License MIT](https://img.shields.io/badge/License-MIT-2F81F7)](../LICENSE)
[![Website](https://img.shields.io/badge/Website-lazying.art-111827)](https://lazying.art)

</div>

Le projet associe une application Ubuntu native en GTK 4 à un petit compagnon
DD-WRT. Il ajoute des politiques à côté d'Astrill, tandis qu'Astrill conserve
la gestion de son tunnel, de son serveur, du DNS et de ses règles natives.

## Fonctions principales

- Routage Direct ou Astrill par service, site, appareil, réseau IPv4, protocole et port.
- Catalogue de 261 profils avec recherche et filtres par pays du fournisseur, catégorie et type.
- Sélection groupée du résultat visible avec les modes Suggested, Direct et Astrill.
- Synchronisation bidirectionnelle des sites, appareils, interfaces, DNS et connexion.
- Activation A/B transactionnelle, retour arrière, surveillance et restauration native.
- Identité DHCP distincte pour les applications Ubuntu grâce à macvlan.

## Modèle de routage

Le routeur ne possède qu'un tunnel Astrill et donc qu'un seul endpoint VPN
actif. Le pays d'une politique est une préférence pour ce tunnel partagé, pas
une connexion simultanée supplémentaire. Le pays du fournisseur dans Services
filtre uniquement le catalogue et ne change pas l'endpoint.

## Démarrage rapide

```bash
git clone git@github.com:lachlanchen/astrill-lazy-router.git
cd astrill-lazy-router
./scripts/install-desktop.sh
astrill-lazy-gui
```

Python 3.11, GTK 4, Libadwaita, un routeur DD-WRT avec Astrill fonctionnel et
un accès SSH root par clé via `astrill-router` sont nécessaires. Consultez
[l'installation du routeur](../docs/ROUTER_INSTALL.md) avant la première application.

## Sécurité et documentation

Le compagnon ne modifie aucun fichier Astrill, utilise des marques et tables
séparées et ferme les politiques VPN lorsque `tun0` est indisponible.

- [Architecture](../docs/ARCHITECTURE.md)
- [Application de bureau](../docs/DESKTOP_APP.md)
- [Modèle de règles](../docs/RULE_MODEL.md)
- [Sécurité](../docs/SECURITY.md)
- [Tests](../docs/TESTING.md)

## Soutien

| Donate | PayPal | Stripe |
| --- | --- | --- |
| [![Donate](https://img.shields.io/badge/Donate-LazyingArt-0EA5E9?style=for-the-badge&logo=ko-fi&logoColor=white)](https://chat.lazying.art/donate) | [![PayPal](https://img.shields.io/badge/PayPal-RongzhouChen-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://paypal.me/RongzhouChen) | [![Stripe](https://img.shields.io/badge/Stripe-Donate-635BFF?style=for-the-badge&logo=stripe&logoColor=white)](https://buy.stripe.com/aFadR8gIaflgfQV6T4fw400) |

Publié sous licence [MIT](../LICENSE). Ce projet indépendant n'est pas affilié
à Astrill et ne fournit ni compte, ni identifiant, ni moyen de contourner les
limites de connexion du fournisseur.
