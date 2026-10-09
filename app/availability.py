"""Evidence-based digital availability; unknown is distinct from not found."""
from __future__ import annotations

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


def normalize(value):
    value = unicodedata.normalize('NFKD', str(value)).casefold().replace('ё', 'е')
    return ' '.join(re.findall(r'[^\W_]+', ''.join(c for c in value if not unicodedata.combining(c)), re.UNICODE))


def identities(movie):
    return list(dict.fromkeys(str(movie.get(k) or '').strip() for k in (
        'title_original', 'original_title', 'english_title', 'title_ru', 'russian_title', 'title') if movie.get(k)))


def title_match(candidate, movie, candidate_year=None):
    year = str(movie.get('year') or '')
    if not year.isdigit():
        return 0.0
    years = re.findall(r'\b(?:19|20)\d{2}\b', candidate)
    if str(candidate_year or '') != year and year not in years:
        return 0.0
    # Compare only title aliases, not arbitrary words in technical metadata.
    title_part = re.split(r'\b(?:19|20)\d{2}\b', candidate, maxsplit=1)[0]
    aliases = [normalize(p) for p in re.split(r'[/|\[\]()]', title_part) if normalize(p)]
    targets = [normalize(n) for n in identities(movie)]
    score = max((difflib.SequenceMatcher(None, t, a).ratio() for t in targets for a in aliases), default=0)
    return round(score, 3)


def parse_rutracker(html, movie):
    root = Tree(html).root
    rows, parsed = [], False
    for row in root.all('tr'):
        links = [a for a in row.all('a') if re.search(r'viewtopic\.php\?t=\d+', a.attrs.get('href', ''))]
        if not links:
            continue
        parsed = True
        link = max(links, key=lambda a: len(a.text()))
        title = link.text()
        category = next((a.text() for a in row.all('a') if re.search(r'viewforum\.php\?f=\d+', a.attrs.get('href', ''))), '')
        if re.search(r'сериал|сезон|саундтрек|soundtrack|trailer|трейлер|аудиокниг|OST\b|\bS\d{2}E\d', title + ' ' + category, re.I):
            continue
        # Only categories known to contain feature/documentary/animated movies.
        if not re.search(r'фильм|кино|movie|film|анимац|мультфильм', category, re.I):
            continue
        confidence = title_match(title, movie)
        if confidence < .72:
            continue
        def cell(cls):
            node = next(row.all(cls=cls), None)
            return node.text() if node else None
        seeds_raw = cell('seedmed') or cell('seed')
        seeds = int(seeds_raw.replace(' ', '')) if seeds_raw and seeds_raw.replace(' ', '').isdigit() else None
        status = cell('tor-status')
        if status and re.search(r'поглощ|закрыт|удален|удалён|не проверен|сомнител', status, re.I):
            continue
        topic = re.search(r'viewtopic\.php\?t=(\d+)', link.attrs['href'])[1]
        audio = bool(re.search(r'\b(?:DUB|MVO|AVO|DVO|VO)\b|дубляж|дублирован|озвуч|многоголос|двухголос', title, re.I))
        rows.append({'title': title, 'topic_id': topic, 'category': category, 'size': cell('tor-size'),
                     'seeds': seeds, 'status': status, 'confidence': confidence,
                     'ru_audio_available': audio, 'url': f'https://rutracker.org/forum/viewtopic.php?t={topic}'})
    if not parsed and not re.search(r'не найден|ничего не найден|No (?:topics|results)|Результатов поиска: 0', root.text(), re.I):
        raise SourceError('Публичная выдача недоступна: вход, защита сайта или неизвестная разметка')
    return sorted(rows, key=lambda r: (r['confidence'], r['seeds'] or 0), reverse=True)


def rutracker(movie):
    titles = identities(movie)[:3]
    if not titles or not str(movie.get('year') or '').isdigit():
        return {'status': 'not_checked', 'found': None, 'reason': 'Нужны название и год', 'matches': []}
    matches, errors, urls = [], [], []
    for title in titles:
        url = 'https://rutracker.org/forum/tracker.php?' + urlencode({'nm': f"{title} {movie['year']}"})
        urls.append(url)
        try:
            matches.extend(parse_rutracker(fetch(url), movie))
        except SourceError as error:
            errors.append(str(error))
            # Do not keep hitting a login/captcha/blocked endpoint with aliases.
            break
    unique = list({m['topic_id']: m for m in matches}.values())
    unique.sort(key=lambda r: (r['confidence'], r['seeds'] or 0), reverse=True)
    confident = [r for r in unique if r['confidence'] >= .9]
    return {'status': 'ok' if unique or not errors else 'unavailable',
            'found': True if confident else (None if errors or unique else False),
            'matches': unique[:10], 'best_match': unique[0] if unique else None,
            'search_urls': urls, 'warnings': errors}


def subtitles(movie):
    key = os.environ.get('OPENSUBTITLES_API_KEY') or tmdb._local_secrets().get('OPENSUBTITLES_API_KEY')
    if not key:
        return {'status': 'not_configured', 'available': None, 'count': None}
    queries = []
    if re.fullmatch(r'tt\d+', str(movie.get('imdb_id') or '')):
        # OpenSubtitles returns numeric IMDb IDs without IMDb's leading zeroes.
        queries.append({'imdb_id': str(int(str(movie['imdb_id'])[2:]))})
    if movie.get('tmdb_id'):
        queries.append({'tmdb_id': movie['tmdb_id']})
    if identities(movie) and str(movie.get('year') or '').isdigit():
        queries.append({'query': identities(movie)[0], 'year': movie['year']})
    if not queries:
        return {'status': 'not_checked', 'available': None, 'count': None}
    for identity in queries:
        payload = fetch('https://api.opensubtitles.com/api/v1/subtitles?' + urlencode({
            **identity, 'languages': 'ru', 'type': 'movie', 'ai_translated': 'exclude', 'machine_translated': 'exclude'}),
            headers={'Api-Key': key, 'User-Agent': 'ContentChecker v1.0'}, json_response=True)
        if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
            raise SourceError('OpenSubtitles: неизвестная структура ответа')
        matches = []
        for row in payload['data']:
            attrs = row.get('attributes', {})
            feature = attrs.get('feature_details', {})
            if attrs.get('language') != 'ru' or feature.get('feature_type') != 'Movie':
                continue
            matched = ((identity.get('imdb_id') and str(feature.get('imdb_id') or '').lstrip('0') == str(identity['imdb_id']).lstrip('0')) or
                       (identity.get('tmdb_id') and str(feature.get('tmdb_id')) == str(identity['tmdb_id'])) or
                       ('query' in identity and title_match(feature.get('title', ''), movie, feature.get('year')) >= .9))
            if matched:
                matches.append({'id': row.get('id'), 'url': attrs.get('url'), 'uploaded_at': attrs.get('upload_date')})
        if matches:
            return {'status': 'ok', 'available': True, 'count': len(matches),
                    'count_is_lower_bound': payload.get('total_pages', 1) > 1, 'matches': matches,
                    'source_url': 'https://www.opensubtitles.com/'}
    return {'status': 'ok', 'available': False, 'count': 0, 'source_url': 'https://www.opensubtitles.com/'}


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
