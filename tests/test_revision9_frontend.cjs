// Public hash routes and round-robin schedule controls in the shipped client.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const assetPath = path.join(__dirname, '..', 'webpage', 'app.js');
const source = fs.readFileSync(assetPath, 'utf8');
const instrumented = source.replace(/\s+void render\(\);\s*\}\)\(\);\s*$/, `
  globalThis.appForTest = { route, routeCompetition, routeEvent, renderRoundRobinStage,
    showRoundRobinSchedule, showMatch, onSubmit, setCurrent: value => { current = value; } };
})();`);
assert.notEqual(instrumented, source, 'Could not locate application startup');

const player = name => ({ kind: 'player', members: [name], team: 'Team 1' });
const participants = [player('张三'), player('李四')];
const match = { id: 'm1', a: 0, b: 1, games: [], finished: false, time: '13:00', table: '2', referee: '王裁判' };
const stage = { metadata: { entrants: participants.map(participant => ({ participant })) }, data: {
  participants, matches: [match], scheduled_time: '上午', scheduled_table: '1', scheduled_referee: '李裁判'
} };
const eventData = { competition: { slug: 'xsb26', name: '新生杯' },
  event: { slug: 'women-double', name: '女子双打', schedule: 'round-robin', format: 'double', stages: ['round_robin'] },
  stages: { round_robin: stage } };

function load(mode) {
  const requests = [], redirects = [];
  const nodes = Object.fromEntries(['app', 'modal-root', 'toast-root'].map(id => [id, {
    innerHTML: '', querySelector: () => null, querySelectorAll: () => [], append: () => {},
    get firstChild() { return this.innerHTML ? {} : null; }
  }]));
  const browser = {
    URLSearchParams, location: { hash: '#/' },
    history: { replaceState: (_state, _title, url) => { redirects.push(url); browser.location.hash = url; } },
    fetch: async (url, options = {}) => {
      requests.push({ url, ...options });
      return { ok: true, json: async () => options.method === 'PATCH' ? {} : eventData };
    },
    setTimeout: () => 0, clearTimeout: () => {},
    document: { body: { dataset: { mode }, style: {} }, title: '',
      getElementById: id => nodes[id], addEventListener: () => {},
      createElement: () => ({ remove: () => {} }) },
    window: { addEventListener: () => {} }
  };
  vm.runInNewContext(instrumented, browser, { filename: assetPath });
  return { api: browser.appForTest, browser, nodes, requests, redirects };
}

async function main() {
  const app = load('admin');
  assert.equal(app.api.routeCompetition('xsb26'), '#/xsb26');
  assert.equal(app.api.routeEvent('xsb26', 'women-double', 'group'), '#/xsb26/women-double?stage=group');
  app.browser.location.hash = '#/xsb26/women-double';
  assert.equal(JSON.stringify(app.api.route()), JSON.stringify({ page: 'event', slug: 'xsb26', eventSlug: 'women-double', stage: null }));

  app.browser.location.hash = '#/competitions/xsb26/events/xsb26-women-double?stage=round_robin';
  assert.equal(JSON.stringify(app.api.route()), JSON.stringify({ page: 'event', slug: 'xsb26', eventSlug: 'women-double', stage: 'round_robin' }));
  assert.equal(app.redirects.at(-1), '#/xsb26/women-double?stage=round_robin');
  app.browser.location.hash = '#/competitions/xsb26';
  assert.equal(app.api.route().slug, 'xsb26');
  assert.equal(app.redirects.at(-1), '#/xsb26');

  app.api.setCurrent({ page: 'event', ...eventData, stage: 'round_robin' });
  const html = app.api.renderRoundRobinStage(stage);
  for (const text of ['预定时间：上午', '预定桌号：1', '预定裁判：李裁判']) assert.ok(html.includes(text));
  assert.match(html, /data-action="edit-round-robin-schedule"/);
  assert.match(html, /data-match-id="m1"/);
  app.api.showRoundRobinSchedule();
  assert.match(app.nodes['modal-root'].innerHTML, /name="scheduled_time" value="上午"/);
  assert.match(app.nodes['modal-root'].innerHTML, /name="scheduled_table" value="1"/);
  assert.match(app.nodes['modal-root'].innerHTML, /覆盖到循环赛所有场次/);
  app.api.showMatch('m1');
  assert.match(app.nodes['modal-root'].innerHTML, /name="time" value="13:00"/);
  assert.match(app.nodes['modal-root'].innerHTML, /name="table" value="2"/);

  app.api.showRoundRobinSchedule();
  app.browser.location.hash = '#/xsb26/women-double';
  const form = { dataset: { form: 'round-robin-schedule' }, elements: {
    scheduled_time: { value: ' 14:30 ' }, scheduled_table: { value: ' 5 ' }, scheduled_referee: { value: ' 陈裁判 ' }
  } };
  await app.api.onSubmit({ target: form, preventDefault() {}, submitter: null });
  const patch = app.requests.find(request => request.method === 'PATCH');
  assert.equal(patch.url, '/api/competitions/xsb26/events/women-double/round-robin/schedule');
  assert.deepEqual(JSON.parse(patch.body), { scheduled_time: '14:30', scheduled_table: '5', scheduled_referee: '陈裁判' });
  assert.ok(!app.requests.some(request => request.url.includes('/submit')));

  const publicApp = load('public');
  publicApp.api.setCurrent({ page: 'event', ...eventData, stage: 'round_robin' });
  assert.doesNotMatch(publicApp.api.renderRoundRobinStage(stage), /edit-round-robin-schedule/);
  console.log('Revision 9 frontend checks passed.');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
