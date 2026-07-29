<div align="center">

[English](../README.md) · [العربية](README.ar.md) · [Español](README.es.md) · [Français](README.fr.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Tiếng Việt](README.vi.md) · [中文 (简体)](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [Deutsch](README.de.md) · [Русский](README.ru.md)

[![LazyingArt banner](../figs/banner.png)](https://lazying.art)

# Astrill Lazy Router

**اختر الاتصال المباشر أو Astrill لكل خدمة وموقع وجهاز وتطبيق، من دون استبدال تطبيق Astrill الأصلي في الموجّه.**

[![CI](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml/badge.svg)](https://github.com/lachlanchen/astrill-lazy-router/actions/workflows/ci.yml)
[![License MIT](https://img.shields.io/badge/License-MIT-2F81F7)](../LICENSE)
[![Website](https://img.shields.io/badge/Website-lazying.art-111827)](https://lazying.art)

</div>

يجمع المشروع بين تطبيق Ubuntu أصلي مبني على GTK 4 وإضافة صغيرة لـ DD-WRT.
وهو يضيف طبقة سياسات بجانب Astrill، بينما يبقى Astrill مسؤولاً عن النفق
والخادم وDNS وقواعده الأصلية.

## الإمكانات الأساسية

- توجيه Direct أو Astrill حسب الخدمة أو الموقع أو الجهاز أو شبكة IPv4 أو المنفذ.
- فهرس يضم 261 ملفاً مع البحث والتصفية حسب بلد المزوّد والفئة والنوع.
- تحديد جماعي للنتائج الظاهرة وتطبيق Suggested أو Direct أو Astrill.
- مزامنة ثنائية الاتجاه لإعدادات المواقع والأجهزة والواجهات وDNS والاتصال.
- تطبيق A/B آمن، وتراجع، ومراقبة تلقائية، واستعادة Astrill الأصلي بنقرة واحدة.
- هوية DHCP مستقلة لتطبيقات Ubuntu عبر macvlan.

## نموذج التوجيه

يوجد في الموجّه نفق Astrill واحد فقط، ولذلك توجد نقطة VPN نشطة واحدة في كل
لحظة. بلد السياسة هو تفضيل لهذا النفق المشترك، وليس اتصالاً مستقلاً إضافياً.
أما بلد المزوّد في صفحة Services فهو مرشح للفهرس ولا يغيّر نقطة الاتصال سراً.

## البدء السريع

```bash
git clone git@github.com:lachlanchen/astrill-lazy-router.git
cd astrill-lazy-router
./scripts/install-desktop.sh
astrill-lazy-gui
```

يلزم Python 3.11 وGTK 4 وLibadwaita، وموجّه DD-WRT يعمل عليه Astrill، واتصال
SSH بالمفتاح فقط عبر الاسم `astrill-router`. راجع
[تعليمات تثبيت الموجّه](../docs/ROUTER_INSTALL.md) قبل التطبيق الأول.

## الأمان والتوثيق

الإضافة لا تعدّل ملفات Astrill، وتستخدم علامات وجداول توجيه منفصلة، وتمنع
تسرّب سياسات VPN عند توقف `tun0`. اقرأ المرجع الإنجليزي الكامل:

- [البنية](../docs/ARCHITECTURE.md)
- [تطبيق سطح المكتب](../docs/DESKTOP_APP.md)
- [نموذج القواعد](../docs/RULE_MODEL.md)
- [الأمان](../docs/SECURITY.md)
- [الاختبارات](../docs/TESTING.md)

## الدعم

| Donate | PayPal | Stripe |
| --- | --- | --- |
| [![Donate](https://img.shields.io/badge/Donate-LazyingArt-0EA5E9?style=for-the-badge&logo=ko-fi&logoColor=white)](https://chat.lazying.art/donate) | [![PayPal](https://img.shields.io/badge/PayPal-RongzhouChen-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://paypal.me/RongzhouChen) | [![Stripe](https://img.shields.io/badge/Stripe-Donate-635BFF?style=for-the-badge&logo=stripe&logoColor=white)](https://buy.stripe.com/aFadR8gIaflgfQV6T4fw400) |

المشروع مرخّص وفق [MIT](../LICENSE). وهو مشروع مستقل غير تابع لـ Astrill ولا
يوفّر حسابات أو بيانات اعتماد أو طريقة لتجاوز حدود اتصالات المزوّد.
