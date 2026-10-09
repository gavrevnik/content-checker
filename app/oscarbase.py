"""Independent OscarBase nominations API; year is the ceremony year."""
from __future__ import annotations

import threading
import time
from urllib.parse import urlencode
from app.research_sources import fetch, SourceError

BASE = 'https://api.oscarbase.com/api'
# Historical names have distinct IDs. Do not confuse eligible film year with ceremony year.
SECTIONS = {'best-picture': [7, 9, 10, 11, 12], 'directing': [13, 14, 15],
            'international-feature-film': [103, 104], 'animated-feature-film': [75],
            'documentary-feature-film': [106, 108], 'actor-in-a-leading-role': [1, 2],
            'actress-in-a-leading-role': [4, 5]}
_LOCK = threading.Lock()
_LAST_REQUEST = 0.


def get(url):
    global _LAST_REQUEST
    # Public limit: 100 requests/minute per IP; leave headroom. Shared across this process.
    with _LOCK:
        delay = .75 - (time.monotonic() - _LAST_REQUEST)
        if delay > 0:
            time.sleep(delay)
        _LAST_REQUEST = time.monotonic()
    return fetch(url, json_response=True)


def parse(rows, year, section):
    films = {}
    for row in rows:
        if row.get('ceremony_year') != year or (section != 'all' and row.get('category_id') not in SECTIONS[section]):
            raise SourceError('OscarBase: год или категория ответа не соответствуют запросу')
        if type(row.get('winner')) is not bool or not row.get('category'):
            raise SourceError('OscarBase: неизвестный формат номинации')
        if not row.get('movie_id') or not row.get('movie'):
            continue  # Honorary/person-only nominations cannot be represented as films.
        mid = row['movie_id']
        film = films.setdefault(mid, {'title_original': row['movie'], 'oscarbase_id': mid,
            'festival_year': year, 'section': section, 'winner': False, 'awards': [], 'nominations': [],
            'source_url': f'{BASE}/movies/{mid}'})
        if film['title_original'] != row['movie']:
            raise SourceError('OscarBase: противоречивые названия одного фильма')
        nomination = {'category': row['category'], 'nominee': row.get('nominee'), 'winner': row['winner']}
        if nomination not in film['nominations']:
            film['nominations'].append(nomination)
        if row['winner']:
            film['winner'] = True
            if row['category'] not in film['awards']:
                film['awards'].append(row['category'])
    return list(films.values())


def programme(year, section):
    rows, seen, total, pages = [], set(), None, None
    params = {'year': year, 'limit': 100}
    if section != 'all':
        params['category_ids'] = ','.join(map(str, SECTIONS[section]))
    url = BASE + '/nominations?' + urlencode(params)
    for page in range(1, 21):
        data = get(url + '&page=' + str(page))
        paging = data.get('pagination', {}) if isinstance(data, dict) else {}
        if not isinstance(data, dict) or not isinstance(data.get('data'), list) or paging.get('page') != page or type(paging.get('total')) is not int or type(paging.get('totalPages')) is not int:
            raise SourceError('OscarBase: неизвестная структура пагинации')
        if page == 1:
            total, pages = paging['total'], paging['totalPages']
            if not 0 <= total <= 2000 or not 0 <= pages <= 20:
                raise SourceError('OscarBase: программа превышает лимит проверки')
        if (paging['total'], paging['totalPages']) != (total, pages):
            raise SourceError('OscarBase: список изменился во время проверки')
        for row in data['data']:
            if not isinstance(row, dict) or not row.get('id') or row['id'] in seen:
                raise SourceError('OscarBase: некорректная или повторная запись страницы')
            seen.add(row['id'])
            rows.append(row)
        if page >= pages:
            break
    if len(rows) != total:
        raise SourceError('OscarBase: получен неполный список номинаций')
    films = parse(rows, year, section)
    if not films:
        raise SourceError('OscarBase: номинации не найдены / ещё не опубликованы; отсутствие не доказано')
    return films, url, sum(not r.get('movie_id') or not r.get('movie') for r in rows)
