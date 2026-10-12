# `tests/test_revision6_frontend.cjs`

Runs the actual `webpage/app.js` in a Node VM with a small browser fixture and mocked local API. Checks one-sided WO (including numeric 0), double WO, score parsing, preserved standalone ranking, mirrored matrix scores, server-supplied group ranking rather than client reranking, conditional tie-break columns, gray unused cells, qualifier highlighting, completed-group gating, referee forms and PATCH payloads, numbered knockout leaves including byes/undecided slots, and double-WO knockout advancement to a bye.

Exercises the automatic per-game save queue with uppercase WO and a blank opposing input, verifying its canonical `wo`/0 payload. Also verifies explicit-button-only dialog closing and keyboard focus wrapping. Submit checks cover admin-only visibility, initial idle state, prevention of repeated POSTs while running, rerendered busy state, completion polling, silent success and escaped failure warnings. Network failure checks confirm that a lost POST response is reconciled, failed polls keep an unconfirmed job disabled, and a stale initial status read cannot overwrite a newer submission. All requests are mocked; the suite never invokes Git or pushes the real repository.

Run from the project root with `node tests/test_revision6_frontend.cjs`.
