# Интеграции Content Checker вне AI Digest

Инвентаризация существующего кода на 2026-10-04. Эти подключения уже были в проекте; задача AI Digest не добавляла их. TMDB, OMDb, Кинопоиск и новые фестивальные/availability-ручки описаны отдельно в [AI_DIGEST.md](AI_DIGEST.md).

| Внешний сервис / endpoint | Зачем используется | Код |
| --- | --- | --- |
| MusicBrainz `https://musicbrainz.org/ws/2/artist`, `/release-group`, `/release` | Поиск и разрешение исполнителей/альбомов, дискографии, canonical MBID, даты и типы изданий, ordered artist credits, трек-листы и связи | `app/musicbrainz.py` |
| Cover Art Archive `https://coverartarchive.org/release-group/{mbid}` и `/front-250` | Поиск и локальное кэширование обложек альбомов | `app/musicbrainz.py`, `app/artwork.py` (фактический размер берётся из настройки artwork) |
| ListenBrainz `POST https://api.listenbrainz.org/1/popularity/release-group` | Счётчик прослушиваний release groups для карточек и сравнительной популярности альбомов; пакетные запросы | `app/listenbrainz.py` |
| fanart.tv `GET https://webservice.fanart.tv/v3.2/music/{artist_mbid}` | Фотографии музыкальных исполнителей (artistthumb) по MusicBrainz ID; API key из локальной конфигурации | `app/fanart.py` |
| Codex SDK через локальный Python runner | Уже существующие LLM-рекомендации фильмов/альбомов/персон и группировка бэклога по настроению; структурированный JSON, затем серверная проверка/обогащение | `app/llm.py`, `scripts/run_codex_recommendation.py` |

YouTube Music и поисковые ссылки на RuTracker в обычных карточках — исходящие пользовательские ссылки, не API музыки/скачивания. API-проверка публичных метаданных RuTracker относится к новой задаче. Изображения TMDB CDN используются штатным artwork helper, отдельно от discovery API.

## Основные локальные ручки вне research/MCP

| Ручки | Назначение |
| --- | --- |
| `GET /api/library`, `POST /api/library`, `PATCH /api/library/{id}` | Библиотека, добавление, статус/реакции/заметки; PATCH like/dislike становится evidence для summary при следующем чтении |
| `POST /api/library/{id}/favorite` | Личное избранное фильма, отдельное от like/dislike |
| `GET /api/people`, `POST /api/people` | Любимые актёры, режиссёры и музыкальные исполнители |
| `POST /api/search/movie`, `/api/search/person`, `/api/resolve/movie`, `/api/resolve/person` | Штатный поиск/уточнение сущности; resolve/person учитывает content_type/role |
| `POST /api/resolve/album` | Разрешение альбома через MusicBrainz |
| `POST /api/library/refresh-tmdb`, `/api/library/{id}/refresh-tmdb`, `/api/people/refresh-tmdb`, `/api/people/{id}/refresh-tmdb` | Массовая или отдельная синхронизация фильмов и персон |
| `POST /api/library/refresh-musicbrainz`, `/api/library/{id}/refresh-musicbrainz`, `/api/people/refresh-musicbrainz`, `/api/people/{id}/refresh-musicbrainz` | Синхронизация музыкальных альбомов и исполнителей |
| `POST /api/recommendations/tmdb`, `/api/recommendations/musicbrainz` | Существующий подбор по авторам/персонам через провайдеров |
| `POST /api/recommendations/llm`, `/api/recommendations/people/llm`, `/api/recommendations/prompt` | LLM-рекомендации контента/персон и предварительный просмотр промпта |
| `GET /api/backlog/moods`, `POST /api/backlog/moods/llm` | Чтение и генерация групп бэклога по настроению |
| `GET /api/recommendations/progress`, `POST /api/operations/cancel` | Стадии обработки и кооперативная остановка операции |
| `GET /api/trash`, `POST /api/trash`, `/api/trash/{id}/restore`, `/api/trash/empty` | Корзина, восстановление и явная очистка; очистка — отдельное разрушительное действие |
| `GET /api/meta`, `/api/artwork/...`, `/api/health` | Метаданные интерфейса, локальный кэш изображений, read-only health для Life Hub |

Точные аргументы существующих ручек определяются `app/server.py` и вызывающими формами `app/static/app.js`; это карта назначения, не новый стабильный внешний API-контракт. Новый films-digest использует `/api/research/call` и его проверяемые схемы, без прямой записи в SQLite.
