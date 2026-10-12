// Exercise shipped request, navigation, creation and competition-wide search helpers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assetPath = path.join(__dirname, '..', 'webpage', 'app.js');
const source = fs.readFileSync(assetPath, 'utf8');
const instrumented = source.replace(/\s+void render\(\);\s*\}\)\(\);\s*$/, `
  globalThis.appForTest = { getJson, mutate, render, shell, showCompetitionForm, updateCompetitionSlug,
    showSearch, searchCompetition, searchMatchRows, searchResultsHtml, closeModal, onSubmit,
    setCurrent: value => { current = value; }, getCurrent: () => current, getModal: () => modalState };
})();`);
assert.notEqual(instrumented, source);
const clone = value => JSON.parse(JSON.stringify(value));
const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => clone(body) });
const settle = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const person = (name, kind = 'player', team = '') => ({ kind, members: Array.isArray(name) ? name : [name], team });
const match = (id, a, b, finished = false, games = []) => ({ id, a, b, finished, games, time: '14:00', table: '3', referee: '裁判甲' });
const competition = { slug: 'cup', name: '秋季杯', events: ['single', 'pairs', 'teams'] };
const players = [person('张3'), person('ALIce')];
const single = { competition, event: { slug: 'single', name: '男子单打', schedule: 'group-knockout' }, stages: {
  group: { metadata: {}, data: { drawn: true, groups: [{ id: 1, qualifiers: 1, participants: players,
    matches: [match('g1-m1', 0, 1, true, [[0, 'wo']])], standings: { rows: [{ index: 0, rank: 1, points: 1 }, { index: 1, rank: 2, points: 0 }] } }] } },
  knockout: { metadata: {}, data: { rounds: 1, drawn: true, slots: players, matches: [match('k1', ...players)],
    bronze_match: match('bronze', ...players, true, [['wo', 'wo']]) } }
} };
const pairs = { competition, event: { slug: 'pairs', name: '女子双打', schedule: 'knockout' }, stages: {
  knockout: { data: { matches: [match('p1', person(['张3', '乙'], 'pair'), person(['丙', '丁'], 'pair'), true, [[11, 2]])] } }
} };
const teams = { competition, event: { slug: 'teams', name: '团体', schedule: 'round-robin' }, stages: {
  round_robin: { data: { participants: [person(['戊', '张3'], 'team', '一队'), person(['己'], 'team', '二队')], matches: [match('t1', 0, 1)] } }
} };
function load(mode = 'public', fetch = async () => response({})) {
  const listeners = {}, windowListeners = {};
  let now = 1000;
  const nodes = Object.fromEntries(['app', 'modal-root', 'toast-root'].map(id => [id, {
    innerHTML: '', querySelector: () => null, querySelectorAll: () => [], append: () => {},
    get firstChild() { return this.innerHTML ? {} : null; }
  }]));
  const searchOutput = { innerHTML: '' };
  let competitionForm = null;
  nodes['modal-root'].querySelector = selector => selector === '[data-search-results]' ? searchOutput :
    selector === '#competition-form' ? competitionForm : null;
  class TestDate extends Date { static now() { return now; } }
  const browser = { fetch, URLSearchParams, location: { hash: '#/' }, Date: TestDate,
    setTimeout: () => 0, clearTimeout: () => {},
    document: { body: { dataset: { mode }, style: {} }, getElementById: id => nodes[id],
      addEventListener: (name, callback) => { listeners[name] = callback; }, createElement: () => ({ remove: () => {} }) },
    window: { addEventListener: (name, callback) => { windowListeners[name] = callback; } }
  };
  vm.runInNewContext(instrumented, browser, { filename: assetPath });
  return { api: browser.appForTest, browser, nodes, searchOutput, listeners, windowListeners,
    advance: milliseconds => { now += milliseconds; }, setForm: form => { competitionForm = form; } };
}
function files() {
  const result = {
    './database/index.json': { competitions: ['cup'] }, './database/cup/meta.json': competition,
    './database/cup/single/meta.json': { ...single.event, stages: ['group', 'knockout'] },
    './database/cup/pairs/meta.json': { ...pairs.event, stages: ['knockout'] },
    './database/cup/teams/meta.json': { ...teams.event, stages: ['round_robin'] }
  };
  for (const event of [single, pairs, teams]) for (const [name, stage] of Object.entries(event.stages)) {
    result[`./database/cup/${event.event.slug}/${name}/metadata.json`] = stage.metadata || {};
    result[`./database/cup/${event.event.slug}/${name}/data.json`] = stage.data;
  }
  return result;
}

async function main() {
  const publicFiles = files(), reads = [];
  const publicApp = load('public', async (url, options) => {
    assert.equal(options.cache, 'no-store'); reads.push(url);
    assert.ok(Object.hasOwn(publicFiles, url), url);
    return response(publicFiles[url]);
  });
  const eventPath = '/competitions/cup/events/single';
  await Promise.all([publicApp.api.getJson(eventPath), publicApp.api.getJson(eventPath)]);
  await publicApp.api.getJson('/competitions');
  await publicApp.api.getJson('/competitions/cup');
  assert.equal(reads.filter(url => url.endsWith('/cup/meta.json')).length, 1, 'Raw metadata is reused across route aggregates');
  assert.equal(reads.filter(url => url.endsWith('/single/group/data.json')).length, 1, 'Concurrent aggregate requests share one stage read');
  const cachedRoutesReadCount = reads.length;
  for (const routePath of ['/competitions', '/competitions/cup', eventPath]) await publicApp.api.getJson(routePath);
  assert.equal(reads.length, cachedRoutesReadCount, 'Home, competition and event routes all reuse their aggregate caches');
  const detached = await publicApp.api.getJson(eventPath);
  detached.stages.group.data.groups[0].matches[0].games = [[99, 0]];
  assert.equal((await publicApp.api.getJson(eventPath)).stages.group.data.groups[0].matches[0].games[0][0], 0, 'Local dialog edits cannot poison cache');
  const beforeTTL = reads.length;
  publicApp.advance(60001);
  await publicApp.api.getJson(eventPath);
  assert.ok(reads.length > beforeTTL, 'Expired data is refreshed from the network');

  for (const mode of ['public', 'admin']) {
    const homeRead = deferred();
    const homeApp = load(mode, async url => mode === 'admin' || url.endsWith('/index.json') ? homeRead.promise : response(publicFiles[url]));
    assert.equal(homeApp.api.getCurrent(), null);
    homeApp.browser.location.hash = '#/';
    const homeNavigation = homeApp.api.render();
    assert.match(homeApp.nodes.app.innerHTML, /<h1>赛事中心<\/h1>[^]*正在加载赛事列表/,
      `${mode} cold startup must show the home shell and loading content`);
    homeRead.resolve(response(mode === 'admin' ? { competitions: [competition] } : publicFiles['./database/index.json']));
    await homeNavigation;
    assert.match(homeApp.nodes.app.innerHTML, /秋季杯/);
    assert.doesNotMatch(homeApp.nodes.app.innerHTML, /正在加载赛事列表/);
  }

  const groupRead = deferred(), koRead = deferred(), progress = [];
  const progressive = load('public', async url => url.endsWith('/group/data.json') ? groupRead.promise :
    url.endsWith('/knockout/data.json') ? koRead.promise : response(publicFiles[url]));
  const firstRead = progressive.api.getJson(eventPath);
  await settle();
  const subscribedRead = progressive.api.getJson(eventPath, data => progress.push(clone(data)));
  assert.ok(progress.some(data => data.event?.name === '男子单打'), 'A navigation subscribing to an in-flight read receives known metadata');
  groupRead.resolve(response(single.stages.group.data));
  await settle();
  assert.ok(progress.some(data => data.stages?.group && !data.stages.knockout), 'Group stage appears before slow knockout data');
  koRead.resolve(response(single.stages.knockout.data));
  await Promise.all([firstRead, subscribedRead]);

  const failedStage = deferred();
  const partialFailure = load('public', async url => url.endsWith('/knockout/data.json') ? failedStage.promise : response(publicFiles[url]));
  partialFailure.browser.location.hash = '#/competitions/cup/events/single?stage=group';
  const partialNavigation = partialFailure.api.render();
  await settle();
  assert.match(partialFailure.nodes.app.innerHTML, /matrix-table/, 'Ready group content is visible before the slower stage resolves');
  failedStage.resolve(response({}, 503));
  await partialNavigation;
  assert.match(partialFailure.nodes.app.innerHTML, /matrix-table/, 'An unavailable knockout block must not discard the ready group');
  assert.match(partialFailure.nodes.app.innerHTML, /更新失败/);
  assert.doesNotMatch(partialFailure.nodes.app.innerHTML, /正在加载本阶段对阵/);

  const stale = deferred(); let eventReads = 0;
  const freshSingle = clone(single); freshSingle.stages.group.data.groups[0].matches[0].games = [[11, 4]];
  const racingCache = load('admin', async (url, options) => options.method === 'PATCH' ? response({}) :
    ++eventReads === 1 ? stale.promise : response(freshSingle));
  const oldRead = racingCache.api.getJson(eventPath);
  await settle();
  await racingCache.api.mutate('PATCH', eventPath + '/stages/group/matches/g1-m1', { finished: true });
  const newRead = await racingCache.api.getJson(eventPath);
  stale.resolve(response(single));
  const oldResult = await oldRead;
  assert.equal(oldResult.stages.group.data.groups[0].matches[0].games[0][0], 11, 'An old GET resolving after a mutation must return refreshed data');
  assert.deepEqual(clone(oldResult), clone(newRead));
  assert.equal(eventReads, 2);
  assert.equal((await racingCache.api.getJson(eventPath)).stages.group.data.groups[0].matches[0].games[0][0], 11);

  let navigationReads = 0;
  const stages = load('admin', async () => { navigationReads++; return response(single); });
  stages.browser.location.hash = '#/competitions/cup/events/single?stage=group';
  await stages.api.render();
  stages.browser.location.hash = '#/competitions/cup/events/single?stage=knockout';
  const switchStage = stages.api.render();
  assert.match(stages.nodes.app.innerHTML, /bracket-canvas/, 'Stage switching renders cached bracket immediately');
  await switchStage;
  assert.equal(navigationReads, 1, 'Switching stages does not refetch the event');

  const navigationA = deferred(), navigationB = deferred();
  const navigation = load('admin', async url => url.endsWith('/events/single') ? navigationA.promise : navigationB.promise);
  navigation.api.setCurrent({ page: 'competition', competition, events: [single.event, pairs.event] });
  navigation.browser.location.hash = '#/competitions/cup/events/single';
  const routeA = navigation.api.render();
  assert.match(navigation.nodes.app.innerHTML, /秋季杯[^]*男子单打[^]*正在加载本阶段对阵/);
  assert.doesNotMatch(navigation.nodes.app.innerHTML, /正在加载赛事信息/);
  await settle();
  navigation.browser.location.hash = '#/competitions/cup/events/pairs';
  const routeB = navigation.api.render();
  await settle();
  navigationB.resolve(response(pairs)); await routeB;
  navigationA.resolve(response(single)); await routeA;
  assert.equal(navigation.api.getCurrent().event.slug, 'pairs', 'An older navigation cannot replace the latest route');
  assert.match(navigation.nodes.app.innerHTML, /女子双打/);

  const searchApp = load('public', async url => response(publicFiles[url]));
  searchApp.api.setCurrent({ page: 'event', ...clone(single) });
  searchApp.api.shell('Event');
  assert.match(searchApp.nodes.app.innerHTML, /class="brand-mark" src="\.\/icon.jpg"/);
  assert.doesNotMatch(searchApp.nodes.app.innerHTML, /class="header-link"/);
  searchApp.api.showSearch();
  assert.match(searchApp.nodes['modal-root'].innerHTML, /搜索比赛场次|placeholder="选手名称"/);
  const search = searchApp.api.onSubmit({ preventDefault: () => {}, target: { dataset: { form: 'search' }, elements: { player_name: { value: ' 张3 ' } } } });
  assert.match(searchApp.searchOutput.innerHTML, /正在搜索/);
  await search;
  const html = searchApp.searchOutput.innerHTML;
  assert.match(html, /找到 5 场比赛/);
  assert.match(html, /男子单打 · 小组赛|张3 - ALIce|已完成（1:wo）/);
  assert.match(html, /已完成（wo:wo）/);
  assert.match(html, /张3 \/ 乙 - 丙 \/ 丁/);
  assert.match(html, /一队 - 二队/);
  for (const text of ['预定时间：14:00', '预定桌号：3', '裁判：裁判甲']) assert.ok(html.includes(text));
  assert.equal((html.match(/<tr class="pending">/g) || []).length, 2);
  assert.equal(searchApp.api.searchMatchRows(single.event, single.stages, ' alice ').length, 3, 'Member search is case-insensitive');
  assert.equal(searchApp.api.searchMatchRows(teams.event, teams.stages, '一队').length, 0, 'Search targets member names rather than team labels');
  searchApp.api.closeModal();
  searchApp.api.setCurrent({ page: 'home', competitions: [] }); searchApp.api.shell('Home');
  assert.match(searchApp.nodes.app.innerHTML, /进入赛事后搜索比赛场次" disabled/);

  const slowSearchRead = deferred();
  const closedSearch = load('admin', async () => slowSearchRead.promise);
  closedSearch.api.setCurrent({ page: 'event', ...clone(single) }); closedSearch.api.showSearch();
  const pendingSearch = closedSearch.api.searchCompetition('张3');
  closedSearch.api.closeModal();
  slowSearchRead.resolve(response({ competition, events: [single.event] })); await pendingSearch;
  assert.equal(closedSearch.nodes['modal-root'].innerHTML, '', 'A completed search cannot reopen a closed dialog');

  const oldSearchRead = deferred(); let searchReads = 0;
  const staleSearch = load('admin', async url => url === '/api/competitions/cup' ? response({ competition, events: [single.event] }) :
    ++searchReads === 1 ? oldSearchRead.promise : response(single));
  staleSearch.api.setCurrent({ page: 'event', ...clone(single) }); staleSearch.api.showSearch();
  const oldSearch = staleSearch.api.searchCompetition('张3'); await settle();
  const latestSearch = staleSearch.api.searchCompetition('没人');
  oldSearchRead.resolve(response(single)); await Promise.all([oldSearch, latestSearch]);
  assert.match(staleSearch.searchOutput.innerHTML, /没有找到相关比赛场次/);
  assert.doesNotMatch(staleSearch.searchOutput.innerHTML, /张3 -/);

  const failedProject = load('admin', async url => url === '/api/competitions/cup' ? response({ competition, events: [single.event, pairs.event] }) :
    url.endsWith('/single') ? response(single) : response({}, 503));
  failedProject.api.setCurrent({ page: 'event', ...clone(single) }); failedProject.api.showSearch();
  await failedProject.api.searchCompetition('张3');
  assert.match(failedProject.searchOutput.innerHTML, /找到 3 场比赛/);
  assert.match(failedProject.searchOutput.innerHTML, /女子双打加载失败/);

  const writes = [];
  const create = load('admin', async (url, options) => options.method === 'POST' ?
    (writes.push(JSON.parse(options.body)), response({})) : response({ competitions: [] }));
  const elements = { name: { value: ' 校赛 ' }, start_date: { value: '2026-10-13' }, end_date: { value: '2026-10-14' },
    venue: { value: ' 体育馆 ' }, custom_slug: { checked: false }, slug: { value: 'hidden-unused', disabled: true, required: false } };
  const form = { dataset: { form: 'competition' }, elements };
  create.setForm(form); create.api.showCompetitionForm();
  assert.match(create.nodes['modal-root'].innerHTML, /name="custom_slug" type="checkbox"/);
  assert.match(create.nodes['modal-root'].innerHTML, /name="slug"[^>]*disabled/);
  await create.api.onSubmit({ preventDefault: () => {}, target: form });
  assert.equal(Object.hasOwn(writes[0], 'slug'), false, 'Unchecked creation omits slug entirely');
  create.api.showCompetitionForm(); elements.custom_slug.checked = true; elements.slug.value = ' pku-cup ';
  create.api.updateCompetitionSlug();
  assert.equal(elements.slug.disabled, false); assert.equal(elements.slug.required, true);
  await create.api.onSubmit({ preventDefault: () => {}, target: form });
  assert.equal(writes[1].slug, 'pku-cup');
  create.api.showCompetitionForm({ ...competition, venue: '体育馆' });
  assert.doesNotMatch(create.nodes['modal-root'].innerHTML, /custom_slug|competition-slug/);
  elements.custom_slug.checked = false; create.api.updateCompetitionSlug();
  assert.equal(elements.slug.disabled, true); assert.equal(elements.slug.required, false);
  console.log('Revision 7 frontend checks passed.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
