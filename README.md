# Where does your data live? - 30-min Data Architecture intro

Interactive [reveal.js](https://github.com/hakimel/reveal.js) deck (v6, vendored - works offline) with a **live lab** on four real engines.
Built from `Course/DataArchitecture - Course outline.pdf`: the intro maps the whole 24-session course, the deep dive is **Session 2 - file formats & storage strategy** (row vs columnar layout).

## Two ways to view it

| | Live (docker) | Static (GitHub Pages / any web host) |
|---|---|---|
| Slides, diagrams, quiz | yes | yes |
| Demo lab | real queries on Redis, Postgres, DuckDB, S3 | **replays recorded results** (`deck/snapshot.json`); editing a query shows "needs the live lab" |
| Needs | Docker, ~3 GB RAM | nothing |

The deck detects the mode on load (it probes `api/status`; no answer = replay the recording) and the status pill says which. If you start docker after opening the deck, reload the page. Use live mode when teaching; use the hosted copy for sharing with students.

## Run it (live)

```bash
docker compose up --build        # first run ~2 min; seeds 5M orders in ~20 s
open http://localhost:8000       # the deck + lab API (same origin)
```

Press **S** for speaker view (timings are in the notes), **Esc** for the overview, **Alt+click** to zoom into a diagram.
Bucket console for showing the Parquet folders live: <http://localhost:9001> (`minioadmin` / `minioadmin`).
Reset everything: `docker compose down -v`. Edit `deck/*` and just refresh - it is bind-mounted.

## Publish on GitHub Pages

Everything the static copy needs is already here (relative URLs, vendored reveal.js, `.nojekyll`, a root `index.html` that redirects to `deck/`).

1. Push this folder as its own repository (`git init && git add -A && git commit && git remote add origin <url> && git push -u origin main`).
2. Repo **Settings > Pages > Build and deployment > Source: Deploy from a branch**, branch `main`, folder `/ (root)`.
3. After a minute: `https://<user>.github.io/<repo>/` (deck) and `.../student-notes.html` (notes).

Test it locally first: `python3 -m http.server 8080` in this folder (with docker stopped), open <http://localhost:8080/> - you should see "recorded results" in the status pill and a working demo.

**Refreshing the recording** (after changing data/engines): with the stack up, run `python3 tools/record_snapshot.py` (it calls `/api/run` for each default query of each lane and saves the results next to `/api/meta` + `/api/footprint` into `deck/snapshot.json`). The timings are from one laptop - say so when you show them.

## Run of show (30 min, 12 slides)

| Min | Section | Format |
|---|---|---|
| 0-1 | Title, agenda | static |
| 1-4 | The map (whole architecture, S1-S24) | static diagram |
| 4-7 | Meet the four stores (Redis, Postgres, DuckDB, S3) | static infographic |
| 7-10 | Speed-vs-cost ladder, then "which store?" scenarios | static, answers revealed by click |
| 10-13 | **Deep dive (Session 2)**: row vs columnar layout | static diagram |
| 13-25 | **Live demo**: footprint chart, 6-workload race | **interactive** (the only interactive section) |
| 25-26 | Cheat-sheet | static |
| 26-30 | Quiz (6 questions, keys 1-4) | interactive |

Four slides (Parquet anatomy, pushdown, partitioning/formats, Parquet X-ray) are kept in `deck/index.html` with `data-visibility="hidden"` - delete that attribute to bring them back for a longer session.

Student notes: `student-notes.html` (source of the shared Google Doc).

## What the lab runs

Same synthetic `orders` table (5,000,000 rows, 13 columns, 2024-2025) in:

| Lane | Engine | Layout |
|---|---|---|
| Redis | redis:7 | hot last 300k orders as hashes + hand-built `cust:<id>` index + a precomputed aggregate |
| Postgres | postgres:16 | row store, PK + `customer_id` index |
| DuckDB (OLAP) | embedded in the API | native columnar file, same SQL |
| S3 + Parquet | RustFS (S3-compatible) | zstd Parquet, queried in place by DuckDB; also hive-partitioned by year/month |

Every SQL lane takes the same SQL text and is editable live (read-only `SELECT` only). Typical local numbers: Postgres revenue-by-category ~270 ms vs DuckDB ~3 ms; Postgres table 1.29 GB vs zstd Parquet 116 MB.

## Honest caveats (say them out loud)

- S3 is **local** here: real S3 adds ~20-100 ms per request, so the S3 lane is flattered on latency. DuckDB's file cache is disabled so every run does hit the object store.
- The point-lookup gap on Parquet is small locally (~2x) because fixed HTTP overhead dominates; the mechanism (min/max skipping on sorted data) is what matters.
- Redis holds only the hot 300k rows, so its by-customer and ad-hoc answers are partial; that is the point of the "cache coverage" lesson.
- MinIO stopped publishing public Docker images, so the compose file uses RustFS, an S3-compatible server. Swap `image:` back if you have a MinIO image available.

## Layout

```
index.html           redirect to deck/ (for GitHub Pages)
docker-compose.yml   redis + postgres + S3 + api
api/                 FastAPI app (app.py), seeding (seed.py), config (common.py)
deck/                index.html, app.js, deck.css, snapshot.json (recorded lab results), vendor/reveal (reveal.js 6.0.2 dist, MIT)
tools/record_snapshot.py  re-records deck/snapshot.json from the live stack
student-notes.html   handout for students
```
