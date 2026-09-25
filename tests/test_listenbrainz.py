from __future__ import annotations

import json
import socket
import unittest
from unittest.mock import patch

from app import listenbrainz


class Response:
    def __init__(self, payload: object):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.body


class ListenBrainzTests(unittest.TestCase):
    def test_release_group_popularity_uses_one_batch_and_preserves_null(self) -> None:
        payload = [
            {"release_group_mbid":"rg-1", "total_listen_count":123, "total_user_count":10},
            {"release_group_mbid":"rg-2", "total_listen_count":None, "total_user_count":None},
        ]
        with patch.object(listenbrainz, "get_user_token", return_value=(None,"")), \
             patch.object(listenbrainz.urllib.request, "urlopen", return_value=Response(payload)) as call:
            result = listenbrainz.release_group_popularity(["rg-1", "rg-2"])
        self.assertEqual(result, {"rg-1":123, "rg-2":None})
        request = call.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(
            json.loads(request.data.decode("utf-8")),
            {"release_group_mbids":["rg-1", "rg-2"]},
        )
        self.assertIn("gavrevns@gmail.com", request.headers["User-agent"])

    def test_timeout_is_not_retried_and_optional_token_is_sent(self) -> None:
        with patch.object(listenbrainz, "get_user_token", return_value=("secret-token","test")), \
             patch.object(
                 listenbrainz.urllib.request, "urlopen",
                 side_effect=socket.timeout("read operation timed out"),
             ) as call:
            with self.assertRaisesRegex(listenbrainz.ListenBrainzError, "timed out"):
                listenbrainz.release_group_popularity(["rg-1"])
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.args[0].headers["Authorization"], "Token secret-token")

    def test_tolerant_batches_keep_albums_after_one_request_fails(self) -> None:
        mbids = [f"rg-{index}" for index in range(11)]
        errors = []
        batches = []
        response = Response([{"release_group_mbid":"rg-10", "total_listen_count":42}])
        with patch.object(listenbrainz, "get_user_token", return_value=("secret-token","test")), \
             patch.object(
                 listenbrainz.urllib.request, "urlopen",
                 side_effect=[socket.timeout("read operation timed out"), response],
             ) as call:
            result = listenbrainz.release_group_popularity(
                mbids, continue_on_error=True,
                on_batch=lambda: batches.append(True), on_error=errors.append,
            )
        self.assertEqual(call.call_count, 2)
        self.assertEqual(len(batches), 2)
        self.assertEqual(len(errors), 1)
        self.assertTrue(all(result[mbid] is None for mbid in mbids[:10]))
        self.assertEqual(result["rg-10"], 42)

    def test_enrich_albums_maps_counts_by_release_group_mbid(self) -> None:
        items = [{"release_group_mbid":"rg-2"}, {"release_group_mbid":"rg-1"}]
        with patch.object(
            listenbrainz, "release_group_popularity", return_value={"rg-1":12, "rg-2":34}
        ) as fetch:
            listenbrainz.enrich_albums(items)
        fetch.assert_called_once()
        self.assertEqual([item["total_listen_count"] for item in items], [34, 12])


if __name__ == "__main__":
    unittest.main()
