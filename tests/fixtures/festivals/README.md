# Festival parser fixtures

Small excerpts of public official markup retrieved on 2026-10-04. Images and unrelated layout are omitted. Titles/directors and parser-relevant classes are retained. No live requests occur in tests.

- `cannes-selection.html`: https://www.festival-cannes.com/en/retrospective/2025/selection/ (one competition entry and section boundaries).
- `cannes-person-award.html`: https://www.festival-cannes.com/en/retrospective/2026/awards/ (personal directing award with the film named in plain text).
- `cannes-awards.html`: https://www.festival-cannes.com/en/retrospective/2025/awards/ (one Palme d'Or entry).
- `venice-selection.html`: https://www.labiennale.org/en/cinema/2026/venezia-83-competition (one film article).
- `venice-awards.html`: https://www.labiennale.org/en/news/official-awards-83rd-venice-international-film-festival (Golden Lion paragraph).
- `berlin.json`: POST https://www.berlinale.de/api/v1/en/program, Year=[2026,2026], FilmSection=["60"] (first entry, total reduced to 1 for isolated parsing).
- `berlin-awards.html`: POST https://www.berlinale.de/api/v1/en/award, Year=[2026,2026] (one Golden Bear entry).

Direct oscars.org requests returned HTTP 403. The current nominations adapter uses independent OscarBase JSON instead of that HTML endpoint.

- `oscarbase-2026.json`: public `https://api.oscarbase.com/api/nominations?year=2026&category=Best%20Picture&limit=100&page=1`, fetched 2026-10-04. Independent OscarBase API; 38 person nominations, 10 films; used to verify deduplication and ceremony-year checks.
