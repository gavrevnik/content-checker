const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = vm.createContext({
  $: () => ({addEventListener(){}}),
  validUrl: value => /^https?:\/\//.test(value) ? value : '',
  escapeHtml: value => String(value).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c])),
});
vm.runInContext(fs.readFileSync('app/static/ai-digest.js','utf8'), context);
const run = code => vm.runInContext(code, context);
assert.equal(run("digestState.filter='available'; digestMatches({availability:{digital_available_confidence:'high'}})"), true);
assert.equal(run("digestMatches({availability:{digital_available_confidence:'medium'}})"), true);
assert.equal(run("digestMatches({availability:{digital_available_confidence:'low'}})"), false);
assert.equal(run("digestMatches({availability:{digital_available_confidence:'not_found'}})"), false);
assert.equal(run("digestState.filter='all'; digestState.signals.add('ru_subtitles_available'); digestMatches({availability:{ru_subtitles_available:null}})"), false);
assert.equal(run("digestMatches({availability:{ru_subtitles_available:true}})"), true);
assert.equal(run("digestState.signals.add('rutracker_found'); digestMatches({availability:{ru_subtitles_available:true,rutracker_found:false}})"), false);
assert.equal(run("digestMatches({availability:{ru_subtitles_available:true,rutracker_found:true}})"), true);
assert.equal(run("digestLinks([{url:'javascript:alert(1)',label:'x'}])"), '');
assert.ok(run("digestLinks([{url:'https://example.org',label:'<script>'}])").includes('&lt;script&gt;'));
const html = fs.readFileSync('app/static/index.html','utf8');
assert.ok(html.includes('<script src="/app.js" defer>'));
assert.ok(html.includes('<script src="/ai-digest.js" defer>'));
assert.ok(html.indexOf('src="/app.js"') < html.indexOf('src="/ai-digest.js"'));
console.log('AI Digest UI: filters, combined signals, link escaping and script order passed');
run("digestState.signals.clear(); digestState.novelty='new'");
assert.equal(run("digestMatches({library:{status:'new'}})"), true);
assert.equal(run("digestMatches({library:{status:'existing'}})"), false);
assert.equal(run("digestMatches({})"), false);
assert.equal(run("digestState.novelty='existing'; digestMatches({library:{status:'existing',items:[{trashed:true}]}})"), true);
assert.ok(run("digestPersonalization({library:{status:'existing'},library_at_creation:{status:'new'},scoring:{ai_score:8.7,query_score:9,taste_score:8,ai_reason:'<script>',taste_confidence:'low',summary_revision:1}})").includes('&lt;script&gt;'));
assert.ok(run("digestPersonalization({library:{status:'existing'},library_at_creation:{status:'new'}})").includes('При создании: Новый для базы'));
console.log('AI Digest UI: novelty and score rendering passed');
// Exercise the full template and sorted original indices without browser automation.
const elements = new Map();
context.$ = selector => { if (!elements.has(selector)) elements.set(selector, {innerHTML:''}); return elements.get(selector); };
context.addedDate = value => value;
context.title = item => item.title_ru || item.title_original || '';
context.text = value => value ?? '';
context.moviePosterHtml = () => '';
context.movieLinksHtml = () => '';
context.movieRatings = () => '—';
run(`digestState.novelty='all'; digestState.sort='score'; digestState.current={query_summary:'Test',query:'Test',issues:['Failure <script>'],created_at:'2026-10-04',excluded:[{title:'Future',reason:'future_release'}],items:[
 {resolved:true,item:{title_original:'Lower'},library:{status:'existing'},scoring:{ai_score:6,query_score:6,taste_score:6,ai_reason:'ok',taste_confidence:'low',summary_revision:1}},
 {resolved:true,item:{title_original:'Higher'},library:{status:'new'},scoring:{ai_score:9,query_score:9,taste_score:9,ai_reason:'ok',taste_confidence:'high',summary_revision:1}},
 {resolved:false,item:{title_original:'Fallback',year:2025},library:{status:'uncertain'},links:[{url:'https://example.org'}]}
]}; renderDigest()`);
const rendered = elements.get('#digest-detail').innerHTML;
assert.ok(rendered.indexOf('Higher') < rendered.indexOf('Lower'));
assert.ok(rendered.includes('data-digest-details="1"'));
assert.ok(rendered.includes('Без полной карточки'));
assert.ok(rendered.includes('Исключено при создании: 1'));
assert.ok(rendered.includes('digest-novelty-filter'));
console.log('AI Digest UI: complete template, score sorting, stable indices and exclusions passed');

assert.ok(rendered.indexOf('digest-issues') > rendered.indexOf('Fallback'));
assert.ok(rendered.includes('Failure &lt;script&gt;'));
assert.equal((run("digestIssues({issues:['same','same'],items:[]})").match(/<li>/g)||[]).length, 1);
// Loading the list must not automatically open a modal. Explicit selection opens it.
(async () => {
  let opened = 0;
  const dialog = {open:false, showModal(){ this.open=true; opened++; }};
  elements.set('#digest-dialog', dialog);
  elements.set('#digest-refresh', {});
  elements.set('#digest-more', {classList:{toggle(){}}});
  context.request = async path => path.includes('/api/library') ? {items:[]} : path.includes('limit=') ? {digests:[{id:'d1',query_summary:'Small row',count:0}],total:1} : {id:'d1',query_summary:'Modal',items:[]};
  await run('loadDigests()');
  assert.equal(opened,0);
  assert.ok(elements.get('#digest-list').innerHTML.includes('aria-haspopup="dialog"'));
  await run("selectDigest('d1')");
  assert.equal(opened,1);
  assert.equal(elements.get('#digest-dialog-title').textContent,'Modal');
  console.log('AI Digest UI: modal lifecycle and separate escaped issues passed');
})().catch(error => {console.error(error);process.exitCode=1;});
