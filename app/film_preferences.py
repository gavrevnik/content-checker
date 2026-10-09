"""Incremental, revision-checked LLM summary of actual movie reactions."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from app import storage


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def current_feedback(db):
    rows = db.execute("""SELECT i.id,i.title_original,i.title_ru,i.reaction,i.notes,
        m.release_year AS year,m.overview,
        COALESCE((SELECT group_concat(g.name_ru, '; ') FROM movie_genres mg
                  JOIN genres g ON g.id=mg.genre_id WHERE mg.movie_id=i.id),'') AS genres,
        COALESCE((SELECT group_concat(p.name_original, '; ') FROM movie_people mp
                  JOIN people p ON p.id=mp.person_id WHERE mp.movie_id=i.id AND mp.credit_role='director'),'') AS directors
        FROM content_items i JOIN movies m ON m.content_id=i.id
        WHERE i.status='consumed' AND i.reaction IN ('like','dislike') ORDER BY i.id""").fetchall()
    return {r['id']: {**dict(r), 'overview': (r['overview'] or '')[:1500]} for r in rows}


def state(db):
    row = db.execute("SELECT * FROM film_preference_summary WHERE id='main'").fetchone()
    summary = dict(row) if row else {'summary': '', 'revision': 0, 'updated_at': None}
    summary.pop('id', None)
    reviewed = {r['movie_id']: dict(r) for r in db.execute('SELECT * FROM film_preference_evidence')}
    current = current_feedback(db)
    pending = []
    for mid in sorted(set(current) | set(reviewed)):
        snapshot = current.get(mid, {'id': mid, 'reaction': '', 'withdrawn': True})
        digest = fingerprint(snapshot)
        previous = reviewed.get(mid)
        if not previous or previous['fingerprint'] != digest:
            pending.append({'movie_id': mid, 'fingerprint': digest, 'snapshot': snapshot,
                            'previous_conclusion': previous['conclusion'] if previous else None,
                            'previous_snapshot': json.loads(previous['snapshot']) if previous else None})
    return summary, current, reviewed, pending


def context(limit=100, offset=0, include_ratings=False):
    with storage.connect() as db:
        db.execute('BEGIN')
        summary, current, reviewed, pending = state(db)
    return {'ratings': list(current.values())[offset:offset+limit] if include_ratings else [],
            'summary': summary, 'stale': bool(pending), 'pending_count': len(pending),
            'pending': pending[offset:offset+limit], 'reviewed_count': len(reviewed),
            'rating_count': len(current), 'likes': sum(r['reaction'] == 'like' for r in current.values()),
            'dislikes': sum(r['reaction'] == 'dislike' for r in current.values()),
            'note': 'Только личные реакции просмотренных фильмов, включая сохранённые в корзине. AI score и внешние рейтинги не feedback. notes могут быть прежними AI-рекомендациями; без подтверждённого авторства не считать их мнением пользователя.'}


def save(payload):
    with storage.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        summary, current, reviewed, pending = state(db)
        if payload['expected_revision'] != summary['revision']:
            raise ValueError('Summary изменилось; перечитайте film_preferences_context')
        indexed = {p['movie_id']: p for p in pending}
        evidence = payload['evidence']
        if not evidence:
            raise ValueError('Нужен непустой список нового/изменённого feedback; нет изменений — не переписывайте summary')
        if len({e['movie_id'] for e in evidence}) != len(evidence):
            raise ValueError('Повтор movie_id в evidence')
        for item in evidence:
            if item['movie_id'] not in indexed or item['fingerprint'] != indexed[item['movie_id']]['fingerprint']:
                raise ValueError('Оценки изменились; перечитайте film_preferences_context')
        revision = summary['revision'] + 1
        timestamp = datetime.now(timezone.utc).isoformat()
        for item in evidence:
            db.execute('''INSERT INTO film_preference_evidence VALUES (?,?,?,?,?,?)
                ON CONFLICT(movie_id) DO UPDATE SET fingerprint=excluded.fingerprint,snapshot=excluded.snapshot,
                conclusion=excluded.conclusion,summary_revision=excluded.summary_revision,reviewed_at=excluded.reviewed_at''',
                (item['movie_id'], item['fingerprint'], json.dumps(indexed[item['movie_id']]['snapshot'], ensure_ascii=False),
                 item['conclusion'], revision, timestamp))
        db.execute("""INSERT INTO film_preference_summary VALUES ('main',?,?,?) ON CONFLICT(id)
            DO UPDATE SET summary=excluded.summary,revision=excluded.revision,updated_at=excluded.updated_at""",
            (payload['summary'], revision, timestamp))
    return context()


def scoring_context(db, expected_revision):
    summary, current, reviewed, pending = state(db)
    if pending:
        raise ValueError('Сначала обновите summary вкусов: есть новый/изменённый feedback')
    if expected_revision != summary['revision']:
        raise ValueError('Для AI score нужен актуальный summary_revision')
    return summary, current


def score(entry, summary, current):
    fields = ('query_score', 'taste_score', 'ai_reason', 'taste_confidence')
    if not all(k in entry for k in fields):
        raise ValueError('Для AI score нужны query_score, taste_score, ai_reason, taste_confidence')
    ids = list(dict.fromkeys(entry.get('taste_evidence_ids', [])))
    if any(mid not in current for mid in ids):
        raise ValueError('taste_evidence_ids должны ссылаться на действующие личные оценки')
    if current and not ids:
        raise ValueError('Обоснуйте taste_score хотя бы одним ID оценённого фильма')
    if not current and (entry['taste_score'] != 5 or entry['taste_confidence'] != 'low'):
        raise ValueError('Без личных оценок taste_score=5, taste_confidence=low')
    return {'ai_score': round(.7 * entry['query_score'] + .3 * entry['taste_score'], 1),
            **{k: entry[k] for k in fields}, 'taste_evidence_ids': ids,
            'summary_revision': summary['revision']}
