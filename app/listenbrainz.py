from __future__ import annotations

from personal_radar_connectors import listenbrainz as listenbrainz_client
from personal_radar_connectors.listenbrainz import ListenBrainzError
from personal_radar_connectors.credentials import read_literal_secrets

import ast
import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable


from personal_radar_connectors.listenbrainz import USER_AGENT, BATCH_SIZE
ROOT = Path(__file__).resolve().parents[1]


def get_user_token() -> tuple[str | None,str]:
    token=os.environ.get("LISTENBRAINZ_USER_TOKEN", "").strip()
    if token:
        return token,"environment"
    token=read_literal_secrets(ROOT / "SECRETS").get("LISTENBRAINZ_USER_TOKEN", "")
    return (token,"SECRETS") if token else (None,"")


def configuration() -> dict[str, Any]:
    token, source = get_user_token()
    return {
        "configured": True,
        "provider": "ListenBrainz Popularity API",
        "authentication": "optional user token",
        "batch_size": BATCH_SIZE,
        "user_agent": USER_AGENT,
        "authenticated": bool(token),
        "token_source": source,
    }


def release_group_popularity(release_group_mbids: Iterable[str], **options) -> dict[str, int | None]:
    return listenbrainz_client.release_group_popularity(release_group_mbids, token=get_user_token()[0], **options)


def enrich_albums(
    items: list[dict[str, Any]],
    *,
    continue_on_error: bool = False,
    on_batch: Callable[[], None] | None = None,
    on_error: Callable[[ListenBrainzError], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    counts = release_group_popularity(
        (item.get("release_group_mbid") or item.get("mbid") or "" for item in items),
        continue_on_error=continue_on_error,
        on_batch=on_batch,
        on_error=on_error,
        should_cancel=should_cancel,
    )
    for item in items:
        mbid = str(item.get("release_group_mbid") or item.get("mbid") or "")
        if mbid in counts:
            item["total_listen_count"] = counts[mbid]
    return items
