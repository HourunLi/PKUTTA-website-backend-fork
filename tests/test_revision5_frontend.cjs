// Exercise the shared application bundle with the real render and request helpers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const assetPath = path.join(__dirname, '..', 'webpage', 'app.js');
const source = fs.readFileSync(assetPath, 'utf8');
const instrumented = source.replace(/\s+void render\(\);\s*\}\)\(\);\s*$/, `
  globalThis.appForTest = { getJson, renderRoundRobinTable, renderGroupStage, renderKnockoutStage,
    showGroupSchedule, showMatch, onSubmit, setCurrent: value => { current = value; } };
})();`);
assert.notEqual(instrumented, source, 'Could not locate application startup');

function load(mode, fetch) {
  const nodes = Object.fromEntries(['app', 'modal-root', 'toast-root'].map(id => [id, {
    innerHTML: '', firstChild: null, querySelector: () => null, querySelectorAll: () => [], append: () => {}
  }]));
  const browser = {
    fetch, URLSearchParams, location: { hash: '#/' }, setTimeout: () => {},
    document: {
      body: { dataset: { mode }, style: {} }, getElementById: id => nodes[id],
      addEventListener: () => {}, createElement: () => ({ remove: () => {} })
    },
    window: { addEventListener: () => {} }
  };
  vm.runInNewContext(instrumented, browser, { filename: assetPath });
  return { api: browser.appForTest, nodes };
}
const clone = value => JSON.parse(JSON.stringify(value));
const person = name => ({ kind: 'player', members: [name], team: '' });
const participants = [person('张3'), person('李4')];
const match = { id: 'g1-m1', a: 0, b: 1, finished: true, games: [[11, 4], [11, 5], [6, 11]] };
const group = { id: 1, qualifiers: 1, participants, matches: [match], scheduled_time: '14:30', scheduled_table: '3' };
const eventData = {
  competition: { slug: 'cup', name: '校赛' },
  event: { slug: 'single', name: '男子单打', schedule: 'group-knockout', format: 'single' },
  stages: { group: { metadata: {}, data: { drawn: true, groups: [group] } }, knockout: { metadata: {}, data: {} } }
};

async function main() {
  const publicFiles = {
    './database/index.json': { competitions: ['杯赛'] },
    './database/%E6%9D%AF%E8%B5%9B/meta.json': { name: '杯赛', events: ['男子 单打'] },
    './database/%E6%9D%AF%E8%B5%9B/%E7%94%B7%E5%AD%90%20%E5%8D%95%E6%89%93/meta.json': {
      name: '男子单打', schedule: 'group-knockout', stages: ['group', 'knockout']
    }
  };
  for (const stage of ['group', 'knockout']) {
    const prefix = './database/%E6%9D%AF%E8%B5%9B/%E7%94%B7%E5%AD%90%20%E5%8D%95%E6%89%93/' + stage;
    publicFiles[prefix + '/metadata.json'] = { type: stage };
    publicFiles[prefix + '/data.json'] = stage === 'group' ? eventData.stages.group.data : {};
  }
  const reads = [];
  const publicApp = load('public', async (url, options) => {
    assert.equal(options.cache, 'no-store');
    assert.ok(Object.hasOwn(publicFiles, url), `Unexpected public data path: ${url}`);
    reads.push(url);
    return { ok: true, json: async () => clone(publicFiles[url]) };
  });
  const { api } = publicApp;
  const home = await api.getJson('/competitions');
  assert.equal(home.competitions[0].slug, '杯赛');
  const competition = await api.getJson('/competitions/%E6%9D%AF%E8%B5%9B');
  assert.equal(competition.events[0].slug, '男子 单打');
  const event = await api.getJson('/competitions/%E6%9D%AF%E8%B5%9B/events/%E7%94%B7%E5%AD%90%20%E5%8D%95%E6%89%93');
  assert.deepEqual(Object.keys(event.stages), ['group', 'knockout']);
  assert.equal(event.stages.group.data.groups[0].scheduled_time, '14:30');
  assert.equal(reads.filter(url => url.endsWith('/index.json') && url !== './database/index.json').length, 0);

  api.setCurrent({ ...eventData, page: 'event', stage: 'group' });
  for (const qualifiers of [0, 1]) {
    const html = api.renderRoundRobinTable(participants, [match], qualifiers);
    assert.equal((html.match(/data-match-id="g1-m1"/g) || []).length, 2);
    assert.equal((html.match(/<td class="match-cell finished">/g) || []).length, 2);
    assert.match(html, />2:1<\/button>/);
    assert.match(html, />1:2<\/button>/);
    const matrix = html.split('class="data-table matrix-table"')[1];
    assert.doesNotMatch(matrix, /data-rank-column|排名/);
    assert.equal((matrix.match(/<tr>/g) || []).length, participants.length + 1);
    const changed = api.renderRoundRobinTable(participants, [{ ...match, games: [[2, 11]] }], qualifiers);
    assert.match(changed, />0:1<\/button>/);
    assert.match(changed, />1:0<\/button>/);
  }
  const groupHtml = api.renderGroupStage(eventData.stages.group);
  assert.match(groupHtml, /预定时间：14:30/);
  assert.match(groupHtml, /预定桌号：3/);
  assert.match(groupHtml, /<th scope="col">选手<\/th>/);
  assert.doesNotMatch(groupHtml, /edit-group-schedule|姓名\/名称/);
  const resultsHtml = api.renderKnockoutStage({ data: { rounds: 1, results: [{ rank: '冠军', participant: participants[0] }] } });
  assert.match(resultsHtml, /<th scope="col">选手<\/th>/);
  assert.doesNotMatch(resultsHtml, /<th scope="col">姓名<\/th>/);

  const partialMatch = { ...match, games: [[11, 4], [999, null]], finished: false };
  api.setCurrent({ ...eventData, stage: 'group', stages: { group: { data: { groups: [{ ...group, matches: [partialMatch] }] } } } });
  api.showMatch(match.id);
  assert.doesNotMatch(publicApp.nodes['modal-root'].innerHTML, /999|第 2 局/);

  const writes = [];
  const adminApp = load('admin', async (url, options) => {
    if (options.method === 'PATCH') {
      writes.push({ url, body: JSON.parse(options.body) });
      return { ok: true, json: async () => ({}) };
    }
    assert.equal(url, '/api/competitions/cup/events/single');
    return { ok: true, json: async () => clone(eventData) };
  });
  adminApp.api.setCurrent({ ...eventData, page: 'event', stage: 'group' });
  assert.match(adminApp.api.renderGroupStage(eventData.stages.group), /data-action="edit-group-schedule" data-group-id="1"/);
  adminApp.api.showGroupSchedule('1');
  const scheduleModal = adminApp.nodes['modal-root'].innerHTML;
  assert.match(scheduleModal, /name="scheduled_time" value="14:30"/);
  assert.match(scheduleModal, /name="scheduled_table" value="3"/);
  assert.match(scheduleModal, /覆盖到本组所有场次/);
  await adminApp.api.onSubmit({
    preventDefault: () => {}, submitter: { disabled: false, isConnected: false },
    target: { dataset: { form: 'group-schedule' }, elements: {
      scheduled_time: { value: ' 15:40 ' }, scheduled_table: { value: ' ' }, scheduled_referee: { value: '' }
    } }
  });
  assert.deepEqual(writes, [{ url: '/api/competitions/cup/events/single/groups/1', body: { scheduled_time: '15:40', scheduled_table: '', scheduled_referee: '' } }]);
  console.log('Revision 5 frontend checks passed.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
