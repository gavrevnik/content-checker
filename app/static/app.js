const state = { items: [], interests: [], trash: [], view: "backlog", contentType: "movie", backlogRecommendMode: "llm", query: "", apiItems: [], apiFilteredItems: [], apiPeriod: "", llmItems: [], llmFilteredItems: [], llmErrors: [], llmRawResponse: "", recommendationCancelled: false, backlogMoods: [], selectedBacklogMood: "", peopleRecommendationItems: [], peopleRecommendationErrors: [], peopleRecommendationCancelled: false, movieSearchItems: [], personSearchItems: [], apiProgressWarnings: [], recommendationSort: {key:"year",direction:"desc"}, tmdb: {}, musicbrainz: {}, llm: {}, llmContextSeed: "", sortKey: "release_date", sortDirection: "desc", peopleSort: {director:{key:"name_ru",direction:"asc"},actor:{key:"name_ru",direction:"asc"},artist:{key:"name_original",direction:"asc"}}, currentDetailId: "", currentPersonId: "", currentPersonRole: "", pendingMovieDetails: null, pendingAlbumDetails: null, pendingPersonDetails: null, pendingArtistDetails: null, pendingMovieRaw: null, pendingAlbumRaw: null, pendingPersonRaw: null, pendingArtistRaw: null, pendingMovieSearchField: "", pendingAlbumSearchField: "" };
const activeOperations = {recommendation:"",moods:"",people:"",refresh:""};
const operationRunning = () => Object.values(activeOperations).some(Boolean);
let contentTypeSwitching = false;
const $ = selector => document.querySelector(selector);
const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
function normalizeBacklogMood(mood) {
  return {
    label:String(mood?.label || "").trim(),
    itemIds:[...new Set((mood?.item_ids || mood?.itemIds || []).map(String).filter(Boolean))],
  };
}

function filterBacklogByMood(items) {
  if (!state.selectedBacklogMood) return items;
  const selected = state.backlogMoods.find(mood=>mood.label === state.selectedBacklogMood);
  if (!selected) return items;
  const mappedIds = new Set(selected.itemIds);
  return items.filter(item=>mappedIds.has(String(item.id)));
}

async function request(url, options = {}) {
  const response = await fetch(url, { headers: {"Content-Type":"application/json"}, ...options });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Не удалось выполнить запрос");
  return payload;
}

function toast(message) {
  const node = $("#toast"); node.textContent = message; node.classList.add("visible");
  clearTimeout(toast.timer); toast.timer = setTimeout(() => node.classList.remove("visible"), 2600);
}

function awardsSummary(raw) {
  try {
    const awards = typeof raw === "string" ? JSON.parse(raw || "[]") : (raw || []);
    if (Array.isArray(awards)) return awards.map(item => item.summary || [item.event, item.award, item.result].filter(Boolean).join(" · ")).filter(Boolean).join("; ") || "—";
    return awards.summary || "—";
  } catch { return String(raw || "—"); }
}

function text(value) { return value === null || value === undefined || value === "" ? "—" : String(value); }
function addedDate(value) {
  const date = new Date(String(value || ""));
  return Number.isNaN(date.getTime()) ? text(value) : new Intl.DateTimeFormat("ru-RU", {
    day:"2-digit",month:"2-digit",year:"numeric",
  }).format(date);
}
function actionIcon(name) {
  const thumb = `<path d="M7 10v11H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3Z"/><path d="m7 10 4-7a2 2 0 0 1 3.7 1.5L14 8h5a2 2 0 0 1 2 2.4l-1.6 8a2 2 0 0 1-2 1.6H7"/>`;
  const paths = {
    like: thumb,
    dislike: `<g transform="rotate(180 12 12)">${thumb}</g>`,
    trash: `<path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6"/>`,
    calendarCheck: `<path d="M8 2v4M16 2v4M3 10h18"/><rect x="3" y="4" width="18" height="18" rx="2"/><path d="m8.5 16 2 2 5-5"/>`,
    expand: `<path d="M9 3H3v6M15 3h6v6M21 15v6h-6M9 21H3v-6"/>`,
    collapse: `<path d="M3 9h6V3M21 9h-6V3M15 21v-6h6M9 21v-6H3"/>`,
  };
  return `<svg class="action-icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || ""}</svg>`;
}
function setRecommendationResultExpanded(dialogId, expanded) {
  const dialog = $(`#${dialogId}`);
  if (!dialog) return;
  dialog.classList.toggle("result-expanded",expanded);
  document.querySelectorAll(`[data-result-size-toggle="${dialogId}"]`).forEach(button=>{
    const label = expanded ? "Свернуть таблицу" : "Увеличить таблицу";
    button.innerHTML = actionIcon(expanded ? "collapse" : "expand");
    button.title = label; button.setAttribute("aria-label",label); button.setAttribute("aria-pressed",String(expanded));
  });
}
function toggleRecommendationResultSize(dialogId) {
  const dialog = $(`#${dialogId}`);
  if (dialog) setRecommendationResultExpanded(dialogId,!dialog.classList.contains("result-expanded"));
}
function movieRatings(item) { return `${text(item.imdb_rating)} / ${text(item.kinopoisk_rating)}`; }
function plannedSoonButton(item) {
  const active = Boolean(item.planned_soon);
  const label = active ? "Убрать из ближайших планов" : "Запланировать на ближайшее время";
  return `<button type="button" class="icon-button outline-action-button planned-soon-button${active ? " active" : ""}" data-planned-soon-toggle data-id="${escapeHtml(item.id)}" title="${label}" aria-label="${label}" aria-pressed="${active}">${actionIcon("calendarCheck")}</button>`;
}
function providerWarningText(warning) {
  const labels = {"cover-art-archive":"Cover Art Archive",listenbrainz:"ListenBrainz",musicbrainz:"MusicBrainz",kinopoisk:"Кинопоиск","tmdb-images":"TMDB Images","fanart.tv":"fanart.tv",tmdb:"TMDB"};
  const label = labels[String(warning?.provider || "").toLowerCase()] || warning?.provider || "API", message = String(warning?.message || "");
  if (!message) return "";
  return message.toLowerCase().includes(String(label).toLowerCase()) ? message : `${label}: ${message}`;
}
function providerWarnings(items) {
  const messages = items.flatMap(item => item?.provider_warnings || []).map(providerWarningText).filter(Boolean);
  return [...new Set(messages)];
}
function showProviderWarnings(items) {
  const warnings = [...new Set([...state.apiProgressWarnings.map(providerWarningText), ...providerWarnings(items)].filter(Boolean))], node = $("#backlog-recommend-warning");
  node.textContent = warnings.join(" "); node.classList.toggle("hidden", !warnings.length);
}
function compactObject(value) { return Object.fromEntries(Object.entries(value).filter(([,item]) => item !== "" && item !== null && item !== undefined)); }
function formatRaw(value) {
  try {
    const parsed = typeof value === "string" ? JSON.parse(value || "{}") : (value || {});
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return text(parsed);
    const labels = {title_original:"Оригинальное название",title_ru:"Название на русском",year:"Год",directors:"Режиссёр",artists:"Исполнитель",notes:"Заметка",tmdb_id:"TMDB ID",release_group_mbid:"MusicBrainz ID",mbid:"MusicBrainz ID",name:"Исполнитель",name_original:"Оригинальное имя",name_ru:"Имя на русском",role:"Тип"};
    return Object.entries(parsed).filter(([,item]) => item !== "").map(([key,item]) => `${labels[key] || key}: ${item}`).join(" · ") || "—";
  } catch { return text(value); }
}
function title(item) { return item.title_ru || item.title_original; }
function validUrl(value) { return /^https?:\/\//.test(String(value || "")) ? String(value) : ""; }
function rutrackerMovieUrl(item) {
  const name = String(item?.title_ru || item?.title_original || "").trim();
  if (!name) return "";
  const year = item?.year || String(item?.release_date || "").slice(0,4);
  const query = [name, year].filter(Boolean).join(" ");
  return `https://rutracker.org/forum/tracker.php?${new URLSearchParams({nm:query}).toString()}`;
}
function movieLinks(item, includeTmdb = false) {
  const links = [];
  const imdb = validUrl(item.imdb_link) || (item.imdb_id ? `https://www.imdb.com/title/${encodeURIComponent(item.imdb_id)}/` : "");
  const kinopoisk = validUrl(item.kinopoisk_link) || (item.kinopoisk_id ? `https://www.kinopoisk.ru/film/${encodeURIComponent(item.kinopoisk_id)}/` : "");
  const tmdb = validUrl(item.tmdb_link) || (item.tmdb_id ? `https://www.themoviedb.org/movie/${encodeURIComponent(item.tmdb_id)}` : "");
  if (imdb) links.push(["IMDb", imdb]);
  if (kinopoisk) links.push(["КП", kinopoisk]);
  const rutracker = rutrackerMovieUrl(item);
  if (rutracker) links.push(["RuTr", rutracker]);
  if (includeTmdb && tmdb) links.push(["TMDB", tmdb]);
  return links;
}
function movieLinksHtml(item, includeTmdb = false) {
  const links = movieLinks(item, includeTmdb);
  return links.length
    ? links.map(([label, url]) => `<a class="external-link" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${label} ↗</a>`).join(" ")
    : "—";
}
function backlogMovieLinksHtml(item) {
  const links = movieLinks(item).filter(([label]) => ["КП", "RuTr"].includes(label));
  return links.length
    ? links.map(([label, url]) => `<a class="external-link" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${label} ↗</a>`).join(" ")
    : "";
}
function movieDraftLinksHtml(item) {
  const links = [];
  const tmdb = validUrl(item?.tmdb_link) || (item?.tmdb_id ? `https://www.themoviedb.org/movie/${encodeURIComponent(item.tmdb_id)}` : "");
  const kinopoisk = validUrl(item?.kinopoisk_link) || (item?.kinopoisk_id ? `https://www.kinopoisk.ru/film/${encodeURIComponent(item.kinopoisk_id)}/` : "");
  if (tmdb) links.push(["TMDB", tmdb]);
  if (kinopoisk) links.push(["КП", kinopoisk]);
  const rutracker = rutrackerMovieUrl(item);
  if (rutracker) links.push(["RuTr", rutracker]);
  return links.length
    ? links.map(([label, url]) => `<a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${label} ↗</a>`).join(" ")
    : "—";
}
function albumLinksHtml(item) {
  const url = youtubeMusicAlbumUrl(item);
  return `<a class="external-link" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">YouTube Music ↗</a>`;
}
function youtubeMusicAlbumUrl(item) {
  const year = item?.year || String(item?.first_release_date || "").slice(0, 4);
  const query = [item?.title_original || title(item), year ? `(${year})` : "", item?.artists].filter(Boolean).join(" ");
  return `https://music.youtube.com/search?${new URLSearchParams({q:query}).toString()}`;
}
function youtubeMusicAlbumLinkHtml(item) {
  return `<a class="youtube-music-link" href="${escapeHtml(youtubeMusicAlbumUrl(item))}" target="_blank" rel="noreferrer">Слушать в YouTube Music ↗</a>`;
}
function listenCount(item) { return item.total_listen_count === null || item.total_listen_count === undefined || item.total_listen_count === "" ? "—" : new Intl.NumberFormat("ru-RU").format(Number(item.total_listen_count)); }
function localArtworkUrl(relativePath) {
  return relativePath ? `/media/artwork/${String(relativePath).split("/").map(encodeURIComponent).join("/")}` : "";
}
function artworkItemUrl(item, contentType) {
  return item?.id && item?.added_at ? `/api/artwork/${contentType}/${encodeURIComponent(item.id)}` : "";
}
function albumCoverHtml(item, className = "album-cover") {
  const available = item?.cover_path || validUrl(item?.cover_url);
  const url = available && (artworkItemUrl(item,"music") || localArtworkUrl(item?.cover_path) || validUrl(item?.cover_url));
  return url ? `<img class="${className}" src="${escapeHtml(url)}" alt="Обложка ${escapeHtml(title(item))}" loading="lazy" data-artwork>` : `<span class="album-cover-placeholder" aria-hidden="true"></span>`;
}
function moviePosterHtml(item, className = "movie-poster") {
  const available = item?.poster_local_path || item?.poster_path || validUrl(item?.poster_url);
  const url = available && (artworkItemUrl(item,"movie") || localArtworkUrl(item?.poster_local_path) || validUrl(item?.poster_url));
  return url ? `<img class="${className}" src="${escapeHtml(url)}" alt="Постер ${escapeHtml(title(item))}" loading="lazy" data-artwork>` : `<span class="movie-poster-placeholder" aria-hidden="true"></span>`;
}
function personPhotoUrl(item) {
  const available = item?.profile_local_path || item?.profile_path || validUrl(item?.profile_url);
  const storedUrl = item?.id && available ? `/api/artwork/person/${encodeURIComponent(item.id)}` : "";
  return available && (storedUrl || localArtworkUrl(item?.profile_local_path) || validUrl(item?.profile_url));
}
function personPhotoHtml(item, className = "person-photo") {
  const url = personPhotoUrl(item);
  return url ? `<img class="${className}" src="${escapeHtml(url)}" alt="Фото ${escapeHtml(titlePerson(item))}" loading="lazy" data-artwork>` : `<span class="person-photo-placeholder ${className === "detail-person-photo" ? "detail-person-photo" : ""}" aria-hidden="true"></span>`;
}
function personImdbId(item) {
  if (item?.imdb_id) return String(item.imdb_id);
  try {
    const metadata = typeof item?.details_json === "string" ? JSON.parse(item.details_json || "{}") : (item?.details_json || {});
    return String(metadata.imdb_id || metadata.external_ids?.imdb_id || "");
  } catch { return ""; }
}
function personExternalLinkHtml(item) {
  const music = item?.content_type === "music" || item?.role === "artist";
  const mbid = String(item?.mbid || (music ? item?.external_id : "") || "");
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(mbid)) {
    return `<a class="external-link person-provider-link" data-person-external-link href="https://musicbrainz.org/artist/${encodeURIComponent(mbid)}" target="_blank" rel="noreferrer">MusicBrainz ↗</a>`;
  }
  const imdbId = personImdbId(item), validImdbId = /^nm\d+$/.test(imdbId) ? imdbId : "";
  return validImdbId ? `<a class="external-link person-provider-link" data-person-external-link href="https://www.imdb.com/name/${encodeURIComponent(validImdbId)}/" target="_blank" rel="noreferrer">IMDb ↗</a>` : "";
}
function personPortraitHtml(item, className = "person-photo", recommendationIndex = null) {
  const photo = personPhotoHtml(item,className);
  const viewablePhoto = recommendationIndex !== null && personPhotoUrl(item)
    ? `<button type="button" class="person-photo-view-button" data-view-person-photo="${recommendationIndex}" aria-label="Открыть фотографию ${escapeHtml(titlePerson(item))} крупнее" title="Открыть фотографию крупнее">${photo}</button>`
    : photo;
  return `<div class="person-portrait">${viewablePhoto}${personExternalLinkHtml(item)}</div>`;
}
function openPersonPhotoViewer(index) {
  const item = state.peopleRecommendationItems[index], url = personPhotoUrl(item);
  if (!item || !url) return;
  const image = $("#person-photo-view-image");
  $("#person-photo-view-title").textContent = titlePerson(item);
  image.src = url;
  image.alt = `Фото ${titlePerson(item)}`;
  $("#person-photo-view-dialog").showModal();
}
function albumTypes(item) {
  let secondary = item.secondary_types || item.secondary_types_json || [];
  if (typeof secondary === "string") { try { secondary = JSON.parse(secondary); } catch { secondary = []; } }
  return [item.primary_type, ...(Array.isArray(secondary) ? secondary : [])].filter(Boolean).join(" · ");
}
function formatTrackList(value) {
  if (!Array.isArray(value) || !value.length) return "—";
  return value.map(track => `${track.number ? `${track.number}. ` : ""}${track.title || ""}`).join("; ");
}
function renderAlbumDraftPreview(item = null) {
  const node = $("#add-album-preview");
  if (!item) { node.innerHTML = ""; node.classList.add("hidden"); return; }
  const fields = [["Исполнитель",item.artists],["Первый выпуск",item.first_release_date || item.year],["Количество песен",item.track_count],["Тип",albumTypes(item)],["Жанры",item.genres],["Прослушивания ListenBrainz",listenCount(item)]];
  node.innerHTML = fields.map(([label,value]) => `<div class="detail"><span>${label}</span><p>${escapeHtml(text(value))}</p></div>`).join("");
  node.classList.remove("hidden");
}
function renderMovieDraftPreview(item = null) {
  const node = $("#add-movie-preview");
  if (!item) {
    node.innerHTML = ""; node.classList.add("hidden"); return;
  }
  const fields = [
    ["IMDb rating", item.imdb_rating],
    ["Кинопоиск rating", item.kinopoisk_rating],
    ["Актёры", item.cast, true],
    ["Ключевые персоны", formatKeyPeople(item), true],
    ["Описание", item.overview, true],
  ];
  const poster = item.poster_local_path || item.poster_url ? `<div class="detail wide"><span>Постер</span>${moviePosterHtml(item,"detail-poster")}</div>` : "";
  node.innerHTML = poster + fields.map(([label, value, wide]) => `<div class="detail ${wide ? "wide" : ""}"><span>${label}</span><p>${escapeHtml(text(value))}</p></div>`).join("");
  node.classList.remove("hidden");
}
function filtered(items) {
  const query = state.query.trim().toLocaleLowerCase("ru");
  if (!query) return items;
  return items.filter(item => [
    item.title_ru, item.title_original, item.directors, item.artists, item.genres, item.countries,
    item.key_actors, item.key_directors, item.key_people, formatKeyPeople(item),
  ].filter(Boolean).join(" ").toLocaleLowerCase("ru").includes(query));
}

function peopleList(value) { return String(value || "").split(";").map(item => item.trim()).filter(Boolean); }
function directorsCellHtml(value) {
  const directors = peopleList(value);
  if (!directors.length) return "—";
  return directors.map(name => {
    const match = name.match(/^(.*?)\s*\(([^()]*)\)\s*$/);
    if (!match) return `<span class="director-entry">${escapeHtml(name)}</span>`;
    return `<span class="director-entry"><span>${escapeHtml(match[1].trim())}</span><span class="director-original">(${escapeHtml(match[2].trim())})</span></span>`;
  }).join("");
}
function formatKeyPeople(item, compact = false, localizedOnly = false, hideRole = false) {
  const groups = [["Актёры", peopleList(item.key_actors)], ["Режиссёры", peopleList(item.key_directors)]];
  if (compact) {
    const populated = groups.filter(([,people]) => people.length);
    if (!populated.length) return item.key_people || "";
    const total = populated.reduce((sum, [,people]) => sum + people.length, 0);
    const firstName = localizedOnly ? populated[0][1][0].replace(/\s*\([^)]*\)\s*/g, " ").trim() : populated[0][1][0];
    return `${hideRole ? "" : `${populated[0][0]}: `}${firstName}${total > 1 ? " и др." : ""}`;
  }
  const parts = groups.filter(([,people]) => people.length).map(([label, people]) => {
    return `${label}: ${people.join("; ")}`;
  });
  return parts.join(" · ") || item.key_people || "";
}

function sortedBacklog(items) {
  const numeric = new Set(["imdb_rating", "kinopoisk_rating", "total_listen_count", "track_count", "year", "planned_soon"]);
  return [...items].sort((left, right) => {
    const a = left[state.sortKey] ?? "", b = right[state.sortKey] ?? "";
    let result;
    if (numeric.has(state.sortKey)) result = (Number(a) || -Infinity) - (Number(b) || -Infinity);
    else result = String(a).localeCompare(String(b), "ru", {numeric:true, sensitivity:"base"});
    return state.sortDirection === "asc" ? result : -result;
  });
}

function updateSortIndicators() {
  document.querySelectorAll("[data-sort]").forEach(button => {
    const active = button.dataset.sort === state.sortKey;
    button.classList.toggle("active", active); button.dataset.direction = active ? state.sortDirection : "";
    button.setAttribute("aria-sort", active ? (state.sortDirection === "asc" ? "ascending" : "descending") : "none");
  });
}

function movieRow(item) {
  const action = `<button type="button" class="mini-button like reaction-button outline-action-button" data-item-action="like" data-id="${escapeHtml(item.id)}" title="Понравилось" aria-label="Понравилось">${actionIcon("like")}</button><button type="button" class="mini-button dislike reaction-button outline-action-button" data-item-action="dislike" data-id="${escapeHtml(item.id)}" title="Не понравилось" aria-label="Не понравилось">${actionIcon("dislike")}</button><button type="button" class="icon-button trash-button outline-action-button" data-trash-entity="movie" data-id="${escapeHtml(item.id)}" title="В корзину" aria-label="Переместить фильм в корзину">${actionIcon("trash")}</button>`;
  const titleCell = `<button class="movie-title-button" data-details-id="${escapeHtml(item.id)}"><strong>${escapeHtml(title(item))}</strong><span>${escapeHtml(item.title_original)}</span></button><div class="movie-title-links">${backlogMovieLinksHtml(item)}</div>`;
  return `<tr>
    <td class="movie-poster-cell">${moviePosterHtml(item)}</td><td class="movie-title backlog-title-cell">${titleCell}</td>
    <td class="backlog-release-cell">${escapeHtml(text(item.release_date || item.year))}</td><td class="backlog-director-cell">${directorsCellHtml(item.directors)}</td>
    <td>${escapeHtml(movieRatings(item))}</td><td class="cell-people">${escapeHtml(text(formatKeyPeople(item, true, true, true)))}</td>
    <td class="countries-cell">${escapeHtml(text(item.countries))}</td><td class="added-at-cell">${escapeHtml(addedDate(item.added_at))}</td><td class="planned-soon-cell">${plannedSoonButton(item)}</td>
    <td><div class="table-actions">${action}</div></td>
  </tr>`;
}

function albumRow(item) {
  const action = `<button type="button" class="mini-button like reaction-button outline-action-button" data-item-action="like" data-id="${escapeHtml(item.id)}" title="Понравилось" aria-label="Понравилось">${actionIcon("like")}</button><button type="button" class="mini-button dislike reaction-button outline-action-button" data-item-action="dislike" data-id="${escapeHtml(item.id)}" title="Не понравилось" aria-label="Не понравилось">${actionIcon("dislike")}</button><button type="button" class="icon-button trash-button outline-action-button" data-trash-entity="album" data-id="${escapeHtml(item.id)}" title="В корзину" aria-label="Переместить альбом в корзину">${actionIcon("trash")}</button>`;
  const titleCell = `<button class="movie-title-button" data-details-id="${escapeHtml(item.id)}"><strong>${escapeHtml(item.title_original || title(item))}</strong><span>${escapeHtml(text(item.artists))}</span></button>`;
  return `<tr><td class="album-cover-cell">${albumCoverHtml(item)}</td><td class="movie-title">${titleCell}</td><td>${escapeHtml(text(item.artists))}</td><td>${escapeHtml(text(item.first_release_date || item.year))}</td><td>${escapeHtml(text(item.track_count))}</td><td>${escapeHtml(listenCount(item))}</td><td class="cell-links"><a class="external-link" href="${escapeHtml(youtubeMusicAlbumUrl(item))}" target="_blank" rel="noreferrer">YouTube Music ↗</a></td><td class="added-at-cell">${escapeHtml(addedDate(item.added_at))}</td><td class="planned-soon-cell">${plannedSoonButton(item)}</td><td><div class="table-actions">${action}</div></td></tr>`;
}

function recommendationMovieRow(item, index, filtered = false) {
  const notes = state.backlogRecommendMode === "llm" ? `<td class="cell-notes">${escapeHtml(text(item.notes))}</td>` : "";
  const detailsAttribute = filtered ? `data-filtered-rec-details="${index}"` : `data-modal-rec-details="${index}"`;
  const actions = filtered
    ? `<button type="button" class="mini-button done card-action-button" data-filtered-rec-action="backlog" data-index="${index}">В бэклог</button>`
    : `<button type="button" class="mini-button done card-action-button" data-modal-rec-action="backlog" data-index="${index}">В бэклог</button><button type="button" class="mini-button like reaction-button outline-action-button" data-modal-rec-action="like" data-index="${index}" title="Понравилось" aria-label="Понравилось">${actionIcon("like")}</button><button type="button" class="mini-button dislike reaction-button outline-action-button" data-modal-rec-action="dislike" data-index="${index}" title="Не понравилось" aria-label="Не понравилось">${actionIcon("dislike")}</button><button type="button" class="mini-button remove card-action-button" data-modal-rec-action="remove" data-index="${index}">Убрать</button>`;
  return `<tr>
    <td class="movie-poster-cell">${moviePosterHtml(item)}</td><td class="movie-title"><button type="button" class="movie-title-button" ${detailsAttribute}><strong>${escapeHtml(title(item))}</strong><span>${escapeHtml(item.title_original)}</span></button></td>
    <td>${escapeHtml(text(item.release_date || item.year))}</td><td>${escapeHtml(text(item.directors))}</td>
    <td>${escapeHtml(movieRatings(item))}</td><td>${escapeHtml(text(item.countries))}</td><td>${escapeHtml(text(item.genres))}</td>
    <td class="cell-people">${escapeHtml(text(formatKeyPeople(item, true)))}</td>
    ${notes}
    <td class="cell-links">${movieLinksHtml(item)}</td>
    <td><div class="table-actions">${actions}</div></td>
  </tr>`;
}

function recommendationAlbumRow(item, index, filtered = false) {
  const notes = state.backlogRecommendMode === "llm" ? `<td class="cell-notes">${escapeHtml(text(item.notes))}</td>` : "";
  const detailsAttribute = filtered ? `data-filtered-rec-details="${index}"` : `data-modal-rec-details="${index}"`;
  const actions = filtered
    ? `<button type="button" class="mini-button done card-action-button" data-filtered-rec-action="backlog" data-index="${index}">В бэклог</button>`
    : `<button type="button" class="mini-button done card-action-button" data-modal-rec-action="backlog" data-index="${index}">В бэклог</button><button type="button" class="mini-button like reaction-button outline-action-button" data-modal-rec-action="like" data-index="${index}" title="Понравилось" aria-label="Понравилось">${actionIcon("like")}</button><button type="button" class="mini-button dislike reaction-button outline-action-button" data-modal-rec-action="dislike" data-index="${index}" title="Не понравилось" aria-label="Не понравилось">${actionIcon("dislike")}</button><button type="button" class="mini-button remove card-action-button" data-modal-rec-action="remove" data-index="${index}">Убрать</button>`;
  return `<tr><td class="album-cover-cell">${albumCoverHtml(item)}</td><td class="movie-title"><button type="button" class="movie-title-button" ${detailsAttribute}><strong>${escapeHtml(title(item))}</strong><span>${escapeHtml(item.title_original)}</span></button></td><td>${escapeHtml(text(item.artists))}</td><td>${escapeHtml(text(item.first_release_date || item.year))}</td><td>${escapeHtml(text(item.track_count))}</td><td>${escapeHtml(text(albumTypes(item)))}</td><td>${escapeHtml(text(item.genres))}</td><td>${escapeHtml(listenCount(item))}</td>${notes}<td class="cell-links">${albumLinksHtml(item)}</td><td><div class="table-actions">${actions}</div></td></tr>`;
}

function recommendationSortValue(item, key) {
  if (key === "title") return title(item);
  if (key === "year") return item.year || item.first_release_date || item.release_date || "";
  if (key === "rating") return item.imdb_rating ?? item.tmdb_rating ?? "";
  if (key === "types") return albumTypes(item);
  if (key === "key_people") return formatKeyPeople(item, true);
  return item[key] ?? "";
}

function sortedRecommendationEntries(items) {
  const numeric = new Set(["year","rating","track_count","total_listen_count"]), setting = state.recommendationSort;
  return items.map((item,index) => ({item,index})).sort((left,right) => {
    const a = recommendationSortValue(left.item,setting.key), b = recommendationSortValue(right.item,setting.key);
    let result = numeric.has(setting.key)
      ? (Number(String(a).slice(0,4)) || -Infinity) - (Number(String(b).slice(0,4)) || -Infinity)
      : String(a).localeCompare(String(b),"ru",{numeric:true,sensitivity:"base"});
    if (!result) result = title(left.item).localeCompare(title(right.item),"ru",{numeric:true,sensitivity:"base"});
    return setting.direction === "asc" ? result : -result;
  });
}

function updateRecommendationSortIndicators() {
  document.querySelectorAll("[data-recommend-sort]").forEach(button => {
    const active = button.dataset.recommendSort === state.recommendationSort.key;
    button.classList.toggle("active",active); button.dataset.direction = active ? state.recommendationSort.direction : "";
    button.setAttribute("aria-sort",active ? (state.recommendationSort.direction === "asc" ? "ascending" : "descending") : "none");
  });
}

function currentFilteredRecommendations() {
  return state.backlogRecommendMode === "llm" ? state.llmFilteredItems : state.apiFilteredItems;
}

function filteredRecommendationLabel(item) {
  if (state.contentType === "music") {
    return [text(item.artists), title(item)].filter(value => value && value !== "—").join(" — ");
  }
  const year = item.year || String(item.release_date || "").slice(0,4);
  return `${title(item)}${year ? ` (${year})` : ""}`;
}

function renderFilteredRecommendations() {
  const records = currentFilteredRecommendations();
  const section = $("#backlog-filtered-result");
  section.classList.toggle("hidden", !records.length);
  $("#open-filtered-reasons").classList.toggle("hidden", !records.length);
  $("#filtered-reasons-title").textContent = `Не прошли фильтрацию · ${records.length}`;
  $("#backlog-filtered-summary").innerHTML = records.map(record =>
    `<li><strong>${escapeHtml(filteredRecommendationLabel(record.item || {}))}</strong> — ${escapeHtml(text(record.reason))}</li>`
  ).join("");
  if (!records.length && $("#filtered-reasons-dialog").open) $("#filtered-reasons-dialog").close();
  const row = state.contentType === "music" ? recommendationAlbumRow : recommendationMovieRow;
  $("#backlog-filtered-body").innerHTML = sortedRecommendationEntries(records.map(record => record.item || {}))
    .map(({item,index}) => row(item,index,true)).join("");
}

function renderLlmRecommendations() {
  $("#open-llm-raw-response").classList.toggle("hidden", !state.llmRawResponse);
  $("#backlog-recommend-body").innerHTML = state.llmItems.length
    ? sortedRecommendationEntries(state.llmItems).map(({item,index}) => (state.contentType === "music" ? recommendationAlbumRow : recommendationMovieRow)(item,index)).join("")
    : `<tr><td colspan="11" class="empty">Подходящих новых ${state.contentType === "music" ? "альбомов" : "фильмов"} не найдено</td></tr>`;
  $("#backlog-recommend-message").textContent = `${state.recommendationCancelled ? "Остановлено. " : ""}Уточнено ${state.contentType === "music" ? "альбомов" : "фильмов"}: ${state.llmItems.length}. Не прошли фильтрацию: ${state.llmFilteredItems.length}.`;
  $("#backlog-recommend-message").classList.remove("error");
  renderFilteredRecommendations();
  updateRecommendationSortIndicators();
  showProviderWarnings([...state.llmItems, ...state.llmFilteredItems.map(record => record.item)]);
}

function renderApiRecommendations() {
  $("#open-llm-raw-response").classList.add("hidden");
  $("#backlog-recommend-body").innerHTML = state.apiItems.length
    ? sortedRecommendationEntries(state.apiItems).map(({item,index}) => (state.contentType === "music" ? recommendationAlbumRow : recommendationMovieRow)(item,index)).join("")
    : `<tr><td colspan="10" class="empty">Новых ${state.contentType === "music" ? "альбомов" : "фильмов"} по выбранным условиям нет</td></tr>`;
  const period = state.apiPeriod ? `За период ${state.apiPeriod}` : "За весь доступный период";
  $("#backlog-recommend-message").textContent = `${state.recommendationCancelled ? "Остановлено. " : ""}${period} найдено ${state.apiItems.length} ${state.contentType === "music" ? "альбомов" : "фильмов"}, не прошли фильтрацию ${state.apiFilteredItems.length}.`;
  $("#backlog-recommend-message").classList.remove("error");
  renderFilteredRecommendations();
  updateRecommendationSortIndicators();
  showProviderWarnings([...state.apiItems, ...state.apiFilteredItems.map(record => record.item)]);
}

function favoriteButton(item) {
  const active = Boolean(item.favorite);
  return `<button class="favorite-button ${active ? "active" : ""}" data-favorite-toggle data-id="${escapeHtml(item.id)}" aria-pressed="${active}" title="${active ? "Убрать из избранного" : "Добавить в избранное"}" aria-label="${active ? "Убрать из избранного" : "Добавить в избранное"}">${active ? "★" : "☆"}</button>`;
}

function ratedCard(item) {
  const reaction = item.reaction || "";
  const subtitle = state.contentType === "music" ? `${text(item.artists)} · ${text(item.year)}` : `${text(item.directors)} · ${text(item.year)}`;
  return `<article class="rated-card" data-card-id="${escapeHtml(item.id)}" tabindex="0">
    <div class="rated-card-main"><div><h3>${escapeHtml(title(item))}</h3><span class="rated-original">${escapeHtml(item.title_original)}</span><p>${escapeHtml(subtitle)}</p></div>${state.contentType === "music" ? albumCoverHtml(item,"rated-card-cover") : moviePosterHtml(item,"rated-card-poster")}</div>
    <div class="rated-actions">
      <button type="button" class="mini-button like reaction-button outline-action-button${reaction === "like" ? " active" : ""}" data-reaction-toggle="like" data-id="${escapeHtml(item.id)}" title="${reaction === "like" ? "Убрать отметку «Понравилось»" : "Понравилось"}" aria-label="${reaction === "like" ? "Убрать отметку «Понравилось»" : "Понравилось"}" aria-pressed="${reaction === "like"}">${actionIcon("like")}</button>
      <button type="button" class="mini-button dislike reaction-button outline-action-button${reaction === "dislike" ? " active" : ""}" data-reaction-toggle="dislike" data-id="${escapeHtml(item.id)}" title="${reaction === "dislike" ? "Убрать отметку «Не понравилось»" : "Не понравилось"}" aria-label="${reaction === "dislike" ? "Убрать отметку «Не понравилось»" : "Не понравилось"}" aria-pressed="${reaction === "dislike"}">${actionIcon("dislike")}</button>
      <button class="mini-button card-action-button" data-item-action="backlog" data-id="${escapeHtml(item.id)}">В бэклог</button>
      ${favoriteButton(item)}
    </div>
  </article>`;
}

function favoriteCard(item) {
  const artwork = state.contentType === "music" ? albumCoverHtml(item,"rated-card-cover") : moviePosterHtml(item,"rated-card-poster");
  const subtitle = state.contentType === "music" ? `${text(item.artists)} · ${text(item.year)}` : `${text(item.directors)} · ${text(item.year)}`;
  return `<article class="rated-card favorite-card" data-card-id="${escapeHtml(item.id)}" tabindex="0">
    <div class="favorite-card-head"><div><h3>${escapeHtml(title(item))}</h3><span class="rated-original">${escapeHtml(item.title_original)}</span></div>${artwork}${favoriteButton(item)}</div>
    <p>${escapeHtml(subtitle)}</p>
  </article>`;
}

function renderLibrary() {
  const backlog = sortedBacklog(filtered(filterBacklogByMood(state.items.filter(item => item.status === "backlog"))));
  if (state.contentType === "music") {
    $("#backlog-head").innerHTML = `<tr><th>Обложка</th><th><button class="sort-button" data-sort="title_ru">Название</button></th><th><button class="sort-button" data-sort="artists">Исполнитель</button></th><th><button class="sort-button" data-sort="release_date">Первый выпуск</button></th><th><button class="sort-button" data-sort="track_count">Песен</button></th><th><button class="sort-button" data-sort="total_listen_count">Прослушивания</button></th><th>Link</th><th class="added-at-head"><button class="sort-button" data-sort="added_at">Добавлено</button></th><th class="planned-soon-head"><button class="sort-button planned-soon-sort" data-sort="planned_soon" title="Сначала запланированные" aria-label="Сортировать: сначала запланированные">${actionIcon("calendarCheck")}</button></th><th></th></tr>`;
    $("#backlog-body").innerHTML = backlog.map(albumRow).join("");
  } else {
    $("#backlog-head").innerHTML = `<tr><th>Постер</th><th class="backlog-title-head"><button class="sort-button" data-sort="title_ru">Название</button></th><th class="backlog-release-head"><button class="sort-button" data-sort="release_date">Дата выхода</button></th><th class="backlog-director-head"><button class="sort-button" data-sort="directors">Режиссёр</button></th><th><button class="sort-button" data-sort="imdb_rating">IMDb / КП</button></th><th class="key-people-head"><button class="sort-button" data-sort="key_people">Ключевые персоны</button></th><th class="countries-head"><button class="sort-button" data-sort="countries">Страны</button></th><th class="added-at-head"><button class="sort-button" data-sort="added_at">Добавлено</button></th><th class="planned-soon-head"><button class="sort-button planned-soon-sort" data-sort="planned_soon" title="Сначала запланированные" aria-label="Сортировать: сначала запланированные">${actionIcon("calendarCheck")}</button></th><th></th></tr>`;
    $("#backlog-body").innerHTML = backlog.map(movieRow).join("");
  }
  $("#backlog-count").textContent = backlog.length;
  $("#backlog-empty").textContent = state.selectedBacklogMood
    ? (state.contentType === "music" ? "Для выбранного настроения альбомов не найдено." : "Для выбранного настроения фильмов не найдено.")
    : (state.contentType === "music" ? "В музыкальном бэклоге ничего нет." : "В бэклоге ничего нет.");
  $("#backlog-empty").classList.toggle("hidden", backlog.length > 0);
  $("#refresh-backlog-moods").disabled = !state.items.some(item => item.status === "backlog");
  updateSortIndicators();
  const consumed = filtered(state.items.filter(item => item.status === "consumed"));
  const groups = { unrated: consumed.filter(x => !x.reaction), liked: consumed.filter(x => x.reaction === "like"), disliked: consumed.filter(x => x.reaction === "dislike") };
  for (const [key, items] of Object.entries(groups)) {
    $(`#${key}-list`).innerHTML = items.length ? items.map(ratedCard).join("") : `<div class="empty">Пусто</div>`;
    $(`#${key}-count`).textContent = items.length;
  }
  const favorites = state.items.filter(item => item.favorite);
  $("#favorites-list").innerHTML = favorites.map(favoriteCard).join("");
  $("#favorites-count").textContent = favorites.length;
  $("#favorites-empty").textContent = "В избранном пока ничего нет.";
  $("#favorites-empty").classList.toggle("hidden", favorites.length > 0);
}

function showDetails(item) {
  if (!item) return;
  state.currentDetailId = state.items.some(existing => existing.id === item.id) ? item.id : "";
  $("#refresh-detail").classList.toggle("hidden", !state.currentDetailId);
  $("#details-title").textContent = `${title(item)}${item.year ? ` (${item.year})` : ""}`;
  if ((item.content_type || state.contentType) === "music") {
    $("#details-eyebrow").textContent = "Карточка альбома";
    $("#refresh-detail").textContent = "↻ Актуализировать из MusicBrainz";
    const fields = [["Исполнитель",item.artists],["Оригинальное название",item.title_original],["Первый выпуск",item.first_release_date || item.year],["Количество песен",item.track_count],["Тип",albumTypes(item)],["Жанры",item.genres],["Теги",item.tags],["Прослушивания ListenBrainz",listenCount(item)],["Страна издания",item.country],["Лейбл",item.label],["Каталожный номер",item.catalog_number],["Штрихкод",item.barcode],["Формат",item.media_formats],["Статус издания",item.release_status],["Уточнение",item.disambiguation],["Трек-лист",formatTrackList(item.track_list),true],["Аннотация",item.annotation,true],["Заметка",item.notes,true],["Raw · исходный ввод",formatRaw(item.raw_json),true]];
    const cards = fields.map(([label,value,wide]) => detailFieldHtml(label,value,wide,label === "Заметка" ? item.id : "")).join("");
    const cover = `<div class="detail wide"><span>Обложка</span><div class="album-detail-artwork">${albumCoverHtml(item,"detail-cover")}${youtubeMusicAlbumLinkHtml(item)}</div></div>`;
    $("#details-content").innerHTML = cover + cards;
    if (!$("#details-dialog").open) $("#details-dialog").showModal();
    return;
  }
  $("#details-eyebrow").textContent = "Карточка фильма";
  $("#refresh-detail").textContent = "↻ Актуализировать из TMDB";
  const fields = [
    ["Оригинальное название", item.title_original], ["Дата выхода", item.release_date || item.year], ["Режиссёр", item.directors], ["Жанры", item.genres],
    ["Длительность", item.duration_minutes ? `${item.duration_minutes} мин.` : ""], ["IMDb rating", item.imdb_rating], ["Кинопоиск rating", item.kinopoisk_rating], ["Голоса IMDb", item.imdb_votes], ["Metascore", item.metascore],
    ["TMDB rating", item.tmdb_rating ? `${item.tmdb_rating} (${text(item.tmdb_vote_count)} голосов)` : ""], ["TMDB ID", item.tmdb_id], ["Кинопоиск ID", item.kinopoisk_id], ["Статус", item.movie_status], ["Страны", item.countries],
    ["Сценаристы", item.writers], ["Актёры", item.cast, true], ["Ключевые персоны", formatKeyPeople(item), true],
    ["Награды", awardsSummary(item.awards_json), true], ["Ключевые слова", item.keywords, true], ["Описание", item.overview, true], ["Заметка", item.notes, true], ["Raw · исходный ввод", formatRaw(item.raw_json), true]
  ];
  const cards = fields.map(([label,value,wide]) => detailFieldHtml(label,value,wide,label === "Заметка" ? item.id : "")).join("");
  const links = `<div class="detail wide"><span>Ссылки</span><p class="detail-links">${movieLinksHtml(item, true)}${validUrl(item.homepage) ? ` <a href="${escapeHtml(item.homepage)}" target="_blank" rel="noreferrer">Сайт ↗</a>` : ""}</p></div>`;
  const poster = item.poster_local_path || item.poster_path || validUrl(item.poster_url) ? `<div class="detail wide"><span>Постер</span>${moviePosterHtml(item,"detail-poster")}</div>` : "";
  $("#details-content").innerHTML = poster + cards + links;
  if (!$("#details-dialog").open) $("#details-dialog").showModal();
}

function detailFieldHtml(label, value, wide = false, editableItemId = "") {
  const editable = editableItemId && state.currentDetailId === editableItemId;
  return `<div class="detail ${wide ? "wide" : ""}${editable ? " editable-note" : ""}"${editable ? ` data-editable-note="${escapeHtml(editableItemId)}" title="Дважды нажмите, чтобы изменить заметку"` : ""}><span>${label}</span><p>${escapeHtml(text(value))}</p></div>`;
}

function editNote(field) {
  const itemId = field.dataset.editableNote;
  const item = state.items.find(existing => existing.id === itemId);
  if (!item || field.querySelector("textarea")) return;
  const paragraph = field.querySelector("p"), editor = document.createElement("textarea");
  editor.className = "inline-note-editor";
  editor.value = item.notes || "";
  editor.setAttribute("aria-label", "Заметка");
  paragraph.replaceWith(editor);
  editor.focus();
  editor.setSelectionRange(editor.value.length,editor.value.length);
  let finished = false;
  const cancel = () => {
    if (finished) return;
    finished = true;
    const restored = document.createElement("p");
    restored.textContent = text(item.notes);
    editor.replaceWith(restored);
  };
  const save = async () => {
    if (finished) return;
    finished = true;
    const notes = editor.value;
    editor.disabled = true;
    try {
      const {item:updated} = await request(`/api/library/${encodeURIComponent(itemId)}`, {method:"PATCH",body:JSON.stringify({notes})});
      state.items = state.items.map(existing => existing.id === updated.id ? updated : existing);
      const saved = document.createElement("p");
      saved.textContent = text(updated.notes);
      editor.replaceWith(saved);
      renderLibrary();
      toast("Заметка сохранена");
    } catch (error) {
      finished = false;
      editor.disabled = false;
      editor.focus();
      toast(error.message);
    }
  };
  editor.addEventListener("blur",save);
  editor.addEventListener("keydown",event=>{
    if(event.key === "Escape"){event.preventDefault();cancel();return;}
    if(event.key === "Enter" && (event.metaKey || event.ctrlKey)){event.preventDefault();editor.blur();}
  });
}

const actionDelay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

function actionFeedbackContainer(button) {
  return button.closest("tr, .rated-card");
}

function clearActionFeedback(button, container) {
  delete button.dataset.actionBusy;
  button.removeAttribute("aria-disabled");
  button.classList.remove("is-confirming");
  container?.classList.remove("action-pending", "action-exiting");
}

function lockActionTable(button) {
  const table = button.closest(".table-wrap");
  if (!table || table.dataset.actionBusy) return null;
  table.dataset.actionBusy = "true";
  table.setAttribute("aria-busy", "true");
  table.inert = true;
  table.classList.add("table-action-busy");
  return () => {
    delete table.dataset.actionBusy;
    table.removeAttribute("aria-busy");
    table.inert = false;
    table.classList.remove("table-action-busy");
  };
}

async function cardActionWithFeedback(button, task, {exit = false} = {}) {
  if (button.dataset.actionBusy) return null;
  const container = actionFeedbackContainer(button);
  button.dataset.actionBusy = "true";
  button.setAttribute("aria-disabled", "true");
  button.classList.add("is-confirming");
  container?.classList.add("action-pending");
  const outcomePromise = Promise.resolve().then(task).then(value => ({value}), error => ({error}));
  await actionDelay(123);
  const outcome = await outcomePromise;
  if (outcome.error) {
    clearActionFeedback(button, container);
    throw outcome.error;
  }
  button.classList.remove("is-confirming");
  if (exit && container) {
    container.classList.add("action-exiting");
    await actionDelay(118);
  }
  return {value:outcome.value,container};
}

async function patchItemWithFeedback(button, id, changes, {exit = false} = {}) {
  try {
    const outcome = await cardActionWithFeedback(button, () => request(
      `/api/library/${encodeURIComponent(id)}`,
      {method:"PATCH", body:JSON.stringify(changes)},
    ), {exit});
    if (!outcome) return;
    const updated = outcome.value.item;
    state.items = state.items.map(existing => existing.id === updated.id ? updated : existing);
    renderLibrary();
    toast(state.contentType === "music" ? "Музыкальная библиотека обновлена" : "Фильмотека обновлена");
  } catch (error) { toast(error.message); }
}

async function toggleFavorite(button) {
  const item = state.items.find(movie => movie.id === button.dataset.id);
  if (!item) return;
  try {
    const outcome = await cardActionWithFeedback(button, () => request(
      `/api/library/${encodeURIComponent(item.id)}/favorite`,
      {method:"POST", body:JSON.stringify({favorite:!item.favorite})},
    ), {exit:state.view === "favorites" && Boolean(item.favorite)});
    if (!outcome) return;
    const result = outcome.value;
    state.items = state.items.map(movie => movie.id === result.item.id ? result.item : movie);
    renderLibrary(); toast(result.item.favorite ? "Добавлено в избранное" : "Удалено из избранного");
  } catch (error) { toast(error.message); }
}

function sortedPeople(items, role) {
  const setting = state.peopleSort[role];
  const numeric = new Set(["tmdb_id"]);
  return [...items].sort((left,right) => {
    const a=left[setting.key] ?? "",b=right[setting.key] ?? "";
    const result=numeric.has(setting.key) ? (Number(a)||-Infinity)-(Number(b)||-Infinity) : String(a).localeCompare(String(b),"ru",{numeric:true,sensitivity:"base"});
    return setting.direction === "asc" ? result : -result;
  });
}

function updatePeopleSortIndicators() {
  document.querySelectorAll("[data-people-sort]").forEach(button => {
    const setting=state.peopleSort[button.dataset.peopleRole],active=setting?.key===button.dataset.peopleSort;
    button.classList.toggle("active",active);button.dataset.direction=active?setting.direction:"";
    button.setAttribute("aria-sort",active?(setting.direction==="asc"?"ascending":"descending"):"none");
  });
}

function renderPeople() {
  const query = state.query.trim().toLocaleLowerCase("ru");
  const people = state.interests.filter(item => !query || [item.name_ru,item.name_original,item.tmdb_id,item.mbid].join(" ").toLocaleLowerCase("ru").includes(query));
  if (state.contentType === "music") {
    const artists=sortedPeople(people,"artist");
    $("#movie-people-board").classList.add("hidden"); $("#music-people-board").classList.remove("hidden");
    $("#artists-body").innerHTML = artists.length ? artists.map(item => `<tr class="person-row" data-person-details-id="${escapeHtml(item.id)}" data-person-details-role="artist" tabindex="0"><td class="person-photo-cell">${personPortraitHtml(item)}</td><td><strong>${escapeHtml(text(item.name_original))}</strong></td><td>${escapeHtml(text(item.artist_type))}</td><td>${escapeHtml(text(item.country || item.area))}</td><td><button type="button" class="icon-button trash-button outline-action-button" data-trash-entity="music_artist" data-id="${escapeHtml(item.id)}" title="В корзину" aria-label="Переместить исполнителя в корзину">${actionIcon("trash")}</button></td></tr>`).join("") : `<tr><td colspan="5" class="empty">Ничего не найдено</td></tr>`;
    $("#artists-count").textContent = artists.length; $("#people-count").textContent = artists.length; updatePeopleSortIndicators();
    return;
  }
  $("#movie-people-board").classList.remove("hidden"); $("#music-people-board").classList.add("hidden");
  for (const role of ["director", "actor"]) {
    const rows = sortedPeople(people.filter(item => item.role === role),role);
    $(`#${role}s-body`).innerHTML = rows.length ? rows.map(item => `<tr class="person-row" data-person-details-id="${escapeHtml(item.id)}" data-person-details-role="${role}" tabindex="0" aria-label="Открыть карточку: ${escapeHtml(titlePerson(item))}"><td class="person-photo-cell">${personPortraitHtml(item)}</td><td><strong>${escapeHtml(text(item.name_ru))}</strong></td><td>${escapeHtml(text(item.name_original))}</td><td><button type="button" class="icon-button trash-button outline-action-button" data-trash-entity="person" data-id="${escapeHtml(item.id)}" data-role="${role}" title="В корзину" aria-label="Переместить персону в корзину">${actionIcon("trash")}</button></td></tr>`).join("") : `<tr><td colspan="4" class="empty">Ничего не найдено</td></tr>`;
    $(`#${role}s-count`).textContent = rows.length;
  }
  $("#people-count").textContent = new Set(people.map(item => item.id)).size; updatePeopleSortIndicators();
}

function showPersonDetails(person) {
  if (!person) return;
  const stored = state.interests.some(existing => existing.id === person.id && (!person.role || existing.role === person.role));
  state.currentPersonId = stored ? person.id : "";
  state.currentPersonRole = person.role || "";
  $("#refresh-person-detail").classList.toggle("hidden", !stored);
  $("#person-details-title").textContent = titlePerson(person);
  if ((person.content_type || state.contentType) === "music") {
    $("#person-details-role").textContent = "Карточка исполнителя";
    $("#refresh-person-detail").textContent = "↻ Актуализировать из MusicBrainz";
    const fields = [["Исполнитель",person.name_original],["Тип",person.artist_type],["Страна",person.country],["Регион",person.area],["Период",[person.life_span_begin,person.life_span_end].filter(Boolean).join(" — ")],["Уточнение",person.disambiguation],["MusicBrainz ID",person.mbid],["Raw · исходный ввод",formatRaw(person.raw_json)]];
    $("#person-details-content").innerHTML = `<div class="detail wide"><span>Фото</span>${personPortraitHtml(person,"detail-person-photo")}</div>` + fields.map(([label,value]) => `<div class="detail"><span>${label}</span><p>${escapeHtml(text(value))}</p></div>`).join("");
    if (!$("#person-details-dialog").open) $("#person-details-dialog").showModal();
    return;
  }
  $("#refresh-person-detail").textContent = "↻ Актуализировать из TMDB";
  $("#person-details-role").textContent = person.role === "director" ? "Карточка режиссёра" : "Карточка актёра";
  const metadata = personMetadata(person);
  const fields = [
    ["Имя на русском", person.name_ru],
    ["Оригинальное имя", person.name_original],
    ["Основная специализация", metadata.known_for_department],
    ["Дата рождения", metadata.birthday],
    ["Дата смерти", metadata.deathday],
    ["Место рождения", metadata.place_of_birth],
    ["Другие имена", Array.isArray(metadata.also_known_as) ? metadata.also_known_as.join("; ") : "", true],
    ["Биография", metadata.biography, true],
    ["TMDB ID", person.tmdb_id],
    ["Raw · исходный ввод", formatRaw(person.raw_json), true],
  ];
  const link = personExternalLinkHtml(person);
  const links = link ? `<div class="detail wide"><span>Ссылки</span><p class="detail-links">${link}</p></div>` : "";
  $("#person-details-content").innerHTML = `<div class="detail wide"><span>Фото</span>${personPortraitHtml(person,"detail-person-photo")}</div>` + fields.map(([label, value, wide]) => `<div class="detail ${wide ? "wide" : ""}"><span>${label}</span><p>${escapeHtml(text(value))}</p></div>`).join("") + links;
  if (!$("#person-details-dialog").open) $("#person-details-dialog").showModal();
}

function titlePerson(person) { return person.name_ru || person.name_original || "Персона"; }

function personMetadata(person) {
  try {
    const value = typeof person?.details_json === "string" ? JSON.parse(person.details_json || "{}") : (person?.details_json || {});
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}

function findInterest(personId, role = "") {
  return state.interests.find(item => String(item.id) === String(personId) && (!role || item.role === role))
    || state.interests.find(item => String(item.id) === String(personId));
}

function renderTrash() {
  const music = state.contentType === "music";
  const movies = state.trash.filter(item => item.entity_type === (music ? "album" : "movie"));
  const people = state.trash.filter(item => item.entity_type === (music ? "music_artist" : "person"));
  const restore = item => `<button class="mini-button card-action-button" data-restore-id="${escapeHtml(item.id)}">Восстановить</button>`;
  $("#trash-content-title").textContent = music ? "Альбомы" : "Фильмы";
  $("#trash-people-title").textContent = music ? "Исполнители" : "Персоны";
  $("#trash-content-head").innerHTML = music
    ? "<tr><th>Альбом</th><th>Исполнитель</th><th>Год</th><th></th></tr>"
    : "<tr><th>Название на русском</th><th>Оригинальное название</th><th>Год</th><th></th></tr>";
  $("#trash-people-head").innerHTML = music
    ? "<tr><th>Исполнитель</th><th>Регион</th><th>Тип</th><th></th></tr>"
    : "<tr><th>Имя на русском</th><th>Оригинальное имя</th><th>Тип</th><th></th></tr>";
  $("#trash-movies-body").innerHTML = movies.length ? movies.map(item => music
    ? `<tr><td><strong>${escapeHtml(text(item.title_original || item.title_ru))}</strong></td><td>${escapeHtml(text(item.artists))}</td><td>${escapeHtml(text(item.year))}</td><td>${restore(item)}</td></tr>`
    : `<tr><td><strong>${escapeHtml(text(item.title_ru))}</strong></td><td>${escapeHtml(text(item.title_original))}</td><td>${escapeHtml(text(item.year))}</td><td>${restore(item)}</td></tr>`).join("") : `<tr><td colspan="4" class="empty">${music ? "Корзина альбомов пуста" : "Корзина фильмов пуста"}</td></tr>`;
  $("#trash-people-body").innerHTML = people.length ? people.map(item => music
    ? `<tr><td><strong>${escapeHtml(text(item.name_original))}</strong></td><td>${escapeHtml(text(item.country || item.area))}</td><td>Исполнитель</td><td>${restore(item)}</td></tr>`
    : `<tr><td><strong>${escapeHtml(text(item.name_ru))}</strong></td><td>${escapeHtml(text(item.name_original))}</td><td>${item.role === "director" ? "Режиссёр" : "Актёр"}</td><td>${restore(item)}</td></tr>`).join("") : `<tr><td colspan="4" class="empty">${music ? "Корзина исполнителей пуста" : "Корзина персон пуста"}</td></tr>`;
  $("#trash-movies-count").textContent = movies.length; $("#trash-people-count").textContent = people.length; $("#trash-count").textContent = movies.length + people.length;
  $("#clear-trash").disabled = state.trash.length === 0;
}

function syncSearchControls() {
  const music = state.contentType === "music";
  document.querySelectorAll("[data-library-search]").forEach(input => {
    const people = input.dataset.searchContext === "people";
    input.placeholder = people ? (music ? "Найти исполнителя" : "Найти персону") : (music ? "Найти альбом" : "Найти фильм");
    input.value = state.query;
  });
}

function switchView(view) {
  state.view = view; document.querySelectorAll("[data-view]").forEach(button => button.classList.toggle("active", button.dataset.view === view));
  $("#library-view").classList.toggle("hidden", !["backlog", "consumed"].includes(view));
  $("#favorites-view").classList.toggle("hidden", view !== "favorites");
  $("#people-view").classList.toggle("hidden", view !== "people"); $("#backlog-panel").classList.toggle("hidden", view !== "backlog"); $("#consumed-panel").classList.toggle("hidden", view !== "consumed");
  $("#trash-view").classList.toggle("hidden", view !== "trash");
  syncSearchControls(); renderLibrary(); renderPeople(); renderTrash();
}

function renderInterestOptions() {
  if (state.contentType === "music") {
    $("#artist-options").innerHTML = state.interests.map(item => `<label><input type="checkbox" name="artist_ids" value="${escapeHtml(item.id)}" checked> ${escapeHtml(item.name_original)}</label>`).join("");
    const all = document.querySelector('[data-select-all="artist"]'); if (all) all.checked = state.interests.length > 0;
    filterPersonOptions("artist"); updateSelectedLabels(); return;
  }
  for (const role of ["actor","director"]) {
    const items = state.interests.filter(item => item.role === role);
    $(`#${role}-options`).innerHTML = items.map(item => `<label><input type="checkbox" name="${role}_ids" value="${escapeHtml(item.id)}"> ${escapeHtml(item.name_original)}</label>`).join("");
    document.querySelector(`[data-select-all="${role}"]`).checked = false;
    filterPersonOptions(role);
  }
  updateSelectedLabels();
}

function filterPersonOptions(role) {
  const input = document.querySelector(`[data-person-search="${role}"]`), query = String(input?.value || "").trim().toLocaleLowerCase("ru");
  document.querySelectorAll(`#${role}-options > label`).forEach(label => {
    const id = label.querySelector("input")?.value, person = findInterest(id, role);
    const haystack = [person?.name_ru,person?.name_original,person?.name].filter(Boolean).join(" ").toLocaleLowerCase("ru");
    label.classList.toggle("hidden", Boolean(query) && !haystack.includes(query));
  });
}

function syncPeopleMenuLayout() {
  const dialog = $("#backlog-recommend-dialog"), open = Boolean(dialog.querySelector(".people-filter details[open]"));
  dialog.classList.toggle("people-menu-open",open);
}

function updateSelectedLabels() {
  for (const role of ["actor","director","artist"]) {
    const boxes = [...document.querySelectorAll(`input[name="${role}_ids"]`)], selected = boxes.filter(box => box.checked).length;
    const label = $(`#${role}s-selected`); if (label) label.textContent = `· ${selected}/${boxes.length}`; const all = document.querySelector(`[data-select-all="${role}"]`);
    if (!all) continue;
    all.checked = selected === boxes.length && boxes.length > 0; all.indeterminate = selected > 0 && selected < boxes.length;
  }
}

function applyContentLabels() {
  const music = state.contentType === "music";
  document.querySelectorAll("[data-content-type]").forEach(button=>{const active=button.dataset.contentType===state.contentType;button.classList.toggle("active",active);button.setAttribute("aria-pressed",String(active));});
  $("#backlog-empty").textContent = music ? "В музыкальном бэклоге ничего нет." : "В бэклоге ничего нет.";
  $("#open-backlog-recommend").textContent = "✦ Подобрать похожее";
  $("#open-backlog-api-recommend").textContent = "↻ Новое от авторов";
  $("#refresh-tmdb").textContent = "⟳ Обновить";
  $("#refresh-people").textContent = "⟳ Обновить";
  $("#open-people-recommend").textContent = "✦ Рекомендовать похожее";
  $("#open-add").textContent = "＋ Добавить вручную";
  $("#open-add-person").textContent = "＋ Добавить вручную";
  $("#backlog-moods-title").textContent = music ? "Настроение альбомов в бэклоге" : "Настроение фильмов в бэклоге";
  syncSearchControls();
}

async function changeContentType(contentType) {
  if (contentTypeSwitching) return;
  contentTypeSwitching = true;
  const buttons = [...document.querySelectorAll("[data-content-type]")];
  buttons.forEach(button => { button.disabled = true; });
  try {
    const [library, people, moods] = await Promise.all([
      request(`/api/library?content_type=${encodeURIComponent(contentType)}`),
      request(`/api/people?content_type=${encodeURIComponent(contentType)}`),
      request(`/api/backlog/moods?content_type=${encodeURIComponent(contentType)}`),
    ]);
    state.contentType = contentType;
    state.query = "";
    state.sortKey = "release_date";
    state.sortDirection = "desc";
    state.items = library.items;
    state.interests = people.items;
    state.selectedBacklogMood = ""; setBacklogMoods(moods.moods); renderBacklogMoodChips();
    applyContentLabels(); renderLibrary(); renderPeople(); renderInterestOptions(); renderTrash();
  } catch (error) {
    toast(error.message);
  } finally {
    contentTypeSwitching = false;
    buttons.forEach(button => { button.disabled = false; });
  }
}

function recommendationValues(form) {
  const data = new FormData(form);
  if (state.contentType === "music") {
    const values={artist_ids:data.getAll("artist_ids"),limit:data.get("limit")};
    for(const name of ["year_from","year_to"]) if(data.has(name)) values[name]=data.get(name);
    if(data.has("excluded_types")) values.excluded_types=String(data.get("excluded_types")||"").split(",").map(value=>value.trim()).filter(Boolean);
    if(data.has("excluded_title_terms")) values.excluded_title_terms=String(data.get("excluded_title_terms")||"").split(",").map(value=>value.trim()).filter(Boolean);
    return values;
  }
  const values = { actor_ids:data.getAll("actor_ids"), director_ids:data.getAll("director_ids"), limit:data.get("limit") };
  for (const name of ["min_imdb_rating", "min_kinopoisk_rating", "min_votes", "min_runtime", "year_from", "year_to", "excluded_genres"]) {
    if (data.has(name)) values[name] = data.get(name);
  }
  return values;
}

function apiRecommendationPeriod(values) {
  const from = String(values.year_from || "").trim(), to = String(values.year_to || "").trim();
  if (from && to) return `${from}–${to}`;
  if (from) return `с ${from} года`;
  if (to) return `до ${to} года`;
  return "";
}

function setLlmContextLimits() {
  const form = $(state.contentType === "music" ? "#album-recommend-form" : "#backlog-recommend-form");
  const likedCount = state.items.filter(item => item.status === "consumed" && item.reaction === "like").length;
  const likedInput = form.elements.namedItem("liked_sample_size");
  const peopleInput = form.elements.namedItem("people_sample_size");
  likedInput.value = String(likedCount);
  peopleInput.value = String(state.interests.length);
}

async function addModalRecommendation(index, action, button) {
  const items = state.backlogRecommendMode === "llm" ? state.llmItems : state.apiItems;
  const source = items[index];
  if (!source) return;
  const unlockTable = lockActionTable(button);
  if (!unlockTable) return;
  try {
    if (action === "remove") {
      const outcome = await cardActionWithFeedback(button, () => true, {exit:true});
      if (!outcome) return;
      items.splice(index, 1);
      if (state.backlogRecommendMode === "llm") renderLlmRecommendations(); else renderApiRecommendations();
      return;
    }
    const payload = {...source};
    if (action === "backlog") { payload.status = "backlog"; payload.reaction = ""; }
    else { payload.status = "consumed"; payload.reaction = action; }
    const outcome = await cardActionWithFeedback(button, () => request(
      "/api/library", {method:"POST", body:JSON.stringify(payload)},
    ), {exit:true});
    if (!outcome) return;
    const {item} = outcome.value;
    state.items.push(item); items.splice(index, 1);
    if (state.backlogRecommendMode === "llm") renderLlmRecommendations(); else renderApiRecommendations();
    renderLibrary();
    toast(action === "backlog" ? "Добавлено в бэклог" : "Добавлено в просмотренное");
  } catch (error) { toast(error.message); }
  finally { unlockTable(); }
}

async function addFilteredRecommendation(index, button) {
  const records = currentFilteredRecommendations();
  const source = records[index]?.item;
  if (!source) return;
  const unlockTable = lockActionTable(button);
  if (!unlockTable) return;
  try {
    const outcome = await cardActionWithFeedback(button, () => request(
      "/api/library",
      {method:"POST", body:JSON.stringify({...source,status:"backlog",reaction:""})},
    ), {exit:true});
    if (!outcome) return;
    const {item} = outcome.value;
    state.items.push(item);
    records.splice(index,1);
    if (state.backlogRecommendMode === "llm") renderLlmRecommendations(); else renderApiRecommendations();
    renderLibrary();
    toast("Добавлено в бэклог");
  } catch (error) { toast(error.message); }
  finally { unlockTable(); }
}

async function refreshTmdb() {
  if(operationRunning()){toast("Дождитесь завершения текущей операции или остановите её");return;}
  const button = $("#refresh-tmdb"), recommendButtons = [$("#open-backlog-recommend"),$("#open-backlog-api-recommend")], notice = $("#refresh-progress"), music = state.contentType === "music";
  button.closest("details")?.removeAttribute("open");
  const progressId = globalThis.crypto?.randomUUID?.() || `refresh-${Date.now()}-${Math.random()}`;
  button.disabled = true; recommendButtons.forEach(item=>{item.disabled=true;});
  notice.classList.remove("hidden","error","warning");
  notice.textContent = music ? "Полностью актуализирую неполные карточки через MusicBrainz и ListenBrainz…" : "Полностью актуализирую неполные карточки через нужные источники…";
  startRecommendationProgress(
    progressId, 1, music ? "MusicBrainz · подготовка карточек" : "TMDB · подготовка карточек", "шагов",
    {container:"#refresh-operation-progress",stages:"#refresh-progress-stages",stop:"#refresh-stop",kind:"refresh",warnings:false},
  );
  try {
    const result = await request(music ? "/api/library/refresh-musicbrainz" : "/api/library/refresh-tmdb", {
      method:"POST",body:JSON.stringify({progress_id:progressId}),
    });
    state.items = (await request(`/api/library?content_type=${state.contentType}`)).items;
    renderLibrary();
    const warnings = providerWarnings([{provider_warnings:result.provider_warnings || []}]);
    notice.textContent = `${result.cancelled ? "Остановлено. " : ""}Обновлено ${result.updated} из ${result.total}. Ошибок: ${result.failed}.${warnings.length ? ` ${warnings.join(" ")}` : ""}`;
    if (result.failed) notice.classList.add("error"); else if (warnings.length) notice.classList.add("warning");
    if (warnings.length) toast(warnings[0]);
  } catch (error) {
    notice.textContent = error.message; notice.classList.add("error");
  } finally {
    await finishRecommendationProgress(progressId);
    button.disabled = false; recommendButtons.forEach(item=>{item.disabled=false;});
  }
}

async function refreshPeople() {
  const button = $("#refresh-people"), notice = $("#people-refresh-progress"), music = state.contentType === "music"; button.closest("details")?.removeAttribute("open");button.disabled = true; notice.classList.remove("hidden","error"); notice.textContent = music ? "Ищу MusicBrainz ID для исполнителей без идентификатора…" : "Ищу TMDB ID для персон без идентификатора…";
  try { const result = await request(music ? "/api/people/refresh-musicbrainz" : "/api/people/refresh-tmdb", {method:"POST",body:"{}"}); state.interests = (await request(`/api/people?content_type=${state.contentType}`)).items; renderPeople(); renderInterestOptions(); notice.textContent = `Обновлено ${result.updated} из ${result.total}. Ошибок: ${result.failed}.`; if (result.failed) notice.classList.add("error"); }
  catch (error) { notice.textContent = error.message; notice.classList.add("error"); } finally { button.disabled = false; }
}

async function refreshCurrentMovie() {
  if (!state.currentDetailId) return;
  const button = $("#refresh-detail"); button.disabled = true; button.textContent = "Обновляю…";
  try {
    const music = state.contentType === "music";
    const {item} = await request(`/api/library/${encodeURIComponent(state.currentDetailId)}/${music ? "refresh-musicbrainz" : "refresh-tmdb"}`, {method:"POST",body:"{}"});
    state.items = state.items.map(existing => existing.id === item.id ? item : existing); renderLibrary(); showDetails(item);
    const warnings = providerWarnings([item]); toast(warnings[0] || (item.refresh_skipped ? "Все необходимые данные уже заполнены" : `Карточка обновлена из ${music ? "MusicBrainz / Cover Art Archive / ListenBrainz" : "TMDB / OMDb / Кинопоиск"}`));
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.textContent = state.contentType === "music" ? "↻ Актуализировать из MusicBrainz" : "↻ Актуализировать из TMDB"; }
}

async function refreshCurrentPerson() {
  if (!state.currentPersonId) return;
  const button = $("#refresh-person-detail"); button.disabled = true; button.textContent = "Обновляю…";
  try {
    const music = state.contentType === "music", role = state.currentPersonRole;
    const {item} = await request(`/api/people/${encodeURIComponent(state.currentPersonId)}/${music ? "refresh-musicbrainz" : "refresh-tmdb"}`, {method:"POST",body:JSON.stringify(music ? {} : {role})});
    state.interests = (await request(`/api/people?content_type=${state.contentType}`)).items;
    const refreshed = findInterest(item.id, role) || item;
    renderPeople(); renderInterestOptions(); showPersonDetails(refreshed);
    const warnings = providerWarnings([refreshed]);
    toast(warnings[0] || (music ? "Исполнитель обновлён из MusicBrainz / fanart.tv" : "Персона обновлена из TMDB / TMDB Images"));
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.textContent = `↻ Актуализировать из ${state.contentType === "music" ? "MusicBrainz" : "TMDB"}`; }
}

function formPayload(form) { return Object.fromEntries(new FormData(form).entries()); }
function llmRecommendationPayload(form) {
  return {
    ...formPayload(form),
    disabled_filters: [...form.querySelectorAll("[data-filter-field] input:disabled")].map(input => input.name),
    context_seed: state.llmContextSeed,
  };
}
function fillForm(form, values, fields) {
  for (const field of fields) {
    const control = form.elements.namedItem(field);
    if (control && values[field] !== undefined && values[field] !== null) control.value = values[field];
  }
}
function resolveStatus(selector, message = "", isError = false, isWarning = false) {
  const node = $(selector); node.textContent = message; node.classList.toggle("hidden", !message); node.classList.toggle("error", isError); node.classList.toggle("warning", isWarning);
}
function clearMovieSearchResults() {
  state.movieSearchItems = [];
  $("#add-movie-search-list").innerHTML = "";
  $("#add-movie-search-count").textContent = "";
  $("#add-movie-search-results").classList.add("hidden");
}
function clearPersonSearchResults() {
  state.personSearchItems = [];
  $("#add-person-search-list").innerHTML = "";
  $("#add-person-search-count").textContent = "";
  $("#add-person-search-results").classList.add("hidden");
}
function renderMovieSearchResults() {
  const items = state.movieSearchItems, section = $("#add-movie-search-results");
  $("#add-movie-search-count").textContent = `${items.length} из TMDB`;
  $("#add-movie-search-list").innerHTML = items.length ? items.map((item,index) => {
    const primary = title(item), original = item.title_original && item.title_original !== primary ? item.title_original : "";
    const rating = item.tmdb_rating ? `TMDB ${item.tmdb_rating}${item.tmdb_vote_count ? ` · ${item.tmdb_vote_count} голосов` : ""}` : "Рейтинг TMDB —";
    const meta = [item.year || String(item.release_date || "").slice(0,4), String(item.original_language || "").toUpperCase(), rating].filter(Boolean).join(" · ");
    return `<article class="tmdb-search-item${item.already_added ? " is-added" : ""}">${moviePosterHtml(item)}<div class="tmdb-search-item-main"><strong>${escapeHtml(primary)}</strong>${original ? `<span class="tmdb-search-item-original">${escapeHtml(original)}</span>` : ""}<span class="tmdb-search-item-meta">${escapeHtml(meta)}</span>${item.overview ? `<span class="tmdb-search-item-description">${escapeHtml(item.overview)}</span>` : ""}</div><div class="tmdb-search-item-actions"><button type="button" class="mini-button secondary" data-movie-search-details="${index}">Подробнее</button><button type="button" class="mini-button done card-action-button" data-movie-search-select="${index}"${item.already_added ? " disabled" : ""}>${item.already_added ? "Уже в базе" : "Выбрать"}</button></div></article>`;
  }).join("") : `<div class="tmdb-search-empty">TMDB не нашёл подходящих фильмов. Уточните название или год.</div>`;
  section.classList.remove("hidden");
}
function renderPersonSearchResults() {
  const items = state.personSearchItems, section = $("#add-person-search-results");
  $("#add-person-search-count").textContent = `${items.length} из TMDB`;
  $("#add-person-search-list").innerHTML = items.length ? items.map((item,index) => {
    const primary = titlePerson(item), original = item.name_original && item.name_original !== primary ? item.name_original : "";
    const department = {Acting:"Актёрское мастерство",Directing:"Режиссура"}[item.known_for_department] || item.known_for_department || "Специализация не указана";
    return `<article class="tmdb-search-item${item.already_added ? " is-added" : ""}">${personPhotoHtml(item)}<div class="tmdb-search-item-main"><strong>${escapeHtml(primary)}</strong>${original ? `<span class="tmdb-search-item-original">${escapeHtml(original)}</span>` : ""}<span class="tmdb-search-item-meta">${escapeHtml(department)}</span>${item.known_for ? `<span class="tmdb-search-item-description">Известен по: ${escapeHtml(item.known_for)}</span>` : ""}</div><div class="tmdb-search-item-actions"><button type="button" class="mini-button secondary" data-person-search-details="${index}">Подробнее</button><button type="button" class="mini-button done card-action-button" data-person-search-select="${index}"${item.already_added ? " disabled" : ""}>${item.already_added ? "Уже в этой роли" : "Выбрать"}</button></div></article>`;
  }).join("") : `<div class="tmdb-search-empty">TMDB не нашёл подходящих персон. Уточните имя.</div>`;
  section.classList.remove("hidden");
}
async function movieSearchDetails(index) {
  const candidate = state.movieSearchItems[index];
  if (!candidate) throw new Error("Вариант фильма больше не доступен");
  if (!candidate.details) {
    const {item} = candidate.tmdb_checked ? {item:candidate} : await request("/api/resolve/movie", {method:"POST",body:JSON.stringify({tmdb_id:candidate.tmdb_id})});
    candidate.details = {...candidate,...item,poster_local_path:item.poster_local_path || candidate.poster_local_path};
  }
  return candidate.details;
}
async function personSearchDetails(index) {
  const candidate = state.personSearchItems[index];
  if (!candidate) throw new Error("Вариант персоны больше не доступен");
  if (!candidate.details) {
    const role = $("#add-person-form").elements.namedItem("role").value;
    const {item} = candidate.details_json ? {item:candidate} : await request("/api/resolve/person", {method:"POST",body:JSON.stringify({tmdb_id:candidate.tmdb_id,role})});
    candidate.details = {...candidate,...item,role,profile_local_path:item.profile_local_path || candidate.profile_local_path};
  }
  return candidate.details;
}
async function openMovieSearchDetails(index, button) {
  button.disabled = true;
  try { showDetails(await movieSearchDetails(index)); }
  catch (error) { $("#add-error").textContent = error.message; }
  finally { button.disabled = false; }
}
async function openPersonSearchDetails(index, button) {
  button.disabled = true;
  try { showPersonDetails(await personSearchDetails(index)); }
  catch (error) { $("#add-person-error").textContent = error.message; }
  finally { button.disabled = false; }
}
async function selectMovieSearchResult(index, button) {
  button.disabled = true; $("#add-error").textContent = "";
  try {
    const rawData = state.pendingMovieRaw || compactObject(formPayload($("#add-form")));
    const details = await movieSearchDetails(index);
    const payload = {...details,raw_data:rawData,content_type:"movie",status:"backlog",reaction:""};
    const {item} = await request("/api/library", {method:"POST",body:JSON.stringify(payload)});
    state.items.push(item); resetMovieDraft(); $("#add-dialog").close(); renderLibrary(); toast("Фильм добавлен в бэклог");
  } catch (error) { $("#add-error").textContent = error.message; button.disabled = false; }
}
async function selectPersonSearchResult(index, button) {
  button.disabled = true; $("#add-person-error").textContent = "";
  try {
    const rawData = state.pendingPersonRaw || compactObject(formPayload($("#add-person-form")));
    const details = await personSearchDetails(index);
    const payload = {...details,role:details.role,raw_data:rawData};
    const {item} = await request("/api/people", {method:"POST",body:JSON.stringify(payload)});
    state.interests.push(item); resetPersonDraft(); $("#add-person-dialog").close(); renderPeople(); renderInterestOptions(); toast("Персона добавлена");
  } catch (error) { $("#add-person-error").textContent = error.message; button.disabled = false; }
}
function resetMovieDraft() {
  $("#add-form").reset();
  state.pendingMovieDetails = null; state.pendingMovieRaw = null; state.pendingMovieSearchField = "";
  clearMovieSearchResults();
  $("#add-movie-links").textContent = "—"; renderMovieDraftPreview();
  resolveStatus("#add-resolve-status"); $("#add-error").textContent = "";
}
function resetPersonDraft() {
  $("#add-person-form").reset();
  state.pendingPersonDetails = null; state.pendingPersonRaw = null;
  clearPersonSearchResults();
  resolveStatus("#add-person-resolve-status"); $("#add-person-error").textContent = "";
}
async function saveMovieDraft(reaction = "") {
  const form = $("#add-form"), current = formPayload(form);
  const status = reaction ? "consumed" : "backlog";
  const payload = {
    ...(state.pendingMovieDetails || {}), ...current,
    raw_data: state.pendingMovieRaw || compactObject(current), content_type:"movie", status, reaction,
  };
  const buttons = form.querySelectorAll("[data-save-movie]");
  buttons.forEach(button => { button.disabled = true; });
  try {
    const {item} = await request("/api/library", {method:"POST", body:JSON.stringify(payload)});
    state.items.push(item); resetMovieDraft(); $("#add-dialog").close(); renderLibrary();
    toast(reaction === "like" ? "Добавлено в понравившееся" : reaction === "dislike" ? "Добавлено в не понравившееся" : "Фильм добавлен в бэклог");
  } catch (error) {
    $("#add-error").textContent = error.message;
  } finally {
    buttons.forEach(button => { button.disabled = false; });
  }
}

async function resolveMovieDraft() {
  const form = $("#add-form"), button = $("#resolve-movie"), draft = formPayload(form);
  if (state.pendingMovieSearchField) draft.search_field = state.pendingMovieSearchField;
  state.pendingMovieRaw ??= compactObject(draft); state.pendingMovieDetails = null; clearMovieSearchResults(); button.disabled = true; button.textContent = "Ищу…"; $("#add-error").textContent = "";
  try {
    const {items} = await request("/api/search/movie", {method:"POST", body:JSON.stringify(draft)});
    state.movieSearchItems = items || []; state.pendingMovieSearchField = "";
    $("#add-movie-links").textContent = "—"; renderMovieDraftPreview(); renderMovieSearchResults();
    const warnings = providerWarnings(state.movieSearchItems);
    resolveStatus("#add-resolve-status", `${state.movieSearchItems.length ? `Найдено вариантов: ${state.movieSearchItems.length}. Выберите фильм или откройте подробную карточку.` : "Подходящих вариантов не найдено."} Исходный ввод сохранится в Raw.${warnings.length ? ` ${warnings.join(" ")}` : ""}`, false, warnings.length > 0);
    if (warnings.length) toast(warnings[0]);
  } catch (error) { resolveStatus("#add-resolve-status", error.message, true); }
  finally { button.disabled = false; button.textContent = "↻ Актуализировать из TMDB"; }
}

function resetAlbumDraft() {
  $("#add-album-form").reset(); state.pendingAlbumDetails = null; state.pendingAlbumRaw = null; state.pendingAlbumSearchField = "";
  $("#add-album-links").textContent = "—"; renderAlbumDraftPreview(); resolveStatus("#add-album-resolve-status"); $("#add-album-error").textContent = "";
}

async function saveAlbumDraft(reaction = "") {
  const form = $("#add-album-form"), current = formPayload(form), status = reaction ? "consumed" : "backlog";
  const payload = {...(state.pendingAlbumDetails || {}),...current,title_ru:current.title_original,raw_data:state.pendingAlbumRaw || compactObject(current),content_type:"music",status,reaction};
  const buttons = form.querySelectorAll("[data-save-album]"); buttons.forEach(button => { button.disabled = true; });
  try { const {item}=await request("/api/library",{method:"POST",body:JSON.stringify(payload)});state.items.push(item);resetAlbumDraft();$("#add-album-dialog").close();renderLibrary();toast(reaction === "like" ? "Альбом добавлен в понравившееся" : reaction === "dislike" ? "Альбом добавлен в не понравившееся" : "Альбом добавлен в бэклог"); }
  catch(error){$("#add-album-error").textContent=error.message;} finally{buttons.forEach(button=>{button.disabled=false;});}
}

async function resolveAlbumDraft() {
  const form=$("#add-album-form"),button=$("#resolve-album"),draft=formPayload(form);state.pendingAlbumRaw??=compactObject(draft);button.disabled=true;button.textContent="Ищу…";$("#add-album-error").textContent="";
  try { const {item}=await request("/api/resolve/album",{method:"POST",body:JSON.stringify(draft)});state.pendingAlbumDetails=item;fillForm(form,item,["title_original","artists","year","release_group_mbid"]);$("#add-album-links").innerHTML=albumLinksHtml(item);renderAlbumDraftPreview(item);const warnings=providerWarnings([item]);resolveStatus("#add-album-resolve-status",`Найдено: ${item.artists} — ${item.title_original}${item.year ? ` (${item.year})` : ""}. Исходный ввод сохранится в Raw.${warnings.length?` ${warnings.join(" ")}`:""}`,false,warnings.length>0);if(warnings.length)toast(warnings[0]); }
  catch(error){resolveStatus("#add-album-resolve-status",error.message,true);} finally{button.disabled=false;button.textContent="↻ Актуализировать из MusicBrainz";}
}

async function resolvePersonDraft() {
  const form = $("#add-person-form"), button = $("#resolve-person"), draft = formPayload(form);
  state.pendingPersonRaw ??= compactObject(draft); state.pendingPersonDetails = null; clearPersonSearchResults(); button.disabled = true; button.textContent = "Ищу…"; $("#add-person-error").textContent = "";
  try {
    const {items} = await request("/api/search/person", {method:"POST", body:JSON.stringify(draft)});
    state.personSearchItems = items || []; renderPersonSearchResults();
    const warnings = providerWarnings(state.personSearchItems);
    resolveStatus("#add-person-resolve-status", `${state.personSearchItems.length ? `Найдено вариантов: ${state.personSearchItems.length}. Выберите персону или откройте подробную карточку.` : "Подходящих вариантов не найдено."} Исходный ввод сохранится в Raw.${warnings.length ? ` ${warnings.join(" ")}` : ""}`, false, warnings.length > 0);
    if (warnings.length) toast(warnings[0]);
  } catch (error) { resolveStatus("#add-person-resolve-status", error.message, true); }
  finally { button.disabled = false; button.textContent = "↻ Актуализировать из TMDB"; }
}

async function resolveArtistDraft() {
  const form=$("#add-artist-form"),button=$("#resolve-artist"),draft={...formPayload(form),content_type:"music"};state.pendingArtistRaw??=compactObject(draft);button.disabled=true;button.textContent="Ищу…";$("#add-artist-error").textContent="";
  try { const {item}=await request("/api/resolve/person",{method:"POST",body:JSON.stringify(draft)});state.pendingArtistDetails=item;fillForm(form,item,["name","mbid"]);resolveStatus("#add-artist-resolve-status",`Найдено: ${item.name_original}. Исходный ввод сохранится в Raw.`); }
  catch(error){resolveStatus("#add-artist-resolve-status",error.message,true);} finally{button.disabled=false;button.textContent="↻ Актуализировать из MusicBrainz";}
}

async function reloadActiveData() {
  const [library, people, trash] = await Promise.all([request(`/api/library?content_type=${state.contentType}`), request(`/api/people?content_type=${state.contentType}`), request("/api/trash")]);
  state.items = library.items; state.interests = people.items; state.trash = trash.items;
  renderLibrary(); renderPeople(); renderInterestOptions(); renderTrash();
}

async function moveToTrash(button) {
  let outcome = null;
  try {
    outcome = await cardActionWithFeedback(button, () => request("/api/trash", {
      method:"POST",
      body:JSON.stringify({entity_type:button.dataset.trashEntity, entity_id:button.dataset.id, role:button.dataset.role || ""}),
    }), {exit:true});
    if (!outcome) return;
    await reloadActiveData();
    toast("Перемещено в корзину");
  } catch (error) {
    if (outcome) {
      outcome.container?.remove();
      toast(`Перемещено в корзину, но список не обновлён: ${error.message}`);
    } else toast(error.message);
  }
}

async function restoreFromTrash(button) {
  let outcome = null;
  try {
    outcome = await cardActionWithFeedback(button, () => request(
      `/api/trash/${encodeURIComponent(button.dataset.restoreId)}/restore`,
      {method:"POST", body:"{}"},
    ), {exit:true});
    if (!outcome) return;
    await reloadActiveData(); toast("Восстановлено из корзины");
  } catch (error) {
    if (outcome) {
      outcome.container?.remove();
      toast(`Восстановлено, но список не обновлён: ${error.message}`);
    } else toast(error.message);
  }
}

async function clearTrash(button) {
  const count = state.trash.length;
  if (!count || !window.confirm(`Удалить навсегда все объекты из корзины (${count})? Это действие нельзя отменить.`)) return;
  button.disabled = true; button.textContent = "Очищаю…";
  try {
    const result = await request("/api/trash/empty", {method:"POST", body:"{}"});
    await reloadActiveData();
    const warning = result.artwork_errors?.length ? ` Не удалось удалить файлов: ${result.artwork_errors.length}.` : "";
    toast(`Корзина очищена: удалено ${result.deleted}.${warning}`);
  } catch (error) { button.disabled = false; toast(error.message); }
  finally { button.textContent = "Очистить всю корзину"; }
}

function setBacklogMoods(moods) {
  state.backlogMoods = (moods || []).slice(0,15).map(normalizeBacklogMood)
    .filter(mood=>mood.label && mood.itemIds.length);
  if (!state.backlogMoods.some(mood=>mood.label === state.selectedBacklogMood)) state.selectedBacklogMood = "";
}

function renderBacklogMoodChips() {
  const node = $("#backlog-mood-chips");
  node.innerHTML = state.backlogMoods.length
    ? state.backlogMoods.map(mood=>
      `<button type="button" class="backlog-mood-chip${state.selectedBacklogMood === mood.label ? " active" : ""}" data-backlog-mood="${escapeHtml(mood.label)}" aria-pressed="${state.selectedBacklogMood === mood.label}">${escapeHtml(mood.label)}</button>`
    ).join("")
    : `<span class="backlog-moods-placeholder">Нажмите «Обновить», чтобы построить карту настроений текущего бэклога.</span>`;
  node.scrollLeft = 0;
}

function toggleBacklogMood(label) {
  state.selectedBacklogMood = state.selectedBacklogMood === label ? "" : label;
  renderBacklogMoodChips();
  renderLibrary();
}

async function refreshBacklogMoods() {
  if (operationRunning()) { toast("Дождитесь завершения текущей операции или остановите её"); return; }
  const button = $("#refresh-backlog-moods"), errorNode = $("#backlog-moods-error");
  button.disabled = true; button.textContent = "Обновляю…"; errorNode.textContent = "";
  let progressId = "";
  try {
    progressId = globalThis.crypto?.randomUUID?.() || `backlog-moods-${Date.now()}-${Math.random()}`;
    startRecommendationProgress(progressId,1,"Codex · настроения бэклога","запросов",{
      container:"#backlog-moods-progress",stages:"#backlog-moods-progress-stages",stop:"#backlog-moods-stop",kind:"moods",warnings:false,
    });
    const result = await request("/api/backlog/moods/llm", {method:"POST",body:JSON.stringify({
      content_type:state.contentType,progress_id:progressId,
    })});
    if (!result.cancelled && result.moods?.length) {
      setBacklogMoods(result.moods);
      state.selectedBacklogMood = "";
      renderBacklogMoodChips(); renderLibrary();
      toast("Карта настроений бэклога обновлена и сохранена");
    }
  } catch (error) { errorNode.textContent = error.message; toast(error.message); }
  finally {
    if (progressId) await finishRecommendationProgress(progressId);
    button.disabled = !state.items.some(item => item.status === "backlog"); button.textContent = "↻ Обновить";
  }
}

function renderRecommendationHead() {
  const sort = (label,key) => `<button class="sort-button" type="button" data-recommend-sort="${key}">${label}</button>`;
  const notes = state.backlogRecommendMode === "llm" ? `<th>${sort("Заметка","notes")}</th>` : "";
  const html = state.contentType === "music"
    ? `<tr><th>Обложка</th><th>${sort("Название","title")}</th><th>${sort("Исполнитель","artists")}</th><th>${sort("Первый выпуск","year")}</th><th>${sort("Песен","track_count")}</th><th>${sort("Тип","types")}</th><th>${sort("Жанр","genres")}</th><th>${sort("Прослушивания","total_listen_count")}</th>${notes}<th>Link</th><th>Действия</th></tr>`
    : `<tr><th>Постер</th><th>${sort("Название","title")}</th><th>${sort("Дата выхода","year")}</th><th>${sort("Режиссёр","directors")}</th><th>${sort("IMDb / КП","rating")}</th><th>${sort("Страны","countries")}</th><th>${sort("Жанр","genres")}</th><th>${sort("Ключевые персоны","key_people")}</th>${notes}<th>Link</th><th>Действия</th></tr>`;
  $("#backlog-recommend-head").innerHTML = html;
  $("#backlog-filtered-head").innerHTML = html;
  updateRecommendationSortIndicators();
}

function setBacklogRecommendMode(mode) {
  if(activeOperations.recommendation){toast("Сначала остановите текущую рекомендацию");return;}
  state.backlogRecommendMode = mode;
  if (recommendationProgressTimer) clearInterval(recommendationProgressTimer);
  recommendationProgressTimer = null;
  document.querySelectorAll("[data-backlog-recommend-mode]").forEach(button => button.classList.toggle("active", button.dataset.backlogRecommendMode === mode));
  const music = state.contentType === "music";
  $("#backlog-recommend-form").classList.toggle("hidden", music || mode !== "llm");
  $("#backlog-api-form").classList.toggle("hidden", music || mode !== "api");
  $("#album-recommend-form").classList.toggle("hidden", !music || mode !== "llm");
  $("#album-api-form").classList.toggle("hidden", !music || mode !== "api");
  $("#api-recommend-progress").classList.add("hidden");
  hideLlmPromptPreview();
  state.apiProgressWarnings = [];
  renderRecommendationHead();
  $("#backlog-recommend-result").classList.add("hidden");
  $("#backlog-recommend-warning").classList.add("hidden");
  $("#backlog-recommend-error").textContent = ""; $("#backlog-api-error").textContent = ""; $("#album-recommend-error").textContent = ""; $("#album-api-error").textContent = "";
}

function openBacklogRecommendation(mode = "llm") {
  if(operationRunning()){toast("Дождитесь завершения текущей операции или остановите её");return;}
  setRecommendationResultExpanded("backlog-recommend-dialog",false);
  const currentYear = new Date().getFullYear();
  for (const yearTo of [$("#backlog-year-to"), $("#api-year-to"), $("#album-llm-year-to"), $("#album-api-year-to")]) {
    yearTo.max = String(currentYear + 2); yearTo.value = String(currentYear);
  }
  state.llmItems = []; state.llmFilteredItems = []; state.apiItems = []; state.apiFilteredItems = []; state.llmErrors = []; state.llmRawResponse = ""; state.recommendationCancelled = false; state.apiProgressWarnings = []; state.recommendationSort = {key:"year",direction:"desc"};
  state.llmContextSeed = globalThis.crypto?.randomUUID?.() || `context-${Date.now()}-${Math.random()}`;
  $("#backlog-recommend-error").textContent = ""; $("#backlog-recommend-result").classList.add("hidden"); $("#backlog-recommend-body").innerHTML = ""; $("#backlog-filtered-result").classList.add("hidden"); $("#backlog-filtered-body").innerHTML = ""; $("#backlog-filtered-summary").innerHTML = ""; $("#open-filtered-reasons").classList.add("hidden"); $("#open-llm-raw-response").classList.add("hidden"); $("#backlog-recommend-message").textContent = "";
  const provider = state.llm.provider || "Codex SDK", model = state.llm.model ? ` · ${state.llm.model}` : "";
  $("#backlog-recommend-provider").textContent = `${provider}${model}${state.llm.configured === false ? " · требуется установка" : ""}`;
  $("#backlog-api-provider").textContent = state.tmdb.configured ? `TMDB · подключён${state.tmdb.omdb_configured ? " · OMDb" : ""}${state.tmdb.kinopoisk_configured ? " · КП" : ""}` : "TMDB · нужен ключ";
  setBacklogRecommendMode(mode);
  if (mode === "llm") setLlmContextLimits();
  if (mode === "api" && state.contentType === "movie") {
    document.querySelectorAll('input[name="actor_ids"],input[name="director_ids"]').forEach(box => { box.checked = false; });
    updateSelectedLabels();
  }
  $("#recommend-eyebrow").textContent = mode === "llm"
    ? "Codex · персональный подбор"
    : `${state.contentType === "music" ? "MusicBrainz" : "TMDB"} · подбор по авторам`;
  $("#recommend-title").textContent = mode === "llm"
    ? (state.contentType === "music" ? "Подобрать похожие альбомы" : "Подобрать похожие фильмы")
    : "Новое от авторов";
  $("#backlog-recommend-dialog").showModal();
}

async function runBacklogRecommendation(event) {
  event.preventDefault();
  const music = state.contentType === "music", button = $(music ? "#album-recommend-submit" : "#backlog-recommend-submit"), errorNode = $(music ? "#album-recommend-error" : "#backlog-recommend-error"), resultNode = $("#backlog-recommend-result");
  button.disabled = true; button.textContent = "Подбираю и уточняю…"; errorNode.textContent = ""; resultNode.classList.add("hidden"); $("#backlog-recommend-warning").classList.add("hidden");
  let progressId = "";
  try {
    const payload = {...llmRecommendationPayload(event.currentTarget),content_type:state.contentType};
    progressId = globalThis.crypto?.randomUUID?.() || `recommend-${Date.now()}-${Math.random()}`;
    payload.progress_id = progressId;
    startRecommendationProgress(progressId, 1, "Codex · запрос к LLM", "запросов");
    const result = await request("/api/recommendations/llm", {method:"POST", body:JSON.stringify(payload)});
    state.llmItems = result.items || []; state.llmFilteredItems = result.filtered_items || []; state.llmErrors = result.errors || []; state.llmRawResponse = result.raw_response || ""; state.recommendationCancelled = Boolean(result.cancelled); renderLlmRecommendations();
    if (result.provider_warnings?.length) showProviderWarnings([{provider_warnings:result.provider_warnings}]);
    $("#backlog-recommend-result-source").textContent = music ? "Codex + MusicBrainz" : "Codex + TMDB + OMDb + Кинопоиск";
    $("#backlog-recommend-model").textContent = result.model || "Codex"; resultNode.classList.remove("hidden");
  } catch (error) { errorNode.textContent = error.message; }
  finally { if (progressId) await finishRecommendationProgress(progressId); button.disabled = false; button.textContent = "Подобрать похожее"; }
}

let recommendationProgressTimer = null;
let recommendationProgressTarget = {container:"#api-recommend-progress",stages:"#api-recommend-progress-stages",stop:"#recommend-stop",kind:"recommendation",warnings:true};
function renderRecommendationProgress(value) {
  const stages = value.stages?.length ? value.stages : [{label:value.label || "API · обработка",processed:value.processed,total:value.total,unit:value.unit || "объектов"}];
  $(recommendationProgressTarget.container).classList.remove("hidden");
  $(recommendationProgressTarget.stages).innerHTML = stages.map(stage => {
    const total = Math.max(0,Number(stage.total)||0), processed = Math.min(total,Math.max(0,Number(stage.processed)||0));
    const percent = total ? Math.round(processed*100/total) : 100;
    return `<div class="recommend-progress-stage"><span><strong>${escapeHtml(stage.label || "API")}</strong> — обработано ${escapeHtml(stage.unit || "объектов")}: ${processed} из ${total} (${percent}%)</span><progress max="${Math.max(1,total)}" value="${total ? processed : 1}"></progress></div>`;
  }).join("");
}
function updateApiProgressWarnings(warnings = []) {
  const known = new Set(state.apiProgressWarnings.map(warning => `${warning.provider}\n${warning.message}`));
  const added = warnings.filter(warning => warning?.message && !known.has(`${warning.provider}\n${warning.message}`));
  for (const warning of added) {
    state.apiProgressWarnings.push(warning); known.add(`${warning.provider}\n${warning.message}`);
  }
  showProviderWarnings([]);
  if (added.length) toast(providerWarningText(added[0]));
}
async function pollApiProgress(progressId) {
  try {
    const response = await fetch(`/api/recommendations/progress?id=${encodeURIComponent(progressId)}`);
    if (!response.ok) return;
    const value = await response.json();
    if (value.found) {
      renderRecommendationProgress(value);
      if(value.cancel_requested && recommendationProgressTarget.stop){const stop=$(recommendationProgressTarget.stop);stop.disabled=true;stop.textContent="Останавливаю…";}
      if(recommendationProgressTarget.warnings)updateApiProgressWarnings(value.warnings || []);
    }
  } catch {}
}
function startRecommendationProgress(progressId, total, label, unit = "объектов", target = null) {
  if (recommendationProgressTimer) clearInterval(recommendationProgressTimer);
  recommendationProgressTarget = target || {container:"#api-recommend-progress",stages:"#api-recommend-progress-stages",stop:"#recommend-stop",kind:"recommendation",warnings:true};
  if(recommendationProgressTarget.kind)activeOperations[recommendationProgressTarget.kind]=progressId;
  if(recommendationProgressTarget.stop){const stop=$(recommendationProgressTarget.stop);stop.classList.remove("hidden");stop.disabled=false;stop.textContent="Стоп";}
  state.apiProgressWarnings = [];
  renderRecommendationProgress({processed:0,total,label,unit,stages:[]});
  recommendationProgressTimer = setInterval(() => pollApiProgress(progressId), 500);
}
async function finishRecommendationProgress(progressId) {
  if (recommendationProgressTimer) clearInterval(recommendationProgressTimer);
  recommendationProgressTimer = null;
  await pollApiProgress(progressId);
  $(recommendationProgressTarget.container).classList.add("hidden");
  if(recommendationProgressTarget.stop){const stop=$(recommendationProgressTarget.stop);stop.classList.add("hidden");stop.disabled=false;stop.textContent="Стоп";}
  if(recommendationProgressTarget.kind&&activeOperations[recommendationProgressTarget.kind]===progressId)activeOperations[recommendationProgressTarget.kind]="";
}

async function stopOperation(kind, button) {
  const progressId = activeOperations[kind];
  if (!progressId) return;
  button.disabled = true; button.textContent = "Останавливаю…";
  try {
    await request("/api/operations/cancel", {method:"POST",body:JSON.stringify({progress_id:progressId})});
  } catch (error) {
    button.disabled = false; button.textContent = "Стоп"; toast(error.message);
  }
}

function renderPeopleRecommendations() {
  const music = state.contentType === "music", items = state.peopleRecommendationItems;
  $("#people-recommend-body").innerHTML = items.length ? items.map((item,index) => {
    const localized = item.name_ru || item.name_original, original = item.name_original || "";
    const names = `<strong>${escapeHtml(text(localized))}</strong>${original && original !== localized ? `<br><span class="cell-muted">${escapeHtml(original)}</span>` : ""}`;
    const role = music ? (item.artist_type || "Исполнитель") : item.role === "director" ? "Режиссёр" : "Актёр";
    return `<tr><td class="person-photo-cell">${personPortraitHtml(item,"person-photo",index)}</td><td>${names}</td><td>${escapeHtml(role)}</td><td>${escapeHtml(text(item.llm_comment || item.notes))}</td><td><div class="table-actions"><button class="mini-button done card-action-button" data-add-recommended-person="${index}">Добавить</button><button class="mini-button remove card-action-button" data-remove-recommended-person="${index}">Убрать</button></div></td></tr>`;
  }).join("") : `<tr><td colspan="5" class="empty">Подходящих новых рекомендаций не найдено</td></tr>`;
  const errors = state.peopleRecommendationErrors;
  const prefix = state.peopleRecommendationCancelled ? "Остановлено. " : "";
  $("#people-recommend-message").textContent = errors.length
    ? `${prefix}Показано: ${items.length}. Не удалось обработать или исключено как повтор: ${errors.length}.`
    : `${prefix}Показано рекомендаций: ${items.length}.`;
}

function setPeopleRecommendationRole(role) {
  if (!["actor","director"].includes(role)) return;
  const roleInput = $("#people-recommend-role"), contextLimit = $("#people-recommend-context-limit");
  roleInput.value = role;
  document.querySelectorAll("[data-people-recommend-role]").forEach(button => {
    const active = button.dataset.peopleRecommendRole === role;
    button.classList.toggle("active",active);
    button.setAttribute("aria-checked",String(active));
  });
  const count = state.interests.filter(person => person.role === role).length;
  contextLimit.max = String(count);
  contextLimit.value = String(count);
  const entity = role === "director" ? "режиссёров" : "актёров";
  $("#people-recommend-title").textContent = role === "director" ? "Порекомендовать режиссёра" : "Порекомендовать актёра";
  $("#people-recommend-form textarea").placeholder = role === "director"
    ? "Например: порекомендуй современных европейских режиссёров с необычным визуальным стилем"
    : "Например: порекомендуй характерных драматических актёров европейского кино";
  $("#people-recommend-context-limit-label").textContent = `Макс. любимых ${entity} в контексте`;
  $("#people-recommend-context").textContent = `В системный промпт попадут до ${count} любимых ${entity} как сигнал вкуса и полный список уже добавленных ${entity}, включая корзину, для исключения повторов. Результаты будут уточнены через TMDB.`;
}

function openPeopleRecommendation() {
  if(operationRunning()){toast("Дождитесь завершения текущей операции или остановите её");return;}
  const music = state.contentType === "music", provider = state.llm.provider || "Codex SDK";
  state.peopleRecommendationItems = []; state.peopleRecommendationErrors = []; state.peopleRecommendationCancelled = false;
  $("#people-recommend-form").reset();
  const roleField = $("#people-recommend-role-field"), contextLimitField = $("#people-recommend-context-limit-field");
  const roleInput = $("#people-recommend-role"), contextLimit = $("#people-recommend-context-limit");
  roleField.classList.toggle("hidden",music); contextLimitField.classList.toggle("hidden",music);
  roleInput.disabled = music; contextLimit.disabled = music;
  if (music) {
    $("#people-recommend-title").textContent = "Порекомендовать исполнителя";
    $("#people-recommend-form textarea").placeholder = "Например: порекомендуй современных трип-хоп исполнителей с мрачным атмосферным звучанием";
    $("#people-recommend-context").textContent = "В системный промпт автоматически добавятся все уже добавленные исполнители, включая корзину. Карточки будут уточнены через MusicBrainz, фотографии — через fanart.tv.";
  } else setPeopleRecommendationRole("director");
  $("#people-recommend-provider").textContent = `${provider}${state.llm.model ? ` · ${state.llm.model}` : ""}${state.llm.configured === false ? " · требуется установка" : ""}`;
  $("#people-recommend-result").classList.add("hidden");
  $("#people-recommend-progress").classList.add("hidden");
  $("#people-recommend-error").textContent = "";
  $("#people-recommend-body").innerHTML = "";
  $("#people-recommend-dialog").showModal();
  $("#people-recommend-form textarea").focus();
}

async function runPeopleRecommendation(event) {
  event.preventDefault();
  const button = $("#people-recommend-submit"), errorNode = $("#people-recommend-error");
  button.disabled = true; button.textContent = "Подбираю и уточняю…"; errorNode.textContent = "";
  $("#people-recommend-result").classList.add("hidden");
  let progressId = "";
  try {
    progressId = globalThis.crypto?.randomUUID?.() || `people-recommend-${Date.now()}-${Math.random()}`;
    const formData = new FormData(event.currentTarget);
    const payload = {content_type:state.contentType,prompt:String(formData.get("prompt") || ""),limit:formData.get("limit"),progress_id:progressId};
    if (state.contentType === "movie") {
      payload.role = formData.get("role");
      payload.people_sample_size = formData.get("people_sample_size");
    }
    startRecommendationProgress(progressId,1,"Codex · запрос к LLM","запросов",{container:"#people-recommend-progress",stages:"#people-recommend-progress-stages",stop:"#people-recommend-stop",kind:"people",warnings:false});
    const result = await request("/api/recommendations/people/llm",{method:"POST",body:JSON.stringify(payload)});
    state.peopleRecommendationItems = result.items || []; state.peopleRecommendationErrors = result.errors || []; state.peopleRecommendationCancelled = Boolean(result.cancelled);
    renderPeopleRecommendations();
    $("#people-recommend-result-source").textContent = state.contentType === "music" ? "Codex + MusicBrainz + fanart.tv" : "Codex + TMDB";
    $("#people-recommend-model").textContent = result.model || "Codex";
    $("#people-recommend-result").classList.remove("hidden");
  } catch (error) { errorNode.textContent = error.message; }
  finally { if(progressId)await finishRecommendationProgress(progressId);button.disabled=false;button.textContent="Порекомендовать"; }
}

async function addRecommendedPerson(index, button) {
  const source = state.peopleRecommendationItems[index];
  if (!source) return;
  const unlockTable = lockActionTable(button);
  if (!unlockTable) return;
  try {
    const outcome = await cardActionWithFeedback(button, () => request(
      "/api/people", {method:"POST",body:JSON.stringify(source)},
    ), {exit:true});
    if (!outcome) return;
    const {item} = outcome.value;
    state.interests.push(item); state.peopleRecommendationItems.splice(index,1);
    renderPeopleRecommendations(); renderPeople(); renderInterestOptions();
    toast(state.contentType === "music" ? "Исполнитель добавлен" : "Персона добавлена");
  } catch(error) { toast(error.message); }
  finally { unlockTable(); }
}

async function removeRecommendedPerson(index, button) {
  if (!state.peopleRecommendationItems[index]) return;
  const unlockTable = lockActionTable(button);
  if (!unlockTable) return;
  try {
    const outcome = await cardActionWithFeedback(button, () => true, {exit:true});
    if (!outcome) return;
    state.peopleRecommendationItems.splice(index,1);
    renderPeopleRecommendations();
  } finally { unlockTable(); }
}

async function runBacklogApiRecommendation(event) {
  event.preventDefault();
  const music = state.contentType === "music", button = $(music ? "#album-api-submit" : "#backlog-api-submit"), errorNode = $(music ? "#album-api-error" : "#backlog-api-error"), resultNode = $("#backlog-recommend-result");
  const values = recommendationValues(event.currentTarget);
  state.apiPeriod = apiRecommendationPeriod(values);
  if (!music && values.actor_ids.length + values.director_ids.length === 0) {
    const message = "Нужно выбрать хотя бы одного актёра или режиссёра.";
    errorNode.textContent = message;
    window.alert(message);
    return;
  }
  button.disabled = true; button.textContent = "Ищу и уточняю…"; errorNode.textContent = ""; resultNode.classList.add("hidden"); $("#backlog-recommend-warning").classList.add("hidden");
  let progressId = "";
  try {
    const payload = {...values};
    progressId = globalThis.crypto?.randomUUID?.() || `recommend-${Date.now()}-${Math.random()}`;
    payload.progress_id = progressId;
    const personTotal = music ? values.artist_ids.length : values.actor_ids.length + values.director_ids.length;
    startRecommendationProgress(progressId, personTotal, music ? "MusicBrainz · альбомы исполнителей" : "TMDB · фильмы персон", music ? "исполнителей" : "персон");
    if (music) {
      const result = await request("/api/recommendations/musicbrainz", {method:"POST",body:JSON.stringify(payload)});
      state.apiItems = result.items || []; state.apiFilteredItems = result.filtered_items || []; state.llmErrors = result.errors || []; state.recommendationCancelled = Boolean(result.cancelled); renderApiRecommendations();
      if (result.provider_warnings?.length) showProviderWarnings([{provider_warnings:result.provider_warnings}]);
      $("#backlog-recommend-result-source").textContent = "MusicBrainz"; $("#backlog-recommend-model").textContent = "MusicBrainz API v2"; resultNode.classList.remove("hidden"); return;
    }
    if (values.year_from !== undefined) payload.date_from = `${values.year_from}-01-01`;
    if (values.year_to !== undefined) payload.date_to = `${values.year_to}-12-31`;
    delete payload.year_from;
    delete payload.year_to;
    if (values.excluded_genres !== undefined) payload.excluded_genres = String(values.excluded_genres).split(",").map(value => value.trim()).filter(Boolean);
    const result = await request("/api/recommendations/tmdb", {method:"POST", body:JSON.stringify(payload)});
    state.apiItems = result.items || []; state.apiFilteredItems = result.filtered_items || []; state.llmErrors = result.errors || []; state.recommendationCancelled = Boolean(result.cancelled); renderApiRecommendations();
    $("#backlog-recommend-result-source").textContent = "TMDB + OMDb + Кинопоиск";
    $("#backlog-recommend-model").textContent = "TMDB API"; resultNode.classList.remove("hidden");
  } catch (error) { errorNode.textContent = error.message; }
  finally { if (progressId) await finishRecommendationProgress(progressId); button.disabled = false; button.textContent = state.contentType === "music" ? "Найти альбомы" : "Найти фильмы"; }
}

async function toggleLlmPromptPreview(button) {
  const panel = $("#llm-prompt-preview");
  if (!panel.classList.contains("hidden")) {
    hideLlmPromptPreview();
    return;
  }
  const form = $(state.contentType === "music" ? "#album-recommend-form" : "#backlog-recommend-form");
  button.disabled = true; button.textContent = "Собираю промпт…";
  try {
    const payload = {...llmRecommendationPayload(form),content_type:state.contentType};
    const result = await request("/api/recommendations/prompt", {method:"POST",body:JSON.stringify(payload)});
    $("#llm-prompt-output").textContent = result.prompt || "";
    panel.classList.remove("hidden");
    button.textContent = "Скрыть промпт";
  } catch (error) {
    toast(error.message); button.textContent = "Посмотреть промпт";
  } finally { button.disabled = false; }
}

function hideLlmPromptPreview() {
  $("#llm-prompt-preview").classList.add("hidden");
  document.querySelectorAll("[data-view-llm-prompt]").forEach(button => { button.textContent = "Посмотреть промпт"; });
}

document.addEventListener("click", async event => {
  const activeFilterDetails=event.target.closest(".people-filter details");document.querySelectorAll(".people-filter details[open]").forEach(details=>{if(details!==activeFilterDetails)details.removeAttribute("open");});
  const activeToolbarMenu=event.target.closest(".toolbar-overflow");document.querySelectorAll(".toolbar-overflow[open]").forEach(details=>{if(details!==activeToolbarMenu)details.removeAttribute("open");});
  const operationStop=event.target.closest("[data-stop-operation]");if(operationStop){await stopOperation(operationStop.dataset.stopOperation,operationStop);return;}
  const contentType=event.target.closest("[data-content-type]");if(contentType){if(operationRunning()){toast("Дождитесь завершения текущей операции или остановите её");return;}if(contentType.dataset.contentType!==state.contentType){try{await changeContentType(contentType.dataset.contentType);}catch(error){toast(error.message);}}return;}
  const nav = event.target.closest("[data-view]"); if (nav) { switchView(nav.dataset.view); return; }
  const filteredReasons = event.target.closest("[data-open-filtered-reasons]"); if (filteredReasons) { if(currentFilteredRecommendations().length) $("#filtered-reasons-dialog").showModal();return; }
  const rawResponse = event.target.closest("[data-open-llm-raw-response]"); if (rawResponse) { if(state.llmRawResponse){$("#llm-raw-response-output").textContent=state.llmRawResponse;$("#llm-raw-response-dialog").showModal();}return; }
  const promptPreview = event.target.closest("[data-view-llm-prompt]"); if (promptPreview) { await toggleLlmPromptPreview(promptPreview); return; }
  const backlogMood = event.target.closest("[data-backlog-mood]"); if (backlogMood) { toggleBacklogMood(backlogMood.dataset.backlogMood);return; }
  const resultSizeToggle = event.target.closest("[data-result-size-toggle]"); if (resultSizeToggle) { toggleRecommendationResultSize(resultSizeToggle.dataset.resultSizeToggle);return; }
  const close = event.target.closest("[data-close-dialog]"); if (close) { if((close.dataset.closeDialog==="backlog-recommend-dialog"&&activeOperations.recommendation)||(close.dataset.closeDialog==="people-recommend-dialog"&&activeOperations.people)){toast("Сначала остановите текущую рекомендацию");return;}$(`#${close.dataset.closeDialog}`).close(); return; }
  const movieSearchDetailsButton = event.target.closest("[data-movie-search-details]"); if (movieSearchDetailsButton) { await openMovieSearchDetails(Number(movieSearchDetailsButton.dataset.movieSearchDetails),movieSearchDetailsButton);return; }
  const movieSearchSelectButton = event.target.closest("[data-movie-search-select]"); if (movieSearchSelectButton) { await selectMovieSearchResult(Number(movieSearchSelectButton.dataset.movieSearchSelect),movieSearchSelectButton);return; }
  const personSearchDetailsButton = event.target.closest("[data-person-search-details]"); if (personSearchDetailsButton) { await openPersonSearchDetails(Number(personSearchDetailsButton.dataset.personSearchDetails),personSearchDetailsButton);return; }
  const personSearchSelectButton = event.target.closest("[data-person-search-select]"); if (personSearchSelectButton) { await selectPersonSearchResult(Number(personSearchSelectButton.dataset.personSearchSelect),personSearchSelectButton);return; }
  const sort = event.target.closest("[data-sort]"); if (sort) { const key=sort.dataset.sort; state.sortDirection = state.sortKey === key ? (state.sortDirection === "asc" ? "desc" : "asc") : (["planned_soon","added_at"].includes(key) ? "desc" : "asc"); state.sortKey=key; renderLibrary(); return; }
  const recommendationSort = event.target.closest("[data-recommend-sort]"); if (recommendationSort) { const key=recommendationSort.dataset.recommendSort,setting=state.recommendationSort;setting.direction=setting.key===key&&setting.direction==="asc"?"desc":"asc";setting.key=key;if(state.backlogRecommendMode==="llm")renderLlmRecommendations();else renderApiRecommendations();return; }
  const peopleSort=event.target.closest("[data-people-sort]");if(peopleSort){const role=peopleSort.dataset.peopleRole,key=peopleSort.dataset.peopleSort,setting=state.peopleSort[role];setting.direction=setting.key===key&&setting.direction==="asc"?"desc":"asc";setting.key=key;renderPeople();return;}
  const peopleRecommendRole=event.target.closest("[data-people-recommend-role]");if(peopleRecommendRole){setPeopleRecommendationRole(peopleRecommendRole.dataset.peopleRecommendRole);return;}
  const trash = event.target.closest("[data-trash-entity]"); if (trash) { await moveToTrash(trash); return; }
  const restore = event.target.closest("[data-restore-id]"); if (restore) { await restoreFromTrash(restore); return; }
  const clearTrashButton = event.target.closest("#clear-trash"); if (clearTrashButton) { await clearTrash(clearTrashButton); return; }
  const details = event.target.closest("[data-details-id]"); if (details) { showDetails(state.items.find(item=>item.id===details.dataset.detailsId)); return; }
  const personPhoto = event.target.closest("[data-view-person-photo]"); if (personPhoto) { openPersonPhotoViewer(Number(personPhoto.dataset.viewPersonPhoto)); return; }
  const personExternalLink = event.target.closest("[data-person-external-link]"); if (personExternalLink) return;
  const personDetails = event.target.closest("[data-person-details-id]"); if (personDetails) { showPersonDetails(findInterest(personDetails.dataset.personDetailsId,personDetails.dataset.personDetailsRole)); return; }
  const addRecommendedPersonButton = event.target.closest("[data-add-recommended-person]"); if (addRecommendedPersonButton) { await addRecommendedPerson(Number(addRecommendedPersonButton.dataset.addRecommendedPerson),addRecommendedPersonButton); return; }
  const removeRecommendedPersonButton = event.target.closest("[data-remove-recommended-person]"); if (removeRecommendedPersonButton) { await removeRecommendedPerson(Number(removeRecommendedPersonButton.dataset.removeRecommendedPerson),removeRecommendedPersonButton);return; }
  const modalRecDetails = event.target.closest("[data-modal-rec-details]"); if (modalRecDetails) { const items=state.backlogRecommendMode === "llm" ? state.llmItems : state.apiItems; showDetails(items[Number(modalRecDetails.dataset.modalRecDetails)]); return; }
  const filteredRecDetails = event.target.closest("[data-filtered-rec-details]"); if (filteredRecDetails) { const record=currentFilteredRecommendations()[Number(filteredRecDetails.dataset.filteredRecDetails)];showDetails(record?.item);return; }
  const plannedSoon = event.target.closest("[data-planned-soon-toggle]"); if (plannedSoon) { const item=state.items.find(x=>x.id===plannedSoon.dataset.id);if(item)await patchItemWithFeedback(plannedSoon,item.id,{planned_soon:!item.planned_soon});return; }
  const itemAction = event.target.closest("[data-item-action]"); if (itemAction) { const action=itemAction.dataset.itemAction;await patchItemWithFeedback(itemAction,itemAction.dataset.id,action === "backlog" ? {status:"backlog"} : {status:"consumed",reaction:action},{exit:true});return; }
  const reaction = event.target.closest("[data-reaction-toggle]"); if (reaction) { const item=state.items.find(x=>x.id===reaction.dataset.id);if(item){const next=item.reaction===reaction.dataset.reactionToggle ? "" : reaction.dataset.reactionToggle;await patchItemWithFeedback(reaction,item.id,{reaction:next},{exit:true});}return; }
  const favorite = event.target.closest("[data-favorite-toggle]"); if (favorite) { await toggleFavorite(favorite); return; }
  const modalRecAction = event.target.closest("[data-modal-rec-action]"); if (modalRecAction) { await addModalRecommendation(Number(modalRecAction.dataset.index),modalRecAction.dataset.modalRecAction,modalRecAction); return; }
  const filteredRecAction = event.target.closest("[data-filtered-rec-action]"); if (filteredRecAction) { await addFilteredRecommendation(Number(filteredRecAction.dataset.index),filteredRecAction); return; }
  const card = event.target.closest("[data-card-id]"); if (card) { showDetails(state.items.find(item=>item.id===card.dataset.cardId)); return; }
  const filterToggle = event.target.closest("[data-filter-toggle]"); if (filterToggle) { const field=filterToggle.closest("[data-filter-field]"); const input=field.querySelector("input"); input.disabled=!input.disabled; field.classList.toggle("filter-disabled",input.disabled); filterToggle.setAttribute("aria-pressed",String(!input.disabled)); filterToggle.textContent=input.disabled?"—":"✓"; filterToggle.setAttribute("aria-label",`${input.disabled?"Включить":"Отключить"} фильтр ${field.dataset.filterLabel}`); if(field.closest("#backlog-recommend-form,#album-recommend-form"))hideLlmPromptPreview(); }
});

document.addEventListener("keydown", event => {
  if(event.key==="Escape")document.querySelectorAll(".people-filter details[open],.toolbar-overflow[open]").forEach(details=>details.removeAttribute("open"));
  if (event.key !== "Enter" && event.key !== " ") return;
  if (event.target.closest("button, a, input, select, textarea")) return;
  const card=event.target.closest("[data-card-id]"); if (card) { event.preventDefault(); showDetails(state.items.find(item=>item.id===card.dataset.cardId)); return; }
  const person=event.target.closest("[data-person-details-id]"); if (person) { event.preventDefault(); showPersonDetails(findInterest(person.dataset.personDetailsId,person.dataset.personDetailsRole)); }
});
document.addEventListener("error",event=>{if(event.target.matches?.("[data-artwork]")){const placeholder=document.createElement("span"),isPerson=event.target.classList.contains("person-photo")||event.target.classList.contains("detail-person-photo");placeholder.className=isPerson?`person-photo-placeholder${event.target.classList.contains("detail-person-photo")?" detail-person-photo":""}`:event.target.classList.contains("movie-poster")||event.target.classList.contains("rated-card-poster")||event.target.classList.contains("detail-poster")?"movie-poster-placeholder":"album-cover-placeholder";placeholder.setAttribute("aria-hidden","true");event.target.replaceWith(placeholder);}},true);
document.addEventListener("change", event => { const all=event.target.closest("[data-select-all]"); if (all) document.querySelectorAll(`input[name="${all.dataset.selectAll}_ids"]`).forEach(box=>box.checked=all.checked); if (all || event.target.matches('input[name="actor_ids"],input[name="director_ids"],input[name="artist_ids"]')) updateSelectedLabels(); });
document.addEventListener("input",event=>{const search=event.target.closest("[data-person-search]");if(search)filterPersonOptions(search.dataset.personSearch);if(event.target.closest("#backlog-recommend-form,#album-recommend-form"))hideLlmPromptPreview();});
document.addEventListener("dblclick",event=>{const note=event.target.closest("[data-editable-note]");if(note)editNote(note);});
document.querySelectorAll(".people-filter details").forEach(details=>details.addEventListener("toggle",()=>{syncPeopleMenuLayout();if(details.open)requestAnimationFrame(()=>details.querySelector("[data-person-search]")?.focus());}));
document.querySelectorAll("dialog").forEach(dialog => dialog.addEventListener("click", event => { if (dialog.id === "backlog-recommend-dialog" || (dialog.id === "people-recommend-dialog" && activeOperations.people)) return; const rect=dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); }));
$("#backlog-recommend-dialog").addEventListener("cancel", event => event.preventDefault());
$("#people-recommend-dialog").addEventListener("cancel", event => {if(activeOperations.people){event.preventDefault();toast("Сначала остановите текущую рекомендацию");}});
$("#person-photo-view-dialog").addEventListener("close", () => $("#person-photo-view-image").removeAttribute("src"));

document.querySelectorAll("[data-library-search]").forEach(input => input.addEventListener("input", event => { state.query=event.target.value;syncSearchControls();renderLibrary();renderPeople(); })); $("#refresh-tmdb").addEventListener("click", refreshTmdb); $("#refresh-people").addEventListener("click", refreshPeople); $("#refresh-detail").addEventListener("click", refreshCurrentMovie); $("#refresh-person-detail").addEventListener("click", refreshCurrentPerson);
$("#open-people-recommend").addEventListener("click",openPeopleRecommendation); $("#people-recommend-form").addEventListener("submit",runPeopleRecommendation);
$("#refresh-backlog-moods").addEventListener("click",refreshBacklogMoods);
$("#backlog-mood-chips").addEventListener("wheel",event=>{if(Math.abs(event.deltaY)>Math.abs(event.deltaX)){event.preventDefault();event.currentTarget.scrollBy({left:event.deltaY,behavior:"auto"});}},{passive:false});
$("#open-backlog-recommend").addEventListener("click",()=>openBacklogRecommendation("llm")); $("#open-backlog-api-recommend").addEventListener("click",()=>openBacklogRecommendation("api")); $("#backlog-recommend-form").addEventListener("submit", runBacklogRecommendation); $("#album-recommend-form").addEventListener("submit", runBacklogRecommendation); $("#backlog-api-form").addEventListener("submit", runBacklogApiRecommendation); $("#album-api-form").addEventListener("submit", runBacklogApiRecommendation);
$("#open-add").addEventListener("click",event=>{ event.currentTarget.closest("details")?.removeAttribute("open");if(state.contentType === "music"){resetAlbumDraft();$("#add-album-dialog").showModal();$("#add-album-form").elements.namedItem("title_original").focus();}else{resetMovieDraft();$("#add-dialog").showModal();$("#add-form").elements.namedItem("title_ru").focus();} });
$("#open-add-person").addEventListener("click",event=>{ event.currentTarget.closest("details")?.removeAttribute("open");if(state.contentType === "music"){state.pendingArtistDetails=null;state.pendingArtistRaw=null;$("#add-artist-form").reset();resolveStatus("#add-artist-resolve-status");$("#add-artist-error").textContent="";$("#add-artist-dialog").showModal();}else{resetPersonDraft();$("#add-person-dialog").showModal();$("#add-person-form").elements.namedItem("name_ru").focus();} });
$("#resolve-movie").addEventListener("click", resolveMovieDraft); $("#resolve-person").addEventListener("click", resolvePersonDraft); $("#resolve-album").addEventListener("click",resolveAlbumDraft); $("#resolve-artist").addEventListener("click",resolveArtistDraft);
$("#clear-add-movie").addEventListener("click", event => {
  event.preventDefault(); event.stopPropagation(); resetMovieDraft();
  if (!$("#add-dialog").open) $("#add-dialog").showModal();
  $("#add-form").elements.namedItem("title_ru").focus();
});
document.querySelectorAll('[data-save-movie="like"],[data-save-movie="dislike"]').forEach(button => button.addEventListener("click", () => saveMovieDraft(button.dataset.saveMovie)));
document.querySelectorAll('[data-save-album="like"],[data-save-album="dislike"]').forEach(button => button.addEventListener("click",()=>saveAlbumDraft(button.dataset.saveAlbum)));
$("#add-form").addEventListener("input", event => {
  const field = event.target.name;
  const searched = !$("#add-movie-search-results").classList.contains("hidden");
  if (!["title_original", "title_ru", "year"].includes(field) || (!state.pendingMovieDetails && !searched)) return;
  state.pendingMovieDetails = null; state.pendingMovieRaw = null; state.pendingMovieSearchField = ["title_original","title_ru"].includes(field) ? field : "";
  clearMovieSearchResults(); $("#add-movie-links").textContent = "—"; renderMovieDraftPreview();
  resolveStatus("#add-resolve-status", "Данные изменены. Нажмите «Актуализировать из TMDB», чтобы выполнить новый поиск.");
});
$("#add-form").addEventListener("submit", event => { event.preventDefault(); saveMovieDraft(); });
$("#clear-add-album").addEventListener("click",event=>{event.preventDefault();event.stopPropagation();resetAlbumDraft();if(!$("#add-album-dialog").open)$("#add-album-dialog").showModal();$("#add-album-form").elements.namedItem("title_original").focus();});
$("#add-album-form").addEventListener("input",event=>{const field=event.target.name;if(!state.pendingAlbumDetails||!["title_original","artists","year"].includes(field))return;state.pendingAlbumDetails=null;state.pendingAlbumSearchField=field;$("#add-album-links").textContent="—";renderAlbumDraftPreview();resolveStatus("#add-album-resolve-status","Данные изменены. Нажмите «Актуализировать из MusicBrainz», чтобы выполнить новый поиск.");});
$("#add-album-form").addEventListener("submit",event=>{event.preventDefault();saveAlbumDraft();});
$("#add-person-form").addEventListener("input",event=>{const field=event.target.name,searched=!$("#add-person-search-results").classList.contains("hidden");if(!["role","tmdb_id","name_original","name_ru"].includes(field)||(!state.pendingPersonDetails&&!searched))return;state.pendingPersonDetails=null;state.pendingPersonRaw=null;clearPersonSearchResults();resolveStatus("#add-person-resolve-status","Данные изменены. Нажмите «Актуализировать из TMDB», чтобы выполнить новый поиск.");});
$("#add-person-form").addEventListener("submit",async event=>{ event.preventDefault(); const form=event.currentTarget,current=formPayload(form); const payload={...(state.pendingPersonDetails||{}),...current,raw_data:state.pendingPersonRaw||compactObject(current)}; try{ const {item}=await request("/api/people",{method:"POST",body:JSON.stringify(payload)});state.interests.push(item);resetPersonDraft();$("#add-person-dialog").close();renderPeople();renderInterestOptions();toast("Персона добавлена");}catch(error){$("#add-person-error").textContent=error.message;} });
$("#add-artist-form").addEventListener("submit",async event=>{event.preventDefault();const form=event.currentTarget,current=formPayload(form),payload={...(state.pendingArtistDetails||{}),...current,content_type:"music",raw_data:state.pendingArtistRaw||compactObject(current)};try{const {item}=await request("/api/people",{method:"POST",body:JSON.stringify(payload)});state.interests.push(item);form.reset();state.pendingArtistDetails=null;state.pendingArtistRaw=null;$("#add-artist-error").textContent="";$("#add-artist-dialog").close();renderPeople();renderInterestOptions();toast("Исполнитель добавлен");}catch(error){$("#add-artist-error").textContent=error.message;}});

async function initialize() {
  try { const [{items},meta,trash,moods]=await Promise.all([request("/api/library?content_type=movie"),request("/api/meta"),request("/api/trash"),request("/api/backlog/moods?content_type=movie")]); state.items=items;state.interests=meta.interests;state.trash=trash.items;state.tmdb=meta.tmdb;state.musicbrainz=meta.musicbrainz||{};state.llm=meta.llm||{};setBacklogMoods(moods.moods);renderBacklogMoodChips();applyContentLabels();renderInterestOptions();renderLibrary();renderPeople();renderTrash(); }
  catch(error){toast(error.message);}
}
initialize();
