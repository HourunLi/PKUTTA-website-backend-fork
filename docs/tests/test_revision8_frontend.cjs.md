# `tests/test_revision8_frontend.cjs`

Runs the actual delivered `webpage/app.js` in Node's `vm` in both public and admin modes. Checks that completed standalone round-robin stages use server standings for the third ranking table and final result order, display conditional ranking criteria, omit roster ranks, and suppress stale traces/results when a match becomes unfinished. Server order deliberately differs from the scores to detect accidental legacy client reranking.

Verifies group qualifier results and knockout/round-robin final results have clickable centered participant cells; final rank cells use gold/silver/bronze classes while group qualifiers do not. Result and registration `details` state persists through rerenders. The first stage alone shows the default-collapsed 参赛名单 card, with comma-separated names, college details and an icon-only download button.

Exercises the delegated download click, cancellation of summary toggling, Blob MIME type, actual UTF-8 BOM bytes, temporary object-URL cleanup and event-slug filename. Export on a later stage still uses the first stage's original order and seeds. An independent CSV state-machine parser verifies single, double and variable-size team formats, standard headers, Chinese names, legitimate seed -1, excluded byes, commas, quotes and embedded line breaks.

Run: `node tests/test_revision8_frontend.cjs`.
