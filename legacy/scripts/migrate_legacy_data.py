#!/usr/bin/env python3
"""Create normalized CSV data from the repository's legacy notes.

The script is intentionally conservative: uncertain legacy entries are preserved with
an explanatory note instead of being guessed away. Existing CSV files are only
overwritten when --force is supplied.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

LIBRARY_FIELDS = [
    "id", "content_type", "title_ru", "title_original", "status", "reaction", "year",
    "creators", "participants", "genres", "duration_minutes", "external_rating",
    "external_rating_source", "external_id", "source", "url", "location", "metadata_json",
    "notes", "added_at", "consumed_at",
]
INTEREST_FIELDS = [
    "id", "content_type", "role", "name_original", "name_ru", "provider", "external_id", "active", "notes",
]
MAPPING_FIELDS = [
    "content_type", "library_id", "provider", "external_id", "canonical_title_original",
    "canonical_title_ru", "alias", "alias_language", "notes",
]

MIGRATED_AT = "2026-08-16"


WATCHED_MOVIES = [
    ("Дюна: Часть вторая", "Dune: Part Two", "2024"),
    ("Оппенгеймер", "Oppenheimer", "2023"),
    ("Дюна", "Dune", "2021"),
    ("Бедные-несчастные", "Poor Things", "2023"),
    ("Ещё по одной", "Another Round", "2020"),
    ("Убийцы цветочной луны", "Killers of the Flower Moon", "2023"),
    ("Боб Дилан: Никому не известный", "A Complete Unknown", "2024"),
    ("Довод", "Tenet", "2020"),
    ("Не смотрите наверх", "Don't Look Up", "2021"),
    ("Битва за битвой", "One Battle After Another", "2025"),
    ("F1", "F1: The Movie", "2025"),
    ("Эддингтон", "Eddington", "2025"),
    ("Быстрее пули", "Bullet Train", "2022"),
    ("Убийца", "The Killer", "2023"),
    ("Микки 17", "Mickey 17", "2025"),
    ("Виды доброты", "Kinds of Kindness", "2024"),
    ("Барби", "Barbie", "2023"),
    ("Все страхи Бо", "Beau Is Afraid", "2023"),
    ("Бэтмен", "The Batman", "2022"),
    ("Банши Инишерина", "The Banshees of Inisherin", "2022"),
    ("Лига справедливости Зака Снайдера", "Zack Snyder's Justice League", "2021"),
    ("Последняя дуэль", "The Last Duel", "2021"),
    ("Французский вестник", "The French Dispatch", "2021"),
    ("Гнев человеческий", "Wrath of Man", "2021"),
    ("Аннетт", "Annette", "2021"),
    ("Манк", "Mank", "2020"),
    ("Джентльмены", "The Gentlemen", "2019"),
    ("Финикийская схема", "The Phoenician Scheme", "2025"),
    ("Чёрная вдова", "Black Widow", "2021"),
    ("Бруталист", "The Brutalist", "2024"),
    ("Анора", "Anora", "2024"),
    ("Земля кочевников", "Nomadland", "2020"),
    ("Человек-паук: Нет пути домой", "Spider-Man: No Way Home", "2021"),
    ("Анатомия падения", "Anatomy of a Fall", "2023"),
    ("Зона интересов", "The Zone of Interest", "2023"),
    ("Субстанция", "The Substance", "2024"),
]

BACKLOG_MOVIES = [
    ("Взвод", "Platoon", "1986", ""),
    ("Всё везде и сразу", "Everything Everywhere All at Once", "2022", "Merged duplicate RU/EN entries from lib_list_2.md"),
    ("Чужие", "Aliens", "1986", "James Cameron section"),
    ("Бездна", "The Abyss", "1989", "James Cameron section"),
    ("Робокоп", "RoboCop", "1987", ""),
    ("Общество мёртвых поэтов", "Dead Poets Society", "1989", "Legacy typo normalized"),
    ("Изгоняющий дьявола", "The Exorcist", "1973", ""),
    ("Ансельм. Шум времени", "Anselm", "2023", ""),
    ("Соль Земли", "The Salt of the Earth", "2014", ""),
    ("Падение империи", "Civil War", "2024", ""),
    ("Камон Камон", "C'mon C'mon", "2021", ""),
    ("Аллея кошмаров", "Nightmare Alley", "2021", ""),
    ("Раскопки", "The Dig", "2021", ""),
    ("Солнце моё", "Aftersun", "2022", ""),
    ("Май, декабрь", "May December", "2023", ""),
    ("Мгла", "The Mist", "2007", ""),
    ("Остров доктора Моро", "The Island of Dr. Moreau", "", "Version/year was not specified in the legacy note"),
    ("Чунгкинский экспресс", "Chungking Express", "1994", ""),
    ("Головокружение", "Vertigo", "1958", "Alfred Hitchcock"),
    ("Древо жизни", "The Tree of Life", "2011", ""),
    ("Клык", "Dogtooth", "2009", "Yorgos Lanthimos"),
    ("Дети небес", "Children of Heaven", "1997", ""),
    ("Лабиринт Фавна", "Pan's Labyrinth", "2006", ""),
    ("Подкидыш", "The Foundling", "1939", "Soviet film"),
    ("Умереть во имя", "To Die For", "1995", "Gus Van Sant"),
]

EXCLUDED_ALBUMS = [
    ("Величайшая любовь", "The Greatest Love"),
    ("Из пустоты", "From Zero"),
    ("Одно убийство без Бога — Глава 1", "One Assassination Under God - Chapter 1"),
    ("Что случилось с сердцем?", "What Happened To The Heart?"),
    ("Из пустоты: акапеллы", "From Zero: A Cappellas"),
    ("Паперкаты: инструменталы", "Papercuts: Instrumentals"),
    ("Призрачная пятёрка", "The Phantom Five"),
    ("1200 ударов в минуту", "1200 Beats Per Minute"),
]

MOVIE_PEOPLE = [
    ("actor", "Joaquin Phoenix", "73421"), ("actor", "Colin Farrell", "72466"),
    ("actor", "Leonardo DiCaprio", "6193"), ("actor", "Javier Bardem", "3810"),
    ("actor", "Anthony Hopkins", "4173"), ("actor", "Brad Pitt", "287"),
    ("actor", "Antonio Banderas", "3131"), ("actor", "Matthew McConaughey", "10297"),
    ("actor", "Steve Buscemi", "884"), ("actor", "Tim Roth", "3129"),
    ("actor", "Mads Mikkelsen", "1019"), ("actor", "Scarlett Johansson", "1245"),
    ("actor", "Til Schweiger", "1844"), ("actor", "George Clooney", "1461"),
    ("actor", "Tom Hanks", "31"), ("actor", "Robert De Niro", "380"),
    ("actor", "Adrien Brody", "3490"), ("actor", "Matt Damon", "1892"),
    ("actor", "Edward Norton", "819"), ("actor", "Robert Pattinson", "11288"),
    ("actor", "Jude Law", "9642"), ("actor", "Cillian Murphy", "2037"),
    ("actor", "Benicio Del Toro", "1121"), ("actor", "Timothée Chalamet", "1190668"),
    ("actor", "Christian Bale", "3894"), ("actor", "Adam Driver", "1023139"),
    ("actor", "Jake Gyllenhaal", "131"), ("actor", "Willem Dafoe", "5293"),
    ("actor", "Paul Mescal", "2590209"), ("actor", "Michael Fassbender", "17288"),
    ("actor", "Oscar Isaac", "25072"), ("actor", "Barry Keoghan", "1290466"),
    ("actor", "Jesse Plemons", "40685"), ("actor", "Tom Hardy", "2524"),
    ("actor", "Nicolas Cage", "2963"),
    ("director", "Quentin Tarantino", "138"), ("director", "David Fincher", "7467"),
    ("director", "Jim Jarmusch", "4429"), ("director", "Guy Ritchie", "956"),
    ("director", "Christopher Nolan", "525"), ("director", "Darren Aronofsky", "6431"),
    ("director", "Martin McDonagh", "54472"), ("director", "Pedro Almodóvar", "309"),
    ("director", "Paolo Sorrentino", "56194"), ("director", "Cristian Mungiu", "20657"),
    ("director", "Yorgos Lanthimos", "122423"), ("director", "Bong Joon-ho", "21684"),
    ("director", "Lars von Trier", "42"), ("director", "Woody Allen", "1243"),
    ("director", "Denis Villeneuve", "137427"), ("director", "Wes Anderson", "5655"),
    ("director", "Paul Thomas Anderson", "3223"), ("director", "Robert Eggers", "1313360"),
    ("director", "Ari Aster", "1030513"), ("director", "Park Chan-wook", "12453"),
    ("director", "Jonathan Glazer", "53517"), ("director", "Ruben Östlund", "76043"),
    ("director", "Gaspar Noé", "2617"), ("director", "Nicolas Winding Refn", "11252"),
    ("director", "James Cameron", "2710"), ("director", "Wim Wenders", "5652"),
    ("director", "Bennett Miller", "12707"), ("director", "Hayao Miyazaki", "608"),
]

MUSIC_ARTISTS = [
    ("HÆLOS", "132sZpCaM8ie6byAEcOcRs"), ("Marilyn Manson", "2VYQTNDsvvKN9wmU5W7xpj"),
    ("Arctic Monkeys", "7Ln80lUS6He07XvHI8qqHH"), ("Nightwish", "2NPduAUeLVsfIauhRwuft1"),
    ("Hurts", "3w4VAlllkAWI6m0AV0Gn6a"), ("AWOLNATION", "4njdEjTnLfcGImKZu1iSrz"),
    ("London Grammar", "3Bd1cgCjtCI32PYvDC3ynO"), ("alt-J", "3XHO7cRUPCLOr6jwp8vsx5"),
    ("Lorde", "163tK9Wjr9P9DmM0AVK7lm"), ("The xx", "3iOvXCl6edW5Um0fXEBRXy"),
    ("Thirty Seconds to Mars", "0RqtSIYZmd4fiBKVFqyIqD"), ("OneRepublic", "5Pwc4xIPtQLFEnJriah9Y"),
    ("AURORA", "1WgXqy2Dd70QQOU7Ay074N"), ("Radiohead", "4Z8W4fKeB5YxbusRsdQVPb"),
    ("Serj Tankian", "0BEI7i5sgUuivcfwXLzFmM"), ("SVRCINA", "3wRt3iJpZDOg73CTUkfv5C"),
    ("Iron Maiden", "6mdiAmATAx73kdxrNrnlao"), ("Metallica", "2ye2Wgw4gimLv2eAKyk1NB"),
    ("Florence + The Machine", "1moxjboGR7GNWYIMWsRjgG"), ("Poets of the Fall", "1AZ30JnvQU1pbX6sbRE0Yn"),
    ("Kalandra", "2N0vFuOoMtAQfBmhsRo24e"), ("Bring Me the Horizon", "1Ffb6ejR6Fe5IamqA5oRUF"),
    ("Lana Del Rey", "00FQb4jTyendYWaN8pK0wa"), ("Stone Sour", "49qiE8dj4JuNdpYGRPdKbF"),
    ("Rammstein", "6wWVKhxIU2cEi0K81v7HvP"), ("Evanescence", "5nGIFgo0shDenQYSE0Sn7c"),
    ("Linkin Park", "6XyY86QOPPrYVGvF9ch6wz"), ("James Blake", "53KwLdlmrlCelAZMaLVZqU"),
    ("Massive Attack", "6FXMGgJwohJLUSr5nVlf9X"), ("Portishead", "6nxDkvGl2oyp6XpSFFZ89s"),
    ("Archive", "1YSI7NofR3G6oM07i31K09"), ("Woodkid", "2yIat0oYv4pY6CFrAByV7p"),
    ("Sevdaliza", "56Y9pUv2989SUnP7fX8S7G"), ("Son Lux", "4m66TCOBeUsh9Y679q9Y6Y"),
    ("Muse", "12ChZ9vBvYIiAFMG00pY9O"), ("Tame Impala", "5u7v98ib77oUOYOBrvGrge"),
    ("CHVRCHES", "3C1SndVvYvRw6mCGO0mZrm"), ("System of a Down", "5eHT9Un6B3p6vAV8z6Zq9s"),
    ("Slipknot", "05fGUMSTnwsYgiZpHvsqtM"), ("Deftones", "6Ghvu1oVhOSpMB3pYvYpS4"),
    ("Within Temptation", "3Y7XIsS9pZ9oKt899Y6pS4"), ("Ghost", "1mvvUvBTvof9S87vC9vLYm"),
    ("Eivør", "1S9SreA90qV9xQZ6Yq6Z7G"), ("Wardruna", "0S0vWE7riWyBrU198vU9Ay"),
    ("Heilung", "7hy0t94uMAnO989OpgXpS4"), ("Chelsea Wolfe", "7S9SreA90qV9xQZ6Yq6Z7G"),
]

ALIASES = {
    "Platoon": ["взвод"], "Everything Everywhere All at Once": ["все везде и сразу"],
    "Dead Poets Society": ["общ мертвых поэтов"], "The Exorcist": ["exorcist"],
    "The Island of Dr. Moreau": ["остров доктора моро"], "Vertigo": ["хичкок - головокружение"],
    "Dogtooth": ["клык - лантимос"], "To Die For": ["гас ван сент - умереть за"],
}


def blank_item(**values: str) -> dict[str, str]:
    row = {field: "" for field in LIBRARY_FIELDS}
    row.update(values)
    row["added_at"] = MIGRATED_AT
    row["metadata_json"] = json.dumps({"migrated_from": values.get("source", "legacy")}, ensure_ascii=False, separators=(",", ":"))
    return row


def parse_restaurants() -> list[dict[str, str]]:
    path = ROOT / "belgrade_restaurants.md"
    if not path.exists():
        return []
    rows = []
    status, reaction, section = "consumed", "like", ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Куда планирую"):
            status, reaction = "backlog", ""
        elif line.startswith("### "):
            section = line[4:].strip()
        match = re.match(r"- \*\*(.+?)\*\*\s*(?:[–-]\s*)?(.*)", line)
        if not match:
            continue
        displayed, rest = match.groups()
        url_match = re.search(r"https?://\S+", rest)
        url = url_match.group(0).rstrip("–—,.; ") if url_match else ""
        before_url = rest[: url_match.start()].strip(" –—") if url_match else ""
        original = before_url or displayed
        after_url = rest[url_match.end():].strip(" –—") if url_match else ""
        rows.append(blank_item(
            id=f"restaurant-{len(rows)+1:03d}", content_type="restaurant", title_ru=displayed,
            title_original=original, status=status, reaction=reaction, source="belgrade_restaurants.md",
            url=url, location="Belgrade, Serbia", genres=section, notes=after_url,
            consumed_at=MIGRATED_AT if status == "consumed" else "",
        ))
    return rows


def build_rows() -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    library: list[dict[str, str]] = []
    mappings: list[dict[str, str]] = []
    for ru, original, year in WATCHED_MOVIES:
        library.append(blank_item(
            id=f"movie-{len(library)+1:03d}", content_type="movie", title_ru=ru,
            title_original=original, year=year, status="consumed", source="already_watched.md",
            consumed_at=MIGRATED_AT,
        ))
    for ru, original, year, notes in BACKLOG_MOVIES:
        library.append(blank_item(
            id=f"movie-{len(library)+1:03d}", content_type="movie", title_ru=ru,
            title_original=original, year=year, status="backlog", source="lib_list_2.md", notes=notes,
        ))
    for ru, original in EXCLUDED_ALBUMS:
        library.append(blank_item(
            id=f"music-{sum(x['content_type'] == 'music' for x in library)+1:03d}", content_type="music",
            title_ru=ru, title_original=original, status="dismissed", source="checker.ipynb",
            notes="Excluded in the legacy Spotify draft; no like/dislike was inferred",
        ))
    library.extend(parse_restaurants())

    for item in library:
        for alias in ALIASES.get(item["title_original"], []):
            mappings.append({
                "content_type": item["content_type"], "library_id": item["id"], "provider": "legacy",
                "external_id": "", "canonical_title_original": item["title_original"],
                "canonical_title_ru": item["title_ru"], "alias": alias, "alias_language": "ru",
                "notes": "Normalized from legacy notes",
            })

    interests = []
    for index, (role, name, external_id) in enumerate(MOVIE_PEOPLE, 1):
        interests.append({
            "id": f"movie-person-{index:03d}", "content_type": "movie", "role": role,
            "name_original": name, "name_ru": name, "provider": "tmdb", "external_id": external_id,
            "active": "true", "notes": "Migrated from favorite.md/checker.ipynb/lib_list_2.md",
        })
    for index, (name, external_id) in enumerate(MUSIC_ARTISTS, 1):
        interests.append({
            "id": f"music-artist-{index:03d}", "content_type": "music", "role": "artist",
            "name_original": name, "name_ru": name, "provider": "spotify", "external_id": external_id,
            "active": "true", "notes": "Migrated from the Spotify draft in checker.ipynb; API wiring intentionally remains legacy",
        })
    return library, mappings, interests


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]], force: bool) -> None:
    if path.exists() and not force:
        raise SystemExit(f"{path} already exists; pass --force to replace generated data")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    library, mappings, interests = build_rows()
    write_csv(DATA / "library.csv", LIBRARY_FIELDS, library, args.force)
    write_csv(DATA / "mappings.csv", MAPPING_FIELDS, mappings, args.force)
    write_csv(DATA / "interests.csv", INTEREST_FIELDS, interests, args.force)
    print(f"Migrated {len(library)} library items, {len(interests)} interests, {len(mappings)} aliases")


if __name__ == "__main__":
    main()
