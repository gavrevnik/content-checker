from __future__ import annotations

import ast
import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable


BASE_URL = "https://api.listenbrainz.org/1"
USER_AGENT = "WhatsNewChecker/2.0 (gavrevns@gmail.com)"
BATCH_SIZE = 10
REQUEST_TIMEOUT_SECONDS = 15
ROOT = Path(__file__).resolve().parents[1]


class ListenBrainzError(RuntimeError):
    pass


def get_user_token() -> tuple[str | None, str]:
    value = os.environ.get("LISTENBRAINZ_USER_TOKEN", "").strip()
    if value:
        return value, "environment"
    secrets_path = ROOT / "SECRETS"
    if not secrets_path.exists():
        return None, ""
    try:
        tree = ast.parse(secrets_path.read_text(encoding="utf-8"), filename=str(secrets_path))
    except (OSError, SyntaxError):
        return None, ""
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        if not isinstance(node.targets[0], ast.Name) or node.targets[0].id != "LISTENBRAINZ_USER_TOKEN":
            continue
        try:
            token = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            return None, ""
        return (token.strip(), "SECRETS") if isinstance(token, str) and token.strip() else (None, "")
    return None, ""


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


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def release_group_popularity(
    release_group_mbids: Iterable[str],
    *,
    continue_on_error: bool = False,
    on_batch: Callable[[], None] | None = None,
    on_error: Callable[[ListenBrainzError], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, int | None]:
    mbids = list(dict.fromkeys(str(value or "").strip() for value in release_group_mbids))
    mbids = [value for value in mbids if value]
    results: dict[str, int | None] = {}
    token, _ = get_user_token()
    for batch in _chunks(mbids, BATCH_SIZE):
        if should_cancel and should_cancel():
            break
        body = json.dumps({"release_group_mbids": batch}).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if token:
            headers["Authorization"] = f"Token {token}"
        request = urllib.request.Request(
            f"{BASE_URL}/popularity/release-group",
            data=body,
            headers=headers,
            method="POST",
        )
        payload: Any = None
        failure: ListenBrainzError | None = None
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, list):
                raise ListenBrainzError("ListenBrainz вернул ответ неожиданного формата")
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            if code == 401 and not token:
                failure = ListenBrainzError(
                    "ListenBrainz требует user token; добавьте LISTENBRAINZ_USER_TOKEN в SECRETS"
                )
            else:
                failure = ListenBrainzError(f"ListenBrainz: HTTP {code}")
        except (urllib.error.URLError, TimeoutError, socket.timeout) as error:
            reason = getattr(error, "reason", error)
            failure = ListenBrainzError(f"ListenBrainz недоступен: {reason}")
        except json.JSONDecodeError:
            failure = ListenBrainzError("ListenBrainz вернул повреждённый JSON")
        except ListenBrainzError as error:
            failure = error
        finally:
            if on_batch:
                on_batch()
        if failure:
            if on_error:
                on_error(failure)
            if not continue_on_error:
                raise failure
            payload = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            mbid = str(item.get("release_group_mbid") or "").strip()
            count = item.get("total_listen_count")
            if mbid:
                results[mbid] = int(count) if count is not None else None
        for mbid in batch:
            results.setdefault(mbid, None)
    return results


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
