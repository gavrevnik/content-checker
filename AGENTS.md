# Repository instructions

When this checkout is inside `life-stack`, first read the shared [workspace instructions](../AGENTS.md) if they have not already been loaded. This file supplies project-specific overrides. If the shared file is absent in a standalone clone, continue with this file.

## Communication and scope

- Communicate with the user in Russian unless they ask for another language.
- Work only inside this repository unless the user explicitly requests a user-level or system-level change.
- Do not commit, push, force-push, publish, or delete data unless the user explicitly asks for that action.
- Preserve unrelated working-tree changes. This repository is often intentionally dirty between iterations.

## Repository and publishing

- The canonical GitHub repository is https://github.com/gavrevnik/content-checker.
- When the user asks to commit and push, commit the requested current changes, push them to the `master` branch of this repository, and then return the user a link to the updated GitHub `master` branch.

## Application architecture

- The active product scope is movies and music. Keep generic content-type infrastructure extensible, but do not add restaurants or other content types without an explicit request.
- SQLite at `../data/content-checker/library.sqlite3` is the active source of truth in the standard `life-stack` layout. `CONTENT_CHECKER_DATA_DIR` may override the data directory. Do not recreate, overwrite, or migrate the user's live database as part of routine testing.
- Run storage tests against a temporary database. Treat `scripts/migrate_csv_to_sqlite.py --force` as destructive and never run it unless explicitly requested.
- Files under `legacy/` are archival. The active application must not depend on them, and they should not be modified or removed without an explicit request.
- Keep secrets in environment variables or the ignored user-local `SECRETS` file. Never place credentials in tracked source files, logs, test fixtures, or chat output.
- When the frontend/backend contract changes, increment `APP_VERSION` in `app/server.py` so a stale local server is easy to detect.

## Verification

- Automated browser verification is not required by default. Do not start browser automation unless the user explicitly asks for it.
- Prefer focused unit/integration tests, `node --check app/static/app.js`, Python compilation, `git diff --check`, and read-only HTTP smoke checks against localhost.
- Do not invoke paid or external APIs merely for routine verification. Use mocks by default; run a real Codex, TMDB, OMDb, Kinopoisk, MusicBrainz, ListenBrainz, Cover Art Archive, or fanart.tv smoke test only when the integration itself changed and the check is necessary. A real smoke test must not persist recommendations or otherwise mutate the library.
- Keep verification proportional to the change and report any check that could not be run.

## Implementation style

- Follow the existing standard-library Python server and vanilla HTML/CSS/JavaScript architecture unless the user requests a framework change.
- Reuse existing storage, TMDB, OMDb, Kinopoisk, MusicBrainz, ListenBrainz, fanart.tv, artwork, modal, and card helpers instead of creating parallel implementations.
- Preserve the Russian/original naming pair for movies and people throughout storage, API responses, and UI. Preserve canonical MusicBrainz names, MBIDs, release-group identities, and ordered artist credits for music.

## Documentation

- After every significant change, review the relevant `README.md` sections before handing off and update them in the same task when setup, architecture, storage, API contracts, integrations, limitations, or user-facing workflows changed.
- Do not create documentation churn for an internal refactor or small bug fix that leaves documented behavior and operating instructions unchanged.

## Movie AI Digest

- For movie recommendation/digest requests through chat, use [films-digest](skills/films-digest/SKILL.md), the `content-checker` MCP tools and [AI Digest workflow](docs/AI_DIGEST.md). Start with `content_status`, `film_preferences_context` and `library_context`; save requested digests with `ai_digest_save`. If the MCP is not yet visible in the current chat, the identical local tools are available through `/api/research/call`.
- Festival requests default to the main competition (Oscars: Best Picture), including all selected nominees and winners. Festival edition year is distinct from production year. Read category descriptions from `festival_catalog`. Verify title/year/director before selecting a TMDB candidate.
- Use server-produced `movie_ref` and `availability_ref`; keep unresolved films as linked fallback entries. Group chat recommendations by the returned availability confidence; distinguish unknown/not-found, subtitles and audio. Future digital release dates are not evidence of an already available film.
- Saving a user-requested digest authorizes its snapshots, not automatic backlog imports. Creating/testing the MCP itself does not request a real digest. Tests must keep live library data untouched.

- Digests exclude future release_date by default; include_unreleased requires an explicit upcoming-film request. Check library_match; exclude_existing only for explicit new-to-library requests. Keep current membership distinct from the snapshot at creation.
- AI Score uses 70% query relevance and 30% taste. Update all pending personal feedback through film_preferences_save with revision/fingerprints before scoring; never learn tastes from AI Score or public ratings. No background LLM calls.

## Общие коннекторы и Knowledge

- Provider API/transport implementations находятся в [Personal Radar connectors](../personal-radar/connectors/README.md); SQLite, domain filters/dedupe/AI/UI и прикладные MCP остаются здесь. Сохранять обязательные quota adapters; тестировать внешние операции mocks на фактическом общем модуле.
- `personal-radar/knowledge` — канонический каталог долгосрочных знаний. Текущие SQLite-профили, источники/интересы и реакции сохраняются без автоматической миграции. Следовать [границам владения](../personal-radar/docs/storage-boundaries.md); не добавлять auto outbox, GitHub writes или двусторонний sync из пользовательских действий.
- Discovery возвращает source-кандидатов: name/URL/platform/external IDs/description/provenance/evidence/relevance. Review/deduplication по стабильной identity предшествуют потенциальному переносу подтверждённых источников в Knowledge отдельным запросом. Разрешённые локальные импорты продолжают использовать прежние tools/flags/budgets; discovery/digest не разрешают запись Knowledge.
