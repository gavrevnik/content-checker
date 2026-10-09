from __future__ import annotations

from personal_radar_connectors import fanart as fanart_client
from personal_radar_connectors.fanart import FanartError

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app import artwork, tmdb


def get_api_key() -> tuple[str | None, str]:
    value = os.environ.get("FANART_PROJECT_KEY", "").strip()
    if value:
        return value, "environment"
    value = tmdb._local_secrets().get("FANART_PROJECT_KEY", "")
    return (value, "SECRETS") if value else (None, "not configured")


def configuration() -> dict[str, Any]:
    api_key, source = get_api_key()
    return {
        "configured": bool(api_key),
        "provider": "fanart.tv API v3.2",
        "key_source": source,
        "image_type": "artistthumb",
        "cached_width": artwork.FANART_ARTIST_WIDTH,
    }


_request_artist = fanart_client._request_artist


def artist_thumb_url(mbid: str, api_key: str | None = None) -> str:
    return fanart_client.artist_thumb_url(mbid, api_key or get_api_key()[0], request_artist=_request_artist)


def enrich_artist_artwork(artist: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    result = dict(artist)
    mbid = str(result.get("mbid") or result.get("external_id") or "").strip()
    if not mbid:
        return result
    profile_url = artist_thumb_url(mbid)
    result["profile_url"] = profile_url
    result["profile_local_path"] = (
        artwork.cache_music_artist_profile(mbid, profile_url, force=force)
        if profile_url else ""
    )
    result["fanart_checked"] = True
    return result
