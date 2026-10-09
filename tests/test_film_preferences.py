from __future__ import annotations
import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from app import ai_digest, availability, film_preferences, oscarbase, research, storage
from app.research_sources import SourceError


class PersonalDigestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patcher = patch.object(storage, 'DB_PATH', Path(self.temp.name) / 'test.sqlite3')
        self.patcher.start()
        storage.initialize_database()
        self.clock = patch.object(availability, 'now', return_value=datetime(2026, 10, 4, tzinfo=ZoneInfo('Europe/Belgrade')))
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.patcher.stop()
        self.temp.cleanup()

    def movie(self, **kw):
        return storage.add_item({'content_type': 'movie', 'title_original': 'Original', 'title_ru': 'Название',
            'year': 2025, 'tmdb_id': 1, 'imdb_id': 'tt123', 'status': 'consumed', 'reaction': 'like', **kw})

    def refresh_summary(self, count=None):
        context = research.call('film_preferences_context', {})
        pending = context['pending'][:count]
        return research.call('film_preferences_save', {'expected_revision': context['summary']['revision'],
            'summary': 'Предпочтения подтверждены личными реакциями; выборка небольшая.',
            'evidence': [{'movie_id': p['movie_id'], 'fingerprint': p['fingerprint'], 'conclusion': 'Учтена реакция; причина не установлена.'} for p in pending]})

    def payload(self, **movie):
        ref = ai_digest.snapshot('movie', '2', {'item': {'title_original': 'Candidate', 'tmdb_id': 2, 'year': 2025, 'release_date': '2025-10-01', **movie}})['snapshot_ref']
        return {'query': 'что посмотреть', 'query_summary': 'Подборка', 'request_key': 'test', 'items': [{'movie_ref': ref}]}

    def test_summary_lifecycle_changed_removed_and_backlog_feedback(self):
        film = self.movie()
        self.assertEqual(film_preferences.context()['pending_count'], 1)
        self.assertFalse(self.refresh_summary()['stale'])
        storage.update_item(film['id'], {'reaction': 'dislike'})
        ctx = film_preferences.context()
        self.assertEqual(ctx['pending'][0]['previous_snapshot']['reaction'], 'like')
        self.assertEqual(ctx['pending'][0]['snapshot']['reaction'], 'dislike')
        self.refresh_summary()
        storage.update_item(film['id'], {'status': 'backlog'})
        self.assertTrue(film_preferences.context()['pending'][0]['snapshot']['withdrawn'])
        self.assertFalse(self.refresh_summary()['stale'])
        self.assertEqual(film_preferences.context()['rating_count'], 0)

    def test_summary_does_not_learn_from_external_ratings_or_own_scores(self):
        film = self.movie()
        self.refresh_summary()
        with storage.connect() as db:
            db.execute('UPDATE movies SET imdb_rating=9.9 WHERE content_id=?', (film['id'],))
        research.call('ai_digest_save', self.payload())
        self.assertFalse(film_preferences.context()['stale'])

    def test_summary_race_atomicity_revision_fingerprint_and_partial_batch(self):
        first = self.movie()
        self.movie(tmdb_id=3, imdb_id='tt333', title_original='Different', title_ru='Другой')
        context = film_preferences.context()
        self.refresh_summary(1)
        self.assertTrue(film_preferences.context()['stale'])
        payload = {'expected_revision': 0, 'summary': 'x', 'evidence': [
            {'movie_id': p['movie_id'], 'fingerprint': p['fingerprint'], 'conclusion': 'x'} for p in context['pending']]}
        with self.assertRaisesRegex(ValueError, 'Summary изменилось'):
            research.call('film_preferences_save', payload)
        self.refresh_summary()
        storage.update_item(first['id'], {'reaction': 'dislike'})
        pending = film_preferences.context()['pending'][0]
        revision = film_preferences.context()['summary']['revision']
        payload.update(expected_revision=revision, evidence=[{'movie_id': pending['movie_id'], 'fingerprint': pending['fingerprint'], 'conclusion': 'x'}])
        storage.update_item(first['id'], {'reaction': ''})
        with self.assertRaisesRegex(ValueError, 'Оценки изменились'):
            research.call('film_preferences_save', payload)
        self.assertEqual(film_preferences.context()['summary']['revision'], revision)

    def test_deleted_feedback_is_withdrawn(self):
        film = self.movie()
        self.refresh_summary()
        with storage.connect() as db:
            db.execute('DELETE FROM content_items WHERE id=?', (film['id'],))
        self.assertTrue(film_preferences.context()['pending'][0]['snapshot']['withdrawn'])
        self.assertFalse(self.refresh_summary()['stale'])

    def test_newness_ids_aliases_title_year_and_conflicts(self):
        film = self.movie()
        for movie in [{'tmdb_id': 1}, {'imdb_id': 'tt123'}, {'title_ru': 'Название', 'year': 2025}]:
            self.assertEqual(ai_digest.library_match(movie)['status'], 'existing')
        self.assertEqual(ai_digest.library_match({'title_ru': 'Название', 'year': 2024})['status'], 'new')
        self.assertEqual(ai_digest.library_match({'title_ru': 'Название'})['status'], 'uncertain')
        self.assertEqual(ai_digest.library_match({'tmdb_id': 999, 'title_ru': 'Название', 'year': 2025})['status'], 'new')
        self.assertEqual(ai_digest.library_match({'tmdb_id': 999, 'imdb_id': 'tt123'})['status'], 'uncertain')
        with storage.connect() as db:
            db.execute('INSERT INTO content_aliases(content_id,alias) VALUES (?,?)', (film['id'], 'Alias'))
            db.execute("INSERT INTO trash_entries VALUES ('trash','movie',?,'','{}','today')", (film['id'],))
        result = ai_digest.library_match({'title_original': 'Alias', 'year': 2025})
        self.assertTrue(result['items'][0]['trashed'])
        self.assertEqual(result['status'], 'existing')
        self.assertEqual(film_preferences.context()['rating_count'], 1)
        self.assertEqual(research.call('film_preferences_context', {'include_ratings': True})['ratings'][0]['id'], film['id'])

    def test_future_default_unknown_today_and_explicit_upcoming(self):
        for movie, state in [({'release_date': '2026-10-05'}, 'future'), ({'release_date': '2026-10-04'}, 'released'),
                             ({'year': 2027}, 'future'), ({'year': 2025}, 'unknown'), ({'release_date': 'bad'}, 'unknown')]:
            self.assertEqual(ai_digest.release_state(movie), state)
        payload = self.payload(release_date='2026-10-05')
        saved = research.call('ai_digest_save', payload)
        self.assertEqual(saved['items'], [])
        self.assertEqual(saved['excluded'][0]['reason'], 'future_release')
        saved = research.call('ai_digest_save', {**payload, 'request_key': 'explicit', 'include_unreleased': True})
        self.assertEqual(saved['items'][0]['release_state'], 'future')
        fallback = {'title_original': 'Future', 'year': 2027, 'links': [{'url':'https://example.org'}]}
        self.assertEqual(research.call('ai_digest_save', {**payload, 'request_key':'fallback', 'items':[fallback]})['items'], [])

    def test_current_newness_and_historical_snapshot_and_idempotent_retry(self):
        payload = self.payload()
        saved = research.call('ai_digest_save', payload)
        self.assertEqual(saved['items'][0]['library_at_creation']['status'], 'new')
        self.movie(tmdb_id=2, title_original='Candidate')
        loaded = ai_digest.get_digest(saved['id'])
        self.assertEqual(loaded['items'][0]['library']['status'], 'existing')
        self.assertEqual(loaded['items'][0]['library_at_creation']['status'], 'new')
        self.assertEqual(research.call('ai_digest_save', payload)['id'], saved['id'])
        filtered = research.call('ai_digest_save', {**payload, 'request_key':'onlynew', 'exclude_existing': True})
        self.assertEqual(filtered['items'], [])
        self.assertEqual(filtered['excluded'][0]['reason'], 'already_in_library')

    def test_score_needs_fresh_summary_valid_evidence_and_expected_revision(self):
        film = self.movie()
        payload = self.payload()
        payload['summary_revision'] = 0
        payload['items'][0].update(query_score=9, taste_score=8, ai_reason='Соответствует запросу; аналогия с оценённым фильмом.', taste_confidence='medium', taste_evidence_ids=[film['id']])
        with self.assertRaisesRegex(ValueError, 'обновите summary'):
            research.call('ai_digest_save', payload)
        self.refresh_summary()
        with self.assertRaisesRegex(ValueError, 'summary_revision'):
            research.call('ai_digest_save', payload)
        payload['summary_revision'] = 1
        saved = research.call('ai_digest_save', payload)
        self.assertEqual(saved['items'][0]['scoring']['ai_score'], 8.7)
        storage.update_item(film['id'], {'reaction': 'dislike'})
        self.assertEqual(research.call('ai_digest_save', payload)['id'], saved['id'])
        self.refresh_summary()
        payload.update(request_key='wrong-evidence', summary_revision=2)
        payload['items'][0]['taste_evidence_ids'] = ['not-rated']
        with self.assertRaisesRegex(ValueError, 'действующие личные оценки'):
            research.call('ai_digest_save', payload)

    def test_no_feedback_neutral_score_and_schema_ranges(self):
        payload = self.payload()
        payload['summary_revision'] = 0
        entry = payload['items'][0]
        entry.update(query_score=10, taste_score=5, ai_reason='Запрос подходит; предпочтения неизвестны.', taste_confidence='low')
        self.assertEqual(research.call('ai_digest_save', payload)['items'][0]['scoring']['ai_score'], 8.5)
        payload['request_key'] = 'bad'
        entry['taste_score'] = 8
        with self.assertRaisesRegex(ValueError, 'Без личных оценок'):
            research.call('ai_digest_save', payload)
        for value in [11, -1, True, float('nan')]:
            entry['query_score'] = value
            with self.assertRaises(ValueError):
                research.call('ai_digest_save', payload)


class OscarBaseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((Path(__file__).parent / 'fixtures/festivals/oscarbase-2026.json').read_text())

    def test_real_person_nominations_deduplicate_to_ten_films(self):
        films = oscarbase.parse(self.fixture['data'], 2026, 'best-picture')
        self.assertEqual(len(films), 10)
        self.assertEqual(len([f for f in films if f['winner']]), 1)
        self.assertGreater(len(films[0]['nominations']), 1)
        with self.assertRaises(SourceError):
            oscarbase.parse(self.fixture['data'], 2025, 'best-picture')
        with self.assertRaises(SourceError):
            oscarbase.parse(self.fixture['data'], 2026, 'directing')

    def test_complete_pagination_and_guard_against_partial_or_repeated(self):
        rows = self.fixture['data']
        pages = [{'data':rows[:20], 'pagination':{'total':len(rows),'page':1,'totalPages':2}},
                 {'data':rows[20:], 'pagination':{'total':len(rows),'page':2,'totalPages':2}}]
        with patch.object(oscarbase, 'get', side_effect=pages) as get:
            films, url, skipped = oscarbase.programme(2026, 'best-picture')
        self.assertEqual(len(films), 10)
        self.assertEqual(get.call_count, 2)
        self.assertIn('year=2026', url)
        self.assertEqual(skipped, 0)
        broken = copy.deepcopy(pages)
        broken[1]['data'] = []
        with patch.object(oscarbase, 'get', side_effect=broken), self.assertRaises(SourceError):
            oscarbase.programme(2026, 'best-picture')
        broken[1]['data'] = [rows[0]]
        with patch.object(oscarbase, 'get', side_effect=broken), self.assertRaises(SourceError):
            oscarbase.programme(2026, 'best-picture')

    def test_empty_year_not_claimed_as_complete_success(self):
        with patch.object(oscarbase, 'get', return_value={'data':[], 'pagination':{'total':0,'page':1,'totalPages':0}}), self.assertRaises(SourceError):
            oscarbase.programme(2027, 'best-picture')


if __name__ == '__main__':
    unittest.main()
