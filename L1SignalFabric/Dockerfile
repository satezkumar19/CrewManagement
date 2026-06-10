# L1 SignalFabric — service image (FastAPI ingress + live pipeline dashboard)
FROM python:3.11-slim

WORKDIR /app

# Dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App source
COPY . .

EXPOSE 8001

# Dev-safe defaults; override in docker-compose / the environment
ENV HOST=0.0.0.0 \
    PORT=8001 \
    SLACK_DEV_ALLOW_UNVERIFIED=1 \
    L2_STORE_PATH=/app/data/l2_store.jsonl

CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8001"]
