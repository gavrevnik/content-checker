from __future__ import annotations

from personal_radar_connectors import cover_art_archive

from personal_radar_connectors import musicbrainz as musicbrainz_client
from personal_radar_connectors.musicbrainz import MusicBrainzError

import json
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any

from app import artwork, fanart, listenbrainz, recommendation_progress, storage


from personal_radar_connectors.musicbrainz import USER_AGENT, REQUEST_INTERVAL_SECONDS


def configuration() -> dict[str, Any]:
    return {
        "configured": True,
        "provider": "MusicBrainz Web Service API v2",
        "authentication": "public read-only",
        "rate_limit": "1 request/sec",
        "user_agent": USER_AGENT,
    }


def _request_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return musicbrainz_client._request_json(path, params)


def _cover_art_url(release_group_mbid: str) -> str:
    return cover_art_archive._cover_art_url(release_group_mbid)


_lucene = musicbrainz_client._lucene


_score = musicbrainz_client._score


_artist_payload = musicbrainz_client._artist_payload


def search_artist(name: str) -> dict[str, Any]:
    return musicbrainz_client.search_artist(name, normalize=storage._normalized, request_json=_request_json)


def artist_details(mbid: str) -> dict[str, Any]:
    return musicbrainz_client.artist_details(mbid, request_json=_request_json)


def enrich_artist_artwork(payload: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    result = dict(payload)
    mbid = str(result.get("mbid") or result.get("external_id") or "").strip()
    if not mbid or (artwork.is_cached(result.get("profile_local_path")) and not force):
        return result
    try:
        return fanart.enrich_artist_artwork(result, force=force)
    except (fanart.FanartError, artwork.ArtworkError) as error:
        result.setdefault("provider_warnings", []).append({
            "provider": "fanart.tv", "message": str(error),
        })
        return result


def resolve_artist_input(
    payload: dict[str, Any], *, include_artwork: bool = False,
) -> dict[str, Any]:
    mbid = str(payload.get("mbid") or payload.get("external_id") or "").strip()
    if mbid:
        details = artist_details(mbid)
    else:
        found = search_artist(
            str(payload.get("name") or payload.get("name_original") or payload.get("name_ru") or "")
        )
        details = artist_details(found["mbid"])
    return enrich_artist_artwork(details) if include_artwork else details


_artist_credits = musicbrainz_client._artist_credits


_named_values = musicbrainz_client._named_values


_release_group_payload = musicbrainz_client._release_group_payload


def search_album(title: str, artist: str = "", year: Any = None) -> dict[str, Any]:
    return musicbrainz_client.search_album(title, artist, year, normalize=storage._normalized, request_json=_request_json)


_best_release = musicbrainz_client._best_release


def _release_details(mbid: str) -> dict[str, Any]:
    return musicbrainz_client._release_details(mbid, request_json=_request_json)


def album_details(
    mbid: str, *, fetch_popularity: bool = True, progress_id: object = None
) -> dict[str, Any]:
    mbid = str(mbid or "").strip()
    if not mbid:
        raise MusicBrainzError("MusicBrainz ID альбома не указан")
    try:
        raw = _request_json(
            f"release-group/{mbid}",
            {"inc": "artists+releases+genres+tags"},
        )
        payload = _release_group_payload(raw)
        payload["musicbrainz_checked"] = True
        best_release = _best_release(
            [item for item in raw.get("releases", []) if isinstance(item, dict)],
            str(raw.get("first-release-date") or ""),
        )
        release_details: dict[str, Any] = {}
        if best_release:
            release_details = _release_details(str(best_release["id"]))
            payload.update({key: value for key, value in release_details.items() if key != "track_list"})
        payload["details_json"] = {
            "track_list": release_details.get("track_list", []),
            "release_status": release_details.get("release_status", ""),
            "release_title": release_details.get("release_title", ""),
            "annotation": str(raw.get("annotation") or ""),
        }
    finally:
        recommendation_progress.advance(progress_id, "musicbrainz-details")
    try:
        payload["cover_url"] = _cover_art_url(mbid)
        payload["cover_art_checked"] = True
        payload["cover_path"] = (
            artwork.cache_album_cover(mbid, payload["cover_url"])
            if payload["cover_url"] else ""
        )
        payload["cover_cache_checked"] = True
    except artwork.ArtworkError as error:
        payload["cover_cache_checked"] = False
        payload.setdefault("provider_warnings", []).append({
            "provider": "cover-art-archive",
            "message": str(error),
        })
        recommendation_progress.add_warning(progress_id, "Cover Art Archive", str(error))
    except MusicBrainzError as error:
        payload["cover_url"] = ""
        payload["cover_path"] = ""
        payload["cover_art_checked"] = False
        payload["cover_cache_checked"] = False
        payload.setdefault("provider_warnings", []).append({
            "provider": "cover-art-archive",
            "message": str(error),
        })
        recommendation_progress.add_warning(progress_id, "Cover Art Archive", str(error))
    finally:
        recommendation_progress.advance(progress_id, "cover-art")
    if fetch_popularity:
        try:
            listenbrainz.enrich_albums([payload])
        except listenbrainz.ListenBrainzError as error:
            payload.setdefault("provider_warnings", []).append({
                "provider": "listenbrainz",
                "message": str(error),
            })
    return payload


def resolve_album_input(
    payload: dict[str, Any], *, fetch_popularity: bool = True
) -> dict[str, Any]:
    mbid = str(
        payload.get("release_group_mbid") or payload.get("mbid") or payload.get("external_id") or ""
    ).strip()
    if not mbid:
        found = search_album(
            str(payload.get("title_original") or payload.get("title_ru") or ""),
            str(payload.get("artists") or payload.get("artist") or ""),
            payload.get("year"),
        )
        mbid = found["release_group_mbid"]
    return album_details(mbid, fetch_popularity=fetch_popularity)


def _album_needs_refresh(target: dict[str, Any]) -> bool:
    needs_musicbrainz = (
        not str(target.get("release_group_mbid") or "").strip()
        or not str(target.get("musicbrainz_updated_at") or "").strip()
    )
    cover_path = str(target.get("cover_path") or "").strip()
    cover_url = str(target.get("cover_url") or "").strip()
    cover_checked = bool(str(target.get("cover_art_updated_at") or "").strip())
    needs_cover = (
        bool(cover_path or cover_url) and not artwork.is_cached(cover_path)
    ) or (not cover_path and not cover_url and not cover_checked)
    needs_popularity = (
        target.get("total_listen_count") in (None, "")
        and not str(target.get("listenbrainz_updated_at") or "").strip()
    )
    return (
        needs_musicbrainz
        or needs_cover
        or needs_popularity
    )


def refresh_album(item_id: str) -> dict[str, Any]:
    target = storage.get_item(item_id)
    if target.get("content_type") != "music":
        raise MusicBrainzError("Album not found")
    needs_refresh = _album_needs_refresh(target)
    warnings: list[dict[str, str]] = []
    item = target
    if not needs_refresh:
        item["refresh_skipped"] = True
        return item
    details = resolve_album_input(target, fetch_popularity=False)
    item = storage.update_album_from_provider(item_id, details)
    warnings.extend(details.get("provider_warnings", []))
    mbid = str(item.get("release_group_mbid") or "")
    if mbid:
        try:
            counts = listenbrainz.release_group_popularity([mbid])
            storage.update_album_popularity(counts)
            item = storage.get_item(item_id)
        except listenbrainz.ListenBrainzError as error:
            warnings.append({"provider": "listenbrainz", "message": str(error)})
    if warnings:
        item["provider_warnings"] = warnings
    return item


def refresh_library(progress_id: object = None) -> dict[str, Any]:
    targets = [
        target for target in storage.album_refresh_targets()
        if _album_needs_refresh(target)
    ]
    updated_ids: set[str] = set()
    errors: list[dict[str, str]] = []
    provider_warnings: list[dict[str, str]] = []
    attempted_targets: list[dict[str, Any]] = []
    recommendation_progress.start(
        progress_id, len(targets), stage_id="library-refresh",
        label="MusicBrainz / Cover Art Archive · карточки", unit="альбомов",
    )
    for target in targets:
        if recommendation_progress.is_cancelled(progress_id):
            break
        attempted_targets.append(target)
        try:
            details = resolve_album_input(target, fetch_popularity=False)
            storage.update_album_from_provider(str(target["id"]), details)
            provider_warnings.extend(details.get("provider_warnings") or [])
            updated_ids.add(str(target["id"]))
        except Exception as error:
            errors.append({"title": str(target.get("title_original") or "Album"), "error": str(error)})
        finally:
            recommendation_progress.advance(progress_id, "library-refresh")
    popularity_targets = [storage.get_item(str(target["id"])) for target in attempted_targets]
    popularity_mbids = [
        str(item.get("release_group_mbid") or "") for item in popularity_targets
        if item.get("release_group_mbid")
    ]
    if popularity_mbids and not recommendation_progress.is_cancelled(progress_id):
        batch_total = (len(popularity_mbids) + listenbrainz.BATCH_SIZE - 1) // listenbrainz.BATCH_SIZE
        recommendation_progress.set_stage(
            progress_id, "listenbrainz", "ListenBrainz · прослушивания",
            batch_total, "пакетов",
        )
        try:
            counts = listenbrainz.release_group_popularity(
                popularity_mbids,
                on_batch=lambda: recommendation_progress.advance(progress_id, "listenbrainz"),
                should_cancel=lambda: recommendation_progress.is_cancelled(progress_id),
            )
            storage.update_album_popularity(counts)
            updated_ids.update(
                str(item["id"]) for item in popularity_targets
                if item.get("release_group_mbid") in counts
            )
        except listenbrainz.ListenBrainzError as error:
            provider_warnings.append({"provider": "listenbrainz", "message": str(error)})
    recommendation_progress.finish(progress_id)
    return {
        "total": len(targets), "updated": len(updated_ids), "failed": len(errors), "errors": errors,
        "cancelled": recommendation_progress.is_cancelled(progress_id),
        "provider_warnings": provider_warnings + ([
            {"provider": "musicbrainz", "message": f"Не удалось обновить {len(errors)} альбомов из MusicBrainz."}
        ] if errors else []),
    }


def refresh_artist(artist_id: str) -> dict[str, Any]:
    target = next(
        (artist for artist in storage.list_music_artists() if artist["id"] == artist_id), None
    )
    if not target:
        raise MusicBrainzError("Artist not found")
    details = resolve_artist_input(target, include_artwork=True)
    item = storage.update_music_artist(artist_id, details)
    if details.get("provider_warnings"):
        item["provider_warnings"] = details["provider_warnings"]
    return item


def refresh_artists() -> dict[str, Any]:
    targets = storage.artist_refresh_targets()
    updated = 0
    errors: list[dict[str, str]] = []
    for target in targets:
        try:
            refresh_artist(str(target["id"]))
            updated += 1
        except Exception as error:
            errors.append({"title": str(target.get("name_original") or "Artist"), "error": str(error)})
    return {"total": len(targets), "updated": updated, "failed": len(errors), "errors": errors}


def browse_artist_albums(
    artist_mbid: str,
    year_from: int,
    year_to: int,
    *,
    studio_albums_only: bool = False,
    include_filtered: bool = False,
) -> list[dict[str, Any]]:
    response = _request_json(
        "release-group",
        {
            "artist": artist_mbid,
            "type": "album",
            "release-group-status": "website-default",
            "inc": "artist-credits+genres+tags",
            "limit": 100,
        },
    )
    results: list[dict[str, Any]] = []
    for raw in response.get("release-groups", []) or []:
        if not isinstance(raw, dict):
            continue
        item = _release_group_payload(raw)
        item_year = item.get("year")
        if not include_filtered and item.get("primary_type").casefold() != "album".casefold():
            continue
        if not include_filtered and studio_albums_only and item.get("secondary_types"):
            continue
        if not include_filtered and (not item_year or not year_from <= int(item_year) <= year_to):
            continue
        results.append(item)
    return results


def _int_filter(payload: dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(payload.get(key) if payload.get(key) not in (None, "") else default)
    except (TypeError, ValueError) as error:
        raise MusicBrainzError(f"Некорректное значение {key}") from error
    if not minimum <= value <= maximum:
        raise MusicBrainzError(f"{key} должно быть от {minimum} до {maximum}")
    return value


def recommend_albums(filters: dict[str, Any]) -> dict[str, Any]:
    current_year = date.today().year
    year_from = _int_filter(filters, "year_from", 1900, 1900, current_year + 2)
    year_to = _int_filter(filters, "year_to", current_year + 2, 1900, current_year + 2)
    limit = _int_filter(filters, "limit", 20, 1, 50)
    if year_from > year_to:
        raise MusicBrainzError("Начальный год не может быть больше конечного")
    excluded = {
        str(value).strip().casefold()
        for value in (filters.get("excluded_types") or [])
        if str(value).strip()
    }
    excluded_title_terms = [
        (storage._normalized(str(value)), str(value).strip())
        for value in (filters.get("excluded_title_terms") or [])
        if storage._normalized(str(value))
    ]
    selected_ids = {str(value) for value in (filters.get("artist_ids") or [])}
    artists = storage.list_music_artists()
    if "artist_ids" in filters:
        artists = [artist for artist in artists if artist["id"] in selected_ids]
    artists = [artist for artist in artists if artist.get("mbid")]
    if not artists:
        raise MusicBrainzError("Нет выбранных исполнителей с MusicBrainz ID")
    progress_id = filters.get("progress_id")
    recommendation_progress.start(
        progress_id, len(artists), stage_id="musicbrainz-artists",
        label="MusicBrainz · альбомы исполнителей", unit="исполнителей",
    )
    known_ids, known_titles = storage.known_album_keys()
    candidates: dict[str, dict[str, Any]] = {}
    filtered_items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for artist in artists:
        if recommendation_progress.is_cancelled(progress_id):
            break
        try:
            for item in browse_artist_albums(
                str(artist["mbid"]), year_from, year_to, include_filtered=True,
            ):
                mbid = str(item.get("release_group_mbid") or "")
                normalized = storage._normalized(str(item.get("title_original") or ""))
                secondary = {str(value).casefold() for value in item.get("secondary_types", [])}
                if mbid in known_ids or normalized in known_titles:
                    continue
                item_year = item.get("year")
                if not item_year or not year_from <= int(item_year) <= year_to:
                    # The year range is the discovery period, not a rejection reason shown to users.
                    continue
                reasons: list[str] = []
                if not mbid:
                    reasons.append("MusicBrainz ID недоступен")
                primary_type = str(item.get("primary_type") or "")
                if primary_type and primary_type.casefold() != "album":
                    reasons.append("основной тип релиза не Album")
                excluded_secondary = [
                    str(value) for value in item.get("secondary_types", [])
                    if str(value).casefold() in excluded
                ]
                if excluded_secondary:
                    reasons.append(f"исключённый тип: {', '.join(excluded_secondary)}")
                excluded_terms = [label for term, label in excluded_title_terms if term in normalized]
                if excluded_terms:
                    reasons.append(f"название содержит: {', '.join(excluded_terms)}")
                if reasons:
                    filtered_items.append({"item": item, "reason": "; ".join(reasons)})
                    continue
                candidates.setdefault(mbid, item)
        except Exception as error:
            errors.append({"title": str(artist.get("name_original") or "Artist"), "error": str(error)})
            recommendation_progress.add_warning(progress_id, "MusicBrainz", str(error))
        finally:
            recommendation_progress.advance(progress_id, "musicbrainz-artists")
    recommendation_progress.finish_stage(progress_id, "musicbrainz-artists")
    ordered = sorted(
        candidates.values(),
        key=lambda item: int(item.get("year") or 0),
        reverse=True,
    )
    selected = ordered[:limit]
    recommendation_progress.set_stage(
        progress_id, "musicbrainz-details", "MusicBrainz · карточки альбомов",
        len(selected), "альбомов",
    )
    recommendation_progress.set_stage(
        progress_id, "cover-art", "Cover Art Archive · обложки",
        len(selected), "альбомов",
    )
    items: list[dict[str, Any]] = []
    for candidate in selected:
        if recommendation_progress.is_cancelled(progress_id):
            break
        try:
            items.append(album_details(
                str(candidate["release_group_mbid"]), fetch_popularity=False,
                progress_id=progress_id,
            ))
        except Exception as error:
            reason = str(error)
            errors.append({"title": str(candidate.get("title_original") or "Album"), "error": reason})
            filtered_items.append({"item": candidate, "reason": reason})
            recommendation_progress.advance(progress_id, "cover-art")
            recommendation_progress.add_warning(progress_id, "MusicBrainz", str(error))
    recommendation_progress.finish_stage(progress_id, "musicbrainz-details")
    recommendation_progress.finish_stage(progress_id, "cover-art")
    warnings: list[dict[str, str]] = []
    if items:
        batch_total = (len(items) + listenbrainz.BATCH_SIZE - 1) // listenbrainz.BATCH_SIZE
        recommendation_progress.set_stage(
            progress_id, "listenbrainz", "ListenBrainz · прослушивания",
            batch_total, "пакетов",
        )

        def popularity_error(error: listenbrainz.ListenBrainzError) -> None:
            warning = {"provider": "listenbrainz", "message": str(error)}
            if warning not in warnings:
                warnings.append(warning)
            recommendation_progress.add_warning(progress_id, "ListenBrainz", str(error))

        listenbrainz.enrich_albums(
            items,
            continue_on_error=True,
            on_batch=lambda: recommendation_progress.advance(progress_id, "listenbrainz"),
            on_error=popularity_error,
            should_cancel=lambda: recommendation_progress.is_cancelled(progress_id),
        )
        recommendation_progress.finish_stage(progress_id, "listenbrainz")
    else:
        recommendation_progress.set_stage(
            progress_id, "listenbrainz", "ListenBrainz · прослушивания", 0, "пакетов"
        )
        recommendation_progress.finish_stage(progress_id, "listenbrainz")
    if errors:
        warnings.append({
            "provider": "musicbrainz",
            "message": f"MusicBrainz вернул неполные данные: пропущено {len(errors)} позиций.",
        })
    item_warnings = [
        warning for item in items for warning in item.get("provider_warnings", [])
        if isinstance(warning, dict)
    ]
    combined_warnings: list[dict[str, str]] = []
    for warning in [*item_warnings, *warnings]:
        if warning not in combined_warnings:
            combined_warnings.append(warning)
    for item in items:
        item["provider_warnings"] = item.get("provider_warnings", []) + warnings
    recommendation_progress.finish(progress_id)
    return {
        "items": items, "filtered_items": filtered_items,
        "errors": errors, "provider_warnings": combined_warnings,
        "cancelled": recommendation_progress.is_cancelled(progress_id),
    }
