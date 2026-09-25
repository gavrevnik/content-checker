from __future__ import annotations

import argparse
import json
import mimetypes
import os
import threading
import urllib.request
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import artwork, fanart, listenbrainz, llm, musicbrainz, recommendation_progress, storage, tmdb


STATIC_DIR = Path(__file__).resolve().parent / "static"
PID_FILE = Path(__file__).resolve().parents[1] / ".runtime" / "server.pid"
APPLICATION_ID = "whats-new-checker"
APP_VERSION = 56


class Handler(BaseHTTPRequestHandler):
    server_version = "WhatsNewChecker/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[web] {self.address_string()} {fmt % args}")

    def _json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        self._json({"error": message}, status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("Request is too large")
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            raise ValueError("JSON object expected")
        return payload

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._json({
                "ok": True, "application": APPLICATION_ID,
                "version": APP_VERSION, "storage": "sqlite",
            })
            return
        if parsed.path == "/api/library":
            query = parse_qs(parsed.query)
            self._json(
                {
                    "items": storage.list_library(
                        content_type=query.get("content_type", [None])[0],
                        status=query.get("status", [None])[0],
                    )
                }
            )
            return
        if parsed.path == "/api/backlog/moods":
            query = parse_qs(parsed.query)
            content_type = query.get("content_type", ["movie"])[0]
            self._json({"moods": storage.list_backlog_moods(content_type)})
            return
        if parsed.path == "/api/meta":
            self._json(
                {
                    "interests": storage.list_interests(),
                    "content_types": storage.list_content_types(),
                    "tmdb": tmdb.configuration(),
                    "musicbrainz": musicbrainz.configuration(),
                    "fanart": fanart.configuration(),
                    "listenbrainz": listenbrainz.configuration(),
                    "llm": llm.configuration(),
                }
            )
            return
        if parsed.path == "/api/people":
            query = parse_qs(parsed.query)
            self._json({
                "items": storage.list_interests(
                    query.get("content_type", ["movie"])[0], query.get("role", [None])[0]
                )
            })
            return
        if parsed.path == "/api/trash":
            self._json({"items": storage.list_trash()})
            return
        if parsed.path == "/api/recommendations/progress":
            query = parse_qs(parsed.query)
            self._json(recommendation_progress.get(query.get("id", [""])[0]))
            return
        if parsed.path.startswith("/api/artwork/"):
            self._serve_item_artwork(parsed.path)
            return
        if parsed.path.startswith("/media/artwork/"):
            self._serve_artwork_file(unquote(parsed.path[len("/media/artwork/"):]))
            return
        self._serve_static(parsed.path)

    def _serve_item_artwork(self, url_path: str) -> None:
        parts = [unquote(part) for part in url_path.split("/") if part]
        if len(parts) != 4 or parts[:2] != ["api", "artwork"] or parts[2] not in {"movie", "music", "person"}:
            self._error("Not found", HTTPStatus.NOT_FOUND)
            return
        content_type, item_id = parts[2], parts[3]
        try:
            if content_type == "person":
                try:
                    person = storage.get_interest_person(item_id)
                except storage.StorageError:
                    person = storage.get_music_artist(item_id)
                is_music_artist = person.get("content_type") == "music"
                relative = str(person.get("profile_local_path") or "")
                if not artwork.is_cached(relative):
                    relative = (
                        artwork.cache_music_artist_profile(
                            person.get("mbid"), str(person.get("profile_url") or ""),
                        )
                        if is_music_artist else artwork.cache_person_profile(
                            person.get("tmdb_id"), str(person.get("profile_path") or ""),
                            str(person.get("profile_url") or ""),
                        )
                    )
                if not relative:
                    self._error("Изображение отсутствует", HTTPStatus.NOT_FOUND)
                    return
                if relative != person.get("profile_local_path"):
                    if is_music_artist:
                        storage.update_music_artist_artwork_path(
                            item_id, relative, str(person.get("profile_url") or ""),
                        )
                    else:
                        storage.update_person_artwork_path(
                            item_id, relative, str(person.get("profile_path") or ""),
                            artwork.person_profile_url(str(person.get("profile_url") or person.get("profile_path") or "")),
                        )
                self._serve_artwork_file(relative)
                return
            item = storage.get_item(item_id)
            if item.get("content_type") != content_type:
                raise storage.StorageError("Item not found")
            if content_type == "music":
                relative = str(item.get("cover_path") or "")
                if not artwork.is_cached(relative):
                    relative = artwork.cache_album_cover(
                        str(item.get("release_group_mbid") or ""), str(item.get("cover_url") or "")
                    )
            else:
                relative = str(item.get("poster_local_path") or "")
                if not artwork.is_cached(relative):
                    relative = artwork.cache_movie_poster(
                        item.get("tmdb_id"), str(item.get("poster_path") or ""),
                        str(item.get("poster_url") or ""),
                    )
            if not relative:
                self._error("Изображение отсутствует", HTTPStatus.NOT_FOUND)
                return
            stored_path = item.get("cover_path") if content_type == "music" else item.get("poster_local_path")
            if relative != stored_path:
                storage.update_artwork_path(item_id, content_type, relative)
            self._serve_artwork_file(relative)
        except (storage.StorageError, artwork.ArtworkError) as error:
            self._error(str(error), HTTPStatus.NOT_FOUND)

    def _serve_artwork_file(self, relative_path: str) -> None:
        try:
            candidate = artwork.resolve_local_path(relative_path)
        except artwork.ArtworkError:
            self._error("Not found", HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self._error("Not found", HTTPStatus.NOT_FOUND)
            return
        body = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", artwork.content_type(relative_path))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._body()
            if parsed.path == "/api/search/movie":
                self._json(tmdb.search_movie_candidates(payload))
                return
            if parsed.path == "/api/search/person":
                self._json(tmdb.search_person_candidates(payload))
                return
            if parsed.path == "/api/resolve/movie":
                self._json({"item": tmdb.resolve_movie_input(payload)})
                return
            if parsed.path == "/api/resolve/person":
                item = (
                    musicbrainz.resolve_artist_input(payload, include_artwork=True)
                    if payload.get("content_type") == "music"
                    else tmdb.resolve_person_input(payload)
                )
                self._json({"item": item})
                return
            if parsed.path == "/api/resolve/album":
                self._json({"item": musicbrainz.resolve_album_input(payload)})
                return
            if parsed.path == "/api/trash":
                self._json({"item": storage.trash_entity(payload)}, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/trash/empty":
                self._json(storage.empty_trash())
                return
            if parsed.path.startswith("/api/trash/") and parsed.path.endswith("/restore"):
                trash_id = unquote(parsed.path[len("/api/trash/"):-len("/restore")]).strip("/")
                if not trash_id:
                    raise ValueError("Trash item ID is required")
                self._json({"item": storage.restore_trash(trash_id)})
                return
            if parsed.path == "/api/library":
                self._json({"item": storage.add_item(payload)}, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/operations/cancel":
                progress_id = str(payload.get("progress_id") or "").strip()
                if not progress_id:
                    raise ValueError("Progress ID is required")
                self._json({"cancel_requested": recommendation_progress.cancel(progress_id)})
                return
            if parsed.path == "/api/library/refresh-tmdb":
                self._json(tmdb.refresh_library(payload.get("progress_id")))
                return
            if parsed.path == "/api/library/refresh-musicbrainz":
                self._json(musicbrainz.refresh_library(payload.get("progress_id")))
                return
            if parsed.path.startswith("/api/library/") and parsed.path.endswith("/favorite"):
                item_id = unquote(parsed.path[len("/api/library/"):-len("/favorite")]).strip("/")
                if not item_id:
                    raise ValueError("Library item ID is required")
                favorite = payload.get("favorite")
                if not isinstance(favorite, bool):
                    raise ValueError("favorite must be a boolean")
                self._json({"item": storage.set_favorite(item_id, favorite)})
                return
            if parsed.path.startswith("/api/library/") and parsed.path.endswith("/refresh-tmdb"):
                item_id = unquote(parsed.path[len("/api/library/"):-len("/refresh-tmdb")]).strip("/")
                if not item_id:
                    raise ValueError("Movie ID is required")
                self._json({"item": tmdb.refresh_movie(item_id)})
                return
            if parsed.path.startswith("/api/library/") and parsed.path.endswith("/refresh-musicbrainz"):
                item_id = unquote(parsed.path[len("/api/library/"):-len("/refresh-musicbrainz")]).strip("/")
                if not item_id:
                    raise ValueError("Album ID is required")
                self._json({"item": musicbrainz.refresh_album(item_id)})
                return
            if parsed.path == "/api/people":
                if payload.get("content_type") == "music":
                    raw_data = payload.get("raw_data") or dict(payload)
                    payload = (
                        musicbrainz.enrich_artist_artwork(payload)
                        if payload.get("mbid") or payload.get("external_id")
                        else musicbrainz.resolve_artist_input(payload, include_artwork=True)
                    )
                    payload["raw_data"] = raw_data
                self._json({"item": storage.add_interest_person(payload)}, HTTPStatus.CREATED)
                return
            if parsed.path == "/api/people/refresh-tmdb":
                self._json(tmdb.refresh_people())
                return
            if parsed.path == "/api/people/refresh-musicbrainz":
                self._json(musicbrainz.refresh_artists())
                return
            if parsed.path.startswith("/api/people/") and parsed.path.endswith("/refresh-tmdb"):
                person_id = unquote(parsed.path[len("/api/people/"):-len("/refresh-tmdb")]).strip("/")
                if not person_id:
                    raise ValueError("Person ID is required")
                self._json({"item": tmdb.refresh_person(person_id, payload.get("role"))})
                return
            if parsed.path.startswith("/api/people/") and parsed.path.endswith("/refresh-musicbrainz"):
                person_id = unquote(parsed.path[len("/api/people/"):-len("/refresh-musicbrainz")]).strip("/")
                if not person_id:
                    raise ValueError("Artist ID is required")
                self._json({"item": musicbrainz.refresh_artist(person_id)})
                return
            if parsed.path == "/api/recommendations/tmdb":
                self._json(tmdb.recommend_movies(payload))
                return
            if parsed.path == "/api/recommendations/llm":
                self._json(
                    llm.recommend_albums(payload)
                    if payload.get("content_type") == "music"
                    else llm.recommend_movies(payload)
                )
                return
            if parsed.path == "/api/backlog/moods/llm":
                self._json(llm.suggest_backlog_moods(payload))
                return
            if parsed.path == "/api/recommendations/people/llm":
                self._json(llm.recommend_people(payload))
                return
            if parsed.path == "/api/recommendations/prompt":
                self._json({"prompt": llm.build_recommendation_prompt(payload)})
                return
            if parsed.path == "/api/recommendations/musicbrainz":
                self._json(musicbrainz.recommend_albums(payload))
                return
            self._error("Endpoint not found", HTTPStatus.NOT_FOUND)
        except (
            ValueError, storage.StorageError, tmdb.TmdbError, musicbrainz.MusicBrainzError,
            listenbrainz.ListenBrainzError, llm.LlmError,
        ) as error:
            self._error(str(error))
        except Exception as error:
            self._error(f"Internal error: {error}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PATCH(self) -> None:
        parsed = urlparse(self.path)
        prefix = "/api/library/"
        if not parsed.path.startswith(prefix):
            self._error("Endpoint not found", HTTPStatus.NOT_FOUND)
            return
        try:
            item_id = unquote(parsed.path[len(prefix):])
            self._json({"item": storage.update_item(item_id, self._body())})
        except (ValueError, storage.StorageError) as error:
            self._error(str(error))

    def _serve_static(self, url_path: str) -> None:
        relative = "index.html" if url_path in ("", "/") else unquote(url_path.lstrip("/"))
        candidate = (STATIC_DIR / relative).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._error("Not found", HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self._error("Not found", HTTPStatus.NOT_FOUND)
            return
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _health_payload(host: str, port: int) -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=0.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if response.status == 200 and isinstance(payload, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _already_running(host: str, port: int) -> bool:
    payload = _health_payload(host, port)
    return bool(
        payload
        and payload.get("application") == APPLICATION_ID
        and payload.get("version") == APP_VERSION
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local content library")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    url = f"http://{args.host}:{args.port}"
    if _already_running(args.host, args.port):
        if args.open_browser:
            webbrowser.open(url)
        print(f"Already running at {url}")
        return
    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as error:
        raise SystemExit(
            f"Cannot start {APPLICATION_ID}: {args.host}:{args.port} is occupied ({error})"
        ) from error
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    if args.open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    print(f"Content library is available at {url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        try:
            if PID_FILE.read_text(encoding="utf-8").strip() == str(os.getpid()):
                PID_FILE.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
