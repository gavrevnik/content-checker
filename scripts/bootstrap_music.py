from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import listenbrainz, musicbrainz, storage


SEED_PATH = storage.ROOT / "data" / "music_seed_artists.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize MusicBrainz artists and albums without reading legacy at runtime"
    )
    parser.add_argument("--database", type=Path, default=storage.DB_PATH)
    parser.add_argument("--year-from", type=int, default=date.today().year)
    parser.add_argument("--year-to", type=int, default=date.today().year)
    parser.add_argument(
        "--archive-out-of-scope",
        action="store_true",
        help="Move existing albums outside the selected years or types to the reversible trash",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Required because this command changes the selected database"
    )
    args = parser.parse_args()
    if not args.apply:
        parser.error("Add --apply to confirm database changes")
    if args.year_from > args.year_to:
        parser.error("--year-from cannot be greater than --year-to")

    storage.DB_PATH = args.database.resolve()
    storage.initialize_database()
    if args.archive_out_of_scope:
        with storage.connect() as connection:
            out_of_scope = connection.execute(
                "SELECT content_items.id FROM content_items JOIN albums a ON a.content_id = content_items.id "
                "WHERE content_items.content_type = 'music' AND ("
                "a.release_year < ? OR a.release_year > ? OR a.release_year IS NULL "
                "OR lower(a.primary_type) <> 'album' OR COALESCE(a.secondary_types_json, '[]') <> '[]'"
                ") AND NOT EXISTS (SELECT 1 FROM trash_entries t "
                "WHERE t.entity_type = 'album' AND t.entity_id = content_items.id)",
                (args.year_from, args.year_to),
            ).fetchall()
        for row in out_of_scope:
            storage.trash_entity({"entity_type": "album", "entity_id": str(row["id"])})
    names = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    existing_artists = {
        storage._normalized(str(artist.get("name_original") or "")): artist
        for artist in storage.list_music_artists(include_trashed=True)
        if artist.get("mbid")
    }
    added_artists = 0
    added_albums = 0
    errors: list[str] = []
    pending_albums: dict[str, dict] = {}
    for name in names:
        artist = existing_artists.get(storage._normalized(name))
        if not artist:
            try:
                resolved = musicbrainz.resolve_artist_input({"name": name, "content_type": "music"})
                artist = storage.add_music_artist({**resolved, "raw_data": {"name": name}})
            except Exception as error:
                errors.append(f"{name}: {error}")
                continue
        added_artists += 1
        try:
            candidates = musicbrainz.browse_artist_albums(
                str(artist["mbid"]), args.year_from, args.year_to, studio_albums_only=True
            )
        except Exception as error:
            errors.append(f"{name}, albums: {error}")
            continue
        for candidate in candidates:
            try:
                mbid = str(candidate["release_group_mbid"])
                if mbid not in pending_albums:
                    pending_albums[mbid] = musicbrainz.album_details(
                        mbid, fetch_popularity=False
                    )
            except Exception as error:
                errors.append(f"{name} — {candidate.get('title_original')}: {error}")

    albums = list(pending_albums.values())
    try:
        listenbrainz.enrich_albums(albums)
    except listenbrainz.ListenBrainzError as error:
        errors.append(str(error))
    for details in albums:
        try:
            storage.add_item({**details, "status": "backlog", "reaction": ""})
            added_albums += 1
        except storage.StorageError as error:
            if "already" not in str(error).casefold():
                errors.append(f"{details.get('artists')} — {details.get('title_original')}: {error}")

    print(
        f"Artists processed: {added_artists}; albums added: {added_albums}; errors: {len(errors)}"
    )
    for error in errors:
        print(f"- {error}")


if __name__ == "__main__":
    main()
