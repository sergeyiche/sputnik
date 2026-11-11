# Навыки (Skills) ассистента «Спутник»

Документ описывает **концепцию и целевой дизайн**, а не текущую реализацию.
Цель — единый механизм модульного поведения ассистента: тематические ответы по КБ, удержание контекста диалога и (позже) агентные сценарии с инструментами — без монолитного системного промпта.

Опираемся на открытый стандарт **[Agent Skills](https://agentskills.io/specification)** (Anthropic / экосистема агентов): поля `name` / `description`, progressive disclosure, краткие инструкции, опционально `allowed-tools` / scripts. **Источник истины — БД платформы** (тот же SQLite, что тенанты, промпт и стартовые вопросы), а не дерево файлов на диске. Файловый `SKILL.md` остаётся *форматом обмена и сидов* (экспорт/импорт, git-шаблоны), не runtime-хранилищем.

**Маршрутизация и исполнение** у нас свои: RAG-чат без произвольного доступа модели к FS; инструменты вызываются только через явный tool-layer API.

---

## 1. Зачем

Сейчас один системный промпт обслуживает разные сценарии: лекарства, упражнения, быт, проекты фонда, правовая поддержка, поиск врача, эмоциональная поддержка. Всё в одном файле:

- раздувает контекст на каждый запрос;
- смешивает инструкции (формат списка врачей мешает ответу про сон);
- усложняет правки без риска регрессий.

**Навык** — именованный модуль поведения с метаданными discovery и телом инструкций. Подключается по правилам своего *типа* (см. §1.1). Может влиять на промпт, retrieval, работу с историей диалога и (для агентных) на доступные инструменты.

Жёсткие правила безопасности (кризисы, «не врач», дисклеймер, запрет выдуманных URL) **всегда** остаются в базовом промпте. Навык их не ослабляет и не заменяет. Агентный навык **не** получает право обходить safety gate.

### 1.1. Тип навыка (skill type)

Один механизм хранения и админки — три семантики активации. Иначе «Память» и «Запись на консультацию» придётся либо натягивать на тематический роутер, либо плодить параллельные подсистемы.

В API и UI поле называется **`type`**. В SQLite / Python-моделях колонка — **`skill_type`** (`type` неудобен как имя колонки и атрибута).

| `type` | Роль | Когда активен | Что меняет в рантайме | Примеры |
|--------|------|---------------|----------------------|---------|
| **`domain`** | Тема ответа по КБ | Семантический матч вопроса | Промпт + retrieval boost/hints + follow-ups | `medications`, `find-doctor` |
| **`context`** | Режим работы с диалогом | По политике тенанта: always-on, по матчу, или по флагу | Сборка history, память сессии, «внимание» к уточнениям | `dialogue-memory`, `attention` |
| **`workflow`** | Агентный сценарий (многошаговый) | Явный intent / матч + подтверждение пользователя | Промпт-процедура + **state машины** + **tools** | `book-consultation`, `request-callback` |

Правила совместимости:

- Одновременно: **0..N** `context` (обычно 0–2) + **0..K** `domain` (лимит `SKILLS_MAX_ACTIVE`) + **не больше одного** активного `workflow` (эксклюзивный «режим сценария»).
- `workflow` при старте **не вытесняет** safety и базовый промпт; может временно сузить domain-retrieval (сценарий сам знает, какие факты нужны).
- `context`-навыки **не** конкурируют с domain по score за «тему»; у них отдельный канал активации (`activation`).

Это соответствует духу Agent Skills: skill = пакет инструкций (+ опционально tools/resources), а не только «тема FAQ».

---

## 2. Best practices, которые перенимаем

Из [спецификации Agent Skills](https://agentskills.io/specification) и [руководства Anthropic](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices):

| Принцип | Как применяем у нас |
|---------|---------------------|
| **Progressive disclosure** | В рантайме всегда дешёвый слой discovery (имя + description). Полный текст навыка — только при активации. |
| **Описание = WHAT + WHEN** | `description` отвечает: что делает навык и когда его включать; пишется от 3-го лица, с триггерными формулировками. |
| **Краткость** | Тело `SKILL.md` — узкие инструкции, которых нет у «умной» модели и нет в базовом промпте. Цель: заметно меньше 500 строк / ~5k токенов на навык. |
| **Один уровень ссылок** | Детали — в соседних `references/*.md`, на которые указывает только `SKILL.md` (без цепочек). |
| **Свобода по хрупкости** | Справочные темы — мягкие правила; списки врачей / запись на консультацию — жёсткий workflow + валидация слотов. |
| **Не дублировать базу знаний** | Domain-навык не копирует методички. Факты — из RAG. Навык задаёт *как* отвечать и *куда* смотреть в индексе. |
| **Tools / allowed-tools** | У `workflow`-навыков явный список инструментов; модель не получает «все API подряд». |
| **Deterministic where fragile** | Сбор телефона, слота времени, подтверждение записи — код/схема, а не «модель как-нибудь догадается». |

Чего **не** копируем слепо из coding-агентов:

- модель у нас **не читает файлы с диска** сама — рантайм API подмешивает активированные навыки в system / user message;
- исполняемые `scripts/` стандарта у нас = **зарегистрированные tools** бэкенда (`book_slot`, `create_lead`), а не bash в контейнере LLM;
- выбор **domain**-навыка — **детерминированный роутер** (эмбеддинги + порог). Для `workflow` возможен гибрид: роутер предлагает → пользователь подтверждает → входит state machine.

---

## 3. Progressive disclosure в Oracool

Три уровня загрузки (аналог стандарта, адаптированный под RAG):

```
┌─────────────────────────────────────────────────────────┐
│ Level 1 — Discovery (всегда, дёшево)                    │
│ name + description (+ examples для эмбеддингов)         │
│ ~100 токенов на навык в каталоге роутера                │
└──────────────────────────┬──────────────────────────────┘
                           │ матч вопроса
                           ▼
┌─────────────────────────────────────────────────────────┐
│ Level 2 — Instructions (только активные навыки)         │
│ тело SKILL.md → дописывается к системному промпту       │
└──────────────────────────┬──────────────────────────────┘
                           │ при необходимости
                           ▼
┌─────────────────────────────────────────────────────────┐
│ Level 3 — Resources / effects (по type)                 │
│ domain:   boost, hints, suggestions                     │
│ context:  memory store, history policy, summary         │
│ workflow: tools, slot schema, step machine state        │
│ (тяжёлое не обязано целиком попадать в контекст LLM)    │
└─────────────────────────────────────────────────────────┘
```

Базовый промпт (`config/default_system_prompt.md` / промпт из админки) = **Level 0**: всегда в контексте.

---

## 4. Хранение: БД как источник истины

### 4.1. Почему БД, а не `config/skills/` на диске

| Требование | Файлы | БД (platform SQLite) |
|------------|-------|----------------------|
| Правки из админки без деплоя | volume + перечитывание | естественно, как `starter_questions` |
| Мультитенантность | каталоги на тенанта, права, бэкапы | `tenant_id`, уже есть в `packages/platform/store.py` |
| Вкл/выкл, priority, аудит | JSON рядом / git | колонки + `updated_at` |
| Сиды и обмен с экосистемой Agent Skills | нативно | импорт/экспорт в `SKILL.md` |
| Hot-reload эмбеддингов examples | watch файлов | инвалидация кеша при `UPDATE` |

**Решение:** runtime читает навыки только из БД. Опциональный каталог `config/skills/*.md` (или seed в коде) — для первичного наполнения тенанта «Паркинсон» и переноса шаблонов между стендами. После сида источник истины — строки в SQLite.

Это не противоречит стандарту Agent Skills: стандарт задаёт *семантику* навыка (name, description, instructions, progressive disclosure). Где лежат байты — деталь продукта; у coding-агентов это FS, у нас — таблицы рядом с промптом организации.

### 4.2. Схема (черновик)

В том же файле БД платформы (`PLATFORM_DB` / существующий store):

```sql
CREATE TABLE skills (
  id            INTEGER PRIMARY KEY,
  tenant_id     INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  name          TEXT    NOT NULL,          -- [a-z0-9-]+, уникален в рамках тенанта
  title         TEXT    NOT NULL,          -- человекочитаемое имя в админке
  skill_type    TEXT    NOT NULL DEFAULT 'domain',  -- API: type; domain | context | workflow
  activation    TEXT    NOT NULL DEFAULT 'match',   -- match | always | intent | manual
  description   TEXT    NOT NULL,          -- WHAT + WHEN, ≤1024; для discovery
  body          TEXT    NOT NULL,          -- markdown-инструкции (Level 2)
  priority      INTEGER NOT NULL DEFAULT 50,  -- 0–100
  sticky_turns  INTEGER NOT NULL DEFAULT 2,
  enabled       INTEGER NOT NULL DEFAULT 1,
  retrieval_boost TEXT  NOT NULL DEFAULT '[]', -- JSON: ["файл.txt", ...]
  query_hints     TEXT  NOT NULL DEFAULT '[]', -- JSON: ["леводопа", ...]
  allowed_tools   TEXT  NOT NULL DEFAULT '[]', -- JSON: tool names для workflow
  config          TEXT  NOT NULL DEFAULT '{}', -- JSON: type-specific (см. ниже)
  created_at    TEXT    NOT NULL,
  updated_at    TEXT    NOT NULL,
  UNIQUE (tenant_id, name)
);

CREATE TABLE skill_examples (
  id         INTEGER PRIMARY KEY,
  skill_id   INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
  text       TEXT    NOT NULL,             -- exemplar для роутера / intent
  UNIQUE (skill_id, text)
);

-- Состояние агентного сценария в рамках чат-сессии (не путать с навыками-конфигом)
CREATE TABLE skill_runs (
  id           INTEGER PRIMARY KEY,
  tenant_id    INTEGER NOT NULL,
  session_id   TEXT    NOT NULL,           -- chat_sessions.id
  skill_id     INTEGER NOT NULL REFERENCES skills(id),
  status       TEXT    NOT NULL,           -- active | completed | cancelled | failed
  step         TEXT    NOT NULL,           -- id шага state machine
  slots_json   TEXT    NOT NULL DEFAULT '{}',
  created_at   TEXT    NOT NULL,
  updated_at   TEXT    NOT NULL
);

-- Долгоживущие факты диалога (навык memory / attention)
CREATE TABLE dialogue_memories (
  id           INTEGER PRIMARY KEY,
  tenant_id    INTEGER NOT NULL,
  session_id   TEXT    NOT NULL,
  key          TEXT    NOT NULL,           -- patient_name | city | meds | …
  value        TEXT    NOT NULL,
  source       TEXT    NOT NULL,           -- user | assistant | extracted
  confidence   REAL,
  updated_at   TEXT    NOT NULL,
  UNIQUE (tenant_id, session_id, key)
);

-- опционально позже: skill_references, skill_suggestions
```

Индексы: `(tenant_id, enabled, skill_type)`, `(session_id)` на `skill_runs` / `dialogue_memories`.

`config` (примеры по `type`):

```json
// context / dialogue-memory
{"max_facts": 20, "summarize_after_turns": 12, "history_window": 8, "inject_as": "system_block"}

// context / attention
{"prefer_previous_user_turn": true, "clarify_if_ambiguous": true}

// workflow / book-consultation
{"steps": ["intent_confirm", "collect_contacts", "collect_slot", "confirm", "submit"],
 "required_slots": ["name", "phone", "preferred_time"],
 "cancel_phrases": ["отмена", "не надо"]}
```

### 4.3. Поля (семантика как у Agent Skills)

| Поле | Обязательно | Назначение |
|------|-------------|------------|
| `name` | да | машинный id: `[a-z0-9-]+`, ≤64 |
| `title` | да | подпись в админке |
| `type` (`skill_type` в БД) | да | `domain` \| `context` \| `workflow` |
| `activation` | да | `match` \| `always` \| `intent` \| `manual` |
| `description` | да | WHAT + WHEN для discovery / intent, ≤1024 |
| `body` | да | процедурные инструкции; без длинных цитат из методичек |
| `priority` | нет | 0–100; конфликты внутри одного type |
| `sticky_turns` | нет | удержание domain/context после матча |
| `enabled` | да | выкл. без удаления |
| `retrieval_boost` | нет | domain: forced candidates из knowledge |
| `query_hints` | нет | domain: обогащение retrieval-query |
| `allowed_tools` | нет | workflow: whitelist инструментов API |
| `config` | нет | type-specific JSON |
| `examples[]` | для `match`/`intent` | 5–15 эталонных фраз |

Ключевые слова **не** основной механизм; допустимы позже как слабый доп. сигнал поверх семантического скора.

### 4.4. Сиды и экспорт (совместимость со стандартом)

Шаблон сида / экспорта — обычный `SKILL.md` (удобно ревьюить в PR):

```markdown
---
name: medications
description: >
  Справочные ответы о лекарствах при болезни Паркинсона, леводопе,
  периодах «включения/выключения», побочных эффектах и правилах приёма.
  Use when the user asks about pills, doses, side effects, on/off periods,
  medication schedule, or drug shortages.
metadata:
  priority: "80"
  sticky_turns: "2"
  retrieval_boost: "Памятка….txt,Три кита лечения БП.txt"
  query_hints: "леводопа; периоды включения выключения"
---

# Лекарства при БП
…
```

`examples.json` рядом при сиде → строки `skill_examples`.  
Команда/эндпоинт: `seed skills for tenant` (идемпотентно по `name`), `export skill → SKILL.md`.

### 4.5. Кеш роутера

При старте и после CRUD:

1. Загрузить enabled-навыки тенанта + examples.
2. Посчитать эмбеддинги examples (та же e5, что RAG); кеш в памяти процесса: `tenant_id → [(skill, vectors)]`.
3. Инвалидировать кеш тенанта на любом изменении skill / examples.

Эмбеддинги **не** обязаны лежать в SQLite в MVP (пересчёт быстрый при малом числе examples). При росте — колонка/таблица `skill_example_embeddings` или отдельная Chroma-коллекция `skills_router_{tenant}`.

---

## 5. Runtime: как навык меняет ответ

На каждый запрос `/v1/chat`:

1. **Safety gate** — подозрительные / острые запросы до любых навыков.
2. **Active workflow?** — если у сессии есть `skill_runs.status=active`, ход обрабатывает этот workflow (слоты / отмена / tool). Уточнение («а утром?») якорится на **шаг и слоты** `skill_run`, а не на `chat_sessions.topic` прошлого медицинского разговора. Domain-роутер можно не звать или звать лишь для боковой справки (§7.4). `topic` и `skill_runs.step` — разные поля.
3. **Attention** — до скоринга тем. Навык `attention` (см. §6.2) превращает короткую реплику и сохранённую тему сессии в самостоятельный поисковый вопрос. Остальные context-навыки применяют память и окно истории (§6.1).
4. **Domain route** — скоринг **уже разрешённого** вопроса по `type=domain` с `activation=match`. Не скорить сырое «а дозировку?» и не подмешивать предыдущую короткую реплику.
5. **Workflow intent** (если нет active run) — матч по `type=workflow`; при hit → предложить старт («Записать вас?» / кнопка), не вызывать tool до подтверждения.
6. **Retrieve** — RAG по самостоятельному вопросу, с boost/hints активных **domain**-навыков (workflow на шаге FAQ может запросить RAG точечно).
7. **Compose prompt** — `base` ⊕ context-блоки ⊕ domain-блоки ⊕ (workflow step instructions) ⊕ memory facts ⊕ history ⊕ RAG. Тело domain дописывается к запросу и не записывается обратно в промпт админки.
8. **Generate / Act** — LLM; для workflow при необходимости tool-calls только из `allowed_tools`, исполняет бэкенд.
9. **Observe** — `skills`, `skill_run`, извлечённые memory keys в логах / отладочном JSON.

```
вопрос → safety
       → [workflow run?] ──yes──→ уточнение по шагу и слотам → step machine → (tools?) → ответ
       → attention: самостоятельный вопрос из topic сессии
       → domain router по этому вопросу → retrieve+boost
       → (workflow intent?) → confirm → create skill_run
       → prompt → LLM → ответ
```

### 5.1. Конфликты и отсутствие матча

- Нет domain-матча → базовый промпт + активные context-навыки.
- Два domain близко по score → больший `priority`; лимит `SKILLS_MAX_ACTIVE`.
- `sticky_turns` держит domain, только если переписывание осталось в той же теме. Новый самостоятельный вопрос сбрасывает sticky, иначе короткое уточнение навсегда прилипнет к прошлому навыку.
- Boost только **добавляет** кандидатов, не выкидывает чужие документы.
- Новый workflow-intent при уже active run → либо «сначала завершим/отменим текущую запись», либо явная отмена старого.

### 5.2. Связь с follow-up подсказками

Подсказки «по теме» — у domain-навыка. В режиме workflow подсказки = следующие шаги сценария («Указать телефон», «Выбрать время», «Отмена»).

---

## 6. Context-навыки: «Память» и «Внимание»

Это не темы КБ, а **политика диалога**. Вписываются в ту же сущность `skills`, но `type=context` и отдельный канал активации — иначе семантический роутер будет путать «расскажи про память при БП» (domain / daily-living) с навыком удержания контекста.

### 6.1. `dialogue-memory` (Память)

**Задача:** не терять устойчивые факты сессии («меня зовут…», «живу в…», «принимаю леводопу», «запись интересовала утром») при коротком history window.

**Что делает рантайм (Level 3), а не только текст в промпте:**

1. После хода (или параллельно) — извлечение/обновление фактов в `dialogue_memories` (правила + лёгкий LLM-extract с JSON-схемой).
2. Перед генерацией — инжект блока «Известно о собеседнике» (топ-N фактов) в system.
3. Опционально: при `summarize_after_turns` — сжатие старых реплик в summary, чтобы не раздувать токены (summary ≠ замена цитируемых источников КБ).

**Активация:** обычно `always` для тенанта (если включён) или `match` на длинных диалогах.  
**Не делает:** медицинский «электронный паспорт» между сессиями без явного согласия / политики ПДн; не подменяет RAG.

### 6.2. `attention` (Внимание)

**Задача:** короткие follow-up («а дозировку?», «а как их снизить?») не отрывать от предмета разговора.

Предыдущая реплика пользователя для этого недостаточна. Цепочка «леводопа» → «а побочные?» → «а как их снизить?» на третьем шаге теряет препарат, если искать по последней короткой фразе.

**Что делает рантайм (Level 3), а не абзац в промпте админки.** Сейчас это `packages/dialogue/followup.py` и поле `chat_sessions.topic`; позже то же поведение включается навыком `attention`, без переноса правил в редактируемый системный промпт.

1. В сессии хранится самостоятельная **тема** — поисковая формулировка предмета, не сырая короткая реплика и не факт из `dialogue_memories` (имя, город, препарат — это память, §6.1).
2. Короткая или эллиптическая реплика переписывается отдельным вызовом модели в самостоятельный вопрос с этой темой. Обычный развёрнутый вопрос идёт в поиск как есть, без второго вызова.
3. Тема обновляется: новый самостоятельный вопрос заменяет её; уточнение сохраняет предмет. При сбое модели тема склеивается с текущей репликой и не подменяется предыдущей короткой фразой.
4. В запрос к основной модели подставляются строки «Тема диалога» и «Смысл реплики». Фраза «не меняй тему без сигнала» — дополнение к этому, не сам механизм.
5. При низкой уверенности domain-матча по уже разрешённому вопросу — один уточняющий вопрос вместо смены навыка.

**Активация:** `always` или вместе с memory. Можно объединить memory+attention в один skill на MVP, разделить позже. Извлекатель фактов памяти тему не переписывает.

### 6.3. Граница с domain

Вопрос «Как тренировать память при болезни Паркинсона?» → **domain** (`exercises` / `daily-living`), не `dialogue-memory`.  
Роутер domain смотрит examples про *содержание*; context-навыки не участвуют в том же score-ранжировании.

---

## 7. Workflow-навыки и агентность

Агентность = навык + **state machine** + **tools**, а не «длинный промпт с просьбой записаться». Стандарт Agent Skills как раз предполагает optional tools / scripts; у нас tools — серверные функции с аудитом.

### 7.1. Паттерн

1. Intent (router / кнопка «Записаться») → короткое подтверждение пользователя.
2. `skill_runs`: `status=active`, `step=…`, `slots_json`.
3. Каждый ход: валидация слотов кодом → следующий step из `config.steps`.
4. На шаге `submit`: вызов только `allowed_tools` (например `create_consultation_request`).
5. Успех → `completed`; ошибка tool → сообщение пользователю + `failed` / retry; «отмена» → `cancelled`.

LLM ведёт *диалог* и формулировки; **истина по слотам и side-effects — в БД и tools**.

### 7.2. Пример: `book-consultation` (Запись на консультацию)

| Step | Поведение | Tool |
|------|-----------|------|
| `intent_confirm` | «Записать на консультацию к специалисту фонда?» | — |
| `collect_contacts` | Имя, телефон (нормализация/маска) | — |
| `collect_slot` | Предпочтительное время / формат (онлайн/очно) | опц. `list_available_slots` |
| `confirm` | Резюме слотов, явное «Подтверждаю» | — |
| `submit` | Создание заявки | `create_consultation_request` |

`allowed_tools`: `["list_available_slots", "create_consultation_request"]`.  
Пока CRM нет — tool пишет заявку в таблицу `consultation_requests` / письмо оператору (всё равно агентность: структурированный сбор + действие).

### 7.3. Безопасность агентности

- Нет tool вне whitelist навыка и тенанта.
- Нет записи без явного confirm-step.
- ПДн (телефон) — только в слотах/заявке; не в публичные логи ответов.
- Crisis / safety gate прерывает workflow.
- Операторская админка: список заявок, статус skill_run.

### 7.4. Совместимость с domain

Во время записи пользователь может спросить «а какие врачи есть?» — допустим **боковой** domain-ответ с пометкой «вернёмся к записи», не сбрасывая `skill_run`, либо мягкая пауза сценария. Политика фиксируется в `config` (`allow_domain_aside: true`).

---

## 8. Набор навыков (MVP и горизонт)

### 8.1. Domain (MVP по КБ)

| `name` | Тема | Типичный boost |
|--------|------|----------------|
| `medications` | Лекарства, on/off, побочки | Памятки по приёму, «Три кита», дефицит лекарств |
| `exercises` | Гимнастика, ходьба, застывания, тай-чи | Гимнастика, упражнения, методичка |
| `daily-living` | Быт, питание, сон, падения, поездки | Питание, Сон, методичка, запоры |
| `fund-projects` | Проекты и сообщество фонда | Навигатор, О фонде |
| `legal-support` | Правовая поддержка | правовая поддержка БП 2026 |
| `find-doctor` | Поиск / список врачей | Список врачей |

Старт с **3–4** domain (medications, exercises, fund-projects, find-doctor).

### 8.2. Context / workflow (после стабилизации domain)

| `name` | type | activation | Когда браться |
|--------|------|------------|---------------|
| `dialogue-memory` | context | always | После метрик «забывает имя/тему» |
| `attention` | context | always | Вместе с memory или вместо неё на старте |
| `book-consultation` | workflow | intent | Когда появится канал заявки/слотов |
| `request-callback` | workflow | intent | Упрощённый вариант записи (только телефон) |

---

## 9. Оценка качества

Без набора эталонов навыки нельзя «почувствовать» надёжно.

**Domain**

1. Golden set — 40–60 вопросов с ожидаемым domain-навыком (или `none`) и желаемыми документами в top-k.
2. Accuracy / precision@1 роутера, доля ложных срабатываний.
3. Recall нужных документов при boost vs baseline.
4. Выборочная оценка ответов: безопасность, опора на контекст, тон.

**Context**

5. Сценарии «назвал имя → через 6 реплик спросил как меня зовут» — факт в `dialogue_memories` и в ответе.
6. Два коротких уточнения подряд («а побочные?» → «а как их снизить?») — поиск остаётся в сохранённой теме сессии, а не в предыдущей короткой реплике.

**Workflow**

7. Скрипты записи: все обязательные слоты собраны; tool не вызван до confirm; отмена очищает run.
8. Попытка вызвать tool вне `allowed_tools` — отказ на уровне API.

Регрессии роутера — в CI (фикстуры эмбеддингов / закэшированные векторы examples).

---

## 10. Админка и эксплуатация

Вкладка **Навыки** в админке организации (тот же auth, что промпт / стартовые вопросы):

- таблица: title, name, **type**, enabled, priority, число examples;
- форма: description, body, activation, boost/hints (для domain), allowed_tools + config JSON/редактор шагов (для workflow), examples;
- вкл/выкл без удаления;
- превью роутера: «для этого вопроса сработали бы…» (domain/intent; без LLM);
- для workflow: просмотр активных `skill_runs` / заявок;
- кнопки: seed шаблонов, экспорт в `SKILL.md`.

API (черновик):

| Метод | Путь | Назначение |
|-------|------|------------|
| GET/POST | `/v1/…/skills` | список / создать |
| GET/PATCH/DELETE | `/v1/…/skills/{id}` | чтение / правка / удаление |
| PUT | `/v1/…/skills/{id}/examples` | заменить examples |
| POST | `/v1/…/skills/preview` | `{question}` → scored skills по type |
| POST | `/v1/…/skills/seed` | идемпотентный сид |
| GET | `/v1/…/skill-runs` | активные/недавние сценарии (оператор) |

```env
SKILLS_ENABLED=true
SKILLS_SCORE_THRESHOLD=0.35
SKILLS_MAX_ACTIVE=2
SKILLS_SEED_DIR=/app/config/skills
```

Бэкап навыков и memory/runs = бэкап platform SQLite (+ dialogue DB). Git — шаблоны сидов и этот документ.

---

## 11. Этапы внедрения

| Этап | Содержание | Результат |
|------|------------|-----------|
| **A. Спека + схема + сиды** | Документ; `skills` / `skill_examples` (+ колонки `skill_type`/activation); 3–4 domain-сида | Данные в БД |
| **B. Domain router** | Эмбеддинги examples, матч, sticky, логи | `skills` в отладке |
| **C. Prompt merge** | Подмешивание domain `body` | Тон/формат без смены поиска |
| **D. Retrieval boost** | Hints + forced candidates | Прирост релевантности |
| **E. Follow-ups** | Подсказки из domain | UX продолжения |
| **F. Admin UI** | CRUD / preview / seed по type | Управление без деплоя |
| **G. Context** | `dialogue_memories` + attention/history policy | Удержание фактов и follow-up |
| **H. Workflow MVP** | `skill_runs` + один сценарий заявки + 1–2 tools | Агентность без CRM |
| **I. Интеграции** | Слоты календаря / CRM / уведомления оператору | Боевая запись |

A–F можно делать, не блокируясь на агентности: колонки `skill_type`/`activation`/`allowed_tools`/`config` закладываем сразу (default `domain` / `match` / `[]` / `{}`).

CLI ingest не зависит от навыков.

---

## 12. Антипаттерны

- Класть в навык куски методичек — дубль RAG.
- Один навык «обо всём» — снова монолит.
- Роутинг только по подстроке — хрупко на русском.
- Острые состояния только в навыке — пропуск при ошибке роутера.
- 5+ domain на один вопрос — шум.
- Менять дисклеймер/цитирование в навыке вразрез с базовым промптом.
- Делать «Память» domain-навыком про когнитивные упражнения — путаница с КБ; для темы КБ — `daily-living`/`exercises`, для сессии — `context`.
- Агентность одним промптом без state/slots/tools — галлюцинации «вы записаны».
- Вызов tool до явного подтверждения пользователя.
- Давать workflow все инструменты тенанта без whitelist.

---

## 13. Открытые решения

1. Порог score и калибровка e5 на коротких русских вопросах.
2. Куда вставлять инструкции: только system vs system + блок у RAG user message.
3. LLM-классификатор как fallback — по умолчанию **нет** в MVP domain.
4. Показывать ли пользователю активный режим (memory/запись) в UI виджета.
5. Векторы examples: in-memory (MVP) vs SQLite/Chroma.
6. Сиды: копия на тенанта (по умолчанию), не shared global rows.
7. Memory между сессиями / устройствами — только после политики ПДн; MVP = в рамках `session_id`.
8. Объединять ли `dialogue-memory` и `attention` в один skill на старте этапа G.
9. Контракт tools: синхронный JSON в процессе API vs очередь задач для медленных CRM.

---

## 14. Ссылки

- [Agent Skills Specification](https://agentskills.io/specification) (в т.ч. `allowed-tools`)
- [Anthropic: Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [Anthropic: Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- Базовый промпт: `config/default_system_prompt.md`
- RAG: `packages/rag/pipeline.py`
- Диалоги: `packages/dialogue/store.py`, переписывание уточнения: `packages/dialogue/followup.py`
- Платформа: `packages/platform/store.py`
