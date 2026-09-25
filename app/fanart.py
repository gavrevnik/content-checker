from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app import artwork, tmdb


BASE_URL = "https://webservice.fanart.tv/v3.2/music"
USER_AGENT = "WhatsNewChecker/2.1 (gavrevns@gmail.com)"


class FanartError(RuntimeError):
    pass


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


def _request_artist(mbid: str, api_key: str) -> dict[str, Any]:
    artist_id = str(mbid or "").strip()
    if not artist_id:
        raise FanartError("MusicBrainz ID исполнителя не указан")
    url = f"{BASE_URL}/{urllib.parse.quote(artist_id)}?{urllib.parse.urlencode({'api_key': api_key})}"
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        error.close()
        if error.code == 404:
            return {}
        if error.code == 401:
            raise FanartError("fanart.tv отклонил FANART_PROJECT_KEY") from error
        raise FanartError(f"fanart.tv: HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, socket.timeout) as error:
        reason = getattr(error, "reason", error)
        raise FanartError(f"fanart.tv недоступен: {reason}") from error
    except json.JSONDecodeError as error:
        raise FanartError("fanart.tv вернул повреждённый JSON") from error
    if not isinstance(payload, dict):
        raise FanartError("fanart.tv вернул ответ неожиданного формата")
    return payload


def _likes(image: dict[str, Any]) -> int:
    try:
        return int(image.get("likes") or 0)
    except (TypeError, ValueError):
        return 0


def artist_thumb_url(mbid: str, api_key: str | None = None) -> str:
    key = str(api_key or "").strip() or str(get_api_key()[0] or "")
    if not key:
        raise FanartError("FANART_PROJECT_KEY is not configured")
    payload = _request_artist(mbid, key)
    images = [
        image for image in (payload.get("artistthumb") or [])
        if isinstance(image, dict) and str(image.get("url") or "").startswith(("http://", "https://"))
    ]
    if not images:
        return ""
    images.sort(
        key=lambda image: (
            _likes(image),
            str(image.get("lang") or "") in {"00", "en", ""},
            int(image.get("width") or 0) * int(image.get("height") or 0),
        ),
        reverse=True,
    )
    return str(images[0]["url"])


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
