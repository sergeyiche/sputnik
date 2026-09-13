# Сырые исходники базы знаний

Положите сюда файлы в форматах **PDF**, **DOCX**, **TXT**, **MD**.

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
# Только конвертация
./scripts/convert-sources.sh

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
