# Сырые исходники базы знаний

Положите сюда файлы в форматах **PDF**, **DOCX**, **XLSX**, **TXT**, **MD**.

## Порядок обработки

```
data/knowledge_sources/   ← сырые файлы (не трогаем вручную после загрузки)
        ↓  convert
data/knowledge/           ← нормализованные .txt (UTF-8)
        ↓  ingest
ChromaDB                  ← чанки для RAG
```

## Команды

```bash
# Вся папка sources
./scripts/convert-sources.sh

# Один или несколько файлов
./scripts/convert-sources.sh "data/knowledge_sources/Список врачей.xlsx"
./scripts/convert-sources.sh path/to/a.pdf path/to/b.docx

# Принудительно пересобрать
./scripts/convert-sources.sh --force
./scripts/convert-sources.sh --force "data/knowledge_sources/Список врачей.xlsx"

# Конвертация + индексация
./scripts/sync-knowledge.sh

# Пересоздать индекс с нуля
./scripts/sync-knowledge.sh --recreate
```

Конвертация инкрементальная: файл пересобирается только если исходник новее `.txt`.

## Структура папок

Можно использовать подпапки — структура сохраняется:

```
knowledge_sources/
  clinical/
    treatment.pdf
  faq/
    medications.docx
  notes.txt
```

Результат:

```
knowledge/
  clinical/
    treatment.txt
  faq/
    medications.txt
  notes.txt
```
