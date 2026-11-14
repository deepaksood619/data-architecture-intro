"""Shared config + connection helpers for the data-architecture lab."""
import os
import duckdb
import psycopg
import redis

PG_DSN = os.environ.get("PG_DSN", "postgresql://demo:demo@localhost:5432/demo")
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "localhost:9000")
S3_KEY, S3_SECRET, BUCKET = "minioadmin", "minioadmin", "lake"
N_ROWS = int(os.environ.get("N_ROWS", 5_000_000))
HOT_ROWS = 300_000              # the "hot" tail of orders cached in Redis
DATA_DIR = os.environ.get("DATA_DIR", "/data")
DUCK_FILE = f"{DATA_DIR}/olap.duckdb"
META_FILE = f"{DATA_DIR}/meta.json"

FLAT = f"s3://{BUCKET}/orders/orders.parquet"
PARTITIONED = f"s3://{BUCKET}/orders_by_month/*/*/*.parquet"

COLUMNS = ("order_id BIGINT, customer_id INTEGER, product_id INTEGER, category VARCHAR, country VARCHAR, "
           "amount DECIMAL(10,2), quantity SMALLINT, status VARCHAR, payment_method VARCHAR, "
           "coupon_code VARCHAR, created_at TIMESTAMP, shipping_address VARCHAR, user_agent VARCHAR")


def configure_s3(con):
    con.execute("LOAD httpfs")
    # a secret is visible to every cursor of the instance (plain SET s3_* is not)
    con.execute(f"CREATE OR REPLACE SECRET lake (TYPE s3, KEY_ID '{S3_KEY}', SECRET '{S3_SECRET}', "
                f"ENDPOINT '{S3_ENDPOINT}', URL_STYLE 'path', USE_SSL false, REGION 'us-east-1')")


def s3_connection():
    """In-memory DuckDB that sees only object storage (no local tables)."""
    con = duckdb.connect()
    configure_s3(con)
    return con


def pg():
    return psycopg.connect(PG_DSN, autocommit=True)


def rds():
    return redis.Redis(host=REDIS_HOST, decode_responses=True)
