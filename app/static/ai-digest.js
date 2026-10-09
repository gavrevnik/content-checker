/* Uses the library's poster, bilingual title, ratings, links and details modal. */
const digestState = {list: [], total: 0, current: null, filter: 'all', novelty: 'all', sort: 'default', signals: new Set(), loading: false, sequence: 0, movieIds: new Set()};
const digestConfidenceLabels = {high: 'Сильные сигналы', medium: 'Вероятно доступно', low: 'Косвенные сигналы', not_found: 'Не подтверждено'};
const digestSignalLabels = {watch_available: 'Цифровые площадки', ru_subtitles_available: 'Русские субтитры', ru_audio_available: 'Русская аудиодорожка', rutracker_found: 'RuTracker', digital_release: 'Digital Release'};

async function loadDigests(more = false) {
  if (digestState.loading) return;
  digestState.loading = true;
  $('#digest-refresh').disabled = true;
  $('#digest-error').textContent = '';
  try {
    const [response, library] = await Promise.all([request(`/api/ai-digests?limit=30&offset=${more ? digestState.list.length : 0}`), request('/api/library?content_type=movie')]);
    digestState.movieIds = new Set(library.items.filter(m => m.tmdb_id).map(m => String(m.tmdb_id)));
    digestState.list = more ? [...digestState.list, ...response.digests] : response.digests;
    digestState.total = response.total;
    renderDigestList();
    if (!more && digestState.list.length && $('#digest-dialog').open) {
      const selected = digestState.list.find(d => d.id === digestState.current?.id) || digestState.list[0];
      await selectDigest(selected.id);
    }
  } finally { digestState.loading = false; $('#digest-refresh').disabled = false; }
}

function renderDigestList() {
  $('#digest-list').innerHTML = digestState.list.map(d => `<button type="button" class="digest-list-item${d.id === digestState.current?.id ? ' active' : ''}" data-digest-select="${escapeHtml(d.id)}" aria-haspopup="dialog"><strong>${escapeHtml(d.query_summary)}</strong><span>${escapeHtml(addedDate(d.created_at))} · ${d.count} фильмов · Открыть ↗</span></button>`).join('') || '<p class="empty">Здесь появятся подборки, запрошенные в чате.</p>';
  $('#digest-more').classList.toggle('hidden', digestState.list.length >= digestState.total);
}

async function selectDigest(id) {
  const sequence = ++digestState.sequence;
  const digest = await request(`/api/ai-digests/${encodeURIComponent(id)}`);
  if (sequence !== digestState.sequence) return;
  digestState.current = digest;
  renderDigestList(); renderDigest();
  if (!$('#digest-dialog').open) $('#digest-dialog').showModal();
}

function digestMatches(record) {
  const membership = record.library?.status || 'uncertain';
  if (digestState.novelty !== 'all' && membership !== digestState.novelty) return false;
  const a = record.availability || {};
  const level = a.digital_available_confidence || 'not_found';
  if (digestState.filter === 'available' && !['high', 'medium'].includes(level)) return false;
  if (!['all', 'available'].includes(digestState.filter) && level !== digestState.filter) return false;
  return [...digestState.signals].every(key => a[key] === true);
}

function digestLinks(links) {
  return (links || []).filter(l => validUrl(l.url)).map(l => `<a class="external-link" href="${escapeHtml(l.url)}" target="_blank" rel="noreferrer">${escapeHtml(l.label || 'Источник')} ↗</a>`).join(' ');
}

function digestEvidence(a) {
  if (!a) return '<span class="digest-unknown">Доступность не проверена</span>';
  const flags = Object.entries(digestSignalLabels).filter(([key]) => a[key] === true).map(([key, label]) => {
    const yes = a[key] === true, no = a[key] === false;
    return `<span class="digest-signal${yes ? ' positive' : ''}" title="${yes ? 'Найдено' : no ? 'Не найдено при этой проверке' : 'Не удалось проверить / не проверено'}">${yes ? '✓' : no ? '—' : '?'} ${label}</span>`;
  }).join('');
  const evidence = a.evidence || {};
  const statusNames = {ok: 'проверено', unavailable: 'проверка недоступна', not_configured: 'не настроен ключ', not_checked: 'не проверено'};
  const sourceNames = {watch: 'TMDB / JustWatch', releases: 'Даты релизов TMDB', subtitles: 'OpenSubtitles', rutracker: 'RuTracker'};
  const sourceLines = Object.entries(evidence).map(([key, value]) => {
    const links = [];
    if (value.source_url) links.push({url:value.source_url,label:'Источник'});
    if (key === 'watch') for (const offer of value.providers || []) links.push({url:offer.url,label:`${offer.country}: ${offer.provider} (${offer.type})`});
    if (key === 'rutracker') for (const match of value.matches || []) links.push({url:match.url,label:`${match.title} · seeds ${text(match.seeds)} · ${Math.round(match.confidence * 100)}%`});
    if (key === 'subtitles') for (const match of value.matches || []) if(match.url) links.push({url:match.url,label:'Субтитры'});
    const dates = key === 'releases' ? `<p>Digital Release: ${(value.past || []).filter(r => r.type === 4).map(r => escapeHtml(`${r.country} ${r.date}`)).join(', ') || 'не подтверждён'}</p><p>Будущие цифровые релизы: ${(value.future || []).filter(r => r.type === 4).map(r => escapeHtml(`${r.country} ${r.date}`)).join(', ') || 'не найдены'}</p>` : '';
    return `<li><strong>${sourceNames[key] || escapeHtml(key)}</strong>: ${escapeHtml(statusNames[value.status] || value.status)}${value.reason ? ` · ${escapeHtml(value.reason)}` : ''}${key === 'subtitles' && value.count != null ? ` · вариантов: ${value.count}${value.count_is_lower_bound ? '+' : ''}` : ''}${value.first_seen ? ` · впервые замечено ${escapeHtml(addedDate(value.first_seen))}` : ''}<div class="digest-source-links">${digestLinks(links)}</div>${dates}</li>`;
  }).join('');
  return `<div class="digest-signals">${flags}</div><details class="digest-evidence"><summary>Проверено ${escapeHtml(new Date(a.checked_at).toLocaleString('ru-RU'))}${a.watch_countries?.length ? ` · ${escapeHtml(a.watch_countries.join(', '))}` : ''}</summary><ul>${sourceLines}</ul><p>Субтитры не подтверждают озвучку. «Не найдено» не доказывает отсутствие релиза. Данные площадок: JustWatch через TMDB.</p></details>`;
}

function digestPersonalization(record) {
  const membership = record.library || {status:'uncertain'}, original = record.library_at_creation?.status;
  const labels = {new:'Новый для базы', existing:'Уже в базе', uncertain:'Новизна требует проверки'};
  const statuses = {backlog:'бэклог', consumed:'просмотрено', dismissed:'отложено'};
  const details = (membership.items || []).map(m => `${statuses[m.status] || m.status}${m.trashed ? ', корзина' : ''}`).join('; ');
  const score = record.scoring;
  return `<div class="digest-personalization"><span class="digest-signal${membership.status === 'new' ? ' positive' : ''}">${labels[membership.status] || labels.uncertain}${details ? ` · ${escapeHtml(details)}` : ''}</span>${original && original !== membership.status ? `<small>При создании: ${labels[original]}</small>` : ''}${record.release_state === 'unknown' ? '<span class="digest-unknown">Дата релиза не подтверждена</span>' : record.release_state === 'future' ? '<span class="digest-unknown">Будущий релиз</span>' : ''}${score ? `<details><summary><strong>AI ${escapeHtml(score.ai_score)} / 10</strong> · соответствие запросу и вкусам</summary><p>Запрос: ${escapeHtml(score.query_score)} · вкусы: ${escapeHtml(score.taste_score)} · уверенность: ${escapeHtml(({low:'низкая',medium:'средняя',high:'высокая'})[score.taste_confidence] || score.taste_confidence)}</p><p>${escapeHtml(score.ai_reason)}</p><small>70% запрос + 30% вкусы · summary #${escapeHtml(score.summary_revision)}</small></details>` : ''}</div>`;
}

function digestCard(record, index) {
  const item = record.item, a = record.availability || {}, confidence = a.digital_available_confidence || 'not_found';
  const known = record.library?.status === 'existing' || digestState.movieIds.has(String(item.tmdb_id));
  return `<article class="digest-card"><div class="digest-movie-head">${moviePosterHtml(item)}<div><button type="button" class="movie-title-button" data-digest-details="${index}"><strong>${escapeHtml(title(item))}</strong><span>${escapeHtml(item.title_original)}</span></button><p>${escapeHtml(text(item.release_date || item.year))} · ${escapeHtml(text(item.directors))}</p><p class="digest-ratings">IMDb / КП: <strong>${escapeHtml(movieRatings(item))}</strong></p><div class="movie-title-links">${movieLinksHtml(item, true)}</div></div><button class="mini-button done" type="button" data-digest-add="${index}"${known ? ' disabled' : ''}>${known ? 'В библиотеке' : 'В бэклог'}</button></div>${digestPersonalization(record)}<span class="digest-confidence ${escapeHtml(confidence)}">${digestConfidenceLabels[confidence]}</span>${record.festival_note ? `<p>${escapeHtml(record.festival_note)}</p>` : ''}<p>${escapeHtml(record.reason)}</p>${digestEvidence(record.availability)}<div class="digest-source-links">${digestLinks(record.links)}</div></article>`;
}

function digestIssues(d) {
  const notes = [...(d.issues || [])];
  for (const r of d.items) for (const [source, evidence] of Object.entries(r.availability?.evidence || {})) {
    if (['unavailable', 'not_configured'].includes(evidence.status)) {
      const name = {watch:'TMDB / JustWatch', releases:'TMDB', subtitles:'OpenSubtitles', rutracker:'RuTracker'}[source] || source;
      const message = (evidence.warnings || []).join('; ') || evidence.reason || 'проверка недоступна';
      notes.push(`${name}: ${message}`);
    }
  }
  const unique = [...new Set(notes)];
  return unique.length ? `<aside class="digest-issues" aria-label="Ограничения проверки"><h3>Ограничения проверки</h3><ul>${unique.map(note => `<li>${escapeHtml(note)}</li>`).join('')}</ul></aside>` : '';
}

function renderDigest() {
  const d = digestState.current;
  if (!d) return;
  const shown = d.items.map((r, i) => ({record:r, index:i})).filter(({record}) => digestMatches(record));
  if (digestState.sort === 'score') shown.sort((a,b) => (b.record.scoring?.ai_score ?? -1) - (a.record.scoring?.ai_score ?? -1));
  const resolved = shown.filter(({record}) => record.resolved), fallback = shown.filter(({record}) => !record.resolved);
  const filters = [['all','Все'],['available','Ориентировочно доступны'],...Object.entries(digestConfidenceLabels)].map(([key,label]) => `<option value="${key}"${digestState.filter === key ? ' selected' : ''}>${label}</option>`).join('');
  $('#digest-dialog-title').textContent = d.query_summary;
  $('#digest-detail').innerHTML = `<header class="digest-header">${d.summary ? `<p class="digest-summary">${escapeHtml(d.summary)}</p>` : ''}</header><div class="digest-filters"><label>Библиотека <select id="digest-novelty-filter">${[['all','Все фильмы'],['new','Только новые для базы'],['existing','Уже в базе'],['uncertain','Неясное совпадение']].map(([key,label]) => `<option value="${key}"${digestState.novelty === key ? ' selected' : ''}>${label}</option>`).join('')}</select></label><label>Порядок <select id="digest-sort">${[['default','Как в подборке'],['score','По AI Score']].map(([key,label]) => `<option value="${key}"${digestState.sort === key ? ' selected' : ''}>${label}</option>`).join('')}</select></label><label>Доступность <select id="digest-confidence-filter">${filters}</select></label>${Object.entries(digestSignalLabels).map(([key,label]) => `<label><input type="checkbox" data-digest-signal="${key}"${digestState.signals.has(key) ? ' checked' : ''}> ${label}</label>`).join('')}<span class="count">${shown.length} / ${d.items.length}</span></div><div class="digest-cards">${resolved.map(({record,index}) => digestCard(record,index)).join('')}</div>${fallback.length ? `<section class="digest-fallback"><h3>Без полной карточки</h3><ul>${fallback.map(({record}) => `<li><strong>${escapeHtml(title(record.item))}</strong> ${escapeHtml(record.item.year)}${digestPersonalization(record)}<p>${escapeHtml(record.reason)}</p>${record.festival_note ? `<p>${escapeHtml(record.festival_note)}</p>` : ''}${digestLinks(record.links)}${digestEvidence(record.availability)}</li>`).join('')}</ul></section>` : ''}${!shown.length ? '<p class="empty">Нет фильмов с выбранными фильтрами.</p>' : ''}${digestIssues(d)}<details class="digest-request"><summary>Исходный запрос · ${escapeHtml(addedDate(d.created_at))}</summary><p>${escapeHtml(d.query)}</p></details>${d.excluded?.length ? `<details><summary>Исключено при создании: ${d.excluded.length}</summary><ul>${d.excluded.map(e => `<li>${escapeHtml(e.title)} — ${e.reason === 'future_release' ? 'будущий релиз' : 'уже есть или возможное совпадение в базе'}</li>`).join('')}</ul></details>` : ''}`;
}

$('#digest-refresh').addEventListener('click', () => loadDigests().catch(e => { $('#digest-error').textContent = e.message; }));
$('#digest-more').addEventListener('click', () => loadDigests(true).catch(e => { $('#digest-error').textContent = e.message; }));
$('#digest-dialog').addEventListener('change', event => {
  if (event.target.id === 'digest-novelty-filter') digestState.novelty = event.target.value;
  if (event.target.id === 'digest-sort') digestState.sort = event.target.value;
  if (event.target.id === 'digest-confidence-filter') digestState.filter = event.target.value;
  const key = event.target.dataset.digestSignal;
  if (key) event.target.checked ? digestState.signals.add(key) : digestState.signals.delete(key);
  renderDigest();
});
async function handleDigestClick(event) {
  const select = event.target.closest('[data-digest-select]'), details = event.target.closest('[data-digest-details]'), add = event.target.closest('[data-digest-add]');
  try {
    if (select) await selectDigest(select.dataset.digestSelect);
    if (details) showDetails(digestState.current.items[Number(details.dataset.digestDetails)].item);
    if (add) {
      add.disabled = true;
      const item = digestState.current.items[Number(add.dataset.digestAdd)].item;
      await request('/api/library', {method:'POST', body:JSON.stringify({...item,status:'backlog',reaction:''})});
      digestState.movieIds.add(String(item.tmdb_id));
      await reloadActiveData(); await selectDigest(digestState.current.id); toast('Фильм добавлен в бэклог');
    }
  } catch (error) { $(select ? '#digest-error' : '#digest-modal-error').textContent = error.message; if(add) add.disabled = false; }
}
$('#ai-digest-view').addEventListener('click', handleDigestClick);
$('#digest-dialog').addEventListener('click', handleDigestClick);
$('#digest-dialog').addEventListener('close', () => { ++digestState.sequence; $('#digest-modal-error').textContent = ''; });
