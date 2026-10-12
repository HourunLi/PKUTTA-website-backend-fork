// Exercise the ranking function as shipped in the browser bundle.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'webpage', 'app.js'), 'utf8');
const instrumented = source.replace(/\s+void render\(\);\s*\}\)\(\);\s*$/, '\n  globalThis.rankForTest = rankRoundRobin;\n})();');
assert.notEqual(instrumented, source, 'Could not locate application startup for ranking test');
const browser = {
  document: {
    body: { dataset: { mode: 'public' } },
    getElementById: () => null,
    addEventListener: () => {}
  },
  window: { addEventListener: () => {} }
};
vm.runInNewContext(instrumented, browser, { filename: 'webpage/app.js' });
const rank = (participants, matches) => {
  const result = browser.rankForTest(participants, matches);
  return result === null ? null : Array.from(result);
};
const players = (...names) => names.map(name => ({ kind: 'player', members: [name], team: '' }));
const match = (a, b, ...games) => ({ a, b, finished: true, games });
const games = (a, b, count) => Array.from({ length: count }, () => [a, b]);

assert.equal(rank(players('A', 'B'), [
  { ...match(0, 1, [11, 5]), finished: false }
]), null);

assert.deepEqual(rank(players('甲', '乙', '丙'), [
  match(0, 1, [11, 9], [9, 11], [11, 9]),
  match(0, 2, [8, 11]),
  match(1, 2, ...games(11, 5, 3))
]), [2, 1, 3]); // Won games break a match-win tie.

assert.deepEqual(rank(players('Z', 'Zed', 'Able'), [
  match(0, 1, [11, 0]),
  match(0, 2, [5, 11]),
  match(1, 2, [11, 0])
]), [1, 2, 3]); // Losing-game points count; B beats C on mutual wins.

assert.deepEqual(rank(players('甲', '乙', '丙', '丁'), [
  match(0, 1, ...games(11, 0, 3), ...games(0, 11, 2)),
  match(0, 2, [0, 11]),
  match(1, 2, ...games(11, 0, 2), [0, 11]),
  match(0, 3, ...games(11, 0, 2)),
  match(1, 3, [11, 0]),
  match(2, 3, ...games(11, 0, 3))
]), [2, 1, 3, 4]); // Overall exact tie; only mutual games decide.

assert.deepEqual(rank(players('Zoe', 'Amy', 'Kai'), [
  match(0, 1, [11, 5], [999, null]),
  match(0, 2, [5, 11]),
  match(1, 2, [11, 5])
]), [3, 1, 2]); // Unentered game is ignored; a three-way tie uses name.

console.log('Frontend round-robin ranking checks passed.');
