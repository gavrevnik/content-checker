"""Explicit UI feedback joins its application transaction, synthetic SQLite only."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app import storage
from app.catalog_sync import view


class CatalogIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "library.sqlite3"
        self.patch = patch.object(storage, "DB_PATH", self.path)
        self.patch.start()
        storage.initialize_database()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_existing_canonical_rating_outbox_and_local_state(self):
        item = storage.add_item(
            {
                "content_type": "movie",
                "title_original": "Synthetic",
                "title_ru": "Пример",
                "status": "consumed",
                "reaction": "like",
                "notes": "Keep",
            }
        )
        with storage.connect() as db:
            canonical = {
                "id": "fixture-film",
                "type": "film",
                "title": "Synthetic",
                "status": {"liked": True},
            }
            db.execute(
                "INSERT INTO catalog_entity_links VALUES (?,?,?,?,?,?,?,?)",
                (
                    "content_items",
                    item["id"],
                    "item",
                    "fixture-film",
                    "a" * 40,
                    "2026-10-10",
                    json.dumps(canonical),
                    "{}",
                ),
            )
        storage.update_item(item["id"], {"reaction": "dislike"})
        with storage.connect() as db:
            e = db.execute("SELECT * FROM catalog_outbox").fetchone()
            self.assertEqual(json.loads(e["desired_json"]), False)
            self.assertEqual(json.loads(e["before_json"]), True)
        storage.update_item(item["id"], {"reaction": ""})
        with storage.connect() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM catalog_outbox").fetchone()[0], 1
            )
            self.assertEqual(
                json.loads(
                    db.execute("SELECT desired_json FROM catalog_outbox").fetchone()[0]
                ),
                {"$unset": True},
            )
        self.assertEqual(storage.get_item(item["id"])["notes"], "Keep")

    def test_unlinked_discovery_ai_and_favorite_never_emit_canonical_feedback(self):
        item = storage.add_item(
            {
                "content_type": "movie",
                "title_original": "Synthetic",
                "title_ru": "Пример",
                "status": "consumed",
                "reaction": "like",
                "notes": "AI score 9",
            }
        )
        storage.set_favorite(item["id"], True)
        storage.update_item(item["id"], {"reaction": "dislike"})
        with storage.connect() as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM catalog_outbox").fetchone()[0], 0
            )

    def test_curated_item_materialization_keeps_local_consumption_unknown(self):
        from types import SimpleNamespace
        from catalog_sync.materialize import materialize

        snapshot = SimpleNamespace(
            revision="a" * 40,
            records={
                "film-fixture": {
                    "kind": "item",
                    "data": {
                        "id": "film-fixture",
                        "type": "film",
                        "title": "Synthetic",
                        "aliases": ["Пример"],
                        "source_ids": ["fixture-director"],
                        "metadata": {"external_ids": {"tmdb_movie": 4242}},
                        "status": {"liked": True, "watched": True},
                    },
                }
            },
        )
        materialize(
            self.path,
            "content-checker",
            snapshot,
            ["film-fixture"],
            Path(self.tmp.name) / "backups",
        )
        rows = storage.list_library()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "backlog")
        self.assertEqual(rows[0]["reaction"], "like")
        self.assertEqual(
            materialize(
                self.path,
                "content-checker",
                snapshot,
                ["film-fixture"],
                Path(self.tmp.name) / "backups",
            )["created"],
            [],
        )

    def test_outbox_failure_rolls_back_personal_action(self):
        item = storage.add_item(
            {
                "content_type": "movie",
                "title_original": "Synthetic",
                "title_ru": "Пример",
                "status": "consumed",
                "reaction": "like",
            }
        )
        with patch(
            "app.catalog_sync.changed_reaction",
            side_effect=RuntimeError("Synthetic journal failure"),
        ):
            with self.assertRaises(RuntimeError):
                storage.update_item(item["id"], {"reaction": "dislike"})
        self.assertEqual(storage.get_item(item["id"])["reaction"], "like")


if __name__ == "__main__":
    unittest.main()
