# Сценарій демо-відео українською (4-6 хвилин)

> Відео ще не записане. Цей сценарій підготовлений для швидкого запису перед фінальною відправкою,
> якщо кандидат вирішить додати optional video deliverable.

## 0:00-0:35 - Що побудовано

Привіт. Це мій take-home про аналіз відгуків App Store. Сервіс уміє зібрати відтворювану випадкову
вибірку письмових відгуків, порахувати рейтинг і sentiment, знайти типові негативні фрази, згрупувати
конкретні скарги в теми та повернути evidence-backed areas of improvement через API і звіт.

Ключова ідея: числа й evidence визначає детермінований Python-код, тому кожен висновок можна перевірити.

## 0:35-1:20 - Збір і sampling

Показати `collection/itunes.py` і `collection/sampling.py`.

Для демо я використовую legacy iTunes endpoints і чесно позначаю їх як undocumented/demo-only. Вибірка
не означає "останні 100". Я визначаю reachable frame і роблю uniform sampling без replacement по rank.
Seed записується в metadata, тому конкретний sample можна відтворити.

Всі provider rows проходять allowlist sanitizer до збереження. Nickname і profile URL не потрапляють ані
в SQLite, ані в CSV, ані в committed fixture.

## 1:20-2:15 - NLP pipeline

Показати `analysis/`.

Rating metrics рахуються по всій вибірці. Для sentiment використовується pinned локальний
CardiffNLP RoBERTa, який після download працює offline. Для негативних фраз є дві таблиці: common phrases
і distinctive phrases через Fightin' Words.

Для themes я беру negative sentences, embed-ю їх pinned MiniLM і кластеризую agglomerative clustering.
Кожна тема має support, review IDs і representative excerpts. Якщо сигналу недостатньо, pipeline прямо
це показує, а не змушує модель вигадати тему.

## 2:15-3:05 - API

Запустити:

```powershell
uv run reviews serve --host 127.0.0.1 --port 8000
```

Показати `/healthz`, `/readyz`, потім `POST /v1/analyses` у fixture mode. Після відповіді показати:

- `/v1/analyses/{id}`;
- `/metrics`;
- `/insights`;
- `/reviews?format=csv`.

Звернути увагу на `sampling`, provenance, confidence intervals і `Location` header.

## 3:05-4:05 - Report

Показати `reports/nebula_us_seed42.md` і чотири PNG charts.

Звіт не написаний вручну: `reviews report` генерує Markdown і графіки з committed analysis JSON та
population aggregate. Golden test перевіряє, що committed Markdown дорівнює fresh render, а slow
fixture-reproduction test повторно запускає pinned models і порівнює результат.

У areas of improvement відкрити одну тему й показати evidence IDs/excerpts. Пояснити, що це робить
insight auditable.

## 4:05-4:50 - Quality / trade-offs

Показати CI і `docs/decisions.md`.

Коротко назвати trade-offs: undocumented iTunes provider лише для take-home demo; у production для own app
я б використав App Store Connect, для competitor data - licensed provider. SQLite і synchronous request
підходять для bounded demo, а production ingestion потребував би durable queue + Postgres.

Завершити командами gate:

```powershell
uv run ruff check
uv run ruff format --check
uv run mypy src
uv run pytest -m "not slow and not live"
```

І показати зелений результат.
