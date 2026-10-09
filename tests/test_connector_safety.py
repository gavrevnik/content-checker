"""Repeated provider updates and failures must preserve local owner state."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import musicbrainz, storage, tmdb


class ConnectorSafety(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.database=patch.object(storage,'DB_PATH',Path(self.directory.name)/'fixture.sqlite3')
        self.database.start()
        storage.initialize_database()

    def tearDown(self):
        self.database.stop()
        self.directory.cleanup()

    def test_repeated_movie_refresh_preserves_identity_reaction_and_notes(self):
        item=storage.add_item({'content_type':'movie','title_original':'Fixture','title_ru':'Тест','year':2025,'tmdb_id':42,'status':'consumed','reaction':'like','notes':'Personal note'})
        payload={'id':42,'title':'Тест','original_title':'Fixture','release_date':'2025-01-01','credits':{'crew':[{'id':10,'name':'Fixture Director','job':'Director'}],'cast':[]}}
        with patch.object(tmdb,'_get',return_value=payload),patch.object(tmdb,'_interest_index',return_value={}),patch.object(tmdb,'_get_omdb',return_value={}),patch.object(tmdb,'_get_kinopoisk',return_value={}):
            for _ in range(2):
                details=tmdb.movie_details(42,'synthetic-key')
                storage.update_movie_from_provider(item['id'],details)
        updated=storage.get_item(item['id'])
        self.assertEqual((updated['id'],updated['status'],updated['reaction'],updated['notes']),(item['id'],'consumed','like','Personal note'))
        self.assertEqual(len(storage.list_library(content_type='movie')),1)
        with storage.connect() as connection:
            self.assertEqual(connection.execute('SELECT count(*) FROM people').fetchone()[0],1)

    def test_repeated_music_projection_keeps_release_group_ordered_credits_and_feedback(self):
        raw={'id':'release-group-fixture','title':'Fixture Album','first-release-date':'2025-01-01','artist-credit':[{'name':'First','artist':{'id':'artist-1','name':'First'}},{'name':'Second','artist':{'id':'artist-2','name':'Second'}}]}
        details=musicbrainz._release_group_payload(raw)
        item=storage.add_item({**details,'status':'consumed','reaction':'dislike','notes':'Personal note'})
        for _ in range(2):storage.update_album_from_provider(item['id'],{**details,'musicbrainz_checked':True})
        updated=storage.get_item(item['id'])
        self.assertEqual((updated['id'],updated['status'],updated['reaction'],updated['notes']),(item['id'],'consumed','dislike','Personal note'))
        self.assertEqual(updated['release_group_mbid'],'release-group-fixture')
        self.assertEqual(updated['artists'],'First; Second')
        self.assertEqual(len(storage.list_library(content_type='music')),1)

    def test_failed_provider_refresh_leaves_saved_feedback_unchanged(self):
        item=storage.add_item({'content_type':'movie','title_original':'Fixture','title_ru':'Тест','tmdb_id':42,'status':'consumed','reaction':'like','notes':'Personal note'})
        with patch.object(tmdb,'get_api_key',return_value=('synthetic-key','test')),patch.object(tmdb,'_get',side_effect=tmdb.TmdbError('Fixture timeout')):
            with self.assertRaisesRegex(tmdb.TmdbError,'Fixture timeout'):
                tmdb.refresh_movie(item['id'])
        updated=storage.get_item(item['id'])
        self.assertEqual((updated['reaction'],updated['notes'],updated['status']),('like','Personal note','consumed'))
        self.assertEqual(len(storage.list_library(content_type='movie')),1)
