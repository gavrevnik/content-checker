# Repository instructions

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
