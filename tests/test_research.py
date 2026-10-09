from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app import ai_digest, availability, festivals, oscarbase, research, server, storage, tmdb
from app.research_sources import SourceError
from scripts.content_mcp import Bridge

FIXTURES = Path(__file__).parent / 'fixtures' / 'festivals'


class FestivalTests(unittest.TestCase):
    def test_cannes_sections_and_names_from_real_markup(self):
        html = (FIXTURES / 'cannes-selection.html').read_text()
        items = festivals.parse_cannes(html, 2025, 'competition')
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['title_original'], 'AFFEKSJONSVERDI')
        self.assertEqual(items[0]['english_title'], 'SENTIMENTAL VALUE')
        self.assertIn('TRIER', items[0]['directors'])
        self.assertEqual(festivals.parse_cannes(html, 2025, 'un-certain-regard'), [])

    def test_cannes_award_not_section_heading(self):
        awards = festivals.parse_cannes_awards((FIXTURES / 'cannes-awards.html').read_text())
        self.assertEqual(awards['https://www.festival-cannes.com/en/f/un-simple-accident/'], ["Palme d'or"])

    def test_cannes_person_award_maps_to_named_film(self):
        awards = festivals.parse_cannes_awards((FIXTURES / 'cannes-person-award.html').read_text())
        self.assertEqual(awards['title:la bola negra'], ['Award for Best Director (Ex-Aequo)'])
        self.assertNotIn('https://www.festival-cannes.com/en/p/javier-calvo/', awards)

    def test_berlin_award_by_exact_film_url(self):
        awards = festivals.parse_berlin_awards((FIXTURES / 'berlin-awards.html').read_text(), 2026)
        self.assertEqual(awards['https://www.berlinale.de/en/2026/programme/202615786.html'], ['Golden Bear for Best Film'])
        self.assertEqual(festivals.parse_berlin_awards((FIXTURES / 'berlin-awards.html').read_text(), 2025), {})

    def test_cannes_year_not_inferred_from_footer_or_archive_links(self):
        self.assertFalse(festivals.confirms_year('<footer>2026</footer><a>2025</a>', 'cannes', 2025))
        self.assertTrue(festivals.confirms_year('<option selected>2025</option>', 'cannes', 2025))
        self.assertFalse(festivals.confirms_year('<option selected>2026</option>', 'cannes', 2025))

    def test_venice_real_card(self):
        items = festivals.parse_venice((FIXTURES / 'venice-selection.html').read_text(), 2026, 'competition')
        self.assertEqual(items[0]['title_original'], 'NAZA')
        self.assertEqual(items[0]['directors'], 'Yuval Abraham, Rachel Szor')
        self.assertEqual(festivals.parse_venice((FIXTURES / 'venice-selection.html').read_text(), 2025, 'competition'), [])

    def test_venice_award(self):
        awards = festivals.parse_venice_awards((FIXTURES / 'venice-awards.html').read_text())
        self.assertIn('GOLDEN LION for Best Film', awards['kvinde ukendt (woman unknown)'])

    def test_berlin_confirmed_filters_and_production_year(self):
        data = json.loads((FIXTURES / 'berlin.json').read_text())
        item = festivals.parse_berlinale(data, 2026, 'competition')[0]
        self.assertEqual(item['festival_year'], 2026)
        self.assertEqual(item['year'], 2025)
        self.assertEqual(item['title_original'], 'A New Dawn')
        with self.assertRaises(SourceError):
            festivals.parse_berlinale(data, 2025, 'competition')
        with self.assertRaises(SourceError):
            festivals.parse_berlinale(data, 2026, 'panorama')

    def test_unavailable_not_empty_success(self):
        with patch.object(oscarbase, 'get', side_effect=SourceError('HTTP 403')):
            result = festivals.programme('oscars', 2026, refresh=True)
        self.assertEqual(result['status'], 'unavailable')
        self.assertTrue(result['warnings'])

    def test_wrong_year_or_section(self):
        for args in [('bad', 2026), ('cannes', True), ('cannes', 2026, '../../path')]:
            with self.assertRaises(ValueError):
                festivals.programme(*args)


class AvailabilityTests(unittest.TestCase):
    def test_confidence_truth_table(self):
        for evidence, expected in [
            ({'watch': {'available': True}}, 'high'),
            ({'rutracker': {'found': True, 'best_match': {'confidence': .94}}, 'subtitles': {'available': True}}, 'high'),
            ({'rutracker': {'found': True, 'best_match': {'confidence': .94}}}, 'medium'),
            ({'releases': {'digital_released': True}}, 'medium'),
            ({'rutracker': {'found': None, 'best_match': {'confidence': .76}}}, 'low'),
            ({'subtitles': {'available': True}}, 'low'), ({}, 'not_found')]:
            with self.subTest(evidence=evidence):
                self.assertEqual(availability.summarize(evidence)['digital_available_confidence'], expected)
        self.assertIsNone(availability.summarize({'subtitles': {'available': True}})['ru_audio_available'])
        self.assertIsNone(availability.summarize({})['rutracker_found'])

    def test_future_digital_dates_are_not_released(self):
        data = {'results': [{'iso_3166_1': 'US', 'release_dates': [
            {'type': 3, 'release_date': '2026-05-01T00:00:00Z'},
            {'type': 4, 'release_date': '2026-10-05T00:00:00Z'}]}]}
        with patch.object(tmdb, 'get_api_key', return_value=('test', 'test')), patch.object(tmdb, '_get', return_value=data), patch.object(availability, 'now', return_value=datetime(2026, 10, 4, tzinfo=ZoneInfo('Europe/Belgrade'))):
            result = availability.releases({'tmdb_id': 1})
        self.assertFalse(result['digital_released'])
        self.assertEqual(len(result['future']), 1)

    def test_proxy_markets_and_offer_types(self):
        data = {'results': {'US': {'link': 'https://example.org/watch', 'rent': [{'provider_id': 1, 'provider_name': 'Example'}]}, 'RU': {'flatrate': [{'provider_name': 'Ignored'}]}}}
        with patch.object(tmdb, 'get_api_key', return_value=('test', 'test')), patch.object(tmdb, '_get', return_value=data):
            result = availability.providers({'tmdb_id': 1}, availability.MARKETS)
        self.assertTrue(result['available'])
        self.assertEqual(result['countries'], ['US'])
        self.assertEqual(result['providers'][0]['type'], 'rent')

    def test_subtitles_id_match_and_no_audio_inference(self):
        response = {'data': [{'id': '1', 'attributes': {'language': 'ru', 'feature_details': {'feature_type': 'Movie', 'imdb_id': 123}}},
                             {'id': '2', 'attributes': {'language': 'ru', 'feature_details': {'feature_type': 'Movie', 'imdb_id': 999}}}]}
        with patch.dict('os.environ', {'OPENSUBTITLES_API_KEY': 'fake'}), patch.object(availability, 'fetch', return_value=response) as fetch:
            result = availability.subtitles({'imdb_id': 'tt123'})
        self.assertTrue(result['available'])
        self.assertEqual(result['count'], 1)
        self.assertIn('imdb_id=123', fetch.call_args.args[0])
        self.assertNotIn('/download', fetch.call_args.args[0])

    def test_subtitles_imdb_leading_zeroes_match_numeric_provider_id(self):
        for provider_id in [133093, '133093', '0133093']:
            response = {'data': [{'id': '6183945', 'attributes': {'language': 'ru',
                'feature_details': {'feature_type': 'Movie', 'imdb_id': provider_id}}},
                {'id': 'other', 'attributes': {'language': 'ru',
                'feature_details': {'feature_type': 'Movie', 'imdb_id': 133094}}}]}
            with self.subTest(provider_id=provider_id), patch.dict('os.environ', {'OPENSUBTITLES_API_KEY': 'fake'}), patch.object(availability, 'fetch', return_value=response) as fetch:
                result = availability.subtitles({'imdb_id': 'tt0133093'})
                self.assertTrue(result['available'])
                self.assertEqual(result['count'], 1)
                self.assertEqual(fetch.call_count, 1)
                self.assertIn('imdb_id=133093', fetch.call_args.args[0])

    def test_missing_subtitles_key(self):
        with patch.dict('os.environ', {}, clear=True), patch.object(tmdb, '_local_secrets', return_value={}):
            result = availability.subtitles({'imdb_id': 'tt123'})
        self.assertIsNone(result['available'])
        self.assertEqual(result['status'], 'not_configured')

    def test_rutracker_titles_year_and_metadata(self):
        def row(title, category='Зарубежное кино (HD Video)'):
            return f'<tr><td><a href="viewforum.php?f=1">{category}</a></td><td><a href="viewtopic.php?t=123">{title}</a></td><td class="seedmed">18</td><td class="tor-size">2 GB</td><td class="tor-status">проверено</td></tr>'
        movie = {'title_original': 'Sentimental Value', 'title_ru': 'Сентиментальная ценность', 'year': 2025}
        result = availability.parse_rutracker(row('Сентиментальная ценность / Sentimental Value (2025) WEB-DL DUB'), movie)
        self.assertEqual(result[0]['confidence'], 1)
        self.assertEqual(result[0]['seeds'], 18)
        self.assertTrue(result[0]['ru_audio_available'])
        self.assertEqual(availability.parse_rutracker(row('Sentimental Value (2024)'), movie), [])
        self.assertEqual(availability.parse_rutracker(row('Sentimental Value (2025) soundtrack'), movie), [])
        self.assertEqual(availability.parse_rutracker(row('Sentimental Value (2025)', 'Аудиокниги'), movie), [])

    def test_blocked_rutracker_is_unknown_not_false(self):
        with patch.object(availability, 'fetch', return_value='<form>Войти / Login</form>') as fetch:
            result = availability.rutracker({'title_original': 'A', 'title_ru': 'Б', 'year': 2025})
        self.assertIsNone(result['found'])
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(fetch.call_count, 1)

    def test_malformed_provider_response_does_not_hide_other_signals(self):
        with patch.object(availability, 'providers', side_effect=TypeError('malformed')), patch.object(availability, 'releases', return_value={'digital_released': True}), patch.object(availability, 'subtitles', return_value={}), patch.object(availability, 'rutracker', return_value={}):
            result = availability.check({'title_original': 'A', 'year': 2025})
        self.assertEqual(result['digital_available_confidence'], 'medium')
        self.assertEqual(result['evidence']['watch']['status'], 'unavailable')

    def test_independent_provider_failures(self):
        with patch.object(availability, 'providers', side_effect=SourceError('offline')), patch.object(availability, 'releases', return_value={}), patch.object(availability, 'subtitles', return_value={'available': True}), patch.object(availability, 'rutracker', return_value={'found': True, 'best_match': {'confidence': .95}}):
            result = availability.check({'title_original': 'A', 'year': 2025})
        self.assertEqual(result['digital_available_confidence'], 'high')
        self.assertEqual(result['evidence']['watch']['status'], 'unavailable')


class ResearchStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(storage, 'DB_PATH', Path(self.temp.name) / 'test.sqlite3')
        self.db_patch.start()
        storage.initialize_database()

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def movie(self, tmdb_id=123):
        return ai_digest.snapshot('movie', str(tmdb_id), {'item': {'title_original': 'Movie', 'year': '2025', 'tmdb_id': tmdb_id, 'content_type': 'movie'}})['snapshot_ref']

    def payload(self):
        return {'query': 'Что посмотреть?', 'query_summary': 'Фестивальное кино', 'request_key': 'request-1',
                'items': [{'movie_ref': self.movie(), 'reason': 'Рекомендация'}, {'title_original': 'Unknown', 'year': 2026, 'links': [{'url': 'https://example.org/film'}]}]}

    def test_persist_digest_no_library_mutation_and_idempotency(self):
        payload = self.payload()
        saved = research.call('ai_digest_save', payload)
        self.assertEqual(research.call('ai_digest_save', payload)['id'], saved['id'])
        self.assertEqual(ai_digest.list_digests()['total'], 1)
        self.assertEqual(storage.list_library(), [])
        loaded = ai_digest.get_digest(saved['id'])
        self.assertTrue(loaded['items'][0]['resolved'])
        self.assertFalse(loaded['items'][1]['resolved'])
        self.assertEqual(loaded['query'], payload['query'])
        with self.assertRaises(ValueError):
            research.call('ai_digest_save', {**payload, 'query': 'Другой запрос'})

    def test_update_digest_in_place_idempotent_and_conflict_safe(self):
        original = research.call('ai_digest_save', self.payload())
        update = {**self.payload(), 'request_key': 'update-1', 'digest_id': original['id'],
                  'expected_fingerprint': original['request_fingerprint'], 'summary': 'Коротко',
                  'issues': ['Источник временно недоступен']}
        updated = research.call('ai_digest_save', update)
        self.assertEqual(updated['id'], original['id'])
        self.assertEqual(updated['created_at'], original['created_at'])
        self.assertEqual(updated['issues'], update['issues'])
        self.assertEqual(ai_digest.list_digests()['total'], 1)
        self.assertEqual(research.call('ai_digest_save', update), updated)
        with self.assertRaisesRegex(ValueError, 'изменился'):
            research.call('ai_digest_save', {**update, 'request_key': 'update-2'})
        self.assertEqual(ai_digest.get_digest(original['id'])['summary'], 'Коротко')
        with self.assertRaises(ValueError):
            research.call('ai_digest_save', {**self.payload(), 'request_key': 'bad', 'digest_id': original['id']})
        self.assertEqual(storage.list_library(), [])

    def test_cannot_inject_evidence_or_wrong_movie_ref(self):
        payload = self.payload()
        payload['items'][0]['availability'] = {'watch_available': True}
        with self.assertRaises(ValueError):
            research.call('ai_digest_save', payload)
        del payload['items'][0]['availability']
        ref = ai_digest.snapshot('availability', 'key', {'identity': 'tmdb:999', 'availability': {}})['snapshot_ref']
        payload['items'][0]['availability_ref'] = ref
        with self.assertRaises(ValueError):
            research.call('ai_digest_save', payload)
        self.assertEqual(ai_digest.list_digests()['total'], 0)

    def test_fallback_preserves_provider_identity_for_availability(self):
        payload = self.payload()
        ref = ai_digest.snapshot('availability', 'fallback', {'identity': 'imdb:tt1234', 'availability': {'ru_subtitles_available': True}})['snapshot_ref']
        payload['items'] = [{'title_original': 'Unknown', 'year': 2025, 'imdb_id': 'tt1234',
                             'availability_ref': ref, 'links': [{'url': 'https://www.imdb.com/title/tt1234/'}]}]
        saved = research.call('ai_digest_save', payload)
        self.assertFalse(saved['items'][0]['resolved'])
        self.assertEqual(saved['items'][0]['item']['imdb_id'], 'tt1234')
        self.assertTrue(saved['items'][0]['availability']['ru_subtitles_available'])
        payload['request_key'] = 'wrong-fallback'
        payload['items'][0]['imdb_id'] = 'tt9999'
        with self.assertRaises(ValueError):
            research.call('ai_digest_save', payload)

    def test_bad_links_and_duplicate_movies_rejected(self):
        payload = self.payload()
        payload['items'][1]['links'][0]['url'] = 'javascript:alert(1)'
        with self.assertRaises(ValueError):
            research.call('ai_digest_save', payload)
        payload = self.payload()
        payload['items'] = [payload['items'][0], payload['items'][0]]
        with self.assertRaises(ValueError):
            research.call('ai_digest_save', payload)

    def test_movie_cache_avoids_repeat_provider_calls(self):
        with patch.object(tmdb, 'movie_details', return_value={'tmdb_id': 1, 'title_original': 'M'}) as details:
            a = research.call('movie_details', {'tmdb_id': 1})
            b = research.call('movie_details', {'tmdb_id': 1})
        self.assertEqual(a['snapshot_ref'], b['snapshot_ref'])
        self.assertEqual(details.call_count, 1)

    def test_first_seen_survives_subsequent_refresh(self):
        first = {'ru_subtitles_available': True, 'checked_at': '2026-10-01T12:00:00+02:00', 'evidence': {'subtitles': {}}}
        movie = {'tmdb_id': 1, 'title_original': 'M', 'year': 2026}
        with patch.object(availability, 'check', return_value=first):
            ai_digest.check_availability(movie)
        second = {**first, 'checked_at': '2026-10-04T12:00:00+02:00', 'evidence': {'subtitles': {}}}
        with patch.object(availability, 'check', return_value=second):
            result = ai_digest.check_availability(movie, refresh=True)
        self.assertEqual(result['availability']['evidence']['subtitles']['first_seen'], first['checked_at'])

    def test_director_filmography_excludes_other_crew(self):
        data = {'crew': [{'id': 1, 'title': 'D', 'job': 'Director', 'release_date': '2026-01-01'}, {'id': 2, 'title': 'P', 'job': 'Producer'}]}
        with patch.object(research, 'tmdb_get', return_value=data):
            result = research.call('person_filmography', {'person_id': 1, 'role': 'director'})
        self.assertEqual([r['tmdb_id'] for r in result['items']], [1])

    def test_strict_arguments(self):
        for name, args in [('movie_details', {'tmdb_id': True}), ('movie_details', {'tmdb_id': -1}),
                           ('movie_discover', {'page': 100}), ('availability_check', {}),
                           ('ai_digest_list', {'limit': '10'}), ('movie_search', {'query': 'x', 'url': 'https://evil'})]:
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                research.call(name, args)

    def test_http_bridge_round_trip_and_origin_validation(self):
        http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{http.server_port}'
        try:
            bridge = Bridge(base)
            bridge.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'})
            bridge.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
            result = bridge.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'ai_digest_save', 'arguments': self.payload()}})
            self.assertFalse(result['result']['isError'])
            saved = json.loads(result['result']['content'][0]['text'])
            with urllib.request.urlopen(base + '/api/ai-digests/' + saved['id']) as response:
                self.assertEqual(json.load(response)['query'], 'Что посмотреть?')
            for headers in [{'Origin': 'https://evil.example'}, {'Host': 'evil.example'}]:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(urllib.request.Request(base + '/api/ai-digests', headers=headers))
                self.assertEqual(error.exception.code, 403)
                error.exception.close()
        finally:
            http.shutdown(); thread.join(); http.server_close()


class McpTests(unittest.TestCase):
    def test_handshake_tools_and_errors(self):
        bridge = Bridge('http://127.0.0.1:8765')
        req = lambda method, **kw: {'jsonrpc': '2.0', 'id': 1, 'method': method, **kw}
        self.assertIn('error', bridge.handle(req('tools/list')))
        result = bridge.handle(req('initialize', params={'protocolVersion': '2025-06-18'}))
        self.assertEqual(result['result']['protocolVersion'], '2025-03-26')
        self.assertIsNone(bridge.handle({'jsonrpc': '2.0', 'method': 'notifications/initialized'}))
        self.assertEqual(len(bridge.handle(req('tools/list'))['result']['tools']), len(research.TOOLS))
        self.assertEqual(bridge.handle(req('missing'))['error']['code'], -32601)
        self.assertEqual(bridge.handle(req('tools/call', params={'name': 'missing'}))['error']['code'], -32602)

    def test_bridge_rejects_external_destination_and_stale_app(self):
        for url in ['http://evil.example', 'https://localhost', 'http://localhost/secret', 'http://user@localhost']:
            with self.assertRaises(ValueError):
                Bridge(url)
        bridge = Bridge('http://127.0.0.1:8765'); bridge.ready = True
        with patch.object(bridge, 'api', return_value={'application': 'other', 'version': 99}):
            result = bridge.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'content_status'}})
        self.assertTrue(result['result']['isError'])


if __name__ == '__main__':
    unittest.main()
