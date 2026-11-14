/* Only the demo (lab + X-ray) and the quiz are interactive; everything else is rendered once. Plain JS, no build step. */
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const h = (tag, attrs = {}, html = '') => { const e = document.createElement(tag); Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v)); e.innerHTML = html; return e; };
const fmtMs = ms => ms < 1 ? ms.toFixed(2) + ' ms' : ms < 100 ? ms.toFixed(1) + ' ms' : ms < 1000 ? Math.round(ms) + ' ms' : (ms / 1000).toFixed(2) + ' s';
const fmtBytes = b => b >= 1e9 ? (b / 1e9).toFixed(2) + ' GB' : b >= 1e6 ? Math.round(b / 1e6) + ' MB' : Math.round(b / 1e3) + ' KB';

/* Two modes. LIVE: the docker stack answers api/* (deck served by the api container, same origin).
   STATIC: no backend (e.g. GitHub Pages) -> replay the results recorded in snapshot.json. Paths are relative on purpose
   so the deck works from a sub-path such as https://user.github.io/repo/deck/. */
let LIVE = false, STATIC = false, SNAP = null;
async function isLive() {
  if (LIVE) return true;
  if (STATIC) return false;
  try { const r = await fetch('api/status'); if (r.ok && 'ready' in await r.json()) return LIVE = true; } catch { /* no backend */ }
  try { SNAP = await (await fetch('snapshot.json')).json(); STATIC = true; } catch { /* no snapshot either */ }   // no backend: replay the recording
  return false;
}
async function api(path, body) {
  if (!(await isLive())) {
    if (!STATIC) throw new Error('lab offline');
    if (path === 'api/status') return { ready: true, step: 'ready', error: null };
    if (path === 'api/meta') return SNAP.meta;
    if (path === 'api/footprint') return SNAP.footprint;
    const rec = SNAP.runs[`${body.lane}|${body.workload}`];
    if (!rec || body.text !== rec.text)
      return { error: 'Static copy: editing queries needs the live lab (docker compose up).' };
    return rec.res;
  }
  const r = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}

const STORES = {
  redis: { name: 'Redis', css: 'redis', color: 'var(--redis)' },
  postgres: { name: 'Postgres', css: 'pg', color: 'var(--pg)' },
  duckdb: { name: 'DuckDB (OLAP)', css: 'duck', color: 'var(--duck)' },
  s3: { name: 'S3 + Parquet', css: 's3', color: 'var(--s3)' },
};
const LANES = Object.keys(STORES);
let META = null;

/* ───────────── status pill ───────────── */
async function pollStatus() {
  const pill = $('#pill');
  try {
    const s = await api('api/status');
    if (s.error) { pill.className = 'err'; pill.textContent = 'lab: ' + s.error.slice(0, 60); setTimeout(pollStatus, 4000); return; }
    if (s.ready) {
      META = await api('api/meta');
      pill.className = 'ok'; pill.textContent = STATIC ? `● recorded results · ${META.rows.toLocaleString()} orders · run docker compose up for the live lab` : `● lab online · ${META.rows.toLocaleString()} orders in 4 engines`;
      return;
    }
    pill.className = ''; pill.textContent = 'lab: seeding… ' + s.step;
  } catch { pill.className = 'err'; pill.textContent = 'lab offline: run docker compose up'; }
  setTimeout(pollStatus, 2500);
}

/* ───────────── 3 · storage ladder ───────────── */
function initLadder() {
  const rows = [
    { l: 'redis', lat: 0.2, cost: 10000, latL: '~0.2 ms', costL: '~$10,000 / TB-mo', best: 'sessions · caches · counters · leaderboards' },
    { l: 'postgres', lat: 2, cost: 110, latL: '~2 ms', costL: '~$110 / TB-mo', best: 'transactions · lookups by key · source of truth' },
    { l: 'duckdb', lat: 10, cost: 80, latL: '~10 ms (indexed/sorted)', costL: '~$80 / TB-mo + compute', best: 'aggregations · ad-hoc analytics' },
    { l: 's3', lat: 80, cost: 23, latL: '~80 ms first byte', costL: '~$23 / TB-mo', best: 'lakes · archives · everything cold' },
  ];
  const lg = (v, lo, hi) => Math.max(6, 100 * (Math.log10(v) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo)));
  const t = $('#ladder');
  t.appendChild(h('tr', {}, '<td></td><td class="tiny mut">SIMPLE-READ LATENCY (log)</td><td class="tiny mut">STORAGE COST (log)</td><td class="tiny mut">BEST AT</td>'));
  rows.forEach(r => {
    const tr = h('tr', {}, `<td class="name" style="color:${STORES[r.l].color}">${STORES[r.l].name}</td>
      <td style="width:26%"><div class="bar bg-${STORES[r.l].css}" style="--w:${lg(r.lat, 0.1, 200)}%"></div><div class="val">${r.latL}</div></td>
      <td style="width:26%"><div class="bar bg-${STORES[r.l].css}" style="--w:${lg(r.cost, 10, 20000)}%"></div><div class="val">${r.costL}</div></td>
      <td>${r.best}</td>`);
    t.appendChild(tr);
  });
}

/* ───────────── static diagrams (rendered once, no interaction) ───────────── */
function initLayoutStatic() {
  const cols = ['order_id', 'customer', 'category', 'amount', 'status'];
  const data = [[1, 'C17', 'Toys', 49, 'delivered'], [2, 'C03', 'Home', 120, 'delivered'], [3, 'C17', 'Toys', 15, 'refunded'], [4, 'C88', 'Books', 32, 'delivered'],
    [5, 'C03', 'Home', 210, 'shipped'], [6, 'C42', 'Toys', 8, 'delivered'], [7, 'C88', 'Books', 64, 'pending'], [8, 'C17', 'Home', 99, 'delivered']];
  const R = data.length, C = cols.length;
  const Q = { A: (r, c) => r === 4, B: (r, c) => c === 3 };
  const grid = (hit) => `<tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr>` + data.map((row, r) => `<tr>${row.map((v, c) => `<td>${v}</td>`).join('')}</tr>`).join('');
  $('#laygrid').innerHTML = grid();
  const strip = (idx, hit, w) => {
    let hits = 0, runs = 0, prev = false, total = 0;
    const cells = idx.map(([r, c]) => { const x = hit(r, c); total += w(c); if (x) { hits++; if (!prev) runs++; } prev = x; return `<i class="col${c}${x ? ' hit' : ''}" style="flex:${w(c)}"></i>`; }).join('');
    return { cells, hits, runs, total };
  };
  const rowIdx = [], colIdx = [];
  for (let r = 0; r < R; r++) for (let c = 0; c < C; c++) rowIdx.push([r, c]);
  for (let c = 0; c < C; c++) for (let r = 0; r < R; r++) colIdx.push([r, c]);
  const block = (id, hit, withComp) => {
    const rs = strip(rowIdx, hit, () => 1), cs = strip(colIdx, hit, () => 1);
    let html = `<div class="strip-lab"><span class="c-pg">ROW STORE</span><span class="metric">reads <b>${rs.hits}/40</b> values · <b>${rs.runs}</b> seek${rs.runs > 1 ? 's' : ''}</span></div><div class="strip">${rs.cells}</div>` +
      `<div class="strip-lab"><span class="c-s3">COLUMN STORE</span><span class="metric">reads <b>${cs.hits}/40</b> values · <b>${cs.runs}</b> seek${cs.runs > 1 ? 's' : ''}</span></div><div class="strip">${cs.cells}</div>`;
    if (withComp) {
      const w = [.45, .9, .2, .75, .12], cc = strip(colIdx, hit, c => w[c]);
      html += `<div class="strip-lab"><span class="c-s3">COLUMN STORE, compressed</span><span class="metric">stored in <b>~${Math.round(100 * cc.total / 40)}%</b> of the space (dictionary · delta · run-length)</span></div><div class="strip">${cc.cells}</div>`;
    }
    $(id).innerHTML = html;
  };
  block('#lay-A', Q.A, false); block('#lay-B', Q.B, true);
}

function initRowGroups() {
  const n = 41, hot = 34;     // 4,200,000 / 122,880 -> row group 34
  if (!$('#rg-sorted') || !$('#rg-unsorted')) return;      // slides are hidden (skipped)
  $('#rg-sorted').innerHTML = Array.from({ length: n }, (_, i) => `<i class="${i === hot ? 'read' : ''}"></i>`).join('');
  $('#rg-unsorted').innerHTML = Array.from({ length: n }, () => '<i class="bad"></i>').join('');
}

function initMatrix() {
  const rows = [
    ['Sub-millisecond reads by key', [3, 2, 1, 1]], ['ACID transactions & integrity', [1, 3, 1, 0]], ['Frequent single-row updates', [3, 3, 1, 0]],
    ['Big scans & aggregations', [0, 1, 3, 2]], ['Unplanned, ad-hoc questions', [0, 2, 3, 2]], ['Cheap long-term retention', [0, 1, 2, 3]],
  ];
  const dots = n => n ? '●'.repeat(n) : '✕';
  $('#mx').innerHTML = `<tr><th>I need…</th>${LANES.map(l => `<th style="color:${STORES[l].color}">${STORES[l].name}</th>`).join('')}</tr>` +
    rows.map(([name, sc]) => { const mx = Math.max(...sc); return `<tr><td>${name}</td>${sc.map(s => `<td class="${s === 0 ? 'zero' : s === mx ? 'best' : ''}">${dots(s)}</td>`).join('')}</tr>`; }).join('');
}

/* ───────────── query boxes (deep-dive slides) ───────────── */
function renderResult(res, maxRows = 4) {
  if (res.error) return `<div class="err">${esc(res.error)}</div>`;
  let html = `<div class="ms">${fmtMs(res.ms)}</div>`;
  if (res.columns && res.rows.length === 1 && res.columns.length > 5) {      // wide single row: show as field list
    html += '<table class="kv">' + res.columns.slice(0, 6).map((c, i) => `<tr><td>${esc(c)}</td><td title="${esc(res.rows[0][i])}">${esc(res.rows[0][i])}</td></tr>`).join('') + '</table>' +
      `<div class="tiny mut">+ ${res.columns.length - 6} more fields</div>`;
  } else if (res.columns) {
    html += `<table class="res"><tr>${res.columns.map(c => `<th>${esc(c)}</th>`).join('')}</tr>` +
      res.rows.slice(0, maxRows).map(r => `<tr>${r.map(v => `<td title="${esc(v)}">${esc(v)}</td>`).join('')}</tr>`).join('') + '</table>';
    if (res.rows.length > maxRows || res.truncated) html += `<div class="tiny mut">showing ${Math.min(maxRows, res.rows.length)} of ${res.truncated ? res.rows.length + '+' : res.rows.length}</div>`;
  }
  if (res.note) html += `<div class="note">${esc(res.note)}</div>`;
  return html;
}
function initSqlboxes() {
  document.querySelectorAll('.sqlbox').forEach(box => {
    const lane = box.dataset.lane, st = STORES[lane], ta = $('textarea', box), maxRows = +box.dataset.rows || 4;
    const hd = h('div', { class: 'hd' }, `<span class="chip c-${st.css}">${st.name}</span><span class="grow tiny mut">${esc(box.dataset.title || '')}</span>`);
    const btn = h('button', { class: 'btn primary' }, '▶ Run');
    hd.appendChild(btn);
    const out = h('div', { class: 'out' });
    box.insertBefore(hd, ta); box.appendChild(out);
    btn.onclick = async () => {
      btn.disabled = true; out.innerHTML = '<span class="mut">running…</span>';
      try { out.innerHTML = renderResult(await api('api/run', { lane, workload: 'sql', text: ta.value }), maxRows); }
      catch (e) { out.innerHTML = renderResult({ error: e.message }); }
      btn.disabled = false;
    };
  });
}

/* ───────────── lab: footprint ───────────── */
let fpDone = false;
async function initFootprint() {
  if (fpDone) return;
  try {
    const f = await api('api/footprint'); fpDone = true;
    const max = Math.max(...f.bars.map(b => b.bytes)), el = $('#fp'); el.innerHTML = '';
    const col = { raw: '#6b7a93', postgres: 'var(--pg)', duckdb: 'var(--duck)', s3: 'var(--s3)', redis: 'var(--redis)' };
    f.bars.forEach(b => {
      el.appendChild(h('div', {}, esc(b.label)));
      el.appendChild(h('div', { class: 'track' }, `<div class="fill ${b.estimate ? 'est' : ''}" style="background-color:${col[b.lane]}" data-w="${100 * b.bytes / max}"></div>`));
      el.appendChild(h('div', { class: 'num' }, fmtBytes(b.bytes) + (b.estimate ? '*' : '')));
    });
    requestAnimationFrame(() => setTimeout(() => el.querySelectorAll('.fill').forEach(f => f.style.width = f.dataset.w + '%'), 50));
  } catch {
    $('#fp').innerHTML = '<div class="bad" style="grid-column:1/-1;font-size:1.1em">Lab is offline. Start it: <code>cd Course/data-architecture-intro &amp;&amp; docker compose up -d</code>, open <b>http://localhost:8000</b> (not the file), retrying…</div>';
    setTimeout(initFootprint, 4000);
  }
}

/* ───────────── lab: the race ───────────── */
function initLab() {
  const id = () => META?.hot_id ?? 4800000, cid = () => META?.customer_id ?? 2;
  const sql = s => ({ postgres: s, duckdb: s, s3: s });
  const W = {
    lookup: { tab: '1 · Point lookup', ask: () => `Fetch order #${id().toLocaleString()}`, q: () => ({ redis: `HGETALL order:${id()}`, ...sql(`SELECT * FROM orders\nWHERE order_id = ${id()}`) }),
      take: '<b>Row stores and RAM win: sub-millisecond.</b> DuckDB and Parquet manage it <i>only</i> because order_id is sorted, so min/max stats skip almost every row group. Next tab removes that crutch.' },
    by_customer: { tab: '2 · One customer\'s orders', ask: () => `All orders of customer #${cid()}`, q: () => ({ redis: `SMEMBERS cust:${cid()}\n# + HGETALL order:<id> each\n(a hand-built index set)`, ...sql(`SELECT order_id, customer_id, category, amount, status\nFROM orders\nWHERE customer_id = ${cid()}\nORDER BY order_id`) }), ro: ['redis'],
      take: '<b>Postgres wins with its B-tree index.</b> Columnar engines have no index on an unsorted column and must scan. Redis only returns the hot 300k orders: a <i>partial</i> answer, and only because we built the index set by hand.' },
    agg: { tab: '3 · Revenue by category', ask: () => 'SUM(amount) per category across all 5M orders', q: () => ({ redis: 'HGETALL agg:revenue_by_category\n# precomputed by an ETL job', ...sql('SELECT category, round(sum(amount),2) AS revenue\nFROM orders\nGROUP BY 1\nORDER BY 2 DESC') }),
      take: '<b>Columnar wins the real computation.</b> Redis looks fastest only because someone precomputed the answer: it can answer just the questions you predicted. Postgres drags every wide row through memory.' },
    adhoc: { tab: '4 · A brand-new question', ask: () => 'Avg order value of refunds, by country, since 2025', q: () => ({ redis: '# no query language.\n# app-side loop: SCAN 300k keys,\n# HMGET fields, group in Python', ...sql("SELECT country,\n  round(avg(amount),2) AS avg_order_value,\n  count(*) AS orders\nFROM orders\nWHERE status = 'refunded'\n  AND created_at >= '2025-01-01'\nGROUP BY 1 ORDER BY 2 DESC") }), ro: ['redis'],
      take: '<b>Redis can\'t answer unplanned questions.</b> Scanning 300k keys is slower than DuckDB scanning 5M rows. At 5M rows Postgres is still fine: its gap grows linearly, so imagine 500M. S3 pays object-store round trips but needs no running server.' },
    write1: { tab: '5 · Write one order', ask: () => 'Insert a single new order', q: () => ({ redis: 'HSET live:<id> …', postgres: "INSERT INTO live_orders VALUES (…)", duckdb: "INSERT INTO live_orders VALUES (…)", s3: "COPY (SELECT …) TO 's3://lake/live/order_<id>.parquet'" }), ro: LANES,
      take: '<b>Writes are where the layouts diverge.</b> Redis and Postgres append in place. On S3 every write is a <i>new object</i>: write 1 row at a time and you drown in tiny files (the small-files problem).' },
    update1: { tab: '6 · Change one value', ask: () => `Set order #${id().toLocaleString()} status = refunded`, q: () => ({ redis: `HSET live:${id()} status refunded`, postgres: `UPDATE live_orders SET status = 'refunded'\nWHERE order_id = ${id()}`, duckdb: `UPDATE live_orders SET status = 'refunded'\nWHERE order_id = ${id()}`, s3: "# Parquet is immutable:\n# read the partition file,\n# change one value,\n# rewrite the WHOLE file" }), ro: LANES,
      take: '<b>One changed value = ~205,000 rows rewritten on S3.</b> Immutable files are why table formats exist: Iceberg / Delta add transactional metadata over Parquet (Session 4).' },
  };
  let cur = 'lookup'; const results = {};
  const tabs = $('#tabs'), lanes = $('#lanes');
  const draw = () => {
    const w = W[cur];
    tabs.innerHTML = ''; Object.entries(W).forEach(([k, v]) => { const b = h('button', { class: k === cur ? 'on' : '' }, v.tab); b.onclick = () => { cur = k; draw(); }; tabs.appendChild(b); });
    $('#ask').textContent = w.ask();
    const q = w.q(); lanes.innerHTML = '';
    LANES.forEach(l => {
      const st = STORES[l], ro = (w.ro || []).includes(l);
      const el = h('div', { class: 'lane', style: `--c:${st.color}`, 'data-lane': l }, `<div class="hd"><span>${st.name}</span></div>
        <textarea ${ro ? 'readonly' : 'spellcheck="false"'}>${esc(q[l])}</textarea><button class="run">▶ Run</button><div class="out"></div>`);
      $('.run', el).onclick = () => run(l);
      lanes.appendChild(el);
      if (results[cur]?.[l]) paint(l, results[cur][l]);
    });
    rank(); $('#take').innerHTML = results[cur] && Object.keys(results[cur]).length === 4 ? w.take : '';
  };
  const laneEl = l => $(`.lane[data-lane="${l}"]`);
  const paint = (l, res) => { $('.out', laneEl(l)).innerHTML = renderResult(res, 3); };
  async function run(l) {
    const el = laneEl(l), out = $('.out', el); out.innerHTML = '<span class="mut">running…</span>';
    let res;
    try { res = await api('api/run', { lane: l, workload: cur, text: $('textarea', el).value }); } catch (e) { res = { error: e.message }; }
    (results[cur] ||= {})[l] = res; paint(l, res); rank();
    if (Object.keys(results[cur]).length === 4) $('#take').innerHTML = W[cur].take;
    return res;
  }
  function rank() {
    document.querySelectorAll('.lane').forEach(e => { e.classList.remove('win'); $('.ratio', e)?.remove(); });
    const r = results[cur]; if (!r) return;
    const ok = Object.entries(r).filter(([, v]) => !v.error); if (ok.length < 2) return;
    const best = Math.min(...ok.map(([, v]) => v.ms));
    ok.forEach(([l, v]) => {
      const e = laneEl(l); if (!e) return;
      if (v.ms === best) { e.classList.add('win'); e.appendChild(h('div', { class: 'ratio good' }, '🏆 fastest')); }
      else e.appendChild(h('div', { class: 'ratio', style: `color:${v.ms / best > 10 ? 'var(--bad)' : 'var(--warn)'}` }, `${v.ms / best >= 10 ? Math.round(v.ms / best) : (v.ms / best).toFixed(1)}× slower`));
    });
  }
  $('#runall').onclick = async function () {
    this.disabled = true; delete results[cur]; $('#take').innerHTML = ''; draw();
    for (const l of LANES) await run(l);        // sequential: parallel runs would distort the timings
    this.disabled = false;
  };
  draw();
  // warm caches quietly the first time the lab is approached, so the timings shown are steady-state
  let warmed = false;
  Reveal.on('slidechanged', async e => {
    if (e.currentSlide.id === 'lab-foot') initFootprint();
    if (!warmed && (e.currentSlide.id === 'lab-foot' || e.currentSlide.id === 'lab-race') && META) {
      warmed = true;
      for (const k of ['lookup', 'by_customer', 'agg', 'adhoc']) for (const l of LANES) {
        try { await api('api/run', { lane: l, workload: k, text: W[k].q()[l] }); } catch { /* ignore */ }
      }
    }
  });
  setTimeout(draw, 1500); // refresh ids once META has loaded
}

/* ───────────── quiz ───────────── */
function initQuiz() {
  const Q = [
    { q: 'A login service needs to store 1M active sessions, each expiring after 30 minutes, read on every request. Best fit?', o: ['Parquet files on S3', 'Redis with a TTL', 'A DuckDB table', 'Postgres with a nightly cleanup job'], a: 1, e: 'In-memory, sub-millisecond, with native key expiry. Postgres could work but you would pay a lot for durability you do not need.' },
    { q: 'Why is SELECT SUM(amount) faster on a columnar file than on a row store?', o: ['It uses more CPUs', 'It reads only the amount column, and that column compresses well', 'Columnar files are always kept in RAM', 'It uses an index on amount'], a: 1, e: 'Column projection plus per-column compression means far fewer bytes move. CPU count and indexes are not the reason.' },
    { q: 'You must change ONE value inside a 200 MB Parquet file on S3. What actually happens?', o: ['S3 patches the one value in place', 'The file is rewritten (or replaced by a new file)', 'Parquet keeps an undo log inside the file', 'The footer is edited in place'], a: 1, e: 'Objects and Parquet files are immutable. Table formats (Iceberg, Delta) add metadata so a change becomes a new file plus a pointer swap.' },
    { q: 'You partition 5M orders by customer_id (200k distinct values). What do you get?', o: ['Faster queries everywhere', '200,000 tiny files: the small-files problem', 'Automatic skew handling', 'Smaller storage costs'], a: 1, e: 'Partition on low-cardinality columns you filter by (date, region). High-cardinality keys make listing and opening files cost more than reading data.' },
    { q: 'WHERE order_id = 4200000 on sorted Parquet reads almost nothing. Why?', o: ['Parquet builds a hash index', 'Min/max stats in the footer let the engine skip row groups', 'The file is cached in memory', 'S3 supports server-side filtering for all queries'], a: 1, e: 'Each row group stores min and max per column. Sorted data makes ranges disjoint, so all but one row group is skipped. On an unsorted column the ranges overlap and nothing is skipped.' },
    { q: '7 years of audit logs, almost never read, must be kept cheap and durable. Where do they live?', o: ['Redis with persistence on', 'The primary Postgres instance', 'Object storage (Parquet) with a lifecycle policy to a colder tier', 'A warehouse, always-on'], a: 2, e: 'Cheapest bytes, 11-nines durability, lifecycle rules move them to colder tiers over time. Query them with an engine only when needed.' },
  ];
  let i = 0, score = 0, marks = [];
  const body = $('#qbody'), dots = $('#qdots');
  const paintDots = () => { dots.innerHTML = Q.map((_, k) => `<i class="${marks[k] === 1 ? 'done' : marks[k] === 0 ? 'miss' : k === i ? 'cur' : ''}"></i>`).join(''); };
  const show = () => {
    paintDots();
    if (i >= Q.length) {
      const msg = score === Q.length ? 'Architect material 🏛️' : score >= 4 ? 'Solid. You will do fine in the course.' : 'Good: now you know what to pay attention to.';
      body.innerHTML = `<div class="score grad">${score}/${Q.length}</div><p>${msg}</p><button class="btn" id="qre">↻ Play again</button>`;
      $('#qre').onclick = () => { i = 0; score = 0; marks = []; show(); }; return;
    }
    const q = Q[i]; let done = false;
    body.innerHTML = `<div class="q">${i + 1}. ${q.q}</div>` + q.o.map((o, k) => `<button class="opt" data-k="${k}"><b class="mut">${k + 1}</b>&nbsp; ${o}</button>`).join('') + '<div class="exp"></div><button class="btn" id="qnext" style="display:none">Next →</button>';
    const pick = k => {
      if (done) return; done = true; const ok = k === q.a; score += ok; marks[i] = ok ? 1 : 0;
      body.querySelectorAll('.opt').forEach((b, bi) => { if (bi === q.a) b.classList.add('right'); else if (bi === k) b.classList.add('wrong'); });
      $('.exp', body).innerHTML = `<b class="${ok ? 'good' : 'bad'}">${ok ? 'Correct.' : 'Not quite.'}</b> ${q.e}`;
      $('#qnext').style.display = 'inline-block'; paintDots();
    };
    body.querySelectorAll('.opt').forEach(b => b.onclick = () => pick(+b.dataset.k));
    $('#qnext').onclick = () => { i++; show(); };
    body._pick = pick;
  };
  show();
  document.addEventListener('keydown', e => {
    if (!Reveal.getCurrentSlide()?.contains($('#quiz')) || e.target.tagName === 'TEXTAREA') return;
    if (/^[1-4]$/.test(e.key) && body._pick) body._pick(+e.key - 1);
    if (e.key === 'Enter' && $('#qnext')?.style.display !== 'none') $('#qnext').click();
  });
}

/* ───────────── boot ───────────── */
Reveal.initialize({
  width: 1280, height: 720, margin: 0.04, hash: true, controls: true, progress: true, slideNumber: 'c/t',
  transition: 'slide', backgroundTransition: 'fade', center: false, plugins: [RevealNotes, RevealZoom],
}).then(() => {
  [initLadder, initLayoutStatic, initRowGroups, initSqlboxes, initMatrix, initQuiz, initLab].forEach(f => { try { f(); } catch (e) { console.error(f.name, e); } });   // one missing/hidden slide must not stop the rest
  Reveal.sync();   // register fragments created above
  pollStatus();
  // ladder: all four rows show at once; bars grow when the slide opens and replay on re-entry
  const ladderBars = on => document.querySelectorAll('#ladder .bar').forEach(b => b.classList.toggle('go', on));
  Reveal.on('slidechanged', e => ladderBars(!!e.currentSlide.querySelector('#ladder')));
  if (Reveal.getCurrentSlide()?.querySelector('#ladder')) ladderBars(true);
  if (Reveal.getCurrentSlide()?.id === 'lab-foot') initFootprint();
  Reveal.on('slidechanged', e => { if (e.currentSlide.id === 'lab-foot') setTimeout(initFootprint, 300); });
});
