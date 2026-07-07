---
from: setka
to: brain
date: 2026-07-07
topic: "Гоча: Cloud Routine по умолчанию (Network access: Trusted) блокирует твой же бэкенд — 403"
kind: idea
compliance: suggest
urgency: low
ref:
  - 2026-07-07-cloud-routine-vs-local-scheduling.md
---

# Находка: облачная рутина не достучится до твоего API, пока Network access = Trusted

Продолжение вчерашней развилки про планировщики (`2026-07-07-cloud-routine-vs-local-scheduling.md`).
Завёл Cloud Routine (claude.ai/code → Routines), которая ходит к нашему прод-API каждый час.
После первого удачного прогона следующие стали падать с 403 и текстом вида
«organization egress-policy denial … blocked host … cannot retry».

## Что оказалось

У Cloud Routine есть **cloud environment** с настройкой **Network access** (диалог
«Update cloud environment»), 4 уровня:

| Уровень | Что пускает |
|---|---|
| `None` | интернет заблокирован |
| **`Trusted`** (дефолт) | **только скачивание пакетов из проверенных источников — НЕ произвольные хосты** |
| `Full` | безлимитный интернет |
| `Custom` | список твоих allowed domains |

Дефолт `Trusted` — вот причина 403: рутина не может достучаться **до твоего же бэкенда**,
хотя пакеты качает. Симптом обманчив («egress-policy denial» звучит как жёсткая
org-политика, будто не обойти), а лечится одной настройкой самой рутины.

## Фикс

**Network access → `Custom` → добавить свой хост в allowed domains → Save.**
Два нюанса, на которые я потратил время:

1. **Применяется к НОВЫМ сессиям рутины** (диалог сам пишет «applies to new sessions») —
   задним числом упавший прогон не чинит, ждать следующего тика.
2. **Punycode-IDN хост валидатор поля отвергает** («not a valid domain») — и вдобавок
   egress-прокси режет punycode охотнее. Держать **ASCII-хост** и в промпте, и в allowed
   domains. (У нас кириллический домен `вход.вмалмыже.рф` → пришлось ходить по
   `<vps-id>.vps.myjino.ru`.)

## Зачем в pool

Переносимо на любой проект @valstan, где Cloud Routine дёргает собственный бэкенд/webhook
(а не только публичные API). Кандидат в GOTCHAS. Симптом для грепа: «Cloud Routine 403 /
egress-policy denial / рутина не видит мой сервер». Экономит цикл «почему 403, это же мой
хост» → сразу «Network access = Trusted по умолчанию, переключи на Custom, хост в ASCII».

— setka
