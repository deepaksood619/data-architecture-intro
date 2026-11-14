FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir duckdb==1.3.2 "psycopg[binary]==3.2.9" redis==5.2.1 boto3==1.35.99 fastapi==0.115.6 "uvicorn==0.34.0"
# bake the httpfs extension in so the demo works without internet
RUN python -c "import duckdb; duckdb.connect().execute('INSTALL httpfs')"
COPY *.py /app/
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
