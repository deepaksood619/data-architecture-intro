"""Load the same synthetic `orders` data into Postgres, DuckDB, Redis and S3 (MinIO).

Runs once (marker = meta.json); delete the volumes to reseed.
"""
import json
import os
import time

import boto3
import duckdb
from botocore.client import Config

from common import *   # noqa: F401,F403

STATE = {"ready": False, "step": "starting", "error": None}


def step(msg):
    STATE["step"] = msg
    print(f"[seed] {msg}", flush=True)


def s3_client():
    return boto3.client("s3", endpoint_url=f"http://{S3_ENDPOINT}", aws_access_key_id=S3_KEY,
                        aws_secret_access_key=S3_SECRET, region_name="us-east-1",
                        config=Config(signature_version="s3v4"))


def prefix_size(s3, prefix):
    total = files = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
        for o in page.get("Contents", []):
            total += o["Size"]
            files += 1
    return total, files


GEN_SQL = f"""
CREATE TABLE orders AS
SELECT order_id, customer_id, product_id, category, country, amount, quantity,
       CASE WHEN r < .70 THEN 'delivered' WHEN r < .82 THEN 'shipped' WHEN r < .90 THEN 'pending'
            WHEN r < .96 THEN 'cancelled' ELSE 'refunded' END AS status,
       payment_method, coupon_code, created_at, shipping_address, user_agent
FROM (
  SELECT i + 1 AS order_id,
         1 + (random() * 199999)::INT AS customer_id,
         1 + (random() * 4999)::INT AS product_id,
         ['Electronics','Fashion','Home','Grocery','Toys','Books','Beauty','Sports','Auto','Garden']
           [1 + least(9, (pow(random(), 1.6) * 10)::INT)] AS category,
         ['IN','US','GB','DE','FR','SG','AE','AU','CA','JP','BR','ZA']
           [1 + least(11, (pow(random(), 1.8) * 12)::INT)] AS country,
         round(5 + pow(random(), 3) * 995, 2)::DECIMAL(10,2) AS amount,
         (1 + (random() * 4)::INT)::SMALLINT AS quantity,
         random() AS r,
         ['card','upi','netbanking','cod'][1 + (random() * 3)::INT] AS payment_method,
         CASE WHEN random() < .3 THEN 'SAVE' || (10 + (random() * 40)::INT) END AS coupon_code,
         TIMESTAMP '2024-01-01' + to_seconds(i * 63072000 // {N_ROWS}) AS created_at,
         (1 + (random() * 998)::INT)::VARCHAR || ' ' ||
           ['MG Road','Park Street','Baker Street','5th Avenue','Marine Drive'][1 + (random() * 4)::INT] || ', ' ||
           ['Bengaluru','London','New York','Mumbai','Berlin','Paris'][1 + (random() * 5)::INT] || ' ' ||
           (100000 + (random() * 899999)::INT)::VARCHAR AS shipping_address,
         ['Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36',
          'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148',
          'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36',
          'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0 Edg/123.0'][1 + (random() * 3)::INT] AS user_agent
  FROM range({N_ROWS}) t(i)
)
"""


def load_redis(con, table):
    """Fill Redis with the hot tail of orders, the per-customer index sets and the precomputed aggregate.
    Returns the average bytes per hot row (used for the footprint chart)."""
    step("loading Redis hot set + precomputed aggregates")
    r = rds()
    r.flushall()
    hot_from = N_ROWS - HOT_ROWS
    cur = con.execute(f"SELECT * FROM {table} WHERE order_id > {hot_from}")
    cols = [d[0] for d in cur.description]
    pipe = r.pipeline(transaction=False)
    while True:
        batch = cur.fetchmany(10_000)
        if not batch:
            break
        for row in batch:
            mapping = {c: str(v) for c, v in zip(cols, row) if v is not None}
            pipe.hset(f"order:{row[0]}", mapping=mapping)
            pipe.sadd(f"cust:{row[1]}", row[0])
        pipe.execute()
    agg = con.execute(f"SELECT category, round(sum(amount),2) FROM {table} GROUP BY 1").fetchall()
    r.hset("agg:revenue_by_category", mapping={k: str(v) for k, v in agg})
    sample_keys = [f"order:{hot_from + 1 + i * 250}" for i in range(1000)]
    return sum(r.memory_usage(k) or 0 for k in sample_keys) / len(sample_keys)


def ensure_redis():
    """Redis here is memory-only, so a container restart empties it: reload from the DuckDB file."""
    import duckdb
    r = rds()
    if r.exists("agg:revenue_by_category"):
        return
    meta = json.load(open(META_FILE))
    con = duckdb.connect(DUCK_FILE, read_only=True)
    load_redis(con, "orders")
    row = con.execute(f"SELECT * FROM orders WHERE order_id = {meta['hot_id']}").fetchone()
    cols = [d[0] for d in con.description]
    r.hset(f"live:{meta['hot_id']}", mapping={c: str(v) for c, v in zip(cols, row) if v is not None})
    con.close()
    step("redis reloaded after restart")


def seed():
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(META_FILE):
        ensure_redis()
        STATE.update(ready=True, step="ready")
        return
    t0 = time.time()
    s3 = s3_client()
    try:
        s3.create_bucket(Bucket=BUCKET)
    except Exception:
        pass

    step(f"generating {N_ROWS:,} orders")
    gen = duckdb.connect(f"{DATA_DIR}/gen.duckdb")
    gen.execute("DROP TABLE IF EXISTS orders")
    gen.execute("SELECT setseed(0.42)")
    gen.execute(GEN_SQL)
    configure_s3(gen)

    step("writing Parquet to object storage (zstd, snappy, hive-partitioned)")
    gen.execute(f"COPY orders TO '{FLAT}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 122880)")
    gen.execute(f"COPY orders TO 's3://{BUCKET}/orders_snappy/orders.parquet' (FORMAT parquet, COMPRESSION snappy)")
    gen.execute(f"""COPY (SELECT *, year(created_at) AS order_year, month(created_at) AS order_month FROM orders)
                    TO 's3://{BUCKET}/orders_by_month' (FORMAT parquet, COMPRESSION zstd,
                    PARTITION_BY (order_year, order_month), OVERWRITE_OR_IGNORE)""")
    csv_path = f"{DATA_DIR}/orders.csv"
    gen.execute(f"COPY orders TO '{csv_path}' (FORMAT csv, HEADER false)")
    csv_bytes = os.path.getsize(csv_path)

    step("loading Postgres (row store)")
    with pg() as c:
        c.execute("DROP TABLE IF EXISTS orders; DROP TABLE IF EXISTS live_orders")
        c.execute(f"CREATE TABLE orders ({COLUMNS})")
        with c.cursor().copy("COPY orders FROM STDIN (FORMAT csv)") as cp, open(csv_path, "rb") as f:
            while chunk := f.read(1 << 20):
                cp.write(chunk)
        step("Postgres: primary key + customer index + ANALYZE")
        c.execute("ALTER TABLE orders ADD PRIMARY KEY (order_id)")
        c.execute("CREATE INDEX orders_customer_idx ON orders (customer_id)")
        c.execute("ANALYZE orders")
        c.execute(f"CREATE TABLE live_orders ({COLUMNS})")

    step("loading DuckDB native columnar table")
    if os.path.exists(DUCK_FILE):
        os.remove(DUCK_FILE)
    duck = duckdb.connect(DUCK_FILE)
    duck.execute(f"CREATE TABLE orders ({COLUMNS})")
    duck.execute(f"COPY orders FROM '{csv_path}' (FORMAT csv, HEADER false)")
    duck.execute("CREATE TABLE live_orders AS SELECT * FROM orders LIMIT 0")
    duck.execute("CHECKPOINT")

    hot_from = N_ROWS - HOT_ROWS
    per_key = load_redis(gen, "orders")
    r = rds()
    cols = [d[0] for d in gen.execute("SELECT * FROM orders LIMIT 0").description]
    # pick demo ids
    hot_id = hot_from + 100_000
    cust = gen.execute(f"""SELECT customer_id FROM orders WHERE order_id > {hot_from}
                           GROUP BY 1 HAVING count(*) >= 3 ORDER BY customer_id LIMIT 1""").fetchone()[0]
    row = gen.execute(f"SELECT * FROM orders WHERE order_id = {hot_id}").fetchone()
    live_sql = ",".join(["?"] * len(cols))
    with pg() as c:
        c.execute(f"INSERT INTO live_orders VALUES ({','.join(['%s'] * len(cols))})", row)
    duck.execute(f"INSERT INTO live_orders VALUES ({live_sql})", row)
    duck.execute("CHECKPOINT")
    r.hset(f"live:{hot_id}", mapping={c: str(v) for c, v in zip(cols, row) if v is not None})
    part_year, part_month = row[10].year, row[10].month

    step("measuring footprints")
    flat_b, _ = prefix_size(s3, "orders/")
    snappy_b, _ = prefix_size(s3, "orders_snappy/")
    part_b, part_files = prefix_size(s3, "orders_by_month/")
    meta = {
        "rows": N_ROWS, "hot_rows": HOT_ROWS, "hot_id": hot_id, "customer_id": cust,
        "part_file": f"s3://{BUCKET}/orders_by_month/order_year={part_year}/order_month={part_month}/*.parquet",
        "footprint": {"csv": csv_bytes, "parquet_zstd": flat_b, "parquet_snappy": snappy_b,
                      "parquet_partitioned": part_b, "partition_files": part_files,
                      "redis_bytes_per_row": per_key},
        "seconds": round(time.time() - t0),
    }
    gen.close()
    duck.close()
    os.remove(csv_path)
    os.remove(f"{DATA_DIR}/gen.duckdb")
    with open(META_FILE, "w") as f:
        json.dump(meta, f)
    STATE.update(ready=True, step="ready")
    step(f"done in {meta['seconds']}s")
