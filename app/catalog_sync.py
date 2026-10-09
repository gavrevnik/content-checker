"""Thin local canonical feedback adapter; writes share the existing SQLite transaction."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2] / "personal-radar"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from catalog_sync.core import SCHEMA, enqueue, status, encoded, MISSING


def changed_reaction(db, item_id, reaction):
    return enqueue(
        db,
        "content_items",
        item_id,
        "liked",
        True if reaction == "like" else False if reaction == "dislike" else MISSING,
    )


def view(db):
    state = status(db)
    if state["status"] == "not_initialized":
        return {**state, "links": []}
    links = []
    for row in db.execute("SELECT * FROM catalog_entity_links ORDER BY radar_id"):
        links.append(
            {
                "localType": row["local_entity_type"],
                "localId": row["local_entity_id"],
                "radarId": row["radar_id"],
                "radarType": row["radar_entity_type"],
                "canonical": __import__("json").loads(row["canonical_json"]),
                "revision": row["last_synced_revision"],
                "syncedAt": row["last_synced_at"],
                "pending": [
                    {
                        "field": e["field"],
                        "value": __import__("json").loads(e["desired_json"]),
                        "status": e["status"],
                    }
                    for e in db.execute(
                        "SELECT field,desired_json,status FROM catalog_outbox WHERE radar_id=? AND status IN ('pending','conflict')",
                        (row["radar_id"],),
                    )
                ],
            }
        )
    return {**state, "links": links}


def set_interest(db, local_type, local_id, value):
    if value not in ("low", "medium", "high", None):
        raise ValueError("Invalid interest")
    if not db.execute(
        "SELECT 1 FROM catalog_entity_links WHERE local_entity_type=? AND local_entity_id=?",
        (local_type, local_id),
    ).fetchone():
        raise ValueError("Linked canonical source/owner required")
    return {
        "event": enqueue(
            db, local_type, local_id, "interest", MISSING if value is None else value
        )
    }
