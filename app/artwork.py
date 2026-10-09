from __future__ import annotations

from personal_radar_connectors.images import ArtworkError, read_image, album_cover_url, movie_poster_url, person_profile_url, preferred_album_cover_url

import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from app.paths import DATA_DIR

ARTWORK_DIR = DATA_DIR / "artwork"
USER_AGENT = "whats-new-checker/2.1 (gavrevns@gmail.com)"
MAX_IMAGE_BYTES = 15 * 1024 * 1024
ALBUM_COVER_SIZE = 250
MOVIE_POSTER_SIZE = "w185"
PERSON_PROFILE_SIZE = "w185"
FANART_ARTIST_WIDTH = 200


def _safe_identifier(value: object) -> str:
    normalized = "".join(character for character in str(value or "") if character.isalnum() or character in "-_")
    if not normalized:
        raise ArtworkError("Не удалось определить идентификатор изображения")
    return normalized


def _relative_path(kind: str, identifier: object) -> str:
    folder = {"album": "albums", "movie": "movies", "person": "people", "music_artist": "people"}.get(kind)
    if not folder:
        raise ArtworkError("Неизвестный тип изображения")
    return f"{folder}/{_safe_identifier(identifier)}.jpg"


def resolve_local_path(relative_path: str) -> Path:
    candidate = (ARTWORK_DIR / str(relative_path or "").lstrip("/")).resolve()
    try:
        candidate.relative_to(ARTWORK_DIR.resolve())
    except ValueError as error:
        raise ArtworkError("Недопустимый путь изображения") from error
    return candidate


def is_cached(relative_path: object) -> bool:
    if not relative_path:
        return False
    try:
        return resolve_local_path(str(relative_path)).is_file()
    except ArtworkError:
        return False


def delete_cached(relative_path: object) -> bool:
    """Delete one cached artwork file without allowing paths outside ARTWORK_DIR."""
    if not relative_path:
        return False
    candidate = resolve_local_path(str(relative_path))
    try:
        candidate.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ArtworkError(f"Не удалось удалить локальное изображение: {error}") from error


def _download(
    url: str, destination: Path, *, maximum_bytes: int = MAX_IMAGE_BYTES,
    resize_width: int | None = None,
) -> bool:
    if not url:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    temporary_name = ""
    try:
        body=read_image(url,maximum_bytes=maximum_bytes,user_agent=USER_AGENT)
        if body is None:
            return False
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.stem}.", suffix=destination.suffix, dir=destination.parent,
        )
        with os.fdopen(handle, "wb") as stream:
            stream.write(body)
        if resize_width:
            sips = shutil.which("sips")
            if not sips:
                raise ArtworkError("Для уменьшения fanart.tv-изображений требуется системная утилита sips")
            resized = subprocess.run(
                [sips, "-Z", str(resize_width), temporary_name],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if resized.returncode:
                raise ArtworkError("Не удалось уменьшить изображение fanart.tv")
        os.replace(temporary_name, destination)
        return True
    except urllib.error.HTTPError as error:
        error.close()
        if error.code == 404:
            return False
        raise ArtworkError(f"Не удалось загрузить изображение: HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, subprocess.TimeoutExpired, OSError) as error:
        raise ArtworkError(f"Не удалось загрузить изображение: {error}") from error
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass


def cache_album_cover(release_group_mbid: str, cover_url: str = "", *, force: bool = False) -> str:
    relative = _relative_path("album", release_group_mbid)
    destination = resolve_local_path(relative)
    if destination.is_file() and not force:
        return relative
    return relative if _download(preferred_album_cover_url(release_group_mbid, cover_url), destination) else ""


def cache_movie_poster(
    tmdb_id: object, poster_path: str = "", poster_url: str = "", *, force: bool = False,
) -> str:
    relative = _relative_path("movie", tmdb_id)
    destination = resolve_local_path(relative)
    if destination.is_file() and not force:
        return relative
    return relative if _download(movie_poster_url(poster_url or poster_path), destination) else ""


def cache_person_profile(
    tmdb_id: object, profile_path: str = "", profile_url: str = "", *, force: bool = False,
) -> str:
    relative = _relative_path("person", tmdb_id)
    destination = resolve_local_path(relative)
    if destination.is_file() and not force:
        return relative
    return relative if _download(person_profile_url(profile_url or profile_path), destination) else ""


def cache_music_artist_profile(
    mbid: object, profile_url: str = "", *, force: bool = False,
) -> str:
    relative = _relative_path("music_artist", f"music-{mbid}")
    destination = resolve_local_path(relative)
    if destination.is_file() and not force:
        return relative
    return relative if _download(
        str(profile_url or "").strip(), destination, maximum_bytes=2 * 1024 * 1024,
        resize_width=FANART_ARTIST_WIDTH,
    ) else ""


def content_type(relative_path: str) -> str:
    try:
        header = resolve_local_path(relative_path).read_bytes()[:12]
    except OSError:
        header = b""
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    return mimetypes.guess_type(relative_path)[0] or "image/jpeg"
