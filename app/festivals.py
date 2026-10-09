"""Official festival programmes. No guessed nominees on transport/parser errors."""
from __future__ import annotations

import re
import json
import threading
import time
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from app import oscarbase
from app.research_sources import Tree, clean, fetch, SourceError

CATALOG = {
    'cannes': {
        'name': 'Каннский фестиваль', 'url': 'https://www.festival-cannes.com/en/', 'default_section': 'competition',
        'description': 'Official Selection — официальная программа, объединяющая конкурсные и внеконкурсные секции. Участник основного конкурса не обязательно лауреат. Отдельного шорт-листа после отбора нет.',
        'awards': ['Palme d’Or — лучший полнометражный фильм', 'Grand Prix', 'Jury Prize', 'Best Director', 'Best Screenplay', 'Best Actress', 'Best Actor', 'Caméra d’Or — лучший дебют из нескольких программ'],
        'sections': {
            'competition': ['In Competition', 'Основной конкурс полнометражных фильмов за Золотую пальмовую ветвь.'],
            'un-certain-regard': ['Un Certain Regard', 'Самостоятельный конкурс, посвящённый авторскому кино и новым талантам.'],
            'hors-competition': ['Out of Competition', 'Крупные премьеры вне основного конкурса.'],
            'cannes-premiere': ['Cannes Première', 'Дополнительные премьеры признанных авторов.'],
            'seances-de-minuit': ['Midnight Screenings', 'Полуночные показы, часто жанровое кино.'],
            'seances-speciales': ['Special Screenings', 'Специальные показы.'],
            'competition-courts-metrages': ['Short Films Competition', 'Отдельный конкурс короткого метра.'],
            'la-cinef': ['La Cinef', 'Конкурс работ киношкол.'],
            'cannes-classics': ['Cannes Classics', 'Реставрации и документальные фильмы о кино.'],
        }},
    'venice': {
        'name': 'Венецианский фестиваль', 'url': 'https://www.labiennale.org/en/cinema', 'default_section': 'competition',
        'description': 'Золотой лев присуждается фильму основного конкурса Venezia. Orizzonti имеет отдельное жюри и награды.',
        'awards': ['Golden Lion — лучший фильм', 'Silver Lion — Grand Jury Prize', 'Silver Lion — Best Director', 'Volpi Cup — актёр и актриса', 'Best Screenplay', 'Special Jury Prize', 'Marcello Mastroianni Award'],
        'sections': {'competition': ['Venezia Competition', 'Основной международный конкурс за Золотого льва.'], 'orizzonti': ['Orizzonti', 'Новые направления мирового кино.'], 'out-of-competition': ['Out of Competition', 'Показы вне конкурса.'], 'venice-classics': ['Venice Classics', 'Реставрации и фильмы о кино.']}},
    'berlinale': {
        'name': 'Берлинале', 'url': 'https://www.berlinale.de/en/home.html', 'default_section': 'competition',
        'description': 'Основной конкурс присуждает Золотого и Серебряных медведей. Секции имеют разные правила участия и собственные награды.',
        'awards': ['Golden Bear — лучший фильм', 'Silver Bear — Grand Jury Prize', 'Silver Bear — Jury Prize', 'Silver Bear — режиссура, главная и второстепенная роль, сценарий, художественный вклад'],
        'sections': {'competition': ['Competition', 'Основной конкурс за Золотого медведя.'], 'perspectives': ['Perspectives', 'Конкурс дебютных полнометражных фильмов (с 2025).'], 'panorama': ['Panorama', 'Независимое кино и премия зрительских симпатий.'], 'forum': ['Forum', 'Экспериментальные формы и авторское кино.'], 'generation': ['Generation', 'Кино для детей и молодёжи.'], 'berlinale-shorts': ['Berlinale Shorts', 'Международный конкурс короткого метра.'], 'berlinale-special': ['Berlinale Special', 'Специальные и гала-показы.']}},
    'oscars': {
        'name': 'Оскар', 'url': 'https://api.oscarbase.com/', 'provider': 'OscarBase (независимый API)', 'official_source': False, 'default_section': 'best-picture',
        'description': 'Премия Академии, а не фестиваль. Год означает год церемонии, обычно за фильмы предыдущего года. Номинанты и предварительные shortlist — разные списки; здесь финальные номинации.',
        'awards': ['Статуэтка Academy Award в каждой категории'],
        'sections': {'best-picture': ['Best Picture', 'Лучший фильм: финальные номинанты и победитель.'], 'directing': ['Directing', 'Лучшая режиссура.'], 'international-feature-film': ['International Feature Film', 'Международный полнометражный фильм.'], 'animated-feature-film': ['Animated Feature Film', 'Анимационный полнометражный фильм.'], 'documentary-feature-film': ['Documentary Feature Film', 'Документальный полнометражный фильм.'], 'actor-in-a-leading-role': ['Actor in a Leading Role', 'Лучшая мужская главная роль.'], 'actress-in-a-leading-role': ['Actress in a Leading Role', 'Лучшая женская главная роль.']}}
}
_CACHE = {}
_LOCK = threading.Lock()


def catalog():
    return {'festivals': CATALOG, 'note': 'Состав секций и награды меняются по годам; programme возвращает только подтверждённую страницу указанного года.'}


def parse_cannes(html, year, section):
    root = Tree(html).root
    current = None
    found = []
    for node in root.all():
        if node.tag == 'h2' and node.attrs.get('id'):
            current = node.attrs['id']
        if node.tag != 'a' or 'list_item__content__title' not in node.attrs.get('class', '').split():
            continue
        if current not in CATALOG['cannes']['sections'] or (section != 'all' and current != section):
            continue
        titles = list(node.all('p'))
        title = titles[0].text() if titles else node.text()
        english = node.text()[len(title):].strip().strip('()').strip()
        parent_text = node.parent.text()
        director = re.search(r'\bby (.+)', parent_text)
        found.append({'title_original': title, 'english_title': english, 'directors': director[1] if director else '',
                      'festival_year': year, 'section': current, 'source_url': node.attrs.get('href', ''), 'winner': None})
    return dedupe(found)


def parse_cannes_awards(html):
    result = {}
    for row in Tree(html).root.all(cls='list_item'):
        award = next(row.all(cls='list_item__award'), None)
        link = next(row.all('a', 'list_item__content__title'), None)
        if award and link and award.text():
            href = link.attrs.get('href', '')
            if '/en/f/' in href:
                result.setdefault(href, []).append(award.text())
            else:
                # Acting/directing awards link to the person and name the film in plain text.
                matched = re.search(r'\bfor (.+?) ' + re.escape(award.text()) + r'$', row.text())
                if matched:
                    result.setdefault('title:' + matched[1].casefold(), []).append(award.text())
    return result


def parse_venice_awards(html):
    result = {}
    for row in Tree(html).root.all('p'):
        award = next(row.all('strong'), None)
        film = next(row.all('em'), None)
        if award and film and re.search(r'LION|VOLPI|PRIZE|AWARD', award.text(), re.I):
            result.setdefault(film.text().casefold(), []).append(award.text().rstrip(' :'))
    return result


def parse_venice(html, year, section):
    result = []
    for node in Tree(html).root.all('article', 'node-film'):
        title = next(node.all(cls='lb-item-title'), None)
        link = next(node.all('a'), None)
        director = next(node.all('strong', 'no-upper'), None)
        if title and link and f'/cinema/{year}/' in link.attrs.get('href', ''):
            result.append({'title_original': title.text(), 'directors': director.text() if director else '',
                           'section': section, 'festival_year': year, 'winner': None,
                           'source_url': urljoin('https://www.labiennale.org', link.attrs['href'])})
    return dedupe(result)


BERLIN_SECTIONS = {'competition': '60', 'perspectives': '71', 'panorama': '56', 'forum': '50',
                   'generation': '52', 'berlinale-shorts': '47', 'berlinale-special': '48'}


def parse_berlinale(data, year, section):
    result = []
    if not isinstance(data, dict) or not isinstance(data.get('items'), list):
        raise SourceError('Берлинале: неизвестная структура ответа')
    filters = data.get('requestFilter', {})
    if filters.get('Year') != [year, year] or (section != 'all' and filters.get('FilmSection') != [BERLIN_SECTIONS[section]]):
        raise SourceError('Берлинале: сервер не подтвердил фильтры года и секции')
    for item in data['items']:
        href = item.get('targetPath', '')
        if not href.startswith(f'/en/{year}/programme/'):
            raise SourceError('Берлинале: фильм относится к другому году фестиваля')
        root = Tree(item.get('markup', '')).root
        tag = next(root.all(cls='section-tag'), None)
        staff = next(root.all(cls='staff'), None)
        countries = next(root.all(cls='country'), None)
        production_year = re.search(r'\b(19|20)\d{2}\b', countries.text()) if countries else None
        actual_section = next((k for k in BERLIN_SECTIONS if tag and CATALOG['berlinale']['sections'][k][0] in tag.text()), section)
        result.append({'title_original': item['titleOriginal'], 'festival_year': year,
                       'year': int(production_year[0]) if production_year else None,
                       'section': actual_section, 'section_label': tag.text() if tag else '',
                       'directors': staff.text().split('|')[0].removeprefix('by ').strip() if staff else '',
                       'source_url': urljoin('https://www.berlinale.de', href), 'winner': None})
    return dedupe(result)


def berlin_programme(year, section):
    results = []
    for page in range(1, 11):
        data = fetch('https://www.berlinale.de/api/v1/en/program', json_response=True, payload={
            'FilmSection': [] if section == 'all' else [BERLIN_SECTIONS[section]], 'Year': [year, year],
            'Sort': ['desc'], 'Page': page, 'ResultsPerPage': 100, 'Country': [], 'Search': []})
        results.extend(parse_berlinale(data, year, section))
        if page >= data.get('paging', {}).get('last', 1):
            if len(results) != data.get('total'):
                raise SourceError('Берлинале: получен неполный список')
            return results
    raise SourceError('Берлинале: превышен предел 1000 результатов; уточните секцию')


def berlin_awards(year):
    data = fetch('https://www.berlinale.de/api/v1/en/award', json_response=True, payload={
        'FilmSection': [], 'Year': [year, year], 'Sort': ['desc'], 'Page': 1,
        'ResultsPerPage': 100, 'Award': [], 'JuryCountry': [], 'Search': []})
    if data.get('requestFilter', {}).get('Year') != [year, year] or data.get('paging', {}).get('last', 1) > 1:
        raise SourceError('Архив наград не подтвердил полный список указанного года')
    return parse_berlin_awards(data.get('items', ''), year)


def parse_berlin_awards(html, year):
    result = {}
    for row in Tree(html).root.all(cls='award-list__item'):
        award = next(row.all(cls='award-list__type'), None)
        if award:
            for link in row.all('a'):
                if re.fullmatch(rf'/en/{year}/programme/\d+\.html', link.attrs.get('href', '')):
                    result.setdefault(urljoin('https://www.berlinale.de', link.attrs['href']), []).append(award.text())
    return result


def confirms_year(html, festival, year):
    root = Tree(html).root
    if festival == 'cannes':
        selected = [n for n in root.all('option') if 'selected' in n.attrs and n.text().isdigit()]
        if selected:
            return any(n.text() == str(year) for n in selected)
        # Current selection embeds the edition year in Movie JSON-LD, not the footer copyright.
        for node in root.all('script'):
            if node.attrs.get('type') == 'application/ld+json':
                try:
                    data = json.loads(node.text())
                except ValueError:
                    continue
                if data.get('@type') == 'ItemList':
                    entries = data.get('itemListElement', [])
                    entries = entries.values() if isinstance(entries, dict) else entries
                    years = {str(e.get('item', {}).get('dateCreated')) for e in entries}
                    return bool(years) and years == {str(year)}
        return False
    title = next(root.all('title'), None)
    return title is not None and str(year) in title.text()


def parse_oscars(html, year, section):
    root, result = Tree(html).root, []
    category = ''
    labels = {v[0].lower(): k for k, v in CATALOG['oscars']['sections'].items()}
    # Official ceremony markup groups nominees in view-grouping blocks.
    for group in root.all(cls='view-grouping'):
        header = next(group.all(cls='view-grouping-header'), None)
        category = labels.get(header.text().lower(), '') if header else ''
        if not category or (section != 'all' and section != category):
            continue
        for row in group.all(cls='views-row'):
            film = next(row.all(cls='field--name-field-film-title'), None) or next(row.all(cls='field-name-field-film-title'), None)
            if film is None:
                film = next(row.all(cls='views-field-field-film-title'), None)
            if film is not None:
                result.append({'title_original': film.text(), 'festival_year': year, 'section': category,
                               'winner': bool(re.search(r'\bWinner\b', row.text())),
                               'source_url': f'https://www.oscars.org/oscars/ceremonies/{year}'})
    return dedupe(result)


def dedupe(items):
    return list({(r['section'], r['source_url'], r['title_original']): r for r in items}.values())


def programme(festival, year, section=None, refresh=False):
    if festival not in CATALOG:
        raise ValueError('Неизвестный фестиваль')
    if not isinstance(year, int) or isinstance(year, bool) or not 1932 <= year <= datetime.now(ZoneInfo('Europe/Belgrade')).year + 1:
        raise ValueError('Некорректный год фестиваля')
    section = section or CATALOG[festival]['default_section']
    if section not in CATALOG[festival]['sections'] and section != 'all':
        raise ValueError('Неизвестная секция; вызовите festival_catalog')
    key = (festival, year, section)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and not refresh and time.monotonic() - cached[0] < 21600:
            return {**cached[1], 'cached': True}
    if festival == 'cannes':
        urls = [f'https://www.festival-cannes.com/en/retrospective/{year}/selection/']
        if year == datetime.now(ZoneInfo('Europe/Belgrade')).year:
            urls.append('https://www.festival-cannes.com/en/the-selection/')
    elif festival == 'venice':
        if section == 'all':
            return combine(festival, year, refresh)
        path = f'venezia-{year - 1943}-competition' if section == 'competition' else section
        urls = [f'https://www.labiennale.org/en/cinema/{year}/{path}']
    elif festival == 'berlinale':
        urls = ['https://www.berlinale.de/en/archive/programme/programme-archive.html']
    else:
        urls = ['https://api.oscarbase.com/api/nominations']
    errors, items, used = [], [], urls[0]
    skipped = 0
    for url in urls:
        try:
            if festival == 'oscars':
                items, used, skipped = oscarbase.programme(year, section)
                break
            if festival == 'berlinale':
                items = berlin_programme(year, section)
                if not items:
                    raise SourceError('Программа не найдена / ещё не опубликована')
                used = url
                break
            html = fetch(url)
            if not confirms_year(html, festival, year):
                raise SourceError('Год страницы не подтверждён')
            items = globals()['parse_' + festival](html, year, section)
            if not items:
                raise SourceError('Секция не опубликована или разметка изменилась; список не подтверждён')
            used = url
            break
        except SourceError as error:
            errors.append({'url': url, 'message': str(error)})
    warnings = [{'message': f'OscarBase: пропущены персональные номинации без фильма: {skipped}'}] if skipped else []
    if items and festival == 'cannes':
        awards_url = f'https://www.festival-cannes.com/en/retrospective/{year}/awards/'
        try:
            awards_html = fetch(awards_url)
            if not confirms_year(awards_html, 'cannes', year):
                raise SourceError('Год страницы наград не подтверждён')
            awards = parse_cannes_awards(awards_html)
            if not awards:
                raise SourceError('Разметка наград не распознана')
            for item in items:
                item['awards'] = list(dict.fromkeys(awards.get(item['source_url'], []) + awards.get('title:' + item['title_original'].casefold(), [])))
                item['winner'] = bool(item['awards'])
                item['awards_source_url'] = awards_url
        except SourceError as error:
            warnings.append({'url': awards_url, 'message': str(error)})
    elif items and festival == 'venice':
        number = year - 1943
        suffix = 'th' if 10 <= number % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')
        awards_url = f'https://www.labiennale.org/en/news/official-awards-{number}{suffix}-venice-international-film-festival'
        try:
            awards = parse_venice_awards(fetch(awards_url))
            if not awards:
                raise SourceError('Разметка наград не распознана')
            for item in items:
                item['awards'] = awards.get(item['title_original'].casefold(), [])
                item['winner'] = True if item['awards'] else None
                item['awards_source_url'] = awards_url
        except SourceError as error:
            warnings.append({'url': awards_url, 'message': str(error)})
    elif items and festival == 'berlinale':
        try:
            awards = berlin_awards(year)
            if not awards:
                raise SourceError('Награды пока не опубликованы или разметка изменилась')
            for item in items:
                item['awards'] = awards.get(item['source_url'], [])
                item['winner'] = bool(item['awards'])
                item['awards_source_url'] = 'https://www.berlinale.de/en/archive/awards-juries/awards.html'
        except SourceError as error:
            warnings.append({'message': str(error)})
    result = {'festival': festival, 'year': year, 'section': section, 'items': items,
              'status': 'ok' if items else 'unavailable', 'source_url': used,
              'provider': 'OscarBase' if festival == 'oscars' else 'official', 'official_source': festival != 'oscars',
              'checked_at': datetime.now(ZoneInfo('Europe/Belgrade')).isoformat(),
              'warnings': warnings if items else errors, 'cached': False}
    if items:
        with _LOCK:
            _CACHE[key] = (time.monotonic(), result)
    return result


def combine(festival, year, refresh):
    results = [programme(festival, year, section, refresh) for section in CATALOG[festival]['sections']]
    return {'festival': festival, 'year': year, 'section': 'all',
            'status': ('ok' if all(r['status'] == 'ok' for r in results) else 'partial') if any(r['items'] for r in results) else 'unavailable',
            'items': [i for r in results for i in r['items']], 'sections': results}
