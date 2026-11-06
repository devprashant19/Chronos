FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY chronos/ ./chronos/
COPY fixtures/ ./fixtures/

RUN mkdir -p /data
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=12 \
    CMD curl -fsS http://127.0.0.1:8000/health > /dev/null || exit 1

CMD ["uvicorn", "chronos.main:app", "--host", "0.0.0.0", "--port", "8000"]
