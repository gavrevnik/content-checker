from __future__ import annotations

import unittest
import urllib.error
from unittest.mock import patch

from app import artwork, fanart


class FanartTests(unittest.TestCase):
    def test_artist_thumb_prefers_most_liked_photo(self) -> None:
        payload = {"artistthumb": [
            {"url":"https://assets.fanart.tv/first.jpg", "likes":"2", "width":"1000", "height":"1000"},
            {"url":"https://assets.fanart.tv/best.jpg", "likes":"12", "width":"1000", "height":"1000"},
        ]}
        with patch.object(fanart, "_request_artist", return_value=payload):
            self.assertEqual(
                fanart.artist_thumb_url("artist-mbid", "project-key"),
                "https://assets.fanart.tv/best.jpg",
            )

    def test_missing_artist_artwork_is_not_an_error(self) -> None:
        missing = urllib.error.HTTPError("url", 404, "Not found", None, None)
        with patch.object(fanart.urllib.request, "urlopen", side_effect=missing):
            self.assertEqual(fanart._request_artist("artist-mbid", "project-key"), {})

    def test_enrichment_downloads_small_preview(self) -> None:
        with patch.object(fanart, "artist_thumb_url", return_value="https://assets.fanart.tv/photo.jpg"), \
             patch.object(
                 artwork, "cache_music_artist_profile",
                 return_value="people/music-artist-mbid.jpg",
             ) as cache:
            result = fanart.enrich_artist_artwork({"mbid":"artist-mbid", "name":"Artist"})
        cache.assert_called_once_with(
            "artist-mbid", "https://assets.fanart.tv/photo.jpg", force=False,
        )
        self.assertEqual(result["profile_local_path"], "people/music-artist-mbid.jpg")
        self.assertTrue(result["fanart_checked"])

    def test_artist_cache_resizes_source_to_200_pixels(self) -> None:
        with patch.object(artwork, "resolve_local_path", return_value=artwork.ARTWORK_DIR / "test.jpg"), \
             patch.object(artwork, "_download", return_value=True) as download:
            relative = artwork.cache_music_artist_profile(
                "artist-mbid", "https://assets.fanart.tv/photo.jpg", force=True,
            )
        self.assertEqual(relative, "people/music-artist-mbid.jpg")
        self.assertEqual(download.call_args.args[0], "https://assets.fanart.tv/photo.jpg")
        self.assertEqual(download.call_args.kwargs["resize_width"], 200)


if __name__ == "__main__":
    unittest.main()
