"""Digest snapshots reuse provider cards without silently adding them to the library."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

from app import availability, film_preferences, storage


def cache_key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def snapshot(kind, key, data):
    ref = uuid.uuid4().hex
    with storage.connect() as db:
        db.execute('INSERT INTO research_snapshots VALUES (?,?,?,?,?)',
                   (ref, kind, key, json.dumps(data, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
    return {**data, 'snapshot_ref': ref}


def cached(kind, key, hours=24):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with storage.connect() as db:
        row = db.execute('SELECT * FROM research_snapshots WHERE kind=? AND cache_key=? AND created_at>=? ORDER BY created_at DESC LIMIT 1', (kind, key, since)).fetchone()
    return {**json.loads(row['data']), 'snapshot_ref': row['id'], 'cached': True} if row else None


def read_snapshot(ref, kind):
    with storage.connect() as db:
        row = db.execute('SELECT data FROM research_snapshots WHERE id=? AND kind=?', (ref, kind)).fetchone()
    if not row:
        raise ValueError(f'Неизвестный {kind} snapshot_ref: сначала вызовите инструмент проверки')
    return json.loads(row['data'])


def safe_links(values):
    if not isinstance(values, list) or len(values) > 12:
        raise ValueError('links: максимум 12 ссылок')
    result = []
    for link in values:
        if not isinstance(link, dict):
            raise ValueError('Ссылка должна содержать label и url')
        url = str(link.get('url', ''))
        parts = urlparse(url)
        if parts.scheme not in {'https', 'http'} or not parts.hostname or parts.username or len(url) > 2000:
            raise ValueError('Некорректная ссылка')
        result.append({'label': str(link.get('label') or parts.hostname)[:150], 'url': url})
    return result


def movie_identity(movie):
    if movie.get('tmdb_id'):
        return 'tmdb:' + str(movie['tmdb_id'])
    if movie.get('imdb_id'):
        return 'imdb:' + str(movie['imdb_id'])
    return availability.normalize(movie.get('title_original') or movie.get('title_ru') or '') + ':' + str(movie.get('year') or '')


def library_match(movie, db=None):
    if db is None:
        with storage.connect() as connection:
            return library_match(movie, connection)
    rows = db.execute("""SELECT i.id,i.title_original,i.title_ru,i.status,i.reaction,
        m.tmdb_id,m.imdb_id,m.release_year AS year,
        EXISTS(SELECT 1 FROM trash_entries t WHERE t.entity_type='movie' AND t.entity_id=i.id) AS trashed
        FROM content_items i JOIN movies m ON m.content_id=i.id""").fetchall()
    aliases = {}
    for row in db.execute('SELECT content_id,alias FROM content_aliases'):
        aliases.setdefault(row['content_id'], set()).add(availability.normalize(row['alias']))
    titles = {availability.normalize(movie.get(k) or '') for k in ('title_original', 'title_ru', 'english_title')} - {''}
    exact, titled, plausible = [], [], []
    for raw in rows:
        row = dict(raw)
        matches = [k for k in ('tmdb_id', 'imdb_id') if movie.get(k) and row[k] and str(movie[k]) == str(row[k])]
        conflicts = [k for k in ('tmdb_id', 'imdb_id') if movie.get(k) and row[k] and str(movie[k]) != str(row[k])]
        names = {availability.normalize(row[k] or '') for k in ('title_original', 'title_ru')} | aliases.get(row['id'], set())
        if matches and not conflicts:
            exact.append(row)
        elif titles & names and str(movie.get('year') or '') == str(row['year'] or '') and movie.get('year') and not conflicts:
            titled.append(row)
        elif titles & names and not conflicts and (not movie.get('year') or not row['year']):
            plausible.append(row)
        elif matches:  # Conflicting provider IDs require manual identity resolution.
            plausible.append(row)
    candidates = exact or titled or plausible
    status = 'existing' if len(candidates) == 1 and (exact or titled) else 'uncertain' if candidates else 'new'
    return {'status': status, 'items': candidates, 'basis': 'provider_id' if exact else 'title_year' if titled else 'ambiguous' if plausible else 'no_match'}


def release_state(movie):
    today = availability.now().date()
    value = str(movie.get('release_date') or '')
    if value:
        try:
            released = date.fromisoformat(value)
            return 'future' if released > today else 'released'
        except ValueError:
            pass
    year = str(movie.get('year') or '')
    return 'future' if year.isdigit() and int(year) > today.year else 'unknown'


def save(payload):
    query = str(payload.get('query') or '').strip()
    summary = str(payload.get('query_summary') or '').strip()
    request_key = str(payload.get('request_key') or '').strip()
    if not query or len(query) > 8000 or not summary or len(summary) > 300 or not 1 <= len(request_key) <= 100:
        raise ValueError('Нужны query (до 8000), query_summary (до 300), request_key (до 100 символов)')
    entries = payload.get('items')
    if not isinstance(entries, list) or len(entries) > 100:
        raise ValueError('Дайджест должен содержать до 100 фильмов')
    fingerprint = cache_key(payload)
    with storage.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT data FROM ai_digests WHERE request_key=?', (request_key,)).fetchone()
        if existing:
            previous = json.loads(existing['data'])
            if previous.get('request_fingerprint') != fingerprint:
                raise ValueError('request_key уже использован для другого содержимого')
            return previous
        target = None
        if bool(payload.get('digest_id')) != bool(payload.get('expected_fingerprint')):
            raise ValueError('Для обновления нужны digest_id и expected_fingerprint')
        if payload.get('digest_id'):
            row = db.execute('SELECT data FROM ai_digests WHERE id=?', (payload['digest_id'],)).fetchone()
            if not row:
                raise ValueError('Дайджест для обновления не найден')
            target = json.loads(row['data'])
            if target.get('request_fingerprint') != payload['expected_fingerprint']:
                raise ValueError('Дайджест изменился: перечитайте его перед обновлением')
        records, excluded, seen = [], [], set()
        scores = any(any(k in e for k in ('query_score', 'taste_score', 'ai_reason', 'taste_confidence', 'taste_evidence_ids')) for e in entries)
        taste, feedback = film_preferences.scoring_context(db, payload.get('summary_revision')) if scores else (None, None)
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError('Фильм должен быть объектом')
            if entry.get('movie_ref'):
                movie = read_snapshot(entry['movie_ref'], 'movie')['item']
            else:
                movie = {k: str(entry.get(k) or '')[:500] for k in ('title_original', 'title_ru', 'year', 'release_date')}
                movie.update({k: entry[k] for k in ('imdb_id', 'tmdb_id') if entry.get(k)})
                movie['content_type'] = 'movie'
                if not (movie['title_original'] or movie['title_ru']):
                    raise ValueError('Для неразрешённого фильма нужно название')
            identity = movie_identity(movie)
            if identity in seen:
                raise ValueError('В дайджесте повторяется фильм')
            seen.add(identity)
            membership, released = library_match(movie, db), release_state(movie)
            why = 'future_release' if released == 'future' and not payload.get('include_unreleased', False) else (
                'already_in_library' if payload.get('exclude_existing', False) and membership['status'] != 'new' else None)
            if why:
                excluded.append({'title': movie.get('title_ru') or movie.get('title_original'), 'reason': why, 'identity': identity})
                continue
            checked = None
            if entry.get('availability_ref'):
                checked = read_snapshot(entry['availability_ref'], 'availability')
                if checked['identity'] != identity:
                    raise ValueError('Проверка доступности относится к другому фильму')
            links = safe_links(entry.get('links', []))
            if not entry.get('movie_ref') and not links:
                raise ValueError('Для фильма без карточки нужна ссылка на источник')
            scoring = film_preferences.score(entry, taste, feedback) if any(k in entry for k in ('query_score', 'taste_score', 'ai_reason', 'taste_confidence', 'taste_evidence_ids')) else None
            records.append({'item': movie, 'resolved': bool(entry.get('movie_ref')), 'reason': str(entry.get('reason') or '')[:3000],
                            'festival_note': str(entry.get('festival_note') or '')[:1000], 'links': links,
                            'availability': checked['availability'] if checked else None, 'scoring': scoring,
                            'library_at_creation': membership, 'library': membership, 'release_state': released})
        data = {'id': target['id'] if target else uuid.uuid4().hex, 'query': query, 'query_summary': summary,
                'summary': str(payload.get('summary') or '')[:8000], 'issues': payload.get('issues', []), 'items': records, 'excluded': excluded,
                'include_unreleased': payload.get('include_unreleased', False), 'exclude_existing': payload.get('exclude_existing', False),
                'created_at': target['created_at'] if target else datetime.now(timezone.utc).isoformat(),
                'updated_at': datetime.now(timezone.utc).isoformat(), 'request_fingerprint': fingerprint}
        if target:
            db.execute('UPDATE ai_digests SET request_key=?,data=? WHERE id=?',
                       (request_key, json.dumps(data, ensure_ascii=False), data['id']))
        else:
            db.execute('INSERT INTO ai_digests VALUES (?,?,?,?)',
                       (data['id'], request_key, json.dumps(data, ensure_ascii=False), data['created_at']))
    return data


def list_digests(limit=30, offset=0):
    with storage.connect() as db:
        rows = db.execute('SELECT data FROM ai_digests ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?', (limit, offset)).fetchall()
        total = db.execute('SELECT count(*) FROM ai_digests').fetchone()[0]
    records = [json.loads(r['data']) for r in rows]
    return {'digests': [{k: v for k, v in r.items() if k not in {'items', 'fingerprint', 'request_fingerprint', 'excluded'}} | {'count': len(r['items'])} for r in records], 'total': total}


def get_digest(digest_id):
    with storage.connect() as db:
        row = db.execute('SELECT data FROM ai_digests WHERE id=?', (digest_id,)).fetchone()
    if not row:
        raise ValueError('Дайджест не найден')
    result = json.loads(row['data'])
    with storage.connect() as db:
        for record in result['items']:
            record['library'] = library_match(record['item'], db)
    return result


def check_availability(movie, markets=None, refresh=False):
    key = cache_key({'movie': movie, 'markets': markets or availability.MARKETS})
    if not refresh:
        hit = cached('availability', key, hours=6)
        if hit:
            return hit
    result = availability.check(movie, markets)
    identity = movie_identity(movie)
    if result.get('ru_subtitles_available') is True:
        with storage.connect() as db:
            row = db.execute("""SELECT json_extract(data, '$.availability.checked_at') AS checked_at
                FROM research_snapshots WHERE kind='availability'
                AND json_extract(data, '$.identity')=?
                AND json_extract(data, '$.availability.ru_subtitles_available')=1
                ORDER BY created_at LIMIT 1""", (identity,)).fetchone()
        result['evidence']['subtitles']['first_seen'] = row['checked_at'] if row else result['checked_at']
    return snapshot('availability', key, {'identity': identity, 'movie': movie, 'availability': result})
