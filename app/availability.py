"""Evidence-based digital availability; unknown is distinct from not found."""
from __future__ import annotations

from personal_radar_connectors import opensubtitles

from personal_radar_connectors import rutracker as rutracker_client

import difflib
import os
import re
import unicodedata
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from app import tmdb
from app.research_sources import SourceError, Tree, fetch

MARKETS = ['US', 'GB', 'FR', 'DE', 'CA', 'AU', 'IT', 'ES']


def now():
    return datetime.now(ZoneInfo('Europe/Belgrade'))


normalize = rutracker_client.normalize


identities = rutracker_client.identities


title_match = rutracker_client.title_match


parse_rutracker = rutracker_client.parse_rutracker


def rutracker(movie):
    return rutracker_client.rutracker(movie, fetcher=fetch)


def subtitles(movie):
    key=os.environ.get('OPENSUBTITLES_API_KEY') or tmdb._local_secrets().get('OPENSUBTITLES_API_KEY')
    return opensubtitles.subtitles(movie,key,fetcher=fetch)


def providers(movie, markets):
    key, _ = tmdb.get_api_key()
    if not key or not movie.get('tmdb_id'):
        return {'status': 'not_configured' if not key else 'not_checked', 'available': None, 'countries': [], 'providers': []}
    data = tmdb._get(f"/movie/{int(movie['tmdb_id'])}/watch/providers", {}, key)
    if not isinstance(data.get('results'), dict):
        raise SourceError('TMDB: неизвестная структура watch providers')
    offers = []
    for country in markets:
        market = data['results'].get(country, {})
        for kind in ('flatrate', 'rent', 'buy'):
            for provider in market.get(kind, []):
                offers.append({'country': country, 'type': kind, 'provider': provider.get('provider_name'),
                               'provider_id': provider.get('provider_id'), 'url': market.get('link')})
    return {'status': 'ok', 'available': bool(offers), 'countries': sorted({o['country'] for o in offers}),
            'providers': offers, 'attribution': 'Watch provider data by JustWatch via TMDB',
            'source_url': f"https://www.themoviedb.org/movie/{movie['tmdb_id']}/watch"}


def releases(movie):
    key, _ = tmdb.get_api_key()
    if not key or not movie.get('tmdb_id'):
        return {'status': 'not_configured' if not key else 'not_checked', 'digital_released': None, 'past': [], 'future': []}
    data = tmdb._get(f"/movie/{int(movie['tmdb_id'])}/release_dates", {}, key)
    if not isinstance(data.get('results'), list):
        raise SourceError('TMDB: неизвестная структура release dates')
    past, future = [], []
    today = now().date().isoformat()
    for group in data['results']:
        for row in group.get('release_dates', []):
            release_date = str(row.get('release_date') or '')[:10]
            if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', release_date):
                continue
            record = {'country': group.get('iso_3166_1'), 'date': release_date, 'type': row.get('type')}
            (past if release_date <= today else future).append(record)
    return {'status': 'ok', 'digital_released': any(r['type'] == 4 for r in past), 'past': past, 'future': future,
            'source_url': f"https://www.themoviedb.org/movie/{movie['tmdb_id']}/releases"}


def summarize(evidence):
    watch, subs, rut, dates = (evidence.get(k, {}) for k in ('watch', 'subtitles', 'rutracker', 'releases'))
    match = rut.get('best_match') or {}
    strong_rut = rut.get('found') is True and match.get('confidence', 0) >= .9
    if watch.get('available') is True or (strong_rut and subs.get('available') is True):
        confidence = 'high'
    elif dates.get('digital_released') is True or strong_rut:
        confidence = 'medium'
    elif match or subs.get('available') is True:
        confidence = 'low'
    else:
        confidence = 'not_found'
    return {'rutracker_found': rut.get('found'), 'rutracker_confidence': match.get('confidence'),
            'rutracker_seeds': match.get('seeds'), 'best_match_title': match.get('title'),
            'ru_subtitles_available': subs.get('available'),
            'ru_audio_available': True if strong_rut and match.get('ru_audio_available') else None,
            'watch_available': watch.get('available'), 'watch_countries': watch.get('countries', []),
            'digital_release': dates.get('digital_released'), 'digital_available_confidence': confidence,
            'note': 'not_found не доказывает отсутствие релиза. Русские субтитры не подтверждают русскую озвучку.'}


def check(movie, markets=None):
    markets = markets or MARKETS
    if not isinstance(markets, list) or not 1 <= len(markets) <= 10 or any(not re.fullmatch('[A-Z]{2}', str(c)) for c in markets):
        raise ValueError('markets: от 1 до 10 двухбуквенных кодов стран')
    if not isinstance(movie, dict) or not (movie.get('tmdb_id') or identities(movie)):
        raise ValueError('Нужен TMDB ID или название фильма')
    evidence = {}
    for name, call in [('watch', lambda: providers(movie, markets)), ('releases', lambda: releases(movie)),
                       ('subtitles', lambda: subtitles(movie)), ('rutracker', lambda: rutracker(movie))]:
        try:
            evidence[name] = call()
        except (SourceError, tmdb.TmdbError, TypeError, KeyError, AttributeError, ValueError) as error:
            evidence[name] = {'status': 'unavailable', 'reason': str(error) if isinstance(error, SourceError) else f'{name}: проверка источника недоступна'}
    return {**summarize(evidence), 'checked_at': now().isoformat(), 'markets': markets, 'evidence': evidence}
