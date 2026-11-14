"""Re-record deck/snapshot.json from the running docker stack (static replay for GitHub Pages).
Run from this folder with the stack up:  python3 tools/record_snapshot.py"""
import json, urllib.request, sys
B='http://localhost:8000'
def get(p): return json.load(urllib.request.urlopen(B+p))
def post(p,b):
    r=urllib.request.Request(B+p,json.dumps(b).encode(),{'Content-Type':'application/json'}); return json.load(urllib.request.urlopen(r))
meta=get('/api/meta'); i=meta['hot_id']; c=meta['customer_id']
sql=lambda s:{'postgres':s,'duckdb':s,'s3':s}
Q={
'lookup':{'redis':f'HGETALL order:{i}',**sql(f'SELECT * FROM orders\nWHERE order_id = {i}')},
'by_customer':{'redis':f'SMEMBERS cust:{c}\n# + HGETALL order:<id> each\n(a hand-built index set)',**sql(f'SELECT order_id, customer_id, category, amount, status\nFROM orders\nWHERE customer_id = {c}\nORDER BY order_id')},
'agg':{'redis':'HGETALL agg:revenue_by_category\n# precomputed by an ETL job',**sql('SELECT category, round(sum(amount),2) AS revenue\nFROM orders\nGROUP BY 1\nORDER BY 2 DESC')},
'adhoc':{'redis':'# no query language.\n# app-side loop: SCAN 300k keys,\n# HMGET fields, group in Python',**sql("SELECT country,\n  round(avg(amount),2) AS avg_order_value,\n  count(*) AS orders\nFROM orders\nWHERE status = 'refunded'\n  AND created_at >= '2025-01-01'\nGROUP BY 1 ORDER BY 2 DESC")},
'write1':{'redis':'HSET live:<id> …','postgres':'INSERT INTO live_orders VALUES (…)','duckdb':'INSERT INTO live_orders VALUES (…)','s3':"COPY (SELECT …) TO 's3://lake/live/order_<id>.parquet'"},
'update1':{'redis':f'HSET live:{i} status refunded','postgres':f"UPDATE live_orders SET status = 'refunded'\nWHERE order_id = {i}",'duckdb':f"UPDATE live_orders SET status = 'refunded'\nWHERE order_id = {i}",'s3':'# Parquet is immutable:\n# read the partition file,\n# change one value,\n# rewrite the WHOLE file'},
}
runs={}
for w,lanes in Q.items():
    for l,t in lanes.items():
        for _ in range(2 if w in('lookup','by_customer','agg','adhoc') else 1): r=post('/api/run',{'lane':l,'workload':w,'text':t})
        runs[f'{l}|{w}']={'text':t,'res':r}
        print(w,l,r.get('ms'),r.get('error'))
json.dump({'recorded':'2026-10-30','meta':meta,'footprint':get('/api/footprint'),'runs':runs},open(__import__("pathlib").Path(__file__).resolve().parent.parent/"deck"/"snapshot.json","w"),separators=(',',':'))
