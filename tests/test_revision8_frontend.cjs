// Exercise the shipped client: server rankings, final results, registration and CSV downloads.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assetPath = path.join(__dirname, '..', 'webpage', 'app.js');
const source = fs.readFileSync(assetPath, 'utf8');
const instrumented = source.replace(/\s+void render\(\);\s*\}\)\(\);\s*$/, `
  globalThis.appForTest = { renderRoundRobinStage, renderGroupStage, renderKnockoutStage,
    renderEvent, participantCsv, renderParticipantList, showParticipant,
    setCurrent: value => { current = value; } };
})();`);
assert.notEqual(instrumented, source, 'Could not locate application startup');
const clone = value => JSON.parse(JSON.stringify(value));
const person = (name, team = '学院甲', kind = 'player') => ({ kind, members: Array.isArray(name) ? name : [name], team });
const players = [person('张3'), person('李4'), person('王5')];
const matches = [
  { id: 'm1', a: 0, b: 1, games: [[11, 5]], finished: true },
  { id: 'm2', a: 0, b: 2, games: [[11, 5]], finished: true },
  { id: 'm3', a: 1, b: 2, games: [[11, 5]], finished: true }
];
// Deliberately disagrees with score-derived order, exposing accidental client reranking.
const standings = { order: [2, 0, 1], rows: [
  { index: 0, rank: 2, points: 2, tied_points: 1, game_ratio: { won: 2, lost: 1 }, point_ratio: null, draw_rank: 2 },
  { index: 2, rank: 1, points: 2, tied_points: 1, game_ratio: { won: 2, lost: 1 }, point_ratio: { won: 22, lost: 0 }, draw_rank: 1 },
  { index: 1, rank: 3, points: 0, tied_points: null, game_ratio: null, point_ratio: null, draw_rank: null }
] };
const stage = { metadata: { entrants: players.map((participant, index) => ({ participant, seed: 7 - index })) },
  data: { participants: players, matches, standings } };
const event = { slug: 'single', name: '男子单打', schedule: 'round-robin', format: 'single', stages: ['round_robin'] };
const current = (changes = {}) => ({ page: 'event', competition: { slug: 'cup', name: '校赛' },
  event, stages: { round_robin: clone(stage) }, ...changes });

function load(mode = 'public') {
  const listeners = {}, timers = [], downloads = [], blobs = [], revoked = [];
  const nodes = Object.fromEntries(['app', 'modal-root', 'toast-root'].map(id => [id, {
    innerHTML: '', querySelector: () => null, querySelectorAll: () => [], append: () => {},
    get firstChild() { return this.innerHTML ? {} : null; }
  }]));
  const browser = {
    Blob, URL: { createObjectURL: blob => { blobs.push(blob); return `blob:${blobs.length}`; }, revokeObjectURL: url => revoked.push(url) },
    URLSearchParams, location: { hash: '#/' },
    setTimeout: callback => { timers.push(callback); return timers.length - 1; }, clearTimeout: () => {},
    document: { body: { dataset: { mode }, style: {}, append: link => { link.attached = true; } },
      getElementById: id => nodes[id], addEventListener: (name, callback) => { listeners[name] = callback; },
      createElement: tag => ({ tag, click() { downloads.push({ href: this.href, filename: this.download, attached: this.attached }); }, remove() { this.removed = true; } }) },
    window: { addEventListener: () => {} }
  };
  vm.runInNewContext(instrumented, browser, { filename: assetPath });
  return { api: browser.appForTest, nodes, listeners, timers, downloads, blobs, revoked };
}

// Independent RFC-style parser checks commas, quotes, CRLF and embedded line breaks.
function parseCsv(text) {
  const rows = []; let row = [], field = '', quoted = false;
  text = text.replace(/^\uFEFF/, '');
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (char === '"') quoted = false;
      else field += char;
    } else if (char === '"') quoted = true;
    else if (char === ',') { row.push(field); field = ''; }
    else if (char === '\r' || char === '\n') {
      if (char === '\r' && text[i + 1] === '\n') i++;
      row.push(field); rows.push(row); row = []; field = '';
    } else field += char;
  }
  assert.equal(quoted, false, 'CSV quoting must balance');
  if (row.length || field) { row.push(field); rows.push(row); }
  return rows;
}
const clicks = (html, name) => (html.match(new RegExp(name, 'g')) || []).length;
const resultRows = html => html.split('results-table')[1].split('</table>')[0];

async function main() {
  for (const mode of ['public', 'admin']) {
    const app = load(mode), { api } = app;
    api.setCurrent(current());
    const html = api.renderRoundRobinStage(stage);
    assert.equal(clicks(html, '<table '), 4, 'Completed RR shows roster, matrix, trace and final results');
    const roster = html.split('class="data-table roster-table"')[1].split('</table>')[0];
    assert.doesNotMatch(roster, /排名|data-rank-column|toggle-rank/);
    const trace = html.split('group-standings-table')[1].split('</table>')[0];
    assert.ok(trace.indexOf('王5') < trace.indexOf('张3'));
    assert.ok(trace.indexOf('张3') < trace.indexOf('李4'));
    for (const title of ['积分', '同分内部积分', '局胜负比', '球分胜负比', '抽签名次']) assert.match(trace, new RegExp(title));
    assert.match(trace, /class="unused-metric"/);
    const results = resultRows(html);
    assert.ok(results.indexOf('王5') < results.indexOf('张3'));
    assert.ok(results.indexOf('张3') < results.indexOf('李4'));
    for (const medal of ['gold', 'silver', 'bronze']) assert.match(results, new RegExp(`class="medal-${medal}"`));
    assert.equal(clicks(results, 'data-action="participant"'), 3);
    assert.equal(clicks(results, 'class="result-person"'), 3);
    assert.match(html, /<details class="panel ranking-panel round-robin-results"[^>]* open>/);
    const unfinished = clone(stage); unfinished.data.matches[0].finished = false;
    const partialHtml = api.renderRoundRobinStage(unfinished);
    assert.equal(clicks(partialHtml, '<table '), 2, 'An unfinished match suppresses stale server standings/results');
    assert.match(partialHtml, /two-table-layout/);
    assert.doesNotMatch(partialHtml, /比赛结果|group-standings-table/);
    const noTrace = clone(stage); noTrace.data.standings = null;
    assert.equal(clicks(api.renderRoundRobinStage(noTrace), '<table '), 2, 'No local legacy ranking fallback');

    const groupStage = { metadata: clone(stage.metadata), data: { drawn: true, groups: [{ id: 1, participants: players, matches, standings, qualifiers: 2 }] } };
    const groupHtml = api.renderGroupStage(groupStage);
    assert.match(groupHtml, /result-table qualifier-table/);
    assert.doesNotMatch(groupHtml, /medal-gold|medal-silver|medal-bronze/);
    const qualifiers = groupHtml.split('qualifier-table')[1].split('</table>')[0];
    assert.equal(clicks(qualifiers, 'data-action="participant"'), 2);
    assert.equal(clicks(qualifiers, 'class="result-person"'), 2);
    const knockout = { metadata: { entrants: [{ participant: players[2], seed: 99 }] }, data: { rounds: 2,
      results: [1, 2, 3].map((rank, index) => ({ rank, participant: players[index] })) } };
    const knockoutHtml = api.renderKnockoutStage(knockout);
    assert.equal(clicks(resultRows(knockoutHtml), 'data-action="participant"'), 3);
    for (const medal of ['gold', 'silver', 'bronze']) assert.match(knockoutHtml, new RegExp(`class="medal-${medal}"`));

    api.renderEvent();
    assert.equal(clicks(app.nodes.app.innerHTML, 'participant-list-panel'), 1);
    assert.match(app.nodes.app.innerHTML, /<details class="panel ranking-panel participant-list-panel"[^>]*>\s*<summary/);
    const listTag = app.nodes.app.innerHTML.match(/<details class="panel ranking-panel participant-list-panel"[^>]*>/)[0];
    assert.doesNotMatch(listTag, / open[ >]/, 'Registration defaults to collapsed');
    const list = api.renderParticipantList(stage);
    assert.match(list, /参赛名单/);
    assert.equal(clicks(list, 'data-action="participant"'), 3);
    assert.match(list, /<\/button>，<button/);
    assert.match(list, /aria-label="下载参赛名单 CSV"[^>]*><svg/);
    assert.doesNotMatch(list, /<table/);
    app.listeners.toggle({ target: { matches: () => true, dataset: { rankingId: 'cup-single-participants' }, open: true } });
    assert.match(api.renderParticipantList(stage), /data-ranking-id="cup-single-participants" open>/);
    app.listeners.toggle({ target: { matches: () => true, dataset: { rankingId: 'cup-single-round-robin-results' }, open: false } });
    assert.doesNotMatch(api.renderRoundRobinStage(stage).match(/<details[^>]+>/)[0], / open[ >]/);
    api.showParticipant(players[0]);
    assert.match(app.nodes['modal-root'].innerHTML, /<dt>学院<\/dt><dd>学院甲/);
    api.showParticipant(person(['成员甲', '成员乙'], '一队', 'team'));
    assert.match(app.nodes['modal-root'].innerHTML, /团体成员[^]*成员甲、成员乙/);

    api.setCurrent(current({ event: { ...event, schedule: 'group-knockout', stages: ['group', 'knockout'] },
      stages: { group: groupStage, knockout }, requestedStage: 'group' }));
    api.renderEvent();
    assert.equal(clicks(app.nodes.app.innerHTML, 'participant-list-panel'), 1);
    const groupCurrent = current({ event: { ...event, schedule: 'group-knockout', stages: undefined }, stages: { group: groupStage, knockout }, requestedStage: 'knockout' });
    api.setCurrent(groupCurrent); api.renderEvent();
    assert.doesNotMatch(app.nodes.app.innerHTML, /participant-list-panel/, 'Later stage does not repeat registration');

    // Export on a later stage still reads the original first-stage registrations.
    let prevented = false, stopped = false;
    app.listeners.click({ target: { closest: () => ({ dataset: { action: 'download-participants' } }) },
      preventDefault: () => { prevented = true; }, stopPropagation: () => { stopped = true; } });
    assert.equal(prevented, true, 'Cancel summary default toggle when downloading');
    assert.equal(stopped, true, 'Keep download click out of the summary toggle');
    assert.deepEqual(app.downloads, [{ href: 'blob:1', filename: 'single.csv', attached: true }]);
    assert.equal(app.blobs[0].type, 'text/csv;charset=utf-8');
    const bytes = Buffer.from(await app.blobs[0].arrayBuffer());
    assert.deepEqual([...bytes.subarray(0, 3)], [0xef, 0xbb, 0xbf], 'UTF-8 BOM survives Blob construction');
    const exported = parseCsv(bytes.toString('utf8'));
    assert.deepEqual(exported, [['选手姓名', '队伍', '种子级别'], ['张3', '学院甲', '7'], ['李4', '学院甲', '6'], ['王5', '学院甲', '5']]);
    app.timers.at(-1)(); assert.deepEqual(app.revoked, ['blob:1']);
    api.setCurrent(current({ event: { ...event, schedule: 'knockout', stages: ['knockout'] }, stages: { knockout } }));
    api.renderEvent();
    assert.equal(clicks(app.nodes.app.innerHTML, 'participant-list-panel'), 1, 'Standalone KO is its first stage');
  }

  const { api } = load();
  const odd = person('张,三"甲\n乙', '数学,学院"A\r\n楼');
  const singleCsv = api.participantCsv({ metadata: { entrants: [{ participant: odd, seed: -1 }, { participant: { kind: 'bye' }, seed: -1 }] } }, 'single');
  assert.equal(singleCsv[0], '\uFEFF');
  assert.equal(singleCsv.endsWith('\r\n'), true);
  assert.deepEqual(parseCsv(singleCsv), [['选手姓名', '队伍', '种子级别'], [odd.members[0], odd.team, '-1']]);
  const pair = person(['赵,一', '钱"二\n三'], '双打学院', 'pair');
  assert.deepEqual(parseCsv(api.participantCsv({ metadata: { entrants: [{ participant: pair, seed: 0 }] } }, 'double')),
    [['选手1', '选手2', '队伍', '种子级别'], ['赵,一', '钱"二\n三', '双打学院', '0']]);
  const team = person(['成员,甲', '成员"乙', '成员\n丙'], '团体,一队', 'team');
  assert.deepEqual(parseCsv(api.participantCsv({ metadata: { entrants: [{ participant: team, seed: 4 }, { participant: person(['丁'], '二队', 'team'), seed: 1 }] } }, 'team')),
    [['队伍', '种子级别', '成员1', '成员2', '成员3'], ['团体,一队', '4', '成员,甲', '成员"乙', '成员\n丙'], ['二队', '1', '丁']]);
  console.log('Revision 8 frontend checks passed.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
