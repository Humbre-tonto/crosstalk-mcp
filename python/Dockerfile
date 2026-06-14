FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY crosstalk_mcp.py .
# Persist the SQLite db on a volume; set RELAY_TOKEN to require auth.
ENV PORT=8765 \
    RELAY_DB=/data/relay.db
VOLUME ["/data"]
EXPOSE 8765
ENTRYPOINT ["python", "crosstalk_mcp.py"]
