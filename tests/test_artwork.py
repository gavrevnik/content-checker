from __future__ import annotations

import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from app import artwork


class _ImageResponse(io.BytesIO):
    headers = {"Content-Type": "image/jpeg"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class ArtworkTests(unittest.TestCase):
    def test_movie_poster_uses_w185_and_is_reused_from_local_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(artwork, "ARTWORK_DIR", Path(directory)), \
             patch.object(
                 artwork.urllib.request, "urlopen",
                 side_effect=[_ImageResponse(b"jpeg"), _ImageResponse(b"smaller-jpeg")],
             ) as request:
            first = artwork.cache_movie_poster(42, "/poster.jpg")
            second = artwork.cache_movie_poster(42, "/poster.jpg")
            refreshed = artwork.cache_movie_poster(42, "/poster.jpg", force=True)
        self.assertEqual(first, "movies/42.jpg")
        self.assertEqual(second, first)
        self.assertEqual(refreshed, first)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args.args[0].full_url, "https://image.tmdb.org/t/p/w185/poster.jpg")

    def test_old_provider_urls_are_normalized_to_smaller_images(self) -> None:
        self.assertEqual(
            artwork.movie_poster_url("https://image.tmdb.org/t/p/w500/poster.jpg"),
            "https://image.tmdb.org/t/p/w185/poster.jpg",
        )
        self.assertEqual(
            artwork.preferred_album_cover_url(
                "rg-1", "https://coverartarchive.org/release-group/rg-1/front-500"
            ),
            "https://coverartarchive.org/release-group/rg-1/front-250",
        )

    def test_missing_cover_is_not_cached(self) -> None:
        missing = urllib.error.HTTPError("url", 404, "Not found", {}, None)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(artwork, "ARTWORK_DIR", Path(directory)), \
             patch.object(artwork.urllib.request, "urlopen", side_effect=missing):
            self.assertEqual(artwork.cache_album_cover("rg-1"), "")
            self.assertFalse(Path(directory, "albums", "rg-1.jpg").exists())
