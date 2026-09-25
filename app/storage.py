from __future__ import annotations

import json
import re
import sqlite3
import threading
import unicodedata
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.paths import DATA_DIR, ROOT

DB_PATH = DATA_DIR / "library.sqlite3"

VALID_STATUSES = {"backlog", "consumed", "dismissed"}
VALID_REACTIONS = {"", "like", "dislike"}
_lock = threading.RLock()

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS content_types (
    code TEXT PRIMARY KEY,
    name_ru TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1))
);

CREATE TABLE IF NOT EXISTS content_items (
    id TEXT PRIMARY KEY,
    content_type TEXT NOT NULL REFERENCES content_types(code),
    title_ru TEXT NOT NULL,
    title_original TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'backlog' CHECK (status IN ('backlog', 'consumed', 'dismissed')),
    reaction TEXT NOT NULL DEFAULT '' CHECK (reaction IN ('', 'like', 'dislike')),
    planned_soon INTEGER NOT NULL DEFAULT 0 CHECK (planned_soon IN (0, 1)),
    source TEXT NOT NULL DEFAULT 'manual',
    source_url TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    added_at TEXT NOT NULL,
    consumed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS movies (
    content_id TEXT PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
    release_date TEXT,
    release_year INTEGER,
    runtime_minutes INTEGER,
    imdb_rating REAL,
    kinopoisk_rating REAL,
    tmdb_rating REAL,
    tmdb_vote_count INTEGER,
    imdb_id TEXT,
    kinopoisk_id INTEGER,
    tmdb_id INTEGER UNIQUE,
    overview TEXT NOT NULL DEFAULT '',
    original_language TEXT NOT NULL DEFAULT '',
    awards_json TEXT NOT NULL DEFAULT '[]',
    tagline TEXT NOT NULL DEFAULT '',
    content_rating TEXT NOT NULL DEFAULT '',
    imdb_votes TEXT NOT NULL DEFAULT '',
    metascore INTEGER,
    box_office TEXT NOT NULL DEFAULT '',
    poster_path TEXT NOT NULL DEFAULT '',
    poster_url TEXT NOT NULL DEFAULT '',
    poster_local_path TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    tmdb_updated_at TEXT,
    omdb_updated_at TEXT,
    kinopoisk_updated_at TEXT
);

CREATE TABLE IF NOT EXISTS albums (
    content_id TEXT PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
    release_group_mbid TEXT UNIQUE,
    primary_release_mbid TEXT,
    first_release_date TEXT,
    release_year INTEGER,
    track_count INTEGER,
    primary_type TEXT NOT NULL DEFAULT 'Album',
    secondary_types_json TEXT NOT NULL DEFAULT '[]',
    genres_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    total_listen_count INTEGER,
    listenbrainz_updated_at TEXT,
    disambiguation TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT '',
    catalog_number TEXT NOT NULL DEFAULT '',
    barcode TEXT NOT NULL DEFAULT '',
    media_formats TEXT NOT NULL DEFAULT '',
    cover_url TEXT NOT NULL DEFAULT '',
    cover_path TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    musicbrainz_updated_at TEXT,
    cover_art_updated_at TEXT
);

CREATE TABLE IF NOT EXISTS music_artists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    sort_name TEXT NOT NULL DEFAULT '',
    mbid TEXT UNIQUE,
    artist_type TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    area TEXT NOT NULL DEFAULT '',
    disambiguation TEXT NOT NULL DEFAULT '',
    life_span_begin TEXT NOT NULL DEFAULT '',
    life_span_end TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    raw_json TEXT NOT NULL DEFAULT '{}',
    details_json TEXT NOT NULL DEFAULT '{}',
    musicbrainz_updated_at TEXT,
    profile_url TEXT NOT NULL DEFAULT '',
    profile_local_path TEXT NOT NULL DEFAULT '',
    fanart_updated_at TEXT
);

CREATE TABLE IF NOT EXISTS album_artists (
    album_id TEXT NOT NULL REFERENCES albums(content_id) ON DELETE CASCADE,
    artist_id TEXT NOT NULL REFERENCES music_artists(id) ON DELETE CASCADE,
    credit_name TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    is_interest INTEGER NOT NULL DEFAULT 0 CHECK (is_interest IN (0, 1)),
    PRIMARY KEY (album_id, artist_id)
);

CREATE TABLE IF NOT EXISTS people (
    id TEXT PRIMARY KEY,
    name_original TEXT NOT NULL,
    name_ru TEXT NOT NULL,
    tmdb_id INTEGER UNIQUE,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    raw_json TEXT NOT NULL DEFAULT '{}',
    details_json TEXT NOT NULL DEFAULT '{}',
    profile_path TEXT NOT NULL DEFAULT '',
    profile_url TEXT NOT NULL DEFAULT '',
    profile_local_path TEXT NOT NULL DEFAULT '',
    tmdb_updated_at TEXT
);

CREATE TABLE IF NOT EXISTS interest_roles (
    person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    content_type TEXT NOT NULL REFERENCES content_types(code),
    role TEXT NOT NULL CHECK (role IN ('actor', 'director')),
    notes TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (person_id, content_type, role)
);

CREATE TABLE IF NOT EXISTS movie_people (
    movie_id TEXT NOT NULL REFERENCES movies(content_id) ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    credit_role TEXT NOT NULL CHECK (credit_role IN ('actor', 'director')),
    character_name TEXT NOT NULL DEFAULT '',
    job TEXT NOT NULL DEFAULT '',
    is_interest INTEGER NOT NULL DEFAULT 0 CHECK (is_interest IN (0, 1)),
    PRIMARY KEY (movie_id, person_id, credit_role)
);

CREATE TABLE IF NOT EXISTS genres (
    id INTEGER PRIMARY KEY,
    name_ru TEXT NOT NULL,
    name_original TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS movie_genres (
    movie_id TEXT NOT NULL REFERENCES movies(content_id) ON DELETE CASCADE,
    genre_id INTEGER NOT NULL REFERENCES genres(id),
    PRIMARY KEY (movie_id, genre_id)
);

CREATE TABLE IF NOT EXISTS content_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id TEXT NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    external_id TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    UNIQUE (content_id, alias, provider, external_id)
);

CREATE TABLE IF NOT EXISTS trash_entries (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('movie', 'album', 'person', 'music_artist')),
    entity_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    trashed_at TEXT NOT NULL,
    UNIQUE (entity_type, entity_id, role)
);

CREATE TABLE IF NOT EXISTS favorite_movies (
    content_id TEXT PRIMARY KEY REFERENCES content_items(id) ON DELETE CASCADE,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backlog_moods (
    content_type TEXT NOT NULL REFERENCES content_types(code),
    label TEXT NOT NULL,
    position INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (content_type, label)
);

CREATE TABLE IF NOT EXISTS backlog_mood_items (
    content_type TEXT NOT NULL,
    mood_label TEXT NOT NULL,
    item_id TEXT NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    PRIMARY KEY (content_type, mood_label, item_id),
    FOREIGN KEY (content_type, mood_label) REFERENCES backlog_moods(content_type, label) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_content_state ON content_items(content_type, status, reaction);
CREATE INDEX IF NOT EXISTS idx_movie_people_interest ON movie_people(movie_id, is_interest);
CREATE INDEX IF NOT EXISTS idx_album_artists_interest ON album_artists(album_id, is_interest);
CREATE INDEX IF NOT EXISTS idx_alias_value ON content_aliases(alias);
CREATE INDEX IF NOT EXISTS idx_trash_entity ON trash_entries(entity_type, entity_id, role);
CREATE INDEX IF NOT EXISTS idx_backlog_mood_item ON backlog_mood_items(item_id);
"""


class StorageError(ValueError):
    pass


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").casefold()
    return re.sub(r"[^\w]+", "", value)


def _json(value: Any, default: str) -> str:
    if value in (None, ""):
        return default
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except json.JSONDecodeError:
            return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=20, factory=ClosingConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 20000")
    return connection


def initialize_database(path: Path | None = None) -> None:
    with _lock, connect(path) as connection:
        connection.executescript(SCHEMA)
        needs_artwork_backfill = not connection.execute(
            "SELECT 1 FROM schema_version WHERE version = 9"
        ).fetchone()
        needs_person_artwork_backfill = not connection.execute(
            "SELECT 1 FROM schema_version WHERE version = 10"
        ).fetchone()
        trash_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'trash_entries'"
        ).fetchone()
        trash_sql = str(trash_sql_row["sql"] or "") if trash_sql_row else ""
        if "music_artist" not in trash_sql:
            connection.executescript(
                """
                ALTER TABLE trash_entries RENAME TO trash_entries_before_music;
                CREATE TABLE trash_entries (
                    id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL CHECK (entity_type IN ('movie', 'album', 'person', 'music_artist')),
                    entity_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT '',
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    trashed_at TEXT NOT NULL,
                    UNIQUE (entity_type, entity_id, role)
                );
                INSERT INTO trash_entries(id, entity_type, entity_id, role, snapshot_json, trashed_at)
                SELECT id, entity_type, entity_id, role, snapshot_json, trashed_at
                FROM trash_entries_before_music;
                DROP TABLE trash_entries_before_music;
                CREATE INDEX IF NOT EXISTS idx_trash_entity ON trash_entries(entity_type, entity_id, role);
                """
            )
        migrations = {
            "content_items": {
                "raw_json": "TEXT NOT NULL DEFAULT '{}'",
                "planned_soon": "INTEGER NOT NULL DEFAULT 0 CHECK (planned_soon IN (0, 1))",
            },
            "movies": {
                "kinopoisk_rating": "REAL",
                "kinopoisk_id": "INTEGER",
                "tagline": "TEXT NOT NULL DEFAULT ''",
                "content_rating": "TEXT NOT NULL DEFAULT ''",
                "imdb_votes": "TEXT NOT NULL DEFAULT ''",
                "metascore": "INTEGER",
                "box_office": "TEXT NOT NULL DEFAULT ''",
                "poster_path": "TEXT NOT NULL DEFAULT ''",
                "poster_url": "TEXT NOT NULL DEFAULT ''",
                "poster_local_path": "TEXT NOT NULL DEFAULT ''",
                "details_json": "TEXT NOT NULL DEFAULT '{}'",
                "omdb_updated_at": "TEXT",
                "kinopoisk_updated_at": "TEXT",
            },
            "people": {
                "raw_json": "TEXT NOT NULL DEFAULT '{}'",
                "details_json": "TEXT NOT NULL DEFAULT '{}'",
                "profile_path": "TEXT NOT NULL DEFAULT ''",
                "profile_url": "TEXT NOT NULL DEFAULT ''",
                "profile_local_path": "TEXT NOT NULL DEFAULT ''",
                "tmdb_updated_at": "TEXT",
            },
            "albums": {
                "cover_url": "TEXT NOT NULL DEFAULT ''",
                "cover_path": "TEXT NOT NULL DEFAULT ''",
                "cover_art_updated_at": "TEXT",
                "total_listen_count": "INTEGER",
                "listenbrainz_updated_at": "TEXT",
            },
            "music_artists": {
                "profile_url": "TEXT NOT NULL DEFAULT ''",
                "profile_local_path": "TEXT NOT NULL DEFAULT ''",
                "fanart_updated_at": "TEXT",
            },
        }
        for table, columns in migrations.items():
            existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
            for column, definition in columns.items():
                if column not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        album_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(albums)")
        }
        for obsolete_column in ("rating", "rating_votes"):
            if obsolete_column in album_columns:
                connection.execute(f"ALTER TABLE albums DROP COLUMN {obsolete_column}")
        if needs_artwork_backfill:
            for row in connection.execute(
                "SELECT content_id, details_json, poster_path, poster_url FROM movies"
            ).fetchall():
                try:
                    details = json.loads(row["details_json"] or "{}")
                except json.JSONDecodeError:
                    details = {}
                if not isinstance(details, dict):
                    continue
                poster_url = str(row["poster_url"] or details.get("poster_url") or "")
                poster_path = str(row["poster_path"] or details.get("poster_path") or "")
                if not poster_path and "/w500/" in poster_url:
                    poster_path = f"/{poster_url.split('/w500/', 1)[1].lstrip('/')}"
                connection.execute(
                    "UPDATE movies SET poster_path = ?, poster_url = ? WHERE content_id = ?",
                    (poster_path, poster_url, row["content_id"]),
                )
        if needs_person_artwork_backfill:
            for row in connection.execute(
                "SELECT id, details_json, profile_path, profile_url FROM people"
            ).fetchall():
                try:
                    details = json.loads(row["details_json"] or "{}")
                except json.JSONDecodeError:
                    details = {}
                if not isinstance(details, dict):
                    continue
                profile_url = str(row["profile_url"] or details.get("profile_url") or "")
                if "image.tmdb.org/t/p/" in profile_url:
                    profile_url = re.sub(r"(/t/p/)[^/]+/", r"\g<1>w185/", profile_url, count=1)
                profile_path = str(row["profile_path"] or details.get("profile_path") or "")
                match = re.search(r"/t/p/[^/]+(/[^?#]+)", profile_url)
                if not profile_path and match:
                    profile_path = match.group(1)
                connection.execute(
                    "UPDATE people SET profile_path = ?, profile_url = ? WHERE id = ?",
                    (profile_path, profile_url, row["id"]),
                )
        connection.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (1, ?)", (_now(),)
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (2, ?)", (_now(),)
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (3, ?)", (_now(),)
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (4, ?)", (_now(),)
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (5, ?)", (_now(),)
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (6, ?)", (_now(),)
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (7, ?)", (_now(),)
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (8, ?)", (_now(),)
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (9, ?)", (_now(),)
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (10, ?)", (_now(),)
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (11, ?)", (_now(),)
        )
        connection.execute(
            "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (12, ?)", (_now(),)
        )
        connection.execute(
            "INSERT OR IGNORE INTO content_types(code, name_ru, enabled) VALUES ('movie', 'Фильмы', 1)"
        )
        connection.execute(
            "INSERT INTO content_types(code, name_ru, enabled) VALUES ('music', 'Музыка', 1) "
            "ON CONFLICT(code) DO UPDATE SET name_ru = excluded.name_ru, enabled = 1"
        )


def _movie_select(where: str = "", params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    initialize_database()
    query = f"""
        SELECT
            i.id, i.content_type, i.title_ru, i.title_original, i.status, i.reaction,
            COALESCE(i.planned_soon, 0) AS planned_soon,
            i.source, i.source_url AS url, i.notes, COALESCE(i.raw_json, '{{}}') AS raw_json,
            i.metadata_json, i.added_at,
            COALESCE(i.consumed_at, '') AS consumed_at, i.updated_at,
            COALESCE(m.release_date, '') AS release_date,
            COALESCE(m.release_year, '') AS year,
            COALESCE(m.runtime_minutes, '') AS duration_minutes,
            COALESCE(m.imdb_rating, '') AS imdb_rating,
            COALESCE(m.kinopoisk_rating, '') AS kinopoisk_rating,
            COALESCE(m.tmdb_rating, '') AS tmdb_rating,
            COALESCE(m.tmdb_vote_count, '') AS tmdb_vote_count,
            COALESCE(m.imdb_id, '') AS imdb_id,
            COALESCE(m.kinopoisk_id, '') AS kinopoisk_id,
            COALESCE(m.tmdb_id, '') AS tmdb_id,
            COALESCE(m.overview, '') AS overview,
            COALESCE(m.original_language, '') AS original_language,
            COALESCE(m.awards_json, '[]') AS awards_json,
            COALESCE(m.tagline, '') AS tagline,
            COALESCE(m.content_rating, '') AS content_rating,
            COALESCE(m.imdb_votes, '') AS imdb_votes,
            COALESCE(m.metascore, '') AS metascore,
            COALESCE(m.box_office, '') AS box_office,
            COALESCE(m.poster_path, '') AS stored_poster_path,
            COALESCE(m.poster_url, '') AS stored_poster_url,
            COALESCE(m.poster_local_path, '') AS stored_poster_local_path,
            COALESCE(m.details_json, '{{}}') AS details_json,
            COALESCE(m.tmdb_updated_at, '') AS tmdb_updated_at,
            COALESCE(m.omdb_updated_at, '') AS omdb_updated_at,
            COALESCE(m.kinopoisk_updated_at, '') AS kinopoisk_updated_at,
            EXISTS(SELECT 1 FROM favorite_movies f WHERE f.content_id = i.id) AS favorite,
            COALESCE((
                SELECT group_concat(display_name, '; ')
                FROM (
                    SELECT CASE
                        WHEN p.name_ru = '' THEN p.name_original
                        WHEN p.name_original = '' OR p.name_ru = p.name_original THEN p.name_ru
                        ELSE p.name_ru || ' (' || p.name_original || ')'
                    END AS display_name
                    FROM movie_people mp JOIN people p ON p.id = mp.person_id
                    WHERE mp.movie_id = i.id AND mp.credit_role = 'director'
                    ORDER BY p.name_ru, p.name_original
                )
            ), '') AS directors,
            COALESCE((
                SELECT group_concat(name_ru, '; ')
                FROM (
                    SELECT g.name_ru
                    FROM movie_genres mg JOIN genres g ON g.id = mg.genre_id
                    WHERE mg.movie_id = i.id
                    ORDER BY g.name_ru
                )
            ), '') AS genres,
            COALESCE((
                SELECT group_concat(display_name, '; ')
                FROM (
                    SELECT CASE
                        WHEN p.name_ru = '' THEN p.name_original
                        WHEN p.name_original = '' OR p.name_ru = p.name_original THEN p.name_ru
                        ELSE p.name_ru || ' (' || p.name_original || ')'
                    END AS display_name
                    FROM movie_people mp JOIN people p ON p.id = mp.person_id
                    WHERE mp.movie_id = i.id AND mp.is_interest = 1 AND mp.credit_role = 'actor'
                    ORDER BY p.name_ru, p.name_original
                )
            ), '') AS key_actors,
            COALESCE((
                SELECT group_concat(display_name, '; ')
                FROM (
                    SELECT CASE
                        WHEN p.name_ru = '' THEN p.name_original
                        WHEN p.name_original = '' OR p.name_ru = p.name_original THEN p.name_ru
                        ELSE p.name_ru || ' (' || p.name_original || ')'
                    END AS display_name
                    FROM movie_people mp JOIN people p ON p.id = mp.person_id
                    WHERE mp.movie_id = i.id AND mp.is_interest = 1 AND mp.credit_role = 'director'
                    ORDER BY p.name_ru, p.name_original
                )
            ), '') AS key_directors
        FROM content_items i
        JOIN movies m ON m.content_id = i.id
        {where}
        ORDER BY COALESCE(m.release_date, printf('%04d', m.release_year), '') DESC, i.title_ru
    """
    with connect() as connection:
        results = [dict(row) for row in connection.execute(query, params).fetchall()]
    for result in results:
        try:
            details = json.loads(result.get("details_json") or "{}")
        except json.JSONDecodeError:
            details = {}
        if isinstance(details, dict):
            result.update(details)
        result["poster_path"] = result.pop("stored_poster_path", "")
        result["poster_url"] = result.pop("stored_poster_url", "")
        result["poster_local_path"] = result.pop("stored_poster_local_path", "")
        result["favorite"] = bool(result.get("favorite"))
        result["planned_soon"] = bool(result.get("planned_soon"))
        imdb_id = str(result.get("imdb_id") or "")
        tmdb_id = str(result.get("tmdb_id") or "")
        kinopoisk_id = str(result.get("kinopoisk_id") or "")
        result["imdb_link"] = f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else ""
        result["tmdb_link"] = f"https://www.themoviedb.org/movie/{tmdb_id}" if tmdb_id else ""
        result["kinopoisk_link"] = f"https://www.kinopoisk.ru/film/{kinopoisk_id}/" if kinopoisk_id else ""
        result["external_link"] = result["imdb_link"] or result["tmdb_link"] or str(result.get("url") or "")
        key_parts = []
        if result.get("key_actors"):
            key_parts.append(f"Актёры: {result['key_actors']}")
        if result.get("key_directors"):
            key_parts.append(f"Режиссёры: {result['key_directors']}")
        result["key_people"] = "; ".join(key_parts)
    return results


def _album_select(where: str = "", params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    initialize_database()
    query = f"""
        SELECT
            i.id, i.content_type, i.title_ru, i.title_original, i.status, i.reaction,
            COALESCE(i.planned_soon, 0) AS planned_soon,
            i.source, i.source_url AS url, i.notes, COALESCE(i.raw_json, '{{}}') AS raw_json,
            i.metadata_json, i.added_at, COALESCE(i.consumed_at, '') AS consumed_at, i.updated_at,
            COALESCE(a.release_group_mbid, '') AS release_group_mbid,
            COALESCE(a.primary_release_mbid, '') AS primary_release_mbid,
            COALESCE(a.first_release_date, '') AS first_release_date,
            COALESCE(a.first_release_date, '') AS release_date,
            COALESCE(a.release_year, '') AS year,
            COALESCE(a.track_count, '') AS track_count,
            COALESCE(a.primary_type, 'Album') AS primary_type,
            COALESCE(a.secondary_types_json, '[]') AS secondary_types_json,
            COALESCE(a.genres_json, '[]') AS genres_json,
            COALESCE(a.tags_json, '[]') AS tags_json,
            a.total_listen_count AS total_listen_count,
            COALESCE(a.listenbrainz_updated_at, '') AS listenbrainz_updated_at,
            COALESCE(a.disambiguation, '') AS disambiguation,
            COALESCE(a.country, '') AS country,
            COALESCE(a.label, '') AS label,
            COALESCE(a.catalog_number, '') AS catalog_number,
            COALESCE(a.barcode, '') AS barcode,
            COALESCE(a.media_formats, '') AS media_formats,
            COALESCE(a.cover_url, '') AS cover_url,
            COALESCE(a.cover_path, '') AS cover_path,
            COALESCE(a.details_json, '{{}}') AS details_json,
            COALESCE(a.musicbrainz_updated_at, '') AS musicbrainz_updated_at,
            COALESCE(a.cover_art_updated_at, '') AS cover_art_updated_at,
            EXISTS(SELECT 1 FROM favorite_movies f WHERE f.content_id = i.id) AS favorite,
            COALESCE((
                SELECT group_concat(display_name, '; ')
                FROM (
                    SELECT CASE
                        WHEN aa.credit_name <> '' THEN aa.credit_name
                        ELSE ma.name
                    END AS display_name
                    FROM album_artists aa JOIN music_artists ma ON ma.id = aa.artist_id
                    WHERE aa.album_id = i.id
                    ORDER BY aa.position, ma.name
                )
            ), '') AS artists,
            COALESCE((
                SELECT group_concat(ma.mbid, ';')
                FROM album_artists aa JOIN music_artists ma ON ma.id = aa.artist_id
                WHERE aa.album_id = i.id
                ORDER BY aa.position, ma.name
            ), '') AS artist_mbids
        FROM content_items i
        JOIN albums a ON a.content_id = i.id
        {where}
        ORDER BY COALESCE(a.first_release_date, printf('%04d', a.release_year), '') DESC, i.title_original
    """
    with connect() as connection:
        results = [dict(row) for row in connection.execute(query, params).fetchall()]
    for result in results:
        for source_key, target_key in (("genres_json", "genres"), ("tags_json", "tags")):
            try:
                values = json.loads(result.get(source_key) or "[]")
            except json.JSONDecodeError:
                values = []
            if isinstance(values, list):
                result[target_key] = "; ".join(
                    str(value.get("name") if isinstance(value, dict) else value)
                    for value in values
                    if (value.get("name") if isinstance(value, dict) else value)
                )
            else:
                result[target_key] = ""
        try:
            details = json.loads(result.get("details_json") or "{}")
        except json.JSONDecodeError:
            details = {}
        if isinstance(details, dict):
            result.update(details)
        mbid = str(result.get("release_group_mbid") or "")
        result["musicbrainz_link"] = f"https://musicbrainz.org/release-group/{mbid}" if mbid else ""
        result["external_link"] = result["musicbrainz_link"] or str(result.get("url") or "")
        result["favorite"] = bool(result.get("favorite"))
        result["planned_soon"] = bool(result.get("planned_soon"))
    return results


def list_library(
    content_type: str | None = None,
    status: str | None = None,
    include_trashed: bool = False,
) -> list[dict[str, Any]]:
    if content_type == "music":
        clauses = ["i.content_type = 'music'"]
        params: list[Any] = []
        if status:
            clauses.append("i.status = ?")
            params.append(status)
        if not include_trashed:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM trash_entries t "
                "WHERE t.entity_type = 'album' AND t.entity_id = i.id)"
            )
        return _album_select("WHERE " + " AND ".join(clauses), tuple(params))
    clauses = ["i.content_type = 'movie'"]
    params: list[Any] = []
    if content_type:
        clauses.append("i.content_type = ?")
        params.append(content_type)
    if status:
        clauses.append("i.status = ?")
        params.append(status)
    if not include_trashed:
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM trash_entries t "
            "WHERE t.entity_type = 'movie' AND t.entity_id = i.id)"
        )
    return _movie_select("WHERE " + " AND ".join(clauses), tuple(params))


def replace_backlog_moods(content_type: str, moods: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if content_type not in {"movie", "music"}:
        raise StorageError("Unknown content type")
    initialize_database()
    now = _now()
    with _lock, connect() as connection:
        backlog_ids = {
            str(row["id"])
            for row in connection.execute(
                "SELECT id FROM content_items WHERE content_type = ? AND status = 'backlog'",
                (content_type,),
            )
        }
        prepared: list[tuple[str, list[str]]] = []
        seen_labels: set[str] = set()
        for mood in moods[:15]:
            if not isinstance(mood, dict):
                continue
            label = str(mood.get("label") or "").strip()[:80]
            normalized = label.casefold()
            raw_ids = mood.get("item_ids")
            if not label or normalized in seen_labels or not isinstance(raw_ids, list):
                continue
            item_ids = list(dict.fromkeys(
                str(item_id) for item_id in raw_ids if str(item_id) in backlog_ids
            ))
            if not item_ids:
                continue
            prepared.append((label, item_ids))
            seen_labels.add(normalized)
        if not prepared:
            raise StorageError("No valid backlog mood mappings")
        connection.execute("DELETE FROM backlog_moods WHERE content_type = ?", (content_type,))
        for mood_position, (label, item_ids) in enumerate(prepared):
            connection.execute(
                "INSERT INTO backlog_moods(content_type,label,position,updated_at) VALUES (?,?,?,?)",
                (content_type, label, mood_position, now),
            )
            connection.executemany(
                "INSERT INTO backlog_mood_items(content_type,mood_label,item_id,position) VALUES (?,?,?,?)",
                ((content_type, label, item_id, item_position) for item_position, item_id in enumerate(item_ids)),
            )
    return list_backlog_moods(content_type)


def list_backlog_moods(content_type: str) -> list[dict[str, Any]]:
    if content_type not in {"movie", "music"}:
        raise StorageError("Unknown content type")
    initialize_database()
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT m.label, m.position AS mood_position, m.updated_at,
                   mi.item_id, mi.position AS item_position
            FROM backlog_moods m
            JOIN backlog_mood_items mi
              ON mi.content_type = m.content_type AND mi.mood_label = m.label
            JOIN content_items i ON i.id = mi.item_id
            WHERE m.content_type = ? AND i.content_type = ? AND i.status = 'backlog'
            ORDER BY m.position, mi.position
            """,
            (content_type, content_type),
        ).fetchall()
    moods: list[dict[str, Any]] = []
    by_label: dict[str, dict[str, Any]] = {}
    for row in rows:
        label = str(row["label"])
        mood = by_label.get(label)
        if mood is None:
            mood = {"label": label, "item_ids": [], "updated_at": str(row["updated_at"])}
            by_label[label] = mood
            moods.append(mood)
        mood["item_ids"].append(str(row["item_id"]))
    return moods


def get_item(item_id: str) -> dict[str, Any]:
    rows = _movie_select("WHERE i.id = ?", (item_id,))
    if not rows:
        rows = _album_select("WHERE i.id = ?", (item_id,))
    if not rows:
        raise StorageError("Item not found")
    return rows[0]


def set_favorite(item_id: str, favorite: bool) -> dict[str, Any]:
    initialize_database()
    with _lock, connect() as connection:
        if not connection.execute(
            "SELECT 1 FROM content_items WHERE id = ? AND content_type IN ('movie', 'music')", (item_id,)
        ).fetchone():
            raise StorageError("Library item not found")
        if favorite:
            connection.execute(
                "INSERT INTO favorite_movies(content_id, added_at) VALUES (?, ?) "
                "ON CONFLICT(content_id) DO NOTHING",
                (item_id, _now()),
            )
        else:
            connection.execute("DELETE FROM favorite_movies WHERE content_id = ?", (item_id,))
    return get_item(item_id)


def list_interests(
    content_type: str | None = None, role: str | None = None, include_trashed: bool = False,
) -> list[dict[str, Any]]:
    if content_type == "music":
        return list_music_artists(include_trashed=include_trashed)
    initialize_database()
    clauses = ["p.active = 1"]
    if not include_trashed:
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM trash_entries t WHERE t.entity_type = 'person' "
            "AND t.entity_id = p.id AND t.role = ir.role)"
        )
    params: list[Any] = []
    if content_type:
        clauses.append("ir.content_type = ?")
        params.append(content_type)
    if role:
        clauses.append("ir.role = ?")
        params.append(role)
    query = f"""
        SELECT p.id, ir.content_type, ir.role, p.name_original, p.name_ru,
               'tmdb' AS provider, COALESCE(p.tmdb_id, '') AS external_id,
               COALESCE(p.tmdb_id, '') AS tmdb_id, p.active, ir.notes,
               COALESCE(p.profile_path, '') AS profile_path,
               COALESCE(p.profile_url, '') AS profile_url,
               COALESCE(p.profile_local_path, '') AS profile_local_path,
               COALESCE(p.raw_json, '{{}}') AS raw_json,
               COALESCE(p.details_json, '{{}}') AS details_json,
               COALESCE(p.tmdb_updated_at, '') AS tmdb_updated_at
        FROM people p JOIN interest_roles ir ON ir.person_id = p.id
        WHERE {' AND '.join(clauses)}
        ORDER BY ir.role, p.name_original
    """
    with connect() as connection:
        return [dict(row) for row in connection.execute(query, tuple(params)).fetchall()]


def list_music_artists(include_trashed: bool = False) -> list[dict[str, Any]]:
    initialize_database()
    clauses = ["ma.active = 1"]
    if not include_trashed:
        clauses.append(
            "NOT EXISTS (SELECT 1 FROM trash_entries t "
            "WHERE t.entity_type = 'music_artist' AND t.entity_id = ma.id)"
        )
    query = f"""
        SELECT ma.id, 'music' AS content_type, 'artist' AS role,
               ma.name AS name_original, ma.name AS name_ru,
               'musicbrainz' AS provider, COALESCE(ma.mbid, '') AS external_id,
               COALESCE(ma.mbid, '') AS mbid, ma.sort_name, ma.artist_type,
               ma.country, ma.area, ma.disambiguation, ma.life_span_begin,
               ma.life_span_end, ma.active, COALESCE(ma.raw_json, '{{}}') AS raw_json,
               COALESCE(ma.details_json, '{{}}') AS details_json,
               COALESCE(ma.musicbrainz_updated_at, '') AS musicbrainz_updated_at,
               COALESCE(ma.profile_url, '') AS profile_url,
               COALESCE(ma.profile_local_path, '') AS profile_local_path,
               COALESCE(ma.fanart_updated_at, '') AS fanart_updated_at
        FROM music_artists ma
        WHERE {' AND '.join(clauses)}
        ORDER BY ma.sort_name, ma.name
    """
    with connect() as connection:
        return [dict(row) for row in connection.execute(query).fetchall()]


def get_interest_person(person_id: str) -> dict[str, Any]:
    person = next(
        (row for row in list_interests("movie", include_trashed=True) if str(row["id"]) == str(person_id)),
        None,
    )
    if not person:
        raise StorageError("Person not found")
    return person


def get_music_artist(artist_id: str) -> dict[str, Any]:
    artist = next(
        (row for row in list_music_artists(include_trashed=True) if str(row["id"]) == str(artist_id)),
        None,
    )
    if not artist:
        raise StorageError("Artist not found")
    return artist


def list_trash() -> list[dict[str, Any]]:
    initialize_database()
    with connect() as connection:
        rows = connection.execute(
            "SELECT id, entity_type, entity_id, role, snapshot_json, trashed_at "
            "FROM trash_entries ORDER BY trashed_at DESC"
        ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        result = dict(row)
        try:
            snapshot = json.loads(result.pop("snapshot_json") or "{}")
        except json.JSONDecodeError:
            snapshot = {}
        if isinstance(snapshot, dict):
            result.update(snapshot)
        results.append(result)
    return results


def trash_entity(payload: dict[str, Any]) -> dict[str, Any]:
    entity_type = str(payload.get("entity_type") or "").strip()
    entity_id = str(payload.get("entity_id") or payload.get("id") or "").strip()
    role = str(payload.get("role") or "").strip() if entity_type == "person" else ""
    if entity_type not in {"movie", "album", "person", "music_artist"} or not entity_id:
        raise StorageError("Movie, album, person, or artist is required")
    if entity_type == "person" and role not in {"actor", "director"}:
        raise StorageError("Person role must be actor or director")
    initialize_database()
    trash_id = f"trash-{uuid.uuid4().hex[:12]}"
    with _lock, connect() as connection:
        if entity_type == "movie":
            row = connection.execute(
                "SELECT i.title_ru, i.title_original, COALESCE(m.release_year, '') AS year "
                "FROM content_items i JOIN movies m ON m.content_id = i.id WHERE i.id = ?",
                (entity_id,),
            ).fetchone()
        elif entity_type == "album":
            row = connection.execute(
                "SELECT i.title_ru, i.title_original, COALESCE(a.release_year, '') AS year, "
                "'music' AS content_type, COALESCE((SELECT group_concat(ma.name, '; ') "
                "FROM album_artists aa JOIN music_artists ma ON ma.id = aa.artist_id "
                "WHERE aa.album_id = i.id), '') AS artists "
                "FROM content_items i JOIN albums a ON a.content_id = i.id WHERE i.id = ?",
                (entity_id,),
            ).fetchone()
        elif entity_type == "music_artist":
            row = connection.execute(
                "SELECT name AS name_ru, name AS name_original, COALESCE(mbid, '') AS mbid, "
                "'music' AS content_type FROM music_artists WHERE id = ?",
                (entity_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT p.name_ru, p.name_original, COALESCE(p.tmdb_id, '') AS tmdb_id "
                "FROM people p JOIN interest_roles ir ON ir.person_id = p.id "
                "WHERE p.id = ? AND ir.content_type = 'movie' AND ir.role = ?",
                (entity_id, role),
            ).fetchone()
        if not row:
            raise StorageError("Movie or person not found")
        try:
            connection.execute(
                "INSERT INTO trash_entries(id, entity_type, entity_id, role, snapshot_json, trashed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (trash_id, entity_type, entity_id, role, _json(dict(row), "{}"), _now()),
            )
        except sqlite3.IntegrityError as error:
            raise StorageError("This item is already in trash") from error
    return next(item for item in list_trash() if item["id"] == trash_id)


def restore_trash(trash_id: str) -> dict[str, Any]:
    initialize_database()
    with _lock, connect() as connection:
        row = connection.execute(
            "SELECT id, entity_type, entity_id, role, snapshot_json, trashed_at "
            "FROM trash_entries WHERE id = ?",
            (trash_id,),
        ).fetchone()
        if not row:
            raise StorageError("Trash item not found")
        result = dict(row)
        connection.execute("DELETE FROM trash_entries WHERE id = ?", (trash_id,))
    try:
        snapshot = json.loads(result.pop("snapshot_json") or "{}")
    except json.JSONDecodeError:
        snapshot = {}
    if isinstance(snapshot, dict):
        result.update(snapshot)
    return result


def empty_trash() -> dict[str, Any]:
    """Permanently remove every trashed object and its unreferenced cached artwork."""
    from app import artwork

    initialize_database()
    artwork_paths: set[str] = set()
    by_type = {entity_type: 0 for entity_type in ("movie", "album", "person", "music_artist")}
    with _lock, connect() as connection:
        entries = connection.execute(
            "SELECT entity_type, entity_id, role FROM trash_entries"
        ).fetchall()
        if not entries:
            return {
                "deleted": 0, "by_type": by_type, "artwork_deleted": 0,
                "artwork_errors": [],
            }
        for entry in entries:
            by_type[str(entry["entity_type"])] += 1

        artwork_rows = connection.execute(
            "SELECT m.poster_local_path AS path FROM trash_entries t "
            "JOIN movies m ON m.content_id = t.entity_id WHERE t.entity_type = 'movie' "
            "UNION "
            "SELECT a.cover_path AS path FROM trash_entries t "
            "JOIN albums a ON a.content_id = t.entity_id WHERE t.entity_type = 'album' "
            "UNION "
            "SELECT p.profile_local_path AS path FROM trash_entries t "
            "JOIN people p ON p.id = t.entity_id WHERE t.entity_type = 'person' "
            "UNION "
            "SELECT ma.profile_local_path AS path FROM trash_entries t "
            "JOIN music_artists ma ON ma.id = t.entity_id WHERE t.entity_type = 'music_artist'"
        ).fetchall()
        artwork_paths = {str(row["path"] or "").strip() for row in artwork_rows} - {""}

        connection.execute(
            "DELETE FROM content_items WHERE id IN ("
            "SELECT entity_id FROM trash_entries WHERE entity_type IN ('movie', 'album'))"
        )
        connection.execute(
            "UPDATE movie_people SET is_interest = 0 WHERE EXISTS ("
            "SELECT 1 FROM trash_entries t WHERE t.entity_type = 'person' "
            "AND t.entity_id = movie_people.person_id AND t.role = movie_people.credit_role)"
        )
        connection.execute(
            "DELETE FROM interest_roles WHERE content_type = 'movie' AND EXISTS ("
            "SELECT 1 FROM trash_entries t WHERE t.entity_type = 'person' "
            "AND t.entity_id = interest_roles.person_id AND t.role = interest_roles.role)"
        )
        connection.execute(
            "UPDATE people SET active = CASE WHEN EXISTS ("
            "SELECT 1 FROM interest_roles ir WHERE ir.person_id = people.id) THEN 1 ELSE 0 END "
            "WHERE id IN (SELECT entity_id FROM trash_entries WHERE entity_type = 'person')"
        )
        connection.execute(
            "DELETE FROM people WHERE id IN ("
            "SELECT entity_id FROM trash_entries WHERE entity_type = 'person') "
            "AND NOT EXISTS (SELECT 1 FROM interest_roles ir WHERE ir.person_id = people.id) "
            "AND NOT EXISTS (SELECT 1 FROM movie_people mp WHERE mp.person_id = people.id)"
        )
        connection.execute(
            "UPDATE album_artists SET is_interest = 0 WHERE artist_id IN ("
            "SELECT entity_id FROM trash_entries WHERE entity_type = 'music_artist')"
        )
        connection.execute(
            "UPDATE music_artists SET active = 0 WHERE id IN ("
            "SELECT entity_id FROM trash_entries WHERE entity_type = 'music_artist')"
        )
        connection.execute(
            "DELETE FROM music_artists WHERE id IN ("
            "SELECT entity_id FROM trash_entries WHERE entity_type = 'music_artist') "
            "AND NOT EXISTS (SELECT 1 FROM album_artists aa WHERE aa.artist_id = music_artists.id)"
        )
        connection.execute("DELETE FROM trash_entries")

        referenced_rows = connection.execute(
            "SELECT poster_local_path AS path FROM movies WHERE poster_local_path <> '' "
            "UNION SELECT cover_path AS path FROM albums WHERE cover_path <> '' "
            "UNION SELECT profile_local_path AS path FROM people WHERE profile_local_path <> '' "
            "UNION SELECT profile_local_path AS path FROM music_artists WHERE profile_local_path <> ''"
        ).fetchall()
        referenced_paths = {str(row["path"] or "").strip() for row in referenced_rows}

    artwork_deleted = 0
    artwork_errors: list[dict[str, str]] = []
    for relative_path in sorted(artwork_paths - referenced_paths):
        try:
            artwork_deleted += int(artwork.delete_cached(relative_path))
        except artwork.ArtworkError as error:
            artwork_errors.append({"path": relative_path, "error": str(error)})
    return {
        "deleted": len(entries), "by_type": by_type,
        "artwork_deleted": artwork_deleted, "artwork_errors": artwork_errors,
    }


def add_interest_person(person: dict[str, Any]) -> dict[str, Any]:
    if str(person.get("content_type") or "movie") == "music":
        return add_music_artist(person)
    role = str(person.get("role") or "").strip()
    if role not in {"actor", "director"}:
        raise StorageError("Person role must be actor or director")
    name_original = str(person.get("name_original") or person.get("name_ru") or "").strip()
    name_ru = str(person.get("name_ru") or name_original).strip()
    if not name_original or not name_ru:
        raise StorageError("Person name is required")
    tmdb_raw = person.get("tmdb_id") or person.get("external_id")
    tmdb_id = int(tmdb_raw) if str(tmdb_raw).isdigit() else None
    details_json = _json(person.get("details_json") or {}, "{}")
    profile_path = str(person.get("profile_path") or "")
    profile_url = str(person.get("profile_url") or "")
    profile_local_path = str(person.get("profile_local_path") or "")
    raw_json = _json(
        person.get("raw_data") or {
            key: person.get(key) for key in ("role", "tmdb_id", "name_original", "name_ru")
            if person.get(key) not in (None, "")
        },
        "{}",
    )
    initialize_database()
    with _lock, connect() as connection:
        existing = None
        if tmdb_id:
            existing = connection.execute("SELECT id FROM people WHERE tmdb_id = ?", (tmdb_id,)).fetchone()
        if not existing:
            existing = connection.execute(
                "SELECT id FROM people WHERE name_original = ? COLLATE NOCASE OR name_ru = ? COLLATE NOCASE",
                (name_original, name_ru),
            ).fetchone()
        person_id = str(existing["id"]) if existing else f"person-{uuid.uuid4().hex[:12]}"
        if existing:
            connection.execute(
                "UPDATE people SET active = 1, tmdb_id = COALESCE(tmdb_id, ?), "
                "name_original = COALESCE(NULLIF(name_original, ''), ?), name_ru = COALESCE(NULLIF(name_ru, ''), ?), "
                "raw_json = ?, details_json = CASE WHEN ? <> '{}' THEN ? ELSE details_json END, "
                "profile_path = COALESCE(NULLIF(?, ''), profile_path), "
                "profile_url = COALESCE(NULLIF(?, ''), profile_url), "
                "profile_local_path = COALESCE(NULLIF(?, ''), profile_local_path), "
                "tmdb_updated_at = CASE WHEN ? IS NOT NULL THEN ? ELSE tmdb_updated_at END WHERE id = ?",
                (
                    tmdb_id, name_original, name_ru, raw_json, details_json, details_json,
                    profile_path, profile_url, profile_local_path,
                    tmdb_id, _now(), person_id,
                ),
            )
        else:
            connection.execute(
                "INSERT INTO people(id, name_original, name_ru, tmdb_id, active, raw_json, details_json, "
                "profile_path, profile_url, profile_local_path, tmdb_updated_at) "
                "VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
                (
                    person_id, name_original, name_ru, tmdb_id, raw_json, details_json,
                    profile_path, profile_url, profile_local_path, _now() if tmdb_id else None,
                ),
            )
        if connection.execute(
            "SELECT 1 FROM interest_roles WHERE person_id = ? AND content_type = 'movie' AND role = ?",
            (person_id, role),
        ).fetchone():
            raise StorageError("This person is already in this list")
        connection.execute(
            "INSERT INTO interest_roles(person_id, content_type, role, notes) VALUES (?, 'movie', ?, ?)",
            (person_id, role, str(person.get("notes") or "")),
        )
        connection.execute(
            "UPDATE movie_people SET is_interest = 1 WHERE person_id = ? AND credit_role = ?",
            (person_id, role),
        )
    return next(row for row in list_interests("movie", role) if row["id"] == person_id)


def add_music_artist(artist: dict[str, Any]) -> dict[str, Any]:
    name = str(
        artist.get("name") or artist.get("name_original") or artist.get("name_ru") or ""
    ).strip()
    if not name:
        raise StorageError("Artist name is required")
    mbid = str(artist.get("mbid") or artist.get("external_id") or "").strip() or None
    raw_json = _json(
        artist.get("raw_data") or {
            key: artist.get(key)
            for key in ("name", "name_original", "name_ru", "mbid")
            if artist.get(key) not in (None, "")
        },
        "{}",
    )
    profile_url = str(artist.get("profile_url") or "")
    profile_local_path = str(artist.get("profile_local_path") or "")
    fanart_checked = bool(artist.get("fanart_checked"))
    initialize_database()
    with _lock, connect() as connection:
        existing = None
        if mbid:
            existing = connection.execute(
                "SELECT id FROM music_artists WHERE mbid = ?", (mbid,)
            ).fetchone()
        if not existing:
            existing = connection.execute(
                "SELECT id FROM music_artists WHERE name = ? COLLATE NOCASE", (name,)
            ).fetchone()
        artist_id = str(existing["id"]) if existing else f"artist-{uuid.uuid4().hex[:12]}"
        if existing:
            connection.execute(
                "UPDATE music_artists SET active = 1, name = ?, sort_name = COALESCE(NULLIF(?, ''), sort_name), "
                "mbid = COALESCE(?, mbid), artist_type = COALESCE(NULLIF(?, ''), artist_type), "
                "country = COALESCE(NULLIF(?, ''), country), area = COALESCE(NULLIF(?, ''), area), "
                "disambiguation = COALESCE(NULLIF(?, ''), disambiguation), raw_json = ?, "
                "profile_url = COALESCE(NULLIF(?, ''), profile_url), "
                "profile_local_path = COALESCE(NULLIF(?, ''), profile_local_path), "
                "musicbrainz_updated_at = CASE WHEN ? THEN ? ELSE musicbrainz_updated_at END, "
                "fanart_updated_at = CASE WHEN ? THEN ? ELSE fanart_updated_at END WHERE id = ?",
                (
                    name, str(artist.get("sort_name") or ""), mbid,
                    str(artist.get("artist_type") or artist.get("type") or ""),
                    str(artist.get("country") or ""), str(artist.get("area") or ""),
                    str(artist.get("disambiguation") or ""), raw_json, profile_url,
                    profile_local_path, int(bool(artist.get("musicbrainz_checked"))), _now(),
                    int(fanart_checked), _now(), artist_id,
                ),
            )
        else:
            connection.execute(
                "INSERT INTO music_artists(id, name, sort_name, mbid, artist_type, country, area, "
                "disambiguation, life_span_begin, life_span_end, active, raw_json, details_json, musicbrainz_updated_at, "
                "profile_url, profile_local_path, fanart_updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
                (
                    artist_id, name, str(artist.get("sort_name") or name), mbid,
                    str(artist.get("artist_type") or artist.get("type") or ""),
                    str(artist.get("country") or ""), str(artist.get("area") or ""),
                    str(artist.get("disambiguation") or ""), str(artist.get("life_span_begin") or ""),
                    str(artist.get("life_span_end") or ""), raw_json,
                    _json(artist.get("details_json") or {}, "{}"),
                    artist.get("musicbrainz_updated_at") or (
                        _now() if artist.get("musicbrainz_checked") else None
                    ),
                    profile_url, profile_local_path, _now() if fanart_checked else None,
                ),
            )
    return next(row for row in list_music_artists() if row["id"] == artist_id)


def update_music_artist(artist_id: str, details: dict[str, Any]) -> dict[str, Any]:
    initialize_database()
    mbid = str(details.get("mbid") or details.get("external_id") or "").strip() or None
    fanart_checked = bool(details.get("fanart_checked"))
    with _lock, connect() as connection:
        if not connection.execute(
            "SELECT 1 FROM music_artists WHERE id = ?", (artist_id,)
        ).fetchone():
            raise StorageError("Artist not found")
        duplicate = connection.execute(
            "SELECT id FROM music_artists WHERE mbid = ? AND id <> ?", (mbid, artist_id),
        ).fetchone() if mbid else None
        if duplicate:
            raise StorageError("Another artist already uses this MusicBrainz ID")
        life_span = details.get("life-span") if isinstance(details.get("life-span"), dict) else {}
        connection.execute(
            "UPDATE music_artists SET name = COALESCE(NULLIF(?, ''), name), "
            "sort_name = COALESCE(NULLIF(?, ''), sort_name), mbid = COALESCE(?, mbid), "
            "artist_type = COALESCE(NULLIF(?, ''), artist_type), country = COALESCE(NULLIF(?, ''), country), "
            "area = COALESCE(NULLIF(?, ''), area), disambiguation = COALESCE(NULLIF(?, ''), disambiguation), "
            "life_span_begin = COALESCE(NULLIF(?, ''), life_span_begin), "
            "life_span_end = COALESCE(NULLIF(?, ''), life_span_end), details_json = ?, "
            "musicbrainz_updated_at = ?, profile_url = COALESCE(NULLIF(?, ''), profile_url), "
            "profile_local_path = COALESCE(NULLIF(?, ''), profile_local_path), "
            "fanart_updated_at = CASE WHEN ? THEN ? ELSE fanart_updated_at END WHERE id = ?",
            (
                str(details.get("name") or details.get("name_original") or ""),
                str(details.get("sort_name") or details.get("sort-name") or ""), mbid,
                str(details.get("artist_type") or details.get("type") or ""),
                str(details.get("country") or ""), str(details.get("area") or ""),
                str(details.get("disambiguation") or ""),
                str(details.get("life_span_begin") or life_span.get("begin") or ""),
                str(details.get("life_span_end") or life_span.get("end") or ""),
                _json(details.get("details_json") or details, "{}"), _now(),
                str(details.get("profile_url") or ""), str(details.get("profile_local_path") or ""),
                int(fanart_checked), _now(), artist_id,
            ),
        )
    return next(row for row in list_music_artists() if row["id"] == artist_id)


def update_interest_person(person_id: str, details: dict[str, Any]) -> dict[str, Any]:
    initialize_database()
    tmdb_raw = details.get("tmdb_id") or details.get("external_id")
    tmdb_id = int(tmdb_raw) if str(tmdb_raw).isdigit() else None
    with _lock, connect() as connection:
        row = connection.execute("SELECT id FROM people WHERE id = ?", (person_id,)).fetchone()
        if not row:
            raise StorageError("Person not found")
        duplicate = connection.execute(
            "SELECT id FROM people WHERE tmdb_id = ? AND id <> ?", (tmdb_id, person_id),
        ).fetchone() if tmdb_id else None
        if duplicate:
            duplicate_id = str(duplicate["id"])
            duplicate_roles = connection.execute(
                "SELECT 1 FROM interest_roles WHERE person_id = ?", (duplicate_id,),
            ).fetchone()
            if duplicate_roles:
                raise StorageError("Another interest person already uses this TMDB ID")
            credits = connection.execute(
                "SELECT movie_id, credit_role, character_name, job FROM movie_people WHERE person_id = ?",
                (duplicate_id,),
            ).fetchall()
            for credit in credits:
                connection.execute(
                    "INSERT OR REPLACE INTO movie_people(movie_id, person_id, credit_role, character_name, job, is_interest) "
                    "VALUES (?, ?, ?, ?, ?, CASE WHEN EXISTS ("
                    "SELECT 1 FROM interest_roles ir WHERE ir.person_id = ? "
                    "AND ir.content_type = 'movie' AND ir.role = ? AND NOT EXISTS ("
                    "SELECT 1 FROM trash_entries t WHERE t.entity_type = 'person' "
                    "AND t.entity_id = ir.person_id AND t.role = ir.role)) THEN 1 ELSE 0 END)",
                    (
                        credit["movie_id"], person_id, credit["credit_role"],
                        credit["character_name"], credit["job"], person_id,
                        credit["credit_role"],
                    ),
                )
            connection.execute("DELETE FROM people WHERE id = ?", (duplicate_id,))
        connection.execute(
            "UPDATE people SET name_original = COALESCE(NULLIF(?, ''), name_original), "
            "name_ru = COALESCE(NULLIF(?, ''), name_ru), tmdb_id = COALESCE(?, tmdb_id), "
            "details_json = ?, profile_path = COALESCE(NULLIF(?, ''), profile_path), "
            "profile_url = COALESCE(NULLIF(?, ''), profile_url), "
            "profile_local_path = COALESCE(NULLIF(?, ''), profile_local_path), "
            "tmdb_updated_at = ? WHERE id = ?",
            (
                str(details.get("name_original") or ""), str(details.get("name_ru") or ""), tmdb_id,
                _json(details.get("details_json") or {}, "{}"),
                str(details.get("profile_path") or ""), str(details.get("profile_url") or ""),
                str(details.get("profile_local_path") or ""), _now(), person_id,
            ),
        )
    return next(row for row in list_interests("movie") if row["id"] == person_id)


def list_content_types() -> list[dict[str, Any]]:
    initialize_database()
    with connect() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT code, name_ru FROM content_types WHERE enabled = 1 ORDER BY code"
        )]


def _validate(item: dict[str, Any], partial: bool = False) -> None:
    if not partial and item.get("content_type", "movie") not in {"movie", "music"}:
        raise StorageError("Unsupported content type")
    if "status" in item and str(item.get("status") or "backlog") not in VALID_STATUSES:
        raise StorageError("Unsupported status")
    if "reaction" in item and str(item.get("reaction") or "") not in VALID_REACTIONS:
        raise StorageError("Unsupported reaction")
    if not partial and not (str(item.get("title_original", "")).strip() and str(item.get("title_ru", "")).strip()):
        raise StorageError("Both original and Russian titles are required")


def _year(item: dict[str, Any]) -> int | None:
    raw = item.get("year") or str(item.get("release_date") or "")[:4]
    return int(raw) if str(raw).isdigit() else None


DETAIL_FIELDS = (
    "cast", "writers", "countries", "production_companies", "spoken_languages", "keywords",
    "budget", "revenue", "homepage", "movie_status",
)


def _detail_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item.get(key) for key in DETAIL_FIELDS if item.get(key) not in (None, "", [])}


def _duplicate(connection: sqlite3.Connection, item: dict[str, Any]) -> bool:
    tmdb_id = item.get("tmdb_id") or item.get("external_id")
    if str(tmdb_id).isdigit() and connection.execute("SELECT 1 FROM movies WHERE tmdb_id = ?", (int(tmdb_id),)).fetchone():
        return True
    candidate_titles = {_normalized(str(item.get("title_original", ""))), _normalized(str(item.get("title_ru", "")))} - {""}
    year = _year(item)
    rows = connection.execute(
        "SELECT i.title_original, i.title_ru, m.release_year FROM content_items i JOIN movies m ON m.content_id = i.id"
    ).fetchall()
    for row in rows:
        existing_titles = {_normalized(row["title_original"]), _normalized(row["title_ru"])} - {""}
        if not candidate_titles & existing_titles:
            continue
        existing_year = row["release_year"]
        if year is None or existing_year is None or abs(year - existing_year) <= 1:
            return True
    return False


def _person_id(
    connection: sqlite3.Connection,
    name_original: str,
    tmdb_id: int | None = None,
    name_ru: str = "",
) -> str:
    row = None
    if tmdb_id:
        row = connection.execute("SELECT id FROM people WHERE tmdb_id = ?", (tmdb_id,)).fetchone()
    if not row:
        row = connection.execute("SELECT id FROM people WHERE name_original = ? COLLATE NOCASE", (name_original,)).fetchone()
    if row:
        person_id = str(row["id"])
        connection.execute(
            "UPDATE people SET name_original = COALESCE(NULLIF(?, ''), name_original), "
            "name_ru = COALESCE(NULLIF(?, ''), name_ru), tmdb_id = COALESCE(?, tmdb_id) WHERE id = ?",
            (name_original, name_ru, tmdb_id, person_id),
        )
        return person_id
    person_id = f"person-{uuid.uuid4().hex[:12]}"
    connection.execute(
        "INSERT INTO people(id, name_original, name_ru, tmdb_id, active) VALUES (?, ?, ?, ?, 0)",
        (person_id, name_original, name_ru or name_original, tmdb_id),
    )
    return person_id


def _replace_movie_relations(connection: sqlite3.Connection, item_id: str, item: dict[str, Any]) -> None:
    if "genres_data" in item or "genres" in item:
        connection.execute("DELETE FROM movie_genres WHERE movie_id = ?", (item_id,))
        genres_data = item.get("genres_data")
        if not isinstance(genres_data, list):
            genres_data = [
                {"id": -(index + 1), "name": name.strip()}
                for index, name in enumerate(str(item.get("genres") or "").split(";")) if name.strip()
            ]
        for genre in genres_data:
            name = str(genre.get("name") or "").strip()
            if not name:
                continue
            genre_id = int(genre.get("id") or -(zlib.crc32(name.encode("utf-8")) + 1))
            connection.execute(
                "INSERT INTO genres(id, name_ru, name_original) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name_ru = excluded.name_ru",
                (genre_id, name, str(genre.get("original_name") or "")),
            )
            connection.execute("INSERT OR IGNORE INTO movie_genres(movie_id, genre_id) VALUES (?, ?)", (item_id, genre_id))

    if any(key in item for key in ("directors_data", "key_people_data", "directors", "participants")):
        connection.execute("DELETE FROM movie_people WHERE movie_id = ?", (item_id,))
        directors = item.get("directors_data")
        if not isinstance(directors, list):
            directors = [{"name": name.strip()} for name in str(item.get("directors") or item.get("creators") or "").split(";") if name.strip()]
        key_people = item.get("key_people_data")
        if not isinstance(key_people, list):
            key_people = [{"name": name.strip(), "role": "actor"} for name in str(item.get("participants") or "").split(";") if name.strip()]
        people_with_roles = [(person, "director") for person in directors]
        people_with_roles.extend((person, str(person.get("role") or "actor")) for person in key_people)
        for person, default_role in people_with_roles:
            name_original = str(person.get("name_original") or person.get("name") or "").strip()
            name_ru = str(person.get("name_ru") or "").strip()
            if not name_original:
                continue
            tmdb_id = person.get("tmdb_id")
            person_id = _person_id(
                connection, name_original, int(tmdb_id) if str(tmdb_id).isdigit() else None, name_ru,
            )
            role = str(person.get("role") or default_role)
            interest = connection.execute(
                "SELECT 1 FROM interest_roles WHERE person_id = ? AND content_type = 'movie' AND role = ?",
                (person_id, role),
            ).fetchone()
            connection.execute(
                "INSERT OR REPLACE INTO movie_people(movie_id, person_id, credit_role, character_name, job, is_interest) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (item_id, person_id, role, str(person.get("character") or ""), str(person.get("job") or ""), int(bool(interest))),
            )


def _album_duplicate(connection: sqlite3.Connection, item: dict[str, Any]) -> bool:
    mbid = str(item.get("release_group_mbid") or item.get("mbid") or item.get("external_id") or "").strip()
    if mbid and connection.execute(
        "SELECT 1 FROM albums WHERE release_group_mbid = ?", (mbid,)
    ).fetchone():
        return True
    candidate = _normalized(str(item.get("title_original") or item.get("title_ru") or ""))
    artists = _normalized(str(item.get("artists") or item.get("artist") or ""))
    year = _year(item)
    rows = connection.execute(
        "SELECT i.title_original, i.title_ru, a.release_year, COALESCE(("
        "SELECT group_concat(ma.name, ';') FROM album_artists aa "
        "JOIN music_artists ma ON ma.id = aa.artist_id WHERE aa.album_id = i.id), '') AS artists "
        "FROM content_items i JOIN albums a ON a.content_id = i.id"
    ).fetchall()
    for row in rows:
        titles = {_normalized(row["title_original"]), _normalized(row["title_ru"])} - {""}
        if candidate not in titles:
            continue
        if artists and artists != _normalized(row["artists"]):
            continue
        existing_year = row["release_year"]
        if year is None or existing_year is None or abs(year - existing_year) <= 1:
            return True
    return False


def _music_artist_id(connection: sqlite3.Connection, artist: dict[str, Any]) -> str:
    name = str(artist.get("name") or artist.get("name_original") or artist.get("credit_name") or "").strip()
    mbid = str(artist.get("mbid") or artist.get("id") or "").strip() or None
    row = connection.execute(
        "SELECT id FROM music_artists WHERE mbid = ?", (mbid,)
    ).fetchone() if mbid else None
    if not row and name:
        row = connection.execute(
            "SELECT id FROM music_artists WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
    if row:
        artist_id = str(row["id"])
        connection.execute(
            "UPDATE music_artists SET name = COALESCE(NULLIF(?, ''), name), "
            "sort_name = COALESCE(NULLIF(?, ''), sort_name), mbid = COALESCE(?, mbid) WHERE id = ?",
            (name, str(artist.get("sort_name") or artist.get("sort-name") or ""), mbid, artist_id),
        )
        return artist_id
    artist_id = f"artist-{uuid.uuid4().hex[:12]}"
    connection.execute(
        "INSERT INTO music_artists(id, name, sort_name, mbid, active) VALUES (?, ?, ?, ?, 0)",
        (artist_id, name or "Unknown artist", str(artist.get("sort_name") or artist.get("sort-name") or name), mbid),
    )
    return artist_id


def _replace_album_artists(
    connection: sqlite3.Connection, item_id: str, item: dict[str, Any]
) -> None:
    if not any(key in item for key in ("artists_data", "artist_credit", "artists", "artist")):
        return
    connection.execute("DELETE FROM album_artists WHERE album_id = ?", (item_id,))
    artists = item.get("artists_data") or item.get("artist_credit")
    if not isinstance(artists, list):
        artists = [
            {"name": name.strip(), "credit_name": name.strip()}
            for name in str(item.get("artists") or item.get("artist") or "").split(";")
            if name.strip()
        ]
    for position, artist in enumerate(artists):
        if not isinstance(artist, dict):
            continue
        nested = artist.get("artist") if isinstance(artist.get("artist"), dict) else {}
        normalized = {
            **nested,
            **artist,
            "name": artist.get("name") or nested.get("name"),
            "mbid": artist.get("mbid") or nested.get("id"),
        }
        artist_id = _music_artist_id(connection, normalized)
        interest = connection.execute(
            "SELECT 1 FROM music_artists WHERE id = ? AND active = 1", (artist_id,)
        ).fetchone()
        connection.execute(
            "INSERT OR REPLACE INTO album_artists(album_id, artist_id, credit_name, position, is_interest) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                item_id, artist_id,
                str(artist.get("credit_name") or artist.get("name") or nested.get("name") or ""),
                position, int(bool(interest)),
            ),
        )


def _add_album(item: dict[str, Any]) -> dict[str, Any]:
    initialize_database()
    item_id = str(item.get("id") or f"album-{uuid.uuid4().hex[:12]}")
    now = _now()
    status = str(item.get("status") or "backlog")
    reaction = str(item.get("reaction") or "")
    consumed_at = str(item.get("consumed_at") or now) if status == "consumed" else None
    title_original = str(item.get("title_original") or item.get("title_ru") or "").strip()
    title_ru = str(item.get("title_ru") or title_original).strip()
    mbid = str(item.get("release_group_mbid") or item.get("mbid") or item.get("external_id") or "").strip() or None
    secondary_types = item.get("secondary_types_json") or item.get("secondary_types") or []
    genres = item.get("genres_json") or item.get("genres_data") or item.get("genres") or []
    tags = item.get("tags_json") or item.get("tags_data") or item.get("tags") or []
    if isinstance(genres, str):
        genres = [value.strip() for value in genres.split(";") if value.strip()]
    if isinstance(tags, str):
        tags = [value.strip() for value in tags.split(";") if value.strip()]
    with _lock, connect() as connection:
        if _album_duplicate(connection, item):
            raise StorageError("This album is already in the library")
        connection.execute(
            "INSERT INTO content_items(id, content_type, title_ru, title_original, status, reaction, source, "
            "source_url, notes, raw_json, metadata_json, added_at, consumed_at, updated_at) "
            "VALUES (?, 'music', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_id, title_ru, title_original, status, reaction,
                str(item.get("source") or "manual"), str(item.get("url") or item.get("source_url") or ""),
                str(item.get("notes") or ""),
                _json(item.get("raw_data") or {
                    key: item.get(key) for key in ("title_original", "title_ru", "year", "artists", "notes", "release_group_mbid")
                    if item.get(key) not in (None, "")
                }, "{}"),
                _json(item.get("metadata_json"), "{}"), str(item.get("added_at") or now), consumed_at, now,
            ),
        )
        connection.execute(
            "INSERT INTO albums(content_id, release_group_mbid, primary_release_mbid, first_release_date, "
            "release_year, track_count, primary_type, secondary_types_json, genres_json, tags_json, "
            "total_listen_count, listenbrainz_updated_at, disambiguation, country, label, catalog_number, barcode, media_formats, cover_url, cover_path, "
            "details_json, musicbrainz_updated_at, cover_art_updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_id, mbid, str(item.get("primary_release_mbid") or "") or None,
                str(item.get("first_release_date") or item.get("release_date") or "") or None,
                _year(item), item.get("track_count") or None,
                str(item.get("primary_type") or "Album"), _json(secondary_types, "[]"),
                _json(genres, "[]"), _json(tags, "[]"), item.get("total_listen_count"),
                item.get("listenbrainz_updated_at") or (
                    now if "total_listen_count" in item else None
                ), str(item.get("disambiguation") or ""),
                str(item.get("country") or ""), str(item.get("label") or ""),
                str(item.get("catalog_number") or ""), str(item.get("barcode") or ""),
                str(item.get("media_formats") or ""), str(item.get("cover_url") or ""),
                str(item.get("cover_path") or ""),
                _json(item.get("details_json") or {}, "{}"),
                item.get("musicbrainz_updated_at") or (
                    now if item.get("musicbrainz_checked") else None
                ),
                item.get("cover_art_updated_at") or (
                    now if mbid and item.get("cover_art_checked") is not False else None
                ),
            ),
        )
        _replace_album_artists(connection, item_id, item)
        if mbid:
            connection.execute(
                "INSERT OR IGNORE INTO content_aliases(content_id, alias, language, provider, external_id, notes) "
                "VALUES (?, '', '', 'musicbrainz', ?, 'Added automatically')",
                (item_id, mbid),
            )
    return get_item(item_id)


def add_item(item: dict[str, Any]) -> dict[str, Any]:
    item = dict(item)
    item.setdefault("content_type", "movie")
    item.setdefault("status", "backlog")
    item.setdefault("reaction", "")
    _validate(item)
    if item["content_type"] == "music":
        return _add_album(item)
    initialize_database()
    item_id = str(item.get("id") or f"movie-{uuid.uuid4().hex[:12]}")
    now = _now()
    status = str(item.get("status") or "backlog")
    reaction = str(item.get("reaction") or "")
    consumed_at = str(item.get("consumed_at") or now) if status == "consumed" else None
    tmdb_id = item.get("tmdb_id") or item.get("external_id")
    imdb_rating = item.get("imdb_rating")
    if imdb_rating in (None, "") and item.get("external_rating_source") == "imdb":
        imdb_rating = item.get("external_rating")
    tmdb_rating = item.get("tmdb_rating")
    if tmdb_rating in (None, "") and item.get("external_rating_source") == "tmdb":
        tmdb_rating = item.get("external_rating")
    with _lock, connect() as connection:
        if _duplicate(connection, item):
            raise StorageError("This movie is already in the library")
        connection.execute(
            "INSERT INTO content_items(id, content_type, title_ru, title_original, status, reaction, source, source_url, notes, raw_json, metadata_json, added_at, consumed_at, updated_at) "
            "VALUES (?, 'movie', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_id, str(item.get("title_ru") or "").strip(), str(item.get("title_original") or "").strip(),
                status, reaction, str(item.get("source") or "manual"), str(item.get("url") or item.get("source_url") or ""),
                str(item.get("notes") or ""),
                _json(
                    item.get("raw_data") or {
                        key: item.get(key) for key in ("title_original", "title_ru", "year", "directors", "notes", "tmdb_id")
                        if item.get(key) not in (None, "")
                    },
                    "{}",
                ),
                _json(item.get("metadata_json"), "{}"), str(item.get("added_at") or now), consumed_at, now,
            ),
        )
        connection.execute(
            "INSERT INTO movies(content_id, release_date, release_year, runtime_minutes, imdb_rating, kinopoisk_rating, tmdb_rating, tmdb_vote_count, imdb_id, kinopoisk_id, tmdb_id, overview, original_language, awards_json, tagline, content_rating, imdb_votes, metascore, box_office, poster_path, poster_url, poster_local_path, details_json, tmdb_updated_at, omdb_updated_at, kinopoisk_updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_id, item.get("release_date") or None, _year(item), item.get("duration_minutes") or item.get("runtime_minutes") or None,
                imdb_rating or None, item.get("kinopoisk_rating") or None, tmdb_rating or None,
                item.get("tmdb_vote_count") or None, item.get("imdb_id") or None,
                int(item["kinopoisk_id"]) if str(item.get("kinopoisk_id") or "").isdigit() else None,
                int(tmdb_id) if str(tmdb_id).isdigit() else None,
                str(item.get("overview") or ""), str(item.get("original_language") or ""),
                _json(item.get("awards_json"), "[]"), str(item.get("tagline") or ""),
                str(item.get("content_rating") or ""), str(item.get("imdb_votes") or ""),
                item.get("metascore") or None, str(item.get("box_office") or ""),
                str(item.get("poster_path") or ""), str(item.get("poster_url") or ""),
                str(item.get("poster_local_path") or ""),
                _json(item.get("details_json") or _detail_payload(item), "{}"),
                item.get("tmdb_updated_at") or (now if item.get("tmdb_checked") else None),
                item.get("omdb_updated_at") or (now if item.get("omdb_checked") else None),
                item.get("kinopoisk_updated_at") or (now if item.get("kinopoisk_checked") else None),
            ),
        )
        _replace_movie_relations(connection, item_id, item)
        if str(tmdb_id).isdigit():
            connection.execute(
                "INSERT OR IGNORE INTO content_aliases(content_id, alias, language, provider, external_id, notes) VALUES (?, '', '', 'tmdb', ?, 'Added automatically')",
                (item_id, str(tmdb_id)),
            )
        if str(item.get("kinopoisk_id") or "").isdigit():
            connection.execute(
                "INSERT OR IGNORE INTO content_aliases(content_id, alias, language, provider, external_id, notes) VALUES (?, '', '', 'kinopoisk', ?, 'Added automatically')",
                (item_id, str(item["kinopoisk_id"])),
            )
    return get_item(item_id)


def update_item(item_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    allowed = {"status", "reaction", "notes", "planned_soon"}
    changes = {key: value for key, value in changes.items() if key in allowed}
    if not changes:
        raise StorageError("No supported fields to update")
    _validate(changes, partial=True)
    initialize_database()
    with _lock, connect() as connection:
        row = connection.execute(
            "SELECT status, reaction, notes, planned_soon, consumed_at FROM content_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if not row:
            raise StorageError("Item not found")
        status = str(changes.get("status", row["status"]))
        reaction = str(changes.get("reaction", row["reaction"]) or "")
        planned_raw = changes.get("planned_soon", row["planned_soon"])
        planned_soon = int(planned_raw in (True, 1, "1"))
        consumed_at = row["consumed_at"]
        if status == "consumed" and not consumed_at:
            consumed_at = _now()
        if status == "consumed":
            planned_soon = 0
        if status == "backlog":
            consumed_at, reaction = None, ""
        connection.execute(
            "UPDATE content_items SET status = ?, reaction = ?, notes = ?, planned_soon = ?, "
            "consumed_at = ?, updated_at = ? WHERE id = ?",
            (status, reaction, str(changes.get("notes", row["notes"])), planned_soon, consumed_at, _now(), item_id),
        )
    return get_item(item_id)


def update_movie_from_provider(item_id: str, details: dict[str, Any]) -> dict[str, Any]:
    initialize_database()
    refreshed_at = _now()
    with _lock, connect() as connection:
        if not connection.execute("SELECT 1 FROM movies WHERE content_id = ?", (item_id,)).fetchone():
            raise StorageError("Movie not found")
        connection.execute(
            "UPDATE content_items SET title_ru = COALESCE(NULLIF(?, ''), title_ru), title_original = COALESCE(NULLIF(?, ''), title_original), "
            "source_url = COALESCE(NULLIF(?, ''), source_url), updated_at = ? WHERE id = ?",
            (details.get("title_ru", ""), details.get("title_original", ""), details.get("url", ""), _now(), item_id),
        )
        connection.execute(
            "UPDATE movies SET release_date = ?, release_year = ?, runtime_minutes = ?, "
            "imdb_rating = COALESCE(?, imdb_rating), kinopoisk_rating = COALESCE(?, kinopoisk_rating), "
            "tmdb_rating = ?, tmdb_vote_count = ?, imdb_id = COALESCE(NULLIF(?, ''), imdb_id), "
            "kinopoisk_id = COALESCE(?, kinopoisk_id), tmdb_id = ?, overview = ?, original_language = ?, "
            "awards_json = COALESCE(?, awards_json), tagline = COALESCE(NULLIF(?, ''), tagline), "
            "content_rating = COALESCE(NULLIF(?, ''), content_rating), imdb_votes = COALESCE(NULLIF(?, ''), imdb_votes), "
            "metascore = COALESCE(?, metascore), box_office = COALESCE(NULLIF(?, ''), box_office), "
            "poster_path = ?, poster_url = ?, poster_local_path = ?, "
            "details_json = ?, tmdb_updated_at = ?, "
            "omdb_updated_at = CASE WHEN ? THEN ? ELSE omdb_updated_at END, "
            "kinopoisk_updated_at = CASE WHEN ? THEN ? ELSE kinopoisk_updated_at END "
            "WHERE content_id = ?",
            (
                details.get("release_date") or None, _year(details), details.get("duration_minutes") or None,
                details.get("imdb_rating") or None, details.get("kinopoisk_rating") or None,
                details.get("tmdb_rating") or None, details.get("tmdb_vote_count") or None,
                details.get("imdb_id") or "", details.get("kinopoisk_id") or None,
                details.get("tmdb_id") or None, details.get("overview") or "",
                details.get("original_language") or "", _json(details["awards_json"], "[]") if details.get("awards_json") else None,
                str(details.get("tagline") or ""), str(details.get("content_rating") or ""),
                str(details.get("imdb_votes") or ""), details.get("metascore") or None,
                str(details.get("box_office") or ""), str(details.get("poster_path") or ""),
                str(details.get("poster_url") or ""), str(details.get("poster_local_path") or ""),
                _json(details.get("details_json") or _detail_payload(details), "{}"),
                refreshed_at, int(bool(details.get("omdb_checked"))), refreshed_at,
                int(bool(details.get("kinopoisk_checked"))), refreshed_at, item_id,
            ),
        )
        _replace_movie_relations(connection, item_id, details)
        if str(details.get("kinopoisk_id") or "").isdigit():
            connection.execute(
                "INSERT OR IGNORE INTO content_aliases(content_id, alias, language, provider, external_id, notes) VALUES (?, '', '', 'kinopoisk', ?, 'Added automatically')",
                (item_id, str(details["kinopoisk_id"])),
            )
    return get_item(item_id)


def update_movie_ratings(item_id: str, details: dict[str, Any]) -> dict[str, Any]:
    initialize_database()
    refreshed_at = _now()
    has_imdb_rating = "imdb_rating" in details and details.get("imdb_rating") is not None
    has_kinopoisk_rating = (
        "kinopoisk_rating" in details and details.get("kinopoisk_rating") is not None
    )
    with _lock, connect() as connection:
        if not connection.execute(
            "SELECT 1 FROM movies WHERE content_id = ?", (item_id,)
        ).fetchone():
            raise StorageError("Movie not found")
        connection.execute(
            "UPDATE movies SET "
            "imdb_rating = CASE WHEN ? THEN ? ELSE imdb_rating END, "
            "kinopoisk_rating = CASE WHEN ? THEN ? ELSE kinopoisk_rating END, "
            "imdb_id = COALESCE(NULLIF(?, ''), imdb_id), "
            "kinopoisk_id = COALESCE(?, kinopoisk_id), "
            "tmdb_id = COALESCE(?, tmdb_id), "
            "omdb_updated_at = CASE WHEN ? THEN ? ELSE omdb_updated_at END, "
            "kinopoisk_updated_at = CASE WHEN ? THEN ? ELSE kinopoisk_updated_at END "
            "WHERE content_id = ?",
            (
                int(has_imdb_rating), details.get("imdb_rating"),
                int(has_kinopoisk_rating), details.get("kinopoisk_rating"),
                str(details.get("imdb_id") or ""),
                int(details["kinopoisk_id"])
                if str(details.get("kinopoisk_id") or "").isdigit() else None,
                int(details["tmdb_id"])
                if str(details.get("tmdb_id") or "").isdigit() else None,
                int(bool(details.get("omdb_checked"))), refreshed_at,
                int(bool(details.get("kinopoisk_checked"))), refreshed_at, item_id,
            ),
        )
        connection.execute(
            "UPDATE content_items SET updated_at = ? WHERE id = ?", (refreshed_at, item_id)
        )
        if str(details.get("kinopoisk_id") or "").isdigit():
            connection.execute(
                "INSERT OR IGNORE INTO content_aliases(content_id, alias, language, provider, external_id, notes) "
                "VALUES (?, '', '', 'kinopoisk', ?, 'Added automatically')",
                (item_id, str(details["kinopoisk_id"])),
            )
    return get_item(item_id)


def update_movie_poster(item_id: str, details: dict[str, Any]) -> dict[str, Any]:
    initialize_database()
    with _lock, connect() as connection:
        cursor = connection.execute(
            "UPDATE movies SET poster_path = ?, poster_url = ?, poster_local_path = ?, "
            "tmdb_updated_at = ? WHERE content_id = ?",
            (
                str(details.get("poster_path") or ""),
                str(details.get("poster_url") or ""),
                str(details.get("poster_local_path") or ""),
                _now(), item_id,
            ),
        )
        if not cursor.rowcount:
            raise StorageError("Movie not found")
        connection.execute(
            "UPDATE content_items SET updated_at = ? WHERE id = ?", (_now(), item_id)
        )
    return get_item(item_id)


def update_album_from_provider(item_id: str, details: dict[str, Any]) -> dict[str, Any]:
    initialize_database()
    secondary_types = details.get("secondary_types_json") or details.get("secondary_types") or []
    genres = details.get("genres_json") or details.get("genres_data") or details.get("genres") or []
    tags = details.get("tags_json") or details.get("tags_data") or details.get("tags") or []
    if isinstance(genres, str):
        genres = [value.strip() for value in genres.split(";") if value.strip()]
    if isinstance(tags, str):
        tags = [value.strip() for value in tags.split(";") if value.strip()]
    mbid = str(details.get("release_group_mbid") or details.get("mbid") or "").strip() or None
    cover_art_checked = details.get("cover_art_checked") is not False
    cover_cache_checked = "cover_path" in details and details.get("cover_cache_checked") is not False
    popularity_checked = "total_listen_count" in details
    refreshed_at = _now()
    with _lock, connect() as connection:
        if not connection.execute(
            "SELECT 1 FROM albums WHERE content_id = ?", (item_id,)
        ).fetchone():
            raise StorageError("Album not found")
        connection.execute(
            "UPDATE content_items SET title_ru = COALESCE(NULLIF(?, ''), title_ru), "
            "title_original = COALESCE(NULLIF(?, ''), title_original), "
            "source_url = COALESCE(NULLIF(?, ''), source_url), updated_at = ? WHERE id = ?",
            (
                str(details.get("title_ru") or ""), str(details.get("title_original") or ""),
                str(details.get("url") or ""), _now(), item_id,
            ),
        )
        connection.execute(
            "UPDATE albums SET release_group_mbid = COALESCE(?, release_group_mbid), "
            "primary_release_mbid = COALESCE(NULLIF(?, ''), primary_release_mbid), "
            "first_release_date = COALESCE(NULLIF(?, ''), first_release_date), release_year = COALESCE(?, release_year), "
            "track_count = COALESCE(?, track_count), primary_type = COALESCE(NULLIF(?, ''), primary_type), "
            "secondary_types_json = ?, genres_json = ?, tags_json = ?, "
            "total_listen_count = CASE WHEN ? THEN ? ELSE total_listen_count END, "
            "listenbrainz_updated_at = CASE WHEN ? THEN ? ELSE listenbrainz_updated_at END, "
            "disambiguation = COALESCE(NULLIF(?, ''), disambiguation), "
            "country = COALESCE(NULLIF(?, ''), country), label = COALESCE(NULLIF(?, ''), label), "
            "catalog_number = COALESCE(NULLIF(?, ''), catalog_number), barcode = COALESCE(NULLIF(?, ''), barcode), "
            "media_formats = COALESCE(NULLIF(?, ''), media_formats), "
            "cover_url = CASE WHEN ? THEN ? ELSE cover_url END, "
            "cover_path = CASE WHEN ? THEN ? ELSE cover_path END, details_json = ?, "
            "musicbrainz_updated_at = ?, "
            "cover_art_updated_at = CASE WHEN ? THEN ? ELSE cover_art_updated_at END "
            "WHERE content_id = ?",
            (
                mbid, str(details.get("primary_release_mbid") or ""),
                str(details.get("first_release_date") or details.get("release_date") or ""), _year(details),
                details.get("track_count") or None, str(details.get("primary_type") or ""),
                _json(secondary_types, "[]"), _json(genres, "[]"), _json(tags, "[]"),
                int(popularity_checked), details.get("total_listen_count"),
                int(popularity_checked), refreshed_at,
                str(details.get("disambiguation") or ""), str(details.get("country") or ""),
                str(details.get("label") or ""), str(details.get("catalog_number") or ""),
                str(details.get("barcode") or ""), str(details.get("media_formats") or ""),
                int(cover_art_checked), str(details.get("cover_url") or ""),
                int(cover_cache_checked), str(details.get("cover_path") or ""),
                _json(details.get("details_json") or {}, "{}"), refreshed_at,
                int(cover_art_checked), refreshed_at, item_id,
            ),
        )
        _replace_album_artists(connection, item_id, details)
        if mbid:
            connection.execute(
                "INSERT OR IGNORE INTO content_aliases(content_id, alias, language, provider, external_id, notes) "
                "VALUES (?, '', '', 'musicbrainz', ?, 'Added automatically')",
                (item_id, mbid),
            )
    return get_item(item_id)


def movie_refresh_targets() -> list[dict[str, Any]]:
    return [
        {key: row[key] for key in (
            "id", "title_original", "title_ru", "year", "tmdb_id", "imdb_id",
            "imdb_rating", "kinopoisk_rating", "directors", "poster_path", "poster_url",
            "poster_local_path", "release_date", "duration_minutes", "genres", "overview",
            "cast", "tmdb_updated_at", "omdb_updated_at", "kinopoisk_updated_at",
        )}
        for row in list_library(content_type="movie")
    ]


def known_movie_keys() -> tuple[set[str], set[str]]:
    rows = list_library(content_type="movie", include_trashed=True)
    ids = {str(row["tmdb_id"]) for row in rows if row.get("tmdb_id") != ""}
    titles: set[str] = set()
    for row in rows:
        year = str(row.get("year") or "")
        for field in ("title_original", "title_ru"):
            title = _normalized(str(row.get(field) or ""))
            if title:
                titles.add(f"{title}:{year}")
                titles.add(title)
    return ids, titles


def album_refresh_targets() -> list[dict[str, Any]]:
    return [
        {key: row.get(key) for key in (
            "id", "title_original", "title_ru", "year", "release_group_mbid", "artists",
            "cover_url", "cover_path", "total_listen_count", "listenbrainz_updated_at",
            "first_release_date", "track_count", "primary_type", "musicbrainz_updated_at",
            "cover_art_updated_at",
        )}
        for row in list_library(content_type="music")
    ]


def update_artwork_path(item_id: str, content_type: str, relative_path: str) -> dict[str, Any]:
    initialize_database()
    table, column = (
        ("albums", "cover_path") if content_type == "music"
        else ("movies", "poster_local_path")
    )
    with _lock, connect() as connection:
        cursor = connection.execute(
            f"UPDATE {table} SET {column} = ? WHERE content_id = ?",
            (str(relative_path or ""), item_id),
        )
        if not cursor.rowcount:
            raise StorageError("Item not found")
        connection.execute(
            "UPDATE content_items SET updated_at = ? WHERE id = ?", (_now(), item_id)
        )
    return get_item(item_id)


def update_person_artwork_path(
    person_id: str, relative_path: str, profile_path: str = "", profile_url: str = "",
) -> dict[str, Any]:
    initialize_database()
    with _lock, connect() as connection:
        cursor = connection.execute(
            "UPDATE people SET profile_local_path = ?, "
            "profile_path = COALESCE(NULLIF(?, ''), profile_path), "
            "profile_url = COALESCE(NULLIF(?, ''), profile_url) WHERE id = ?",
            (str(relative_path or ""), str(profile_path or ""), str(profile_url or ""), person_id),
        )
        if not cursor.rowcount:
            raise StorageError("Person not found")
    return get_interest_person(person_id)


def update_music_artist_artwork_path(
    artist_id: str, relative_path: str, profile_url: str = "",
) -> dict[str, Any]:
    initialize_database()
    with _lock, connect() as connection:
        cursor = connection.execute(
            "UPDATE music_artists SET profile_local_path = ?, "
            "profile_url = COALESCE(NULLIF(?, ''), profile_url), fanart_updated_at = ? WHERE id = ?",
            (str(relative_path or ""), str(profile_url or ""), _now(), artist_id),
        )
        if not cursor.rowcount:
            raise StorageError("Artist not found")
    return get_music_artist(artist_id)


def update_album_popularity(counts: dict[str, int | None]) -> int:
    initialize_database()
    refreshed_at = _now()
    updated = 0
    with _lock, connect() as connection:
        for mbid, count in counts.items():
            cursor = connection.execute(
                "UPDATE albums SET total_listen_count = ?, listenbrainz_updated_at = ? "
                "WHERE release_group_mbid = ?",
                (count, refreshed_at, str(mbid)),
            )
            updated += cursor.rowcount
    return updated


def artist_refresh_targets() -> list[dict[str, Any]]:
    from app import artwork

    return [
        {key: row.get(key) for key in (
            "id", "name_original", "name_ru", "mbid", "profile_url", "profile_local_path",
            "musicbrainz_updated_at", "fanart_updated_at",
        )}
        for row in list_music_artists()
        if not str(row.get("mbid") or row.get("external_id") or "").strip()
        or not str(row.get("musicbrainz_updated_at") or "").strip()
        or (
            not artwork.is_cached(row.get("profile_local_path"))
            and (
                bool(str(row.get("profile_url") or "").strip())
                or not str(row.get("fanart_updated_at") or "").strip()
            )
        )
    ]


def known_album_keys() -> tuple[set[str], set[str]]:
    rows = list_library(content_type="music", include_trashed=True)
    ids = {str(row["release_group_mbid"]) for row in rows if row.get("release_group_mbid")}
    titles: set[str] = set()
    for row in rows:
        normalized = _normalized(str(row.get("title_original") or row.get("title_ru") or ""))
        artist = _normalized(str(row.get("artists") or ""))
        year = str(row.get("year") or "")
        if normalized:
            titles.add(normalized)
            titles.add(f"{normalized}:{artist}:{year}")
    return ids, titles
