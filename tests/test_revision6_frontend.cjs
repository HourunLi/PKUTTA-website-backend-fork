// Exercise the actual shipped client, including WO, server standings and Submit requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const assetPath = path.join(__dirname, '..', 'webpage', 'app.js');
const source = fs.readFileSync(assetPath, 'utf8');
const instrumented = source.replace(/\s+void render\(\);\s*\}\)\(\);\s*$/, `
  globalThis.appForTest = { bigScore, hasCompleteGameScore, winnerForMatch, rankRoundRobin,
    nullableScore, renderRoundRobinTable, renderGroupStage, renderKnockoutStage,
    showGroupSchedule, showMatch, onSubmit, saveGameRow, waitForGameSaves, shell,
    submitChanges, pollSubmit, getSubmitSerial: () => submitSerial,
    setCurrent: value => { current = value; } };
})();`);
assert.notEqual(instrumented, source, 'Could not locate application startup');
const clone = value => JSON.parse(JSON.stringify(value));
const response = (body, ok = true) => ({ ok, status: ok ? 200 : 500, json: async () => clone(body) });
const settle = () => new Promise(resolve => setImmediate(resolve));
const person = name => ({ kind: 'player', members: [name], team: '' });

function load(mode = 'public', fetch = async () => response({})) {
  const listeners = {}, windowListeners = {}, timers = [];
  const publishButton = { disabled: false, textContent: '', setAttribute: () => {} };
  const nodes = Object.fromEntries(['app', 'modal-root', 'toast-root'].map(id => [id, {
    innerHTML: '', querySelector: () => null, querySelectorAll: () => [], append: () => {},
    get firstChild() { return this.innerHTML ? {} : null; }
  }]));
  nodes.app.querySelector = selector => selector === '[data-action="publish"]' && nodes.app.innerHTML.includes('data-action="publish"') ? publishButton : null;
  const browser = {
    fetch, URLSearchParams, location: { hash: '#/' },
    setTimeout: callback => { timers.push({ callback, active: true }); return timers.length - 1; },
    clearTimeout: id => { if (timers[id]) timers[id].active = false; },
    document: {
      body: { dataset: { mode }, style: {} }, getElementById: id => nodes[id],
      addEventListener: (name, callback) => { listeners[name] = callback; },
      createElement: () => ({ remove: () => {} })
    },
    window: { addEventListener: (name, callback) => { windowListeners[name] = callback; } }
  };
  vm.runInNewContext(instrumented, browser, { filename: assetPath });
  return { api: browser.appForTest, nodes, listeners, windowListeners, timers, publishButton, browser };
}

const participants = [person('张3'), person('李4')];
const match = { id: 'g1-m1', a: 0, b: 1, finished: true, games: [[11, 4]], referee: '王裁判' };
// The server order intentionally differs from raw scores, so client reranking is detectable.
const standings = { order: [1, 0], rows: [
  { index: 1, rank: 1, points: 1, tied_points: 1, game_ratio: { won: 3, lost: 0 }, point_ratio: { won: 33, lost: 0 }, draw_rank: 1 },
  { index: 0, rank: 2, points: 0, tied_points: null, game_ratio: null, point_ratio: null, draw_rank: null }
] };
const group = { id: 1, qualifiers: 1, participants, matches: [match], standings,
  scheduled_time: '14:30', scheduled_table: '3', scheduled_referee: '预定裁判' };
const eventData = {
  competition: { slug: 'cup', name: '校赛' },
  event: { slug: 'single', name: '男子单打', schedule: 'group-knockout' },
  stages: { group: { metadata: {}, data: { drawn: true, groups: [group] } }, knockout: { metadata: {}, data: {} } }
};
const current = () => ({ ...clone(eventData), page: 'event', stage: 'group' });

async function main() {
  const publicApp = load();
  const { api } = publicApp;
  api.setCurrent(current());
  for (const [games, expected] of [
    [[[11, 'wo'], [0, 'WO']], [2, 'wo']],
    [[['wo', 0], ['wo', 11]], ['wo', 2]],
    [[['wo', 'wo']], ['wo', 'wo']],
    [[[11, 4], [null, 'wo']], [2, 'wo']],
    [[[11, 4], [0, 'wo']], [2, 'wo']]
  ]) assert.deepEqual(clone(api.bigScore({ games })), expected);
  assert.equal(api.hasCompleteGameScore(['Wo', 0]), true);
  assert.equal(api.hasCompleteGameScore(['wo', null]), true);
  assert.equal(api.winnerForMatch({ finished: true, games: [[0, 'wo']] }, ...participants), participants[0]);
  assert.equal(api.winnerForMatch({ finished: true, games: [['wo', 0]] }, ...participants), participants[1]);
  assert.equal(api.winnerForMatch({ finished: true, games: [['wo', 'wo']] }, ...participants).kind, 'bye');
  assert.deepEqual(clone(api.rankRoundRobin(participants, [{ ...match, games: [['wo', 0]] }])), [2, 1]);
  for (const [value, expected] of [[' Wo ', 'wo'], ['', null], ['00', 0], ['99', 99]]) {
    assert.equal(api.nullableScore({ value }), expected);
  }
  for (const value of ['1.2', '-1', '100', 'ow']) assert.throws(() => api.nullableScore({ value }), /局分/);

  const table = api.renderRoundRobinTable(participants, [match], 1, 'group-one', standings);
  assert.equal((table.match(/<table /g) || []).length, 3);
  const roster = table.split('class="data-table roster-table"')[1].split('</table>')[0];
  assert.doesNotMatch(roster, /排名|data-rank-column/);
  assert.match(roster, /class="qualifier"[^]*李4/);
  assert.doesNotMatch(roster, /class="qualifier"[^<]*<button[^]*张3/);
  const ranking = table.split('group-standings-table')[1];
  assert.ok(ranking.indexOf('李4') < ranking.indexOf('张3'));
  for (const title of ['名次', '选手', '积分', '同分内部积分', '局胜负比', '球分胜负比', '抽签名次']) assert.match(ranking, new RegExp(title));
  assert.equal((ranking.match(/class="unused-metric"/g) || []).length, 4);
  assert.match(ranking, />3:0<\/td>/);
  const plain = { rows: standings.rows.map(row => ({ ...row, tied_points: null, game_ratio: null, point_ratio: null, draw_rank: null })) };
  assert.doesNotMatch(api.renderRoundRobinTable(participants, [match], 1, 'plain', plain), /同分内部积分|局胜负比|球分胜负比|抽签名次/);
  const woTable = api.renderRoundRobinTable(participants, [{ ...match, games: [[0, 'wo']] }], 0, 'wo', null);
  assert.match(woTable, />1:wo<\/button>/);
  assert.match(woTable, />wo:1<\/button>/);
  assert.match(api.renderGroupStage(eventData.stages.group), /预定裁判：预定裁判/);
  const partial = clone(eventData.stages.group);
  partial.data.groups[0].matches[0].finished = false;
  assert.doesNotMatch(api.renderGroupStage(partial), /group-standings-table|小组出线名单/);
  const bracket = api.renderKnockoutStage({ data: { rounds: 3, drawn: true, slots: [person('甲'), { kind: 'bye' }] } });
  for (let i = 1; i <= 8; i++) assert.match(bracket, new RegExp(`class="slot-number">#${i}<`));
  assert.equal((bracket.match(/class="slot-number"/g) || []).length, 8);
  assert.match(bracket, /#2<\/span><span class="name">轮空/);
  const doubleWoBracket = api.renderKnockoutStage({ data: { rounds: 1, drawn: true, slots: participants,
    matches: [{ ...match, round: 0, index: 0, games: [['wo', 'wo']] }] } });
  assert.match(doubleWoBracket, /冠军 · 轮空/);
  assert.match(doubleWoBracket, />wo:wo<\/button>/);
  api.showMatch(match.id);
  assert.match(publicApp.nodes['modal-root'].innerHTML, /裁判：王裁判/);
  const before = publicApp.nodes['modal-root'].innerHTML;
  publicApp.listeners.click({ target: { closest: () => null }, preventDefault: () => {} });
  publicApp.listeners.keydown({ key: 'Escape', target: { matches: () => false } });
  assert.equal(publicApp.nodes['modal-root'].innerHTML, before);
  let focused = null, prevented = false;
  const focusables = ['first', 'last'].map(name => ({ hidden: false, getClientRects: () => [1], focus: () => { focused = name; } }));
  publicApp.nodes['modal-root'].querySelectorAll = () => focusables;
  publicApp.browser.document.activeElement = focusables[1];
  publicApp.listeners.keydown({ key: 'Tab', shiftKey: false, target: { matches: () => false }, preventDefault: () => { prevented = true; } });
  assert.equal(focused, 'first');
  assert.equal(prevented, true);
  api.shell('Public');
  assert.doesNotMatch(publicApp.nodes.app.innerHTML, /submit-button/);

  const writes = [];
  const adminApp = load('admin', async (url, options) => {
    if (options.method === 'PATCH') {
      writes.push({ url, body: JSON.parse(options.body) });
      return response({});
    }
    assert.equal(url, '/api/competitions/cup/events/single');
    return response(eventData);
  });
  adminApp.api.setCurrent(current());
  adminApp.api.showGroupSchedule(1);
  assert.match(adminApp.nodes['modal-root'].innerHTML, /name="scheduled_referee" value="预定裁判"/);
  await adminApp.api.onSubmit({ preventDefault: () => {}, target: { dataset: { form: 'group-schedule' }, elements: {
    scheduled_time: { value: '15:00' }, scheduled_table: { value: '4' }, scheduled_referee: { value: ' 新裁判 ' }
  } } });
  assert.deepEqual(writes[0].body, { scheduled_time: '15:00', scheduled_table: '4', scheduled_referee: '新裁判' });
  adminApp.api.showMatch(match.id);
  const modal = adminApp.nodes['modal-root'].innerHTML;
  assert.match(modal, /type="text"[^>]*data-game-side="a"/);
  assert.match(modal, /name="referee" value="王裁判"/);
  assert.match(modal, /双方弃权（wo:wo）除外/);
  const local = current();
  local.stages.group.data.groups[0].matches[0].games = [[null, null]];
  adminApp.api.setCurrent(local);
  adminApp.api.showMatch(match.id);
  const fields = [' WO ', ''].map(value => ({ value }));
  const row = { dataset: { gameRow: '0' }, querySelector: selector => fields[selector.includes('"a"') ? 0 : 1] };
  fields[0].matches = () => true;
  fields[0].closest = () => row;
  adminApp.api.saveGameRow(fields[0]);
  await adminApp.api.waitForGameSaves('/competitions/cup/events/single/stages/group/matches/g1-m1/games');
  assert.deepEqual(writes[1].body, { a: 'wo', b: 0 });
  assert.equal(fields[1].value, '0');
  await adminApp.api.onSubmit({ preventDefault: () => {}, target: { dataset: { form: 'match-meta' }, elements: {
    table: { value: '' }, time: { value: '' }, referee: { value: ' 场次裁判 ' }, finished: { checked: true }
  } } });
  assert.deepEqual(writes[2].body, { table: null, time: null, referee: '场次裁判', finished: true });

  let resolvePost, postCount = 0;
  let publishedState = { running: false, success: null, error: null };
  const publisher = load('admin', async (url, options) => {
    assert.equal(url, '/api/submit');
    if (options.method === 'POST') {
      postCount++;
      return new Promise(resolve => { resolvePost = resolve; });
    }
    return response(publishedState);
  });
  publisher.api.shell('Admin');
  publisher.windowListeners.load();
  await settle();
  assert.equal(publisher.nodes['modal-root'].innerHTML, '');
  const publishing = publisher.api.submitChanges();
  assert.equal(publisher.publishButton.disabled, true);
  await publisher.api.submitChanges();
  assert.equal(postCount, 1);
  publisher.api.shell('Rerender while submitting');
  assert.match(publisher.nodes.app.innerHTML, /aria-busy="true"/);
  resolvePost(response({ running: true, success: null, error: null }));
  await publishing;
  publishedState = { running: false, success: true, error: null };
  const timer = publisher.timers.find(item => item.active);
  timer.callback();
  await settle();
  assert.equal(publisher.publishButton.disabled, false);
  assert.equal(publisher.nodes['modal-root'].innerHTML, '');
  const failing = publisher.api.submitChanges();
  resolvePost(response({ running: true, success: null, error: null }));
  await failing;
  publishedState = { running: false, success: false, error: 'git push failed <remote>' };
  await publisher.api.pollSubmit(publisher.api.getSubmitSerial(), true);
  assert.equal(publisher.publishButton.disabled, false);
  assert.match(publisher.nodes['modal-root'].innerHTML, /提交失败/);
  assert.match(publisher.nodes['modal-root'].innerHTML, /git push failed &lt;remote&gt;/);

  let statusFailure = false;
  let reconciledState = { running: true, success: null, error: null };
  const recovery = load('admin', async (url, options) => {
    if (options.method === 'POST') throw new Error('Response lost');
    if (statusFailure) throw new Error('Temporary status read failure');
    return response(reconciledState);
  });
  recovery.api.shell('Recovery');
  await recovery.api.submitChanges();
  await settle();
  assert.equal(recovery.publishButton.disabled, true, 'POST uncertainty must reconcile server running state');
  assert.match(recovery.nodes['modal-root'].innerHTML, /Response lost/);
  statusFailure = true;
  const retry = recovery.timers.find(item => item.active);
  retry.callback();
  await settle();
  assert.equal(recovery.publishButton.disabled, true, 'A failed poll must not enable Submit while the job is unconfirmed');
  statusFailure = false;
  reconciledState = { running: false, success: true, error: null };
  await recovery.api.pollSubmit(recovery.api.getSubmitSerial(), true);
  assert.equal(recovery.publishButton.disabled, false);

  let resolveInitialRead;
  const racing = load('admin', async (url, options) => options.method === 'POST' ?
    response({ running: true, success: null, error: null }) : new Promise(resolve => { resolveInitialRead = resolve; }));
  racing.api.shell('Race');
  racing.windowListeners.load();
  await racing.api.submitChanges();
  resolveInitialRead(response({ running: false, success: null, error: null }));
  await settle();
  assert.equal(racing.publishButton.disabled, true, 'Stale initial status must not overwrite a later submission');
  console.log('Revision 6 frontend checks passed.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
