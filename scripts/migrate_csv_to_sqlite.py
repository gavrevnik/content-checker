#!/usr/bin/env python3
"""One-time import of the preserved movie CSV data into SQLite."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import storage


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate the legacy movie CSV rows to SQLite")
    parser.add_argument("--csv-dir", type=Path, default=ROOT / "legacy" / "csv")
    parser.add_argument("--db", type=Path, default=storage.DB_PATH)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.db.exists():
        if not args.force:
            raise SystemExit(f"{args.db} already exists; pass --force to rebuild it")
        args.db.unlink()

    storage.DB_PATH = args.db
    storage.initialize_database()

    interests = [row for row in read_csv(args.csv_dir / "interests.csv") if row["content_type"] == "movie"]
    with storage.connect() as connection:
        for row in interests:
            tmdb_id = int(row["external_id"]) if row.get("external_id", "").isdigit() else None
            connection.execute(
                "INSERT INTO people(id, name_original, name_ru, tmdb_id, active) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name_original = excluded.name_original, name_ru = excluded.name_ru, tmdb_id = excluded.tmdb_id, active = excluded.active",
                (row["id"], row["name_original"], row["name_ru"] or row["name_original"], tmdb_id, int(row.get("active", "true").lower() == "true")),
            )
            connection.execute(
                "INSERT OR REPLACE INTO interest_roles(person_id, content_type, role, notes) VALUES (?, 'movie', ?, ?)",
                (row["id"], row["role"], row.get("notes", "")),
            )

    movies = [row for row in read_csv(args.csv_dir / "library.csv") if row["content_type"] == "movie"]
    for row in movies:
        metadata = row.get("metadata_json") or "{}"
        try:
            json.loads(metadata)
        except json.JSONDecodeError:
            metadata = "{}"
        storage.add_item({
            "id": row["id"],
            "content_type": "movie",
            "title_ru": row["title_ru"],
            "title_original": row["title_original"],
            "status": row["status"],
            "reaction": row["reaction"],
            "year": row["year"],
            "directors": row.get("creators", ""),
            "participants": row.get("participants", ""),
            "genres": row.get("genres", ""),
            "duration_minutes": row.get("duration_minutes", ""),
            "imdb_rating": row.get("external_rating", "") if row.get("external_rating_source") == "imdb" else "",
            "tmdb_rating": row.get("external_rating", "") if row.get("external_rating_source") == "tmdb" else "",
            "tmdb_id": row.get("external_id", "") if row.get("external_rating_source") == "tmdb" else "",
            "source": row.get("source", "legacy CSV"),
            "url": row.get("url", ""),
            "metadata_json": metadata,
            "notes": row.get("notes", ""),
            "added_at": row.get("added_at", ""),
            "consumed_at": row.get("consumed_at", ""),
        })

    aliases = [row for row in read_csv(args.csv_dir / "mappings.csv") if row["content_type"] == "movie"]
    with storage.connect() as connection:
        for row in aliases:
            connection.execute(
                "INSERT OR IGNORE INTO content_aliases(content_id, alias, language, provider, external_id, notes) VALUES (?, ?, ?, ?, ?, ?)",
                (row["library_id"], row["alias"], row["alias_language"], row["provider"], row["external_id"], row["notes"]),
            )

    print(f"Imported {len(movies)} movies, {len(interests)} movie interests and {len(aliases)} aliases into {args.db}")


if __name__ == "__main__":
    main()
