"""API for the live lab: one endpoint runs a query against Redis / Postgres / DuckDB / S3-Parquet and times it."""
import datetime as dt
import decimal
import json
import os
import re
import shlex
import threading
import time

import duckdb
import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import seed as seeder
from common import *   # noqa: F401,F403

app = FastAPI()
META = {}
L = {}                      # live connections, built once seeding is done
duck_lock = threading.Lock()
s3_lock = threading.Lock()

REDIS_ALLOWED = {"GET", "HGETALL", "HGET", "HMGET", "SMEMBERS", "SCARD", "ZSCORE", "ZREVRANGE", "HLEN",
                 "DBSIZE", "EXISTS", "TYPE", "TTL"}
SQL_START = re.compile(r"^\s*(select|with|from|describe|explain|summarize)\b", re.I)
SQL_BANNED = re.compile(r"\b(copy|attach|detach|install|load|pragma|create|insert|update|delete|drop|alter|export|"
                        r"import|call|set|read_csv|read_text|read_blob|read_json|glob|system|vacuum|checkpoint)\b", re.I)
MAX_ROWS = 50


def boot():
    seeder.seed()
    with open(META_FILE) as f:
        META.update(json.load(f))
    opts = "-c default_transaction_read_only=on -c statement_timeout=30000"
    L["pg_ro"] = psycopg.connect(PG_DSN, autocommit=True, options=opts)
    L["pg_rw"] = pg()
    L["redis"] = rds()
    L["duck"] = duckdb.connect(DUCK_FILE)
    s3 = s3_connection()
    s3.execute(f"CREATE VIEW orders AS SELECT * FROM read_parquet('{FLAT}')")
    s3.execute(f"CREATE VIEW orders_by_month AS SELECT * FROM read_parquet('{PARTITIONED}', hive_partitioning=true)")
    L["s3"] = s3
    seeder.STATE["ready"] = True


@app.on_event("startup")
def startup():
    def go():
        try:
            boot()
        except Exception as e:  # surfaced in the deck's status pill
            seeder.STATE.update(error=repr(e), step="failed")
            raise
    threading.Thread(target=go, daemon=True).start()


def clean(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat(sep=" ")
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


def table(cols, rows, truncated=False):
    return {"columns": list(cols), "rows": [[clean(v) for v in r] for r in rows[:MAX_ROWS]],
            "truncated": truncated or len(rows) > MAX_ROWS}


def guard_sql(text):
    t = text.strip().rstrip(";")
    if ";" in t or not SQL_START.match(t) or SQL_BANNED.search(t):
        raise HTTPException(400, "read-only SELECT queries only (one statement)")
    return t


# ---------------------------------------------------------------- engines
def run_pg(text, workload):
    if workload in ("write1", "update1"):
        hid = META["hot_id"]
        if workload == "write1":
            oid = int(time.time() * 1000)
            q = ("INSERT INTO live_orders VALUES (%s,4242,77,'Books','IN',49.90,1,'pending','upi',NULL,now(),"
                 "'12 MG Road, Bengaluru 560001','demo')")
            shown = q.replace("%s", str(oid))
            args = (oid,)
        else:
            q = shown = f"UPDATE live_orders SET status = 'refunded' WHERE order_id = {hid}"
            args = ()
        t = time.perf_counter()
        with L["pg_rw"].cursor() as c:
            c.execute(q, args)
        return shown, (time.perf_counter() - t) * 1000, {"columns": ["result"], "rows": [["1 row written (fsync'd WAL)"]]}
    sql = guard_sql(text)
    with L["pg_ro"].cursor() as c:
        t = time.perf_counter()
        c.execute(sql)
        rows = c.fetchmany(MAX_ROWS + 1)
        ms = (time.perf_counter() - t) * 1000
        return sql, ms, table([d.name for d in c.description], rows, truncated=len(rows) > MAX_ROWS)


def s3_cursor(con):
    cur = con.cursor()
    for setting in ("enable_external_file_cache=false", "enable_object_cache=false"):
        try:      # every run must really hit object storage - no warm cache flattering the S3 lane
            cur.execute(f"SET {setting}")
        except Exception:
            pass
    return cur


def run_duck(con, text, workload, s3=False):
    if workload in ("write1", "update1"):
        hid = META["hot_id"]
        if not s3:
            if workload == "write1":
                oid = int(time.time() * 1000)
                sql = ("INSERT INTO live_orders VALUES (%d,4242,77,'Books','IN',49.90,1,'pending','upi',NULL,now(),"
                       "'12 MG Road, Bengaluru 560001','demo')" % oid)
            else:
                sql = f"UPDATE live_orders SET status = 'refunded' WHERE order_id = {hid}"
            cur = con.cursor()
            t = time.perf_counter()
            cur.execute(sql)
            return sql, (time.perf_counter() - t) * 1000, {"columns": ["result"], "rows": [["1 row written (WAL)"]]}
        if workload == "write1":
            oid = int(time.time() * 1000)
            sql = (f"COPY (SELECT {oid} AS order_id, 4242 AS customer_id, 'pending' AS status, 49.90 AS amount) "
                   f"TO 's3://{BUCKET}/live/order_{oid}.parquet' (FORMAT parquet)")
            note = "1 new object (1 row). Objects are immutable - every write is a new file."
        else:
            sql = (f"COPY (SELECT * REPLACE (CASE WHEN order_id = {hid} THEN 'refunded' ELSE status END AS status) "
                   f"FROM read_parquet('{META['part_file']}')) TO 's3://{BUCKET}/scratch/rewrite.parquet' "
                   f"(FORMAT parquet, COMPRESSION zstd)")
            note = None
        cur = s3_cursor(con)
        t = time.perf_counter()
        n = cur.execute(sql).fetchone()[0]
        ms = (time.perf_counter() - t) * 1000
        return sql, ms, {"columns": ["result"], "rows": [[note or f"rewrote the whole partition file: {n:,} rows to change 1 value"]]}
    sql = guard_sql(text)
    cur = s3_cursor(con) if s3 else con.cursor()
    timer = threading.Timer(30, cur.interrupt)
    timer.start()
    try:
        t = time.perf_counter()
        cur.execute(sql)
        rows = cur.fetchmany(MAX_ROWS + 1)
        ms = (time.perf_counter() - t) * 1000
    except duckdb.InterruptException:
        raise HTTPException(408, "query cancelled after 30s")
    finally:
        timer.cancel()
    return sql, ms, table([d[0] for d in cur.description], rows, truncated=len(rows) > MAX_ROWS)


def run_redis(text, workload):
    r = L["redis"]
    hid = META["hot_id"]
    if workload == "write1":
        oid = int(time.time() * 1000)
        shown = f"HSET live:{oid} order_id {oid} customer_id 4242 category Books amount 49.90 status pending"
        t = time.perf_counter()
        r.hset(f"live:{oid}", mapping={"order_id": oid, "customer_id": 4242, "category": "Books",
                                       "amount": "49.90", "status": "pending"})
        return shown, (time.perf_counter() - t) * 1000, {"columns": ["result"], "rows": [["OK (in memory; durable only if AOF/RDB on)"]]}
    if workload == "update1":
        shown = f"HSET live:{hid} status refunded"
        t = time.perf_counter()
        r.hset(f"live:{hid}", "status", "refunded")
        return shown, (time.perf_counter() - t) * 1000, {"columns": ["result"], "rows": [["OK (field overwritten in place)"]]}
    if workload == "by_customer":
        cid = META["customer_id"]
        shown = f"SMEMBERS cust:{cid}   # then HGETALL order:<id> for each id (pipelined)"
        t = time.perf_counter()
        ids = sorted(r.smembers(f"cust:{cid}"), key=int)
        pipe = r.pipeline(transaction=False)
        for i in ids:
            pipe.hgetall(f"order:{i}")
        rows = pipe.execute()
        ms = (time.perf_counter() - t) * 1000
        cols = ["order_id", "customer_id", "category", "amount", "status"]
        out = table(cols, [[h.get(c) for c in cols] for h in rows])
        out["note"] = "Redis only holds the hot 300k orders, so this is a partial answer, and only because we hand-built a cust:<id> index set."
        return shown, ms, out
    if workload == "adhoc":
        shown = "# no query language. App-side loop: SCAN order:* -> HMGET status country amount created_at -> group in Python"
        t = time.perf_counter()
        acc = {}
        pipe = r.pipeline(transaction=False)
        batch = []

        def flush():
            for vals in pipe.execute():
                st, country, amount, created = vals
                if st == "refunded" and created and created >= "2025-01-01":
                    a = acc.setdefault(country, [0.0, 0])
                    a[0] += float(amount)
                    a[1] += 1

        for k in r.scan_iter(match="order:*", count=5000):
            pipe.hmget(k, "status", "country", "amount", "created_at")
            batch.append(k)
            if len(batch) >= 5000:
                flush()
                batch = []
        if batch:
            flush()
        ms = (time.perf_counter() - t) * 1000
        rows = sorted(([c, round(s / n, 2), n] for c, (s, n) in acc.items()), key=lambda x: -x[1])
        out = table(["country", "avg_order_value", "orders"], rows)
        out["note"] = "Answers only for the 300k hot orders (not all 5M) - and it still dragged every key over the wire."
        return shown, ms, out
    parts = shlex.split(text, comments=True)
    if not parts or parts[0].upper() not in REDIS_ALLOWED:
        raise HTTPException(400, f"allowed commands: {', '.join(sorted(REDIS_ALLOWED))}")
    t = time.perf_counter()
    res = r.execute_command(*parts)
    ms = (time.perf_counter() - t) * 1000
    if isinstance(res, dict):
        out = table(list(res.keys()), [list(res.values())]) if workload != "agg" else table(["category", "revenue"], list(res.items()))
        if not res:
            out = {"columns": ["result"], "rows": [["(nil) - not in Redis. Cache miss: you must fall back to Postgres."]]}
    elif isinstance(res, (list, set)):
        out = table(["value"], [[v] for v in res])
    else:
        out = {"columns": ["result"], "rows": [["(nil) - not in Redis. Cache miss: you must fall back to Postgres." if res is None else clean(res)]]}
    return text.strip(), ms, out


# ---------------------------------------------------------------- routes
class Run(BaseModel):
    lane: str
    workload: str = "adhoc_sql"
    text: str = ""


@app.get("/api/status")
def status():
    return {"ready": seeder.STATE["ready"] and bool(META), "step": seeder.STATE["step"], "error": seeder.STATE["error"]}


@app.get("/api/meta")
def meta():
    if not META:
        raise HTTPException(503, "still seeding")
    return {k: META[k] for k in ("rows", "hot_rows", "hot_id", "customer_id", "part_file", "seconds")}


@app.post("/api/run")
def run(req: Run):
    if not META:
        raise HTTPException(503, "still seeding - check /api/status")
    try:
        if req.lane == "redis":
            shown, ms, out = run_redis(req.text, req.workload)
        elif req.lane == "postgres":
            shown, ms, out = run_pg(req.text, req.workload)
        elif req.lane == "duckdb":
            with duck_lock:       # one query at a time per engine: no cross-talk in the timings
                shown, ms, out = run_duck(L["duck"], req.text, req.workload)
        elif req.lane == "s3":
            with s3_lock:
                shown, ms, out = run_duck(L["s3"], req.text, req.workload, s3=True)
        else:
            raise HTTPException(400, "unknown lane")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e).splitlines()[0][:300])
    return {"lane": req.lane, "query": shown, "ms": round(ms, 3), **out}


@app.get("/api/footprint")
def footprint():
    if not META:
        raise HTTPException(503, "still seeding")
    f = META["footprint"]
    with L["pg_ro"].cursor() as c:
        c.execute("SELECT pg_relation_size('orders'), pg_indexes_size('orders')")
        heap, idx = c.fetchone()
    duck = os.path.getsize(DUCK_FILE)
    redis_hot = f["redis_bytes_per_row"] * META["hot_rows"]
    bars = [
        {"label": "CSV export", "lane": "raw", "bytes": f["csv"]},
        {"label": "Postgres table + indexes", "lane": "postgres", "bytes": heap + idx},
        {"label": "DuckDB native file", "lane": "duckdb", "bytes": duck},
        {"label": "Parquet - snappy", "lane": "s3", "bytes": f["parquet_snappy"]},
        {"label": "Parquet - zstd", "lane": "s3", "bytes": f["parquet_zstd"]},
        {"label": f"Redis - hot {META['hot_rows']//1000}k rows only", "lane": "redis", "bytes": redis_hot},
        {"label": "Redis - if all rows lived in RAM (est.)", "lane": "redis", "bytes": f["redis_bytes_per_row"] * META["rows"], "estimate": True},
    ]
    return {"rows": META["rows"], "bars": bars, "partition_files": f["partition_files"]}


app.mount("/", StaticFiles(directory="/app/deck", html=True), name="deck")
