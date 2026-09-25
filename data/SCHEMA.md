# SQLite schema

The active datastore is `../data/content-checker/library.sqlite3` in the standard `life-stack` layout. `CONTENT_CHECKER_DATA_DIR` may override the directory. The application initializes missing tables from `app/storage.py`; legacy CSV snapshots are preserved in `legacy/csv/` and are not read at runtime. Movies and music share workflow fields in `content_items`, while provider-specific data remains in separate tables.

## Core tables

- `content_types`: enabled `movie` and `music` content types.
- `content_items`: bilingual titles, workflow status (`backlog`, `consumed`, `dismissed`), reaction (``, `like`, `dislike`), the backlog-only `planned_soon` marker, provenance, notes, original user input in `raw_json`, and timestamps.
- `movies`: release date/year, runtime, separate IMDb, Kinopoisk, and TMDB ratings, vote counts, IMDb/Kinopoisk/TMDB IDs, overview, language, awards, tagline, certification, Metascore, box office, TMDB `poster_path`/`poster_url`, local `poster_local_path`, extended details JSON, and separate successful-check timestamps for TMDB, OMDb, and Kinopoisk.
- `people`: canonical bilingual names, TMDB IDs, original user input in `raw_json`, extended TMDB details JSON, `profile_path`/`profile_url`/`profile_local_path`, and last refresh time.
- `interest_roles`: active actors/directors used for recommendations.
- `movie_people`: directors and matched interest people for each movie.
- `genres` / `movie_genres`: normalized movie genres.
- `content_aliases`: aliases and external mappings used for deduplication.
- `trash_entries`: reversible soft-delete markers for a movie or a person role, plus a compact display snapshot. Active queries exclude marked entities; their normalized data and relationships remain intact.
- `favorite_movies`: independent movie-to-favorite marker and addition timestamp. Adding or removing it does not change movie status, reaction, notes, or recommendation history.
- `albums`: MusicBrainz release groups with first release date, selected release, track count, types, genres/tags, label, country, barcode, media formats, Cover Art Archive `cover_url`, local `cover_path`, ListenBrainz `total_listen_count`, extended details, and provider refresh timestamps. MusicBrainz rating fields are intentionally absent.
- `music_artists`: active recommendation artists and inactive artists discovered through album credits, with MusicBrainz MBID and canonical metadata.
- `album_artists`: ordered album credits and markers for artists in the active interest list.

`awards_json` is a JSON array. A summary-only provider uses:

```json
[{"source":"omdb","summary":"Won 2 Oscars. 15 wins & 30 nominations total"}]
```

A future structured festival provider can add objects such as:

```json
[{"event":"Cannes Film Festival","award":"Palme d'Or","result":"winner","year":2025,"source_url":"https://..."}]
```

TMDB does not expose structured awards. TMDB refresh therefore preserves existing awards unless an auxiliary provider returns new data.

`kinopoisk_id` and `kinopoisk_rating` are filled by Kinopoisk API Unofficial during TMDB enrichment. The provider lookup uses the movie title and a ±1 year window because its IMDb mapping and canonical year may be absent or differ from TMDB; IMDb ID is used as an additional exact match when present in the response. A saved non-empty rating is treated as cached and is not requested again. The public Kinopoisk URL is derived as `https://www.kinopoisk.ru/film/{kinopoisk_id}/`.

`omdb_updated_at` and `kinopoisk_updated_at` are written only after a successful provider response, including a successful response with no matching rating. This distinguishes a confirmed absence from a transient request failure. Missing artwork and ListenBrainz/fanart.tv data follow the same rule through their existing provider timestamps; bulk refresh retries missing local files but does not repeatedly query a confirmed absent remote asset.

`movies.details_json` stores optional provider fields that should not expand the main table: cast, writers, countries, production companies, spoken languages, keywords, budget, revenue, homepage, and provider status. Poster metadata is stored in dedicated columns. The HTTP API flattens optional fields for the movie detail card.

`albums.details_json` stores the selected release status/title, annotation, and track list. An album is modeled as a MusicBrainz `release-group`; `primary_release_mbid` points to the selected official `release` used for edition-specific fields such as track count, label, country, barcode, and media format. `cover_url` stores the 250 px Cover Art Archive release-group URL, while `cover_path` points to a local JPEG under the external `artwork/` directory; image bytes are never stored in SQLite. Movies and TMDB people use the same pattern with `poster_url`/`poster_local_path` and `profile_url`/`profile_local_path`; both request `w185`. An empty URL is a valid cached result when no front image exists, and `cover_art_updated_at` records the last successful availability check. `total_listen_count` is the all-time ListenBrainz popularity count for the release group; `listenbrainz_updated_at` records a successful batch lookup even when the provider returned `null`.
