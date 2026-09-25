from __future__ import annotations

import ast
import difflib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any

from app import artwork, recommendation_progress, storage
from app.storage import ROOT, _normalized


BASE_URL = "https://api.themoviedb.org/3"
OMDB_URL = "https://www.omdbapi.com/"
KINOPOISK_URL = "https://kinopoiskapiunofficial.tech/api/v2.2/films"
TMDB_GENRE_ALIASES = {
    28: {"action", "боевик"},
    12: {"adventure", "приключения"},
    16: {"animation", "animated", "анимация", "мультфильм", "анимационный"},
    35: {"comedy", "комедия"},
    80: {"crime", "криминал", "криминальный"},
    99: {"documentary", "документальный", "документальное кино", "документалистика"},
    18: {"drama", "драма"},
    10751: {"family", "семейный"},
    14: {"fantasy", "фэнтези"},
    36: {"history", "история", "исторический"},
    27: {"horror", "ужасы"},
    10402: {"music", "музыка", "музыкальный"},
    9648: {"mystery", "детектив"},
    10749: {"romance", "мелодрама", "романтика"},
    878: {"science fiction", "sci-fi", "фантастика", "научная фантастика"},
    10770: {"tv movie", "телевизионный фильм"},
    53: {"thriller", "триллер"},
    10752: {"war", "военный"},
    37: {"western", "вестерн"},
}


class TmdbError(RuntimeError):
    pass


def _excluded_genre_filters(raw: Any) -> tuple[set[int], set[str]]:
    values = raw if isinstance(raw, list) else str(raw or "").split(",")
    requested = {_normalized(str(value)) for value in values if _normalized(str(value))}
    genre_ids: set[int] = set()
    names = set(requested)
    for genre_id, aliases in TMDB_GENRE_ALIASES.items():
        normalized_aliases = {_normalized(alias) for alias in aliases}
        if requested & normalized_aliases:
            genre_ids.add(genre_id)
            names.update(normalized_aliases)
    return genre_ids, names


def _local_secrets() -> dict[str, str]:
    secrets_path = ROOT / "SECRETS"
    if not secrets_path.exists():
        return {}
    try:
        tree = ast.parse(secrets_path.read_text(encoding="utf-8"), filename=str(secrets_path))
    except (OSError, SyntaxError):
        return {}
    values: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue
        if isinstance(value, str):
            values[node.targets[0].id] = value.strip()
    return values


def get_api_key() -> tuple[str | None, str]:
    value = os.environ.get("TMDB_API_KEY", "").strip()
    if value:
        return value, "environment"
    value = _local_secrets().get("TMDB_API_KEY", "")
    return (value, "SECRETS") if value else (None, "not configured")


def get_omdb_key() -> tuple[str | None, str]:
    value = os.environ.get("OMDB_API_KEY", "").strip()
    if value:
        return value, "environment"
    value = _local_secrets().get("OMDB_API_KEY", "")
    return (value, "SECRETS") if value else (None, "not configured")


def get_kinopoisk_key() -> tuple[str | None, str]:
    value = os.environ.get("KINOPOISK_API_KEY", "").strip()
    if value:
        return value, "environment"
    value = _local_secrets().get("KINOPOISK_API_KEY", "")
    return (value, "SECRETS") if value else (None, "not configured")


def _request_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "WhatsNewChecker/2.0", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        message = error.read().decode("utf-8", errors="replace")[:300]
        raise TmdbError(f"Provider returned HTTP {error.code}: {message}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise TmdbError(f"Provider request failed: {error}") from error
    if not isinstance(payload, dict):
        raise TmdbError("Provider returned an invalid response")
    return payload


def _get(path: str, params: dict[str, Any], api_key: str) -> dict[str, Any]:
    query = urllib.parse.urlencode({**params, "api_key": api_key})
    try:
        return _request_json(f"{BASE_URL}{path}?{query}")
    except TmdbError as error:
        raise TmdbError(f"TMDB: {error}") from error


def _get_omdb(imdb_id: str) -> dict[str, Any]:
    api_key, _ = get_omdb_key()
    if not api_key or not imdb_id:
        return {}
    query = urllib.parse.urlencode({"i": imdb_id, "apikey": api_key, "plot": "full"})
    try:
        payload = _request_json(f"{OMDB_URL}?{query}")
    except TmdbError:
        return {}
    response_status = payload.get("Response")
    if response_status == "False":
        return {"omdb_checked": True}
    if response_status != "True":
        return {}
    def clean(key: str) -> str:
        value = str(payload.get(key) or "").strip()
        return "" if value in ("", "N/A") else value

    rating_raw = clean("imdbRating")
    metascore_raw = clean("Metascore")
    awards = str(payload.get("Awards") or "").strip()
    try:
        numeric_rating = float(rating_raw) if rating_raw else None
    except ValueError:
        numeric_rating = None
    return {
        "omdb_checked": True,
        "imdb_rating": numeric_rating,
        "imdb_votes": clean("imdbVotes"),
        "metascore": int(metascore_raw) if metascore_raw.isdigit() else None,
        "content_rating": clean("Rated"),
        "box_office": clean("BoxOffice"),
        "omdb_plot": clean("Plot"),
        "omdb_writers": clean("Writer"),
        "omdb_cast": clean("Actors"),
        "omdb_countries": clean("Country"),
        "omdb_languages": clean("Language"),
        "awards_json": [{"source": "omdb", "summary": awards}] if awards and awards != "N/A" else [],
    }


def _get_kinopoisk(imdb_id: str, title_original: str, title_ru: str, year: Any) -> dict[str, Any]:
    """Resolve a movie by IMDb ID, falling back to a normalized title search."""
    api_key, _ = get_kinopoisk_key()
    if not api_key:
        return {}
    target_year = int(year) if str(year).isdigit() else None
    request_errors: list[TmdbError] = []
    request_succeeded = False

    def fetch(params: dict[str, Any]) -> list[dict[str, Any]]:
        nonlocal request_succeeded
        try:
            payload = _request_json(
                f"{KINOPOISK_URL}?{urllib.parse.urlencode(params)}",
                {"X-API-KEY": api_key},
            )
        except TmdbError as error:
            request_errors.append(error)
            return []
        items = payload.get("items", [])
        if not isinstance(items, list):
            return []
        request_succeeded = True
        return [item for item in items if isinstance(item, dict)]

    candidates: list[dict[str, Any]] = []
    if imdb_id:
        imdb_items = fetch({"imdbId": imdb_id})
        candidates = [item for item in imdb_items if str(item.get("imdbId") or "") == imdb_id]

    if not candidates:
        query = (title_ru or title_original).translate(str.maketrans({"ё": "е", "Ё": "Е"}))
        if query:
            params: dict[str, Any] = {"keyword": query, "type": "FILM"}
            if target_year is not None:
                params.update({"yearFrom": max(1000, target_year - 1), "yearTo": target_year + 1})
            title_items = fetch(params)
            wanted = {_normalized(title_original), _normalized(title_ru)} - {""}
            for item in title_items:
                names = {
                    _normalized(str(item.get("nameOriginal") or "")),
                    _normalized(str(item.get("nameEn") or "")),
                    _normalized(str(item.get("nameRu") or "")),
                } - {""}
                candidate_year = item.get("year")
                year_matches = (
                    target_year is None
                    or str(candidate_year).isdigit() and abs(int(candidate_year) - target_year) <= 1
                )
                if wanted & names and year_matches:
                    candidates.append(item)

    if not candidates and request_errors:
        error = request_errors[-1]
        # This companion provider must not make an otherwise valid TMDB refresh fail.
        is_limit = "HTTP 402" in str(error) or "HTTP 429" in str(error)
        message = (
            "Достигнут лимит запросов Kinopoisk API. Фильм создан без рейтинга и ссылки КП."
            if is_limit
            else f"Kinopoisk API недоступен: {error}. Фильм создан без данных КП."
        )
        return {
            "provider_warnings": [{
                "provider": "kinopoisk",
                "kind": "limit" if is_limit else "error",
                "message": message,
            }],
        }
    if not candidates:
        return {"kinopoisk_checked": True} if request_succeeded else {}
    candidate = max(candidates, key=lambda item: float(item.get("ratingKinopoisk") or 0))
    kinopoisk_id = candidate.get("kinopoiskId")
    rating = candidate.get("ratingKinopoisk")
    if not str(kinopoisk_id).isdigit():
        return {"kinopoisk_checked": True}
    try:
        numeric_rating = float(rating) if rating not in (None, "") else None
    except (TypeError, ValueError):
        numeric_rating = None
    return {
        "kinopoisk_checked": True,
        "kinopoisk_id": int(kinopoisk_id),
        "kinopoisk_rating": numeric_rating,
        "kinopoisk_link": f"https://www.kinopoisk.ru/film/{int(kinopoisk_id)}/",
    }


def configuration() -> dict[str, Any]:
    key, source = get_api_key()
    omdb_key, omdb_source = get_omdb_key()
    kinopoisk_key, kinopoisk_source = get_kinopoisk_key()
    return {
        "configured": bool(key), "source": source,
        "omdb_configured": bool(omdb_key), "omdb_source": omdb_source,
        "kinopoisk_configured": bool(kinopoisk_key), "kinopoisk_source": kinopoisk_source,
        "awards_note": "TMDB does not provide structured awards; OMDb summary is used when configured.",
    }


def _interest_index() -> dict[int, set[str]]:
    interests: dict[int, set[str]] = {}
    for row in storage.list_interests("movie"):
        external_id = row.get("external_id")
        role = str(row.get("role") or "")
        if str(external_id).isdigit() and role in {"actor", "director"}:
            interests.setdefault(int(external_id), set()).add(role)
    return interests


def movie_details(
    tmdb_id: int,
    api_key: str | None = None,
    fetch_kinopoisk: bool = True,
    fetch_omdb: bool = True,
) -> dict[str, Any]:
    api_key = api_key or get_api_key()[0]
    if not api_key:
        raise TmdbError("TMDB_API_KEY is not configured")
    details = _get(
        f"/movie/{tmdb_id}",
        {"language": "ru-RU", "append_to_response": "credits,release_dates,keywords"},
        api_key,
    )
    english_details = _get(
        f"/movie/{tmdb_id}",
        {"language": "en-US", "append_to_response": "credits"},
        api_key,
    )
    interests = _interest_index()
    crew = details.get("credits", {}).get("crew", [])
    cast = details.get("credits", {}).get("cast", [])
    english_crew = {person.get("id"): person for person in english_details.get("credits", {}).get("crew", [])}
    english_cast = {person.get("id"): person for person in english_details.get("credits", {}).get("cast", [])}

    def credit_names(person: dict[str, Any], english_index: dict[Any, dict[str, Any]]) -> dict[str, str]:
        original = str(english_index.get(person.get("id"), {}).get("name") or person.get("name") or "")
        localized = str(person.get("name") or original)
        return {"name": localized, "name_ru": localized if localized != original else "", "name_original": original}

    directors_data = [
        {**credit_names(person, english_crew), "tmdb_id": person.get("id"), "role": "director", "job": "Director"}
        for person in crew if person.get("job") == "Director"
    ]
    key_people_data: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for person in crew:
        tmdb_person_id = person.get("id")
        interest_roles = interests.get(tmdb_person_id, set())
        if "director" not in interest_roles or person.get("job") != "Director":
            continue
        marker = (tmdb_person_id, "director")
        if marker not in seen:
            key_people_data.append({**credit_names(person, english_crew), "tmdb_id": tmdb_person_id, "role": "director", "job": "Director"})
            seen.add(marker)
    for person in cast:
        tmdb_person_id = person.get("id")
        interest_roles = interests.get(tmdb_person_id, set())
        if "actor" not in interest_roles:
            continue
        marker = (tmdb_person_id, "actor")
        if marker not in seen:
            key_people_data.append({
                **credit_names(person, english_cast), "tmdb_id": tmdb_person_id, "role": "actor",
                "character": person.get("character", ""),
            })
            seen.add(marker)
    imdb_id = str(details.get("imdb_id") or "")
    companion = (
        _get_omdb(imdb_id)
        if fetch_omdb and imdb_id
        else ({"omdb_checked": True} if fetch_omdb else {})
    )
    release_date = str(details.get("release_date") or "")
    kinopoisk = _get_kinopoisk(
        imdb_id,
        str(details.get("original_title") or ""),
        str(details.get("title") or ""),
        release_date[:4],
    ) if fetch_kinopoisk else {}
    release_groups = details.get("release_dates", {}).get("results", [])
    certification = ""
    for country_code in ("RU", "US"):
        group = next((row for row in release_groups if row.get("iso_3166_1") == country_code), None)
        if group:
            certification = next(
                (str(row.get("certification") or "") for row in group.get("release_dates", []) if row.get("certification")),
                "",
            )
        if certification:
            break
    writers = []
    seen_writers: set[int] = set()
    for person in crew:
        if person.get("department") != "Writing" and person.get("job") not in {"Writer", "Screenplay", "Story"}:
            continue
        person_id = int(person.get("id") or 0)
        if person_id and person_id not in seen_writers:
            writers.append(str(person.get("name") or ""))
            seen_writers.add(person_id)
    cast_names = [str(person.get("name") or "") for person in cast[:10] if person.get("name")]
    countries = [str(row.get("name") or "") for row in details.get("production_countries", []) if row.get("name")]
    companies = [str(row.get("name") or "") for row in details.get("production_companies", []) if row.get("name")]
    languages = [str(row.get("name") or row.get("english_name") or "") for row in details.get("spoken_languages", []) if row.get("name") or row.get("english_name")]
    keywords = [str(row.get("name") or "") for row in details.get("keywords", {}).get("keywords", []) if row.get("name")]
    poster_path = str(details.get("poster_path") or "")
    def display_name(person: dict[str, Any]) -> str:
        localized = person.get("name_ru") or person["name_original"]
        return localized if localized == person["name_original"] else f"{localized} ({person['name_original']})"

    key_actors = "; ".join(display_name(person) for person in key_people_data if person["role"] == "actor")
    key_directors = "; ".join(display_name(person) for person in key_people_data if person["role"] == "director")
    key_parts = []
    if key_actors:
        key_parts.append(f"Актёры: {key_actors}")
    if key_directors:
        key_parts.append(f"Режиссёры: {key_directors}")
    payload = {
        "content_type": "movie",
        "tmdb_checked": True,
        "title_ru": str(details.get("title") or ""),
        "title_original": str(details.get("original_title") or ""),
        "release_date": release_date,
        "year": release_date[:4],
        "directors": "; ".join(display_name(person) for person in directors_data),
        "directors_data": directors_data,
        "key_people": "; ".join(key_parts),
        "key_actors": key_actors,
        "key_directors": key_directors,
        "key_people_data": key_people_data,
        "genres": "; ".join(str(genre.get("name") or "") for genre in details.get("genres", [])),
        "genres_data": details.get("genres", []),
        "duration_minutes": details.get("runtime"),
        "imdb_rating": companion.get("imdb_rating"),
        "omdb_checked": bool(companion.get("omdb_checked")),
        "tmdb_rating": round(float(details.get("vote_average") or 0), 1),
        "tmdb_vote_count": int(details.get("vote_count") or 0),
        "imdb_id": imdb_id,
        "tmdb_id": int(details["id"]),
        **kinopoisk,
        "kinopoisk_checked": bool(kinopoisk.get("kinopoisk_checked")),
        "overview": str(details.get("overview") or companion.get("omdb_plot") or ""),
        "original_language": str(details.get("original_language") or ""),
        "awards_json": companion.get("awards_json", []),
        "tagline": str(details.get("tagline") or ""),
        "content_rating": companion.get("content_rating") or certification,
        "imdb_votes": companion.get("imdb_votes") or "",
        "metascore": companion.get("metascore"),
        "box_office": companion.get("box_office") or "",
        "cast": "; ".join(cast_names) or companion.get("omdb_cast", ""),
        "writers": "; ".join(writers) or companion.get("omdb_writers", ""),
        "countries": "; ".join(countries) or companion.get("omdb_countries", ""),
        "production_companies": "; ".join(companies),
        "spoken_languages": "; ".join(languages) or companion.get("omdb_languages", ""),
        "keywords": "; ".join(keywords),
        "budget": int(details.get("budget") or 0),
        "revenue": int(details.get("revenue") or 0),
        "homepage": str(details.get("homepage") or ""),
        "poster_path": poster_path,
        "poster_url": artwork.movie_poster_url(poster_path),
        "movie_status": str(details.get("status") or ""),
        "url": f"https://www.themoviedb.org/movie/{details['id']}",
        "source": "tmdb",
        "status": "backlog",
        "reaction": "",
    }
    if poster_path:
        try:
            payload["poster_local_path"] = artwork.cache_movie_poster(
                details["id"], poster_path, payload["poster_url"]
            )
        except artwork.ArtworkError as error:
            payload["poster_local_path"] = ""
            payload.setdefault("provider_warnings", []).append({
                "provider": "tmdb-images", "message": str(error)
            })
    else:
        payload["poster_local_path"] = ""
    return payload


def _cyrillic_alias(values: list[Any]) -> str:
    return next((str(value) for value in values if re.search(r"[А-Яа-яЁё]", str(value))), "")


def person_details(tmdb_id: int, api_key: str | None = None) -> dict[str, Any]:
    api_key = api_key or get_api_key()[0]
    if not api_key:
        raise TmdbError("TMDB_API_KEY is not configured")
    details = _get(
        f"/person/{tmdb_id}",
        {"language": "en-US", "append_to_response": "external_ids"},
        api_key,
    )
    localized = _get(f"/person/{tmdb_id}", {"language": "ru-RU"}, api_key)
    aliases = details.get("also_known_as", []) if isinstance(details.get("also_known_as"), list) else []
    original_name = str(details.get("name") or "").strip()
    if not original_name:
        raise TmdbError(f"TMDB person not found: {tmdb_id}")
    localized_name = str(localized.get("name") or "").strip()
    profile_path = str(details.get("profile_path") or "")
    profile_url = artwork.person_profile_url(profile_path)
    external_ids = details.get("external_ids") if isinstance(details.get("external_ids"), dict) else {}
    imdb_id = str(external_ids.get("imdb_id") or "").strip()
    payload = {
        "tmdb_id": int(details["id"]),
        "imdb_id": imdb_id,
        "name_original": original_name,
        "name_ru": localized_name if re.search(r"[А-Яа-яЁё]", localized_name) else (_cyrillic_alias(aliases) or original_name),
        "profile_path": profile_path,
        "profile_url": profile_url,
        "details_json": {
            "also_known_as": aliases,
            "biography": str(details.get("biography") or ""),
            "birthday": str(details.get("birthday") or ""),
            "deathday": str(details.get("deathday") or ""),
            "place_of_birth": str(details.get("place_of_birth") or ""),
            "profile_path": profile_path,
            "profile_url": profile_url,
            "profile_checked": True,
            "known_for_department": str(details.get("known_for_department") or ""),
            "imdb_id": imdb_id,
            "external_ids_checked": True,
        },
    }
    if profile_path:
        try:
            payload["profile_local_path"] = artwork.cache_person_profile(
                payload["tmdb_id"], profile_path, profile_url,
            )
        except artwork.ArtworkError as error:
            payload["profile_local_path"] = ""
            payload["provider_warnings"] = [{"provider": "tmdb-images", "message": str(error)}]
    else:
        payload["profile_local_path"] = ""
    return payload


def resolve_person(query: str, role: str, api_key: str) -> int:
    payload = _get("/search/person", {"language": "en-US", "query": query, "include_adult": "false"}, api_key)
    results = payload.get("results", [])
    if not results:
        raise TmdbError(f"TMDB person not found: {query}")
    expected = "Acting" if role == "actor" else "Directing"
    exact = [row for row in results if _normalized(str(row.get("name") or "")) == _normalized(query)]
    department = [row for row in results if row.get("known_for_department") == expected]
    exact_department = [row for row in exact if row.get("known_for_department") == expected]
    candidates = exact_department or exact or department or results
    candidates.sort(key=lambda row: float(row.get("popularity") or 0), reverse=True)
    return int(candidates[0]["id"])


def resolve_person_input(payload: dict[str, Any]) -> dict[str, Any]:
    api_key, _ = get_api_key()
    if not api_key:
        raise TmdbError("TMDB_API_KEY is not configured")
    role = str(payload.get("role") or "").strip()
    if role not in {"actor", "director"}:
        raise TmdbError("Person role must be actor or director")
    tmdb_raw = payload.get("tmdb_id") or payload.get("external_id")
    if str(tmdb_raw).isdigit():
        tmdb_id = int(tmdb_raw)
    else:
        query = str(payload.get("name_original") or payload.get("name_ru") or "").strip()
        if not query:
            raise TmdbError("Enter a person name or TMDB ID")
        tmdb_id = resolve_person(query, role, api_key)
    return {**person_details(tmdb_id, api_key), "role": role}


def _person_matches_target(target: dict[str, Any], details: dict[str, Any]) -> bool:
    expected = [str(target.get("name_original") or ""), str(target.get("name_ru") or "")]
    metadata = details.get("details_json") if isinstance(details.get("details_json"), dict) else {}
    actual = [str(details.get("name_original") or ""), str(details.get("name_ru") or "")]
    actual.extend(str(value) for value in metadata.get("also_known_as", []))
    expected_norm = [_normalized(value) for value in expected if _normalized(value)]
    actual_norm = [_normalized(value) for value in actual if _normalized(value)]
    return any(
        left == right or difflib.SequenceMatcher(None, left, right).ratio() >= 0.78
        for left in expected_norm for right in actual_norm
    )


def _fetch_person_target(target: dict[str, Any], api_key: str) -> tuple[str, dict[str, Any]]:
    tmdb_id = target.get("tmdb_id") or target.get("external_id")
    if str(tmdb_id).isdigit():
        return str(target["id"]), person_details(int(tmdb_id), api_key)
    query = target.get("name_original") or target.get("name_ru") or ""
    resolved_id = resolve_person(query, target["role"], api_key)
    return str(target["id"]), person_details(resolved_id, api_key)


def _person_needs_refresh(target: dict[str, Any]) -> bool:
    if not str(target.get("tmdb_id") or target.get("external_id") or "").strip():
        return True
    if not str(target.get("tmdb_updated_at") or "").strip():
        return True
    metadata = target.get("details_json")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata or "{}")
        except json.JSONDecodeError:
            metadata = {}
    if not isinstance(metadata, dict):
        return True
    if not str(metadata.get("imdb_id") or "").strip() and metadata.get("external_ids_checked") is not True:
        return True
    return bool(target.get("profile_path") or target.get("profile_url")) and not artwork.is_cached(
        target.get("profile_local_path")
    )


def refresh_person(person_id: str, role: str | None = None) -> dict[str, Any]:
    api_key, _ = get_api_key()
    if not api_key:
        raise TmdbError("TMDB_API_KEY is not configured")
    requested_role = str(role or "").strip()
    if requested_role and requested_role not in {"actor", "director"}:
        raise TmdbError("Person role must be actor or director")
    targets = [
        row for row in storage.list_interests("movie")
        if str(row["id"]) == person_id
    ]
    target = next(
        (row for row in targets if not requested_role or row.get("role") == requested_role),
        None,
    )
    if not target:
        raise TmdbError("Person not found")
    target_id, details = _fetch_person_target(target, api_key)
    storage.update_interest_person(target_id, details)
    item = next((
        row for row in storage.list_interests("movie")
        if str(row["id"]) == target_id and row.get("role") == target.get("role")
    ), None)
    if not item:
        raise TmdbError("Person not found after refresh")
    if details.get("provider_warnings"):
        item["provider_warnings"] = details["provider_warnings"]
    return item


def refresh_people() -> dict[str, Any]:
    api_key, _ = get_api_key()
    if not api_key:
        raise TmdbError("TMDB_API_KEY is not configured")
    targets_by_id: dict[str, dict[str, Any]] = {}
    for target in storage.list_interests("movie"):
        if _person_needs_refresh(target):
            targets_by_id.setdefault(str(target["id"]), target)
    targets = list(targets_by_id.values())

    errors: list[dict[str, str]] = []
    updated = 0
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_fetch_person_target, target, api_key): target for target in targets}
        for future in as_completed(futures):
            target = futures[future]
            try:
                person_id, details = future.result()
                storage.update_interest_person(person_id, details)
                updated += 1
            except Exception as error:
                errors.append({"id": str(target["id"]), "name": str(target["name_original"]), "error": str(error)})
    return {"total": len(targets), "updated": updated, "failed": len(errors), "errors": errors}


def _movie_search_candidate(row: dict[str, Any], known_ids: set[str]) -> dict[str, Any]:
    tmdb_id = int(row.get("id") or row.get("tmdb_id") or 0)
    release_date = str(row.get("release_date") or "")
    poster_path = str(row.get("poster_path") or "")
    poster_url = artwork.movie_poster_url(poster_path)
    payload = {
        "content_type": "movie",
        "tmdb_id": tmdb_id,
        "title_ru": str(row.get("title") or row.get("title_ru") or row.get("original_title") or ""),
        "title_original": str(row.get("original_title") or row.get("title_original") or row.get("title") or ""),
        "release_date": release_date,
        "year": release_date[:4],
        "overview": str(row.get("overview") or ""),
        "original_language": str(row.get("original_language") or ""),
        "tmdb_rating": round(float(row.get("vote_average") or row.get("tmdb_rating") or 0), 1),
        "tmdb_vote_count": int(row.get("vote_count") or row.get("tmdb_vote_count") or 0),
        "popularity": float(row.get("popularity") or 0),
        "poster_path": poster_path,
        "poster_url": poster_url,
        "poster_local_path": str(row.get("poster_local_path") or ""),
        "tmdb_link": f"https://www.themoviedb.org/movie/{tmdb_id}",
        "already_added": str(tmdb_id) in known_ids,
    }
    if poster_path and not payload["poster_local_path"]:
        try:
            payload["poster_local_path"] = artwork.cache_movie_poster(
                tmdb_id, poster_path, poster_url,
            )
        except artwork.ArtworkError as error:
            payload["provider_warnings"] = [{"provider": "tmdb-images", "message": str(error)}]
    return payload


def search_movie_candidates(payload: dict[str, Any], limit: int = 10) -> dict[str, Any]:
    api_key, _ = get_api_key()
    if not api_key:
        raise TmdbError("TMDB_API_KEY is not configured")
    known_ids, _ = storage.known_movie_keys()
    tmdb_raw = payload.get("tmdb_id") or payload.get("external_id")
    if str(tmdb_raw).isdigit():
        details = movie_details(int(tmdb_raw), api_key)
        return {"items": [{**details, "already_added": str(tmdb_raw) in known_ids}]}

    search_field = str(payload.get("search_field") or "").strip()
    title_original = str(payload.get("title_original") or "").strip()
    title_ru = str(payload.get("title_ru") or "").strip()
    if search_field == "title_original":
        title_ru = ""
    elif search_field == "title_ru":
        title_original = ""
    query = title_original or title_ru
    if not query:
        raise TmdbError("Enter a movie title or TMDB ID")
    response = _get(
        "/search/movie",
        {"language": "ru-RU", "query": query, "include_adult": "false"},
        api_key,
    )
    rows = [row for row in response.get("results", []) if isinstance(row, dict) and row.get("id")]
    target_year = int(payload["year"]) if str(payload.get("year") or "").isdigit() else None
    query_norm = _normalized(query)

    def rank(indexed: tuple[int, dict[str, Any]]) -> tuple[float, int, int]:
        index, row = indexed
        names = [
            _normalized(str(row.get("original_title") or "")),
            _normalized(str(row.get("title") or "")),
        ]
        similarity = max((difflib.SequenceMatcher(None, query_norm, name).ratio() for name in names if name), default=0.0)
        release_year = str(row.get("release_date") or "")[:4]
        year_score = -abs(target_year - int(release_year)) if target_year is not None and release_year.isdigit() else 0
        return similarity, year_score, -index

    ranked = [row for _, row in sorted(enumerate(rows), key=rank, reverse=True)[:max(1, min(limit, 20))]]
    return {"items": [_movie_search_candidate(row, known_ids) for row in ranked]}


def _person_known_for(row: dict[str, Any]) -> str:
    titles: list[str] = []
    for item in row.get("known_for", []) or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or item.get("original_title") or item.get("original_name") or "").strip()
        date_value = str(item.get("release_date") or item.get("first_air_date") or "")
        if title:
            titles.append(f"{title} ({date_value[:4]})" if date_value[:4].isdigit() else title)
    return "; ".join(dict.fromkeys(titles))


def _person_search_candidate(
    row: dict[str, Any], role: str, known_ids: set[str],
) -> dict[str, Any]:
    tmdb_id = int(row.get("id") or row.get("tmdb_id") or 0)
    profile_path = str(row.get("profile_path") or "")
    profile_url = artwork.person_profile_url(profile_path)
    payload = {
        "content_type": "movie",
        "role": role,
        "tmdb_id": tmdb_id,
        "name_original": str(row.get("name") or row.get("name_original") or ""),
        "name_ru": str(row.get("name_ru") or ""),
        "known_for_department": str(row.get("known_for_department") or ""),
        "known_for": _person_known_for(row),
        "popularity": float(row.get("popularity") or 0),
        "profile_path": profile_path,
        "profile_url": profile_url,
        "profile_local_path": str(row.get("profile_local_path") or ""),
        "tmdb_link": f"https://www.themoviedb.org/person/{tmdb_id}",
        "already_added": str(tmdb_id) in known_ids,
    }
    if profile_path and not payload["profile_local_path"]:
        try:
            payload["profile_local_path"] = artwork.cache_person_profile(
                tmdb_id, profile_path, profile_url,
            )
        except artwork.ArtworkError as error:
            payload["provider_warnings"] = [{"provider": "tmdb-images", "message": str(error)}]
    return payload


def search_person_candidates(payload: dict[str, Any], limit: int = 10) -> dict[str, Any]:
    api_key, _ = get_api_key()
    if not api_key:
        raise TmdbError("TMDB_API_KEY is not configured")
    role = str(payload.get("role") or "").strip()
    if role not in {"actor", "director"}:
        raise TmdbError("Person role must be actor or director")
    existing = storage.list_interests("movie", role, include_trashed=True)
    known_ids = {
        str(person.get("tmdb_id") or person.get("external_id") or "")
        for person in existing if person.get("tmdb_id") or person.get("external_id")
    }
    tmdb_raw = payload.get("tmdb_id") or payload.get("external_id")
    if str(tmdb_raw).isdigit():
        details = person_details(int(tmdb_raw), api_key)
        return {"items": [{**details, "role": role, "already_added": str(tmdb_raw) in known_ids}]}

    query = str(payload.get("name_original") or payload.get("name_ru") or "").strip()
    if not query:
        raise TmdbError("Enter a person name or TMDB ID")
    response = _get(
        "/search/person",
        {"language": "ru-RU", "query": query, "include_adult": "false"},
        api_key,
    )
    rows = [row for row in response.get("results", []) if isinstance(row, dict) and row.get("id")]
    query_norm = _normalized(query)

    def rank(indexed: tuple[int, dict[str, Any]]) -> tuple[int, float, int]:
        index, row = indexed
        name_norm = _normalized(str(row.get("name") or ""))
        similarity = difflib.SequenceMatcher(None, query_norm, name_norm).ratio() if name_norm else 0.0
        return int(bool(query_norm) and name_norm == query_norm), similarity, -index

    ranked = [row for _, row in sorted(enumerate(rows), key=rank, reverse=True)[:max(1, min(limit, 20))]]
    return {"items": [_person_search_candidate(row, role, known_ids) for row in ranked]}


def resolve_movie(title_original: str, title_ru: str, year: Any, api_key: str) -> int:
    query = title_original or title_ru
    params: dict[str, Any] = {"language": "ru-RU", "query": query}
    if str(year).isdigit():
        params["primary_release_year"] = str(year)
    payload = _get("/search/movie", params, api_key)
    results = payload.get("results", [])
    if not results and "primary_release_year" in params:
        params.pop("primary_release_year")
        results = _get("/search/movie", params, api_key).get("results", [])
    if not results:
        raise TmdbError(f"TMDB movie not found: {query}")
    wanted = {_normalized(title_original), _normalized(title_ru)} - {""}
    target_year = int(year) if str(year).isdigit() else None
    for result in results:
        candidate = {_normalized(str(result.get("original_title") or "")), _normalized(str(result.get("title") or ""))} - {""}
        result_year = str(result.get("release_date") or "")[:4]
        year_ok = target_year is None or (result_year.isdigit() and abs(int(result_year) - target_year) <= 1)
        if wanted & candidate and year_ok:
            return int(result["id"])
    if target_year is not None:
        same_year = [
            result for result in results
            if str(result.get("release_date") or "")[:4].isdigit()
            and abs(int(str(result.get("release_date"))[:4]) - target_year) <= 1
        ]
        if len(same_year) == 1:
            return int(same_year[0]["id"])
    raise TmdbError(f"TMDB match is ambiguous: {query} ({year or 'year unknown'})")


def resolve_movie_input(payload: dict[str, Any]) -> dict[str, Any]:
    api_key, _ = get_api_key()
    if not api_key:
        raise TmdbError("TMDB_API_KEY is not configured")
    search_field = str(payload.get("search_field") or "").strip()
    identity_edited = search_field in {"title_original", "title_ru", "year"}
    tmdb_raw = None if identity_edited else payload.get("tmdb_id") or payload.get("external_id")
    if str(tmdb_raw).isdigit():
        tmdb_id = int(tmdb_raw)
    else:
        title_original = str(payload.get("title_original") or "").strip()
        title_ru = str(payload.get("title_ru") or "").strip()
        if search_field == "title_original":
            title_ru = ""
        elif search_field == "title_ru":
            title_original = ""
        if not title_original and not title_ru:
            raise TmdbError("Enter a movie title or TMDB ID")
        tmdb_id = resolve_movie(title_original, title_ru, payload.get("year"), api_key)
    return movie_details(tmdb_id, api_key)


def _movie_rating_details(target: dict[str, Any]) -> dict[str, Any]:
    needs_imdb = target.get("imdb_rating") in (None, "")
    needs_kinopoisk = target.get("kinopoisk_rating") in (None, "")
    if not needs_imdb and not needs_kinopoisk:
        return {}
    details: dict[str, Any] = {}
    imdb_id = str(target.get("imdb_id") or "")
    if needs_imdb and not imdb_id:
        api_key, _ = get_api_key()
        if not api_key:
            raise TmdbError("TMDB_API_KEY is not configured")
        tmdb_id = target.get("tmdb_id")
        if not str(tmdb_id).isdigit():
            tmdb_id = resolve_movie(
                str(target.get("title_original") or ""),
                str(target.get("title_ru") or ""),
                target.get("year"), api_key,
            )
        basic = _get(f"/movie/{int(tmdb_id)}", {"language": "en-US"}, api_key)
        imdb_id = str(basic.get("imdb_id") or "")
        details.update({"tmdb_id": int(tmdb_id), "imdb_id": imdb_id})
    if needs_imdb:
        companion = _get_omdb(imdb_id) if imdb_id else {"omdb_checked": True}
        if companion.get("imdb_rating") is not None:
            details["imdb_rating"] = companion["imdb_rating"]
        if companion.get("omdb_checked"):
            details["omdb_checked"] = True
    if needs_kinopoisk:
        kinopoisk = _get_kinopoisk(
            imdb_id,
            str(target.get("title_original") or ""),
            str(target.get("title_ru") or ""),
            target.get("year"),
        )
        details.update(kinopoisk)
    return details


def _movie_refresh_requirements(target: dict[str, Any]) -> tuple[bool, bool, bool, bool]:
    tmdb_updated = bool(str(target.get("tmdb_updated_at") or "").strip())
    needs_tmdb = not str(target.get("tmdb_id") or "").isdigit() or not tmdb_updated
    needs_imdb = (
        target.get("imdb_rating") in (None, "")
        and bool(get_omdb_key()[0])
        and not str(target.get("omdb_updated_at") or "").strip()
    )
    needs_kinopoisk = (
        target.get("kinopoisk_rating") in (None, "")
        and bool(get_kinopoisk_key()[0])
        and not str(target.get("kinopoisk_updated_at") or "").strip()
    )
    has_poster_source = bool(
        str(target.get("poster_path") or "").strip()
        or str(target.get("poster_url") or "").strip()
    )
    needs_poster = (
        has_poster_source and not artwork.is_cached(target.get("poster_local_path"))
    ) or (not has_poster_source and not tmdb_updated)
    return needs_tmdb, needs_imdb, needs_kinopoisk, needs_poster


def _needs_movie_refresh(target: dict[str, Any]) -> bool:
    return any(_movie_refresh_requirements(target))


def _poster_details(target: dict[str, Any]) -> dict[str, Any]:
    tmdb_id = target.get("tmdb_id")
    poster_path = str(target.get("poster_path") or "")
    poster_url = str(target.get("poster_url") or "")
    if not poster_path and not poster_url:
        api_key, _ = get_api_key()
        if not api_key:
            raise TmdbError("TMDB_API_KEY is not configured")
        if not str(tmdb_id).isdigit():
            tmdb_id = resolve_movie(
                str(target.get("title_original") or ""),
                str(target.get("title_ru") or ""), target.get("year"), api_key,
            )
        basic = _get(f"/movie/{int(tmdb_id)}", {"language": "ru-RU"}, api_key)
        poster_path = str(basic.get("poster_path") or "")
        tmdb_id = int(basic.get("id") or tmdb_id)
        poster_url = artwork.movie_poster_url(poster_path)
    local_path = ""
    if poster_path or poster_url:
        local_path = artwork.cache_movie_poster(tmdb_id, poster_path, poster_url)
    return {
        "poster_path": poster_path,
        "poster_url": poster_url or artwork.movie_poster_url(poster_path),
        "poster_local_path": local_path,
    }


def _refresh_movie_target(target: dict[str, Any]) -> dict[str, Any]:
    item_id = str(target["id"])
    needs_tmdb, needs_imdb, needs_kinopoisk, needs_poster = _movie_refresh_requirements(target)
    warnings: list[dict[str, str]] = []

    if needs_tmdb:
        api_key, _ = get_api_key()
        if not api_key:
            raise TmdbError("TMDB_API_KEY is not configured")
        tmdb_id = target.get("tmdb_id")
        if not str(tmdb_id).isdigit():
            tmdb_id = resolve_movie(
                str(target.get("title_original") or ""),
                str(target.get("title_ru") or ""), target.get("year"), api_key,
            )
        details = movie_details(
            int(tmdb_id), api_key,
            fetch_kinopoisk=needs_kinopoisk, fetch_omdb=needs_imdb,
        )
        item = storage.update_movie_from_provider(item_id, details)
        warnings.extend(details.get("provider_warnings", []))
    else:
        item = target
        if needs_imdb or needs_kinopoisk:
            details = _movie_rating_details({
                **target,
                "imdb_rating": target.get("imdb_rating") if needs_imdb else 0,
                "kinopoisk_rating": target.get("kinopoisk_rating") if needs_kinopoisk else 0,
            })
            item = storage.update_movie_ratings(item_id, details)
            warnings.extend(details.get("provider_warnings", []))
        if needs_poster:
            poster = _poster_details(item)
            item = storage.update_movie_poster(item_id, poster)
    if warnings:
        item["provider_warnings"] = warnings
    return item


def refresh_library(progress_id: object = None) -> dict[str, Any]:
    targets = [
        target for target in storage.movie_refresh_targets()
        if _needs_movie_refresh(target)
    ]

    def fetch(target: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        return str(target["id"]), _refresh_movie_target(target)

    updated: list[str] = []
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    recommendation_progress.start(
        progress_id, len(targets), stage_id="library-refresh",
        label="Актуализация карточек фильмов", unit="фильмов",
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch, target): target for target in targets}
        for future in as_completed(futures):
            target = futures[future]
            if future.cancelled():
                continue
            try:
                item_id, item = future.result()
                updated.append(item_id)
                warnings.extend(item.get("provider_warnings", []))
            except Exception as error:
                errors.append({"id": str(target["id"]), "title": str(target["title_original"]), "error": str(error)})
            finally:
                recommendation_progress.advance(progress_id, "library-refresh")
            if recommendation_progress.is_cancelled(progress_id):
                for pending in futures:
                    if not pending.running() and not pending.done():
                        pending.cancel()
    recommendation_progress.finish(progress_id)
    return {
        "total": len(targets), "updated": len(updated), "failed": len(errors),
        "errors": errors, "provider_warnings": warnings,
        "cancelled": recommendation_progress.is_cancelled(progress_id),
    }


def refresh_movie(item_id: str) -> dict[str, Any]:
    target = storage.get_item(item_id)
    if not _needs_movie_refresh(target):
        target["refresh_skipped"] = True
        return target
    return _refresh_movie_target(target)


def _recommendation_movie_payload(row: dict[str, Any]) -> dict[str, Any]:
    title_ru = str(row.get("title") or row.get("title_ru") or row.get("original_title") or "Без названия")
    title_original = str(row.get("original_title") or row.get("title_original") or title_ru)
    release_date = str(row.get("release_date") or "")
    poster_path = str(row.get("poster_path") or "")
    return {
        "content_type": "movie",
        "title_ru": title_ru,
        "title_original": title_original,
        "tmdb_id": row.get("id") or row.get("tmdb_id"),
        "release_date": release_date,
        "year": release_date[:4] if release_date[:4].isdigit() else row.get("year"),
        "overview": str(row.get("overview") or ""),
        "original_language": str(row.get("original_language") or ""),
        "tmdb_rating": row.get("vote_average"),
        "tmdb_vote_count": row.get("vote_count"),
        "poster_path": poster_path,
        "poster_url": f"https://image.tmdb.org/t/p/w185{poster_path}" if poster_path else "",
        "source": "tmdb",
        "raw_data": row,
    }


def _known_recommendation_movie(row: dict[str, Any], known_ids: set[str], known_titles: set[str]) -> bool:
    movie_id = str(row.get("id") or row.get("tmdb_id") or "")
    year = str(row.get("release_date") or row.get("year") or "")[:4]
    keys = {
        _normalized(str(row.get("original_title") or row.get("title_original") or "")),
        _normalized(str(row.get("title") or row.get("title_ru") or "")),
    } - {""}
    return movie_id in known_ids or any(key in known_titles or f"{key}:{year}" in known_titles for key in keys)


def recommend_movies(filters: dict[str, Any]) -> dict[str, Any]:
    api_key, _ = get_api_key()
    if not api_key:
        raise TmdbError("TMDB_API_KEY is not configured")

    selected_actor_ids = {str(value) for value in filters.get("actor_ids", []) if str(value)}
    selected_director_ids = {str(value) for value in filters.get("director_ids", []) if str(value)}
    actors = storage.list_interests("movie", "actor")
    directors = storage.list_interests("movie", "director")
    if "actor_ids" in filters:
        actors = [row for row in actors if row["id"] in selected_actor_ids]
    if "director_ids" in filters:
        directors = [row for row in directors if row["id"] in selected_director_ids]
    interests = [*actors, *directors]
    if not interests:
        raise TmdbError("Нужно выбрать хотя бы одного актёра или режиссёра")
    progress_id = filters.get("progress_id")
    recommendation_progress.start(
        progress_id, len(interests), stage_id="tmdb-people",
        label="TMDB · фильмы персон", unit="персон",
    )

    min_imdb_rating = float(filters.get("min_imdb_rating", 0) or 0)
    min_kinopoisk_rating = float(filters.get("min_kinopoisk_rating", 0) or 0)
    min_votes = int(filters.get("min_votes", 0) or 0)
    min_runtime = int(filters.get("min_runtime", 0) or 0)
    date_from = str(filters.get("date_from") or "1900-01-01")
    date_to = str(filters.get("date_to") or date.today().isoformat())
    if date_from > date_to:
        raise TmdbError("Начальный год не может быть больше конечного")
    limit = min(max(int(filters.get("limit", 20) or 20), 1), 50)
    excluded_genre_ids, excluded_genres = _excluded_genre_filters(filters.get("excluded_genres", []))

    known_ids, known_titles = storage.known_movie_keys()
    candidates: dict[int, dict[str, Any]] = {}
    filtered_candidates: dict[int, dict[str, Any]] = {}
    for person in interests:
        if recommendation_progress.is_cancelled(progress_id):
            break
        try:
            credits = _get(f"/person/{person['external_id']}/movie_credits", {"language": "ru-RU"}, api_key)
            source_rows = credits.get("cast", []) if person["role"] == "actor" else [
                row for row in credits.get("crew", []) if row.get("job") == "Director"
            ]
            for row in source_rows:
                movie_id = row.get("id")
                release_date = str(row.get("release_date") or "")
                if not movie_id or _known_recommendation_movie(row, known_ids, known_titles):
                    continue
                if not release_date or not (date_from <= release_date <= date_to):
                    # The selected period defines the discovery window; items outside it are
                    # intentionally absent from the user-facing rejection list.
                    continue
                reasons: list[str] = []
                if excluded_genre_ids & {int(value) for value in row.get("genre_ids", []) if str(value).isdigit()}:
                    reasons.append("исключённый жанр")
                vote_count = int(row.get("vote_count") or 0)
                if vote_count < min_votes:
                    reasons.append(f"голосов TMDB меньше {min_votes}")
                if reasons:
                    filtered_candidates.setdefault(int(movie_id), {
                        "item": _recommendation_movie_payload(row), "reason": "; ".join(reasons),
                    })
                    continue
                candidates.setdefault(int(movie_id), row)
        finally:
            recommendation_progress.advance(progress_id, "tmdb-people")
    recommendation_progress.finish_stage(progress_id, "tmdb-people")

    ranked = sorted(
        candidates.values(),
        key=lambda row: (float(row.get("vote_average") or 0), int(row.get("vote_count") or 0)),
        reverse=True,
    )[: max(limit * 5, 40)]
    recommendation_progress.set_stage(
        progress_id, "movie-details", "TMDB / OMDb / Кинопоиск / Images · карточки",
        len(ranked), "фильмов",
    )

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    try:
        for row in ranked:
            if recommendation_progress.is_cancelled(progress_id):
                break
            try:
                movie_id = str(row["id"])
                year = str(row.get("release_date") or "")[:4]
                keys = {_normalized(str(row.get("original_title") or "")), _normalized(str(row.get("title") or ""))} - {""}
                nearby = [year]
                if year.isdigit():
                    nearby = [str(int(year) - 1), year, str(int(year) + 1)]
                duplicate = movie_id in known_ids or any(
                    f"{key}:{candidate_year}" in known_titles for key in keys for candidate_year in [*nearby, ""]
                )
                if duplicate:
                    continue
                details = movie_details(int(movie_id), api_key)
                for warning in details.get("provider_warnings", []):
                    if isinstance(warning, dict) and warning.get("message"):
                        recommendation_progress.add_warning(
                            progress_id, str(warning.get("provider") or "API"), str(warning["message"])
                        )
                if _known_recommendation_movie(details, known_ids, known_titles):
                    continue
                reasons: list[str] = []
                runtime = int(details.get("duration_minutes") or 0)
                if runtime < min_runtime:
                    reasons.append(f"длительность меньше {min_runtime} мин. или недоступна")
                imdb_rating = float(details.get("imdb_rating") or 0)
                if min_imdb_rating and imdb_rating < min_imdb_rating:
                    reasons.append(f"IMDb rating ниже {min_imdb_rating:g} или недоступен")
                if min_kinopoisk_rating:
                    rating = details.get("kinopoisk_rating")
                    kinopoisk_failed = any(
                        warning.get("provider") == "kinopoisk"
                        for warning in details.get("provider_warnings", [])
                        if isinstance(warning, dict)
                    )
                    if rating in (None, "") and not kinopoisk_failed:
                        reasons.append("рейтинг Кинопоиска недоступен")
                    elif rating not in (None, "") and float(rating) < min_kinopoisk_rating:
                        reasons.append(f"рейтинг Кинопоиска ниже {min_kinopoisk_rating:g}")
                if excluded_genres & {_normalized(genre) for genre in details.get("genres", "").split(";")}:
                    reasons.append("исключённый жанр")
                if reasons:
                    filtered_candidates[int(movie_id)] = {"item": details, "reason": "; ".join(reasons)}
                    continue
                results.append(details)
                if len(results) >= limit:
                    break
            except Exception as error:
                reason = str(error)
                errors.append({"title": str(row.get("title") or row.get("original_title") or "Фильм"), "error": reason})
                filtered_candidates[int(row["id"])] = {
                    "item": _recommendation_movie_payload(row), "reason": reason,
                }
                recommendation_progress.add_warning(progress_id, "TMDB", str(error))
            finally:
                recommendation_progress.advance(progress_id, "movie-details")
    finally:
        recommendation_progress.finish_stage(progress_id, "movie-details")
        recommendation_progress.finish(progress_id)
    return {
        "items": results, "filtered_items": list(filtered_candidates.values()), "errors": errors,
        "cancelled": recommendation_progress.is_cancelled(progress_id),
    }
