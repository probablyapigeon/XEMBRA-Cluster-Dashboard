FROM python:3.14-slim AS wheels
WORKDIR /build
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.14-slim

LABEL org.opencontainers.image.source="https://github.com/probablyapigeon/XEMBRA-Cluster-Dashboard" \
      org.opencontainers.image.description="XEMBRA local cluster dashboard and supervised evolution workers" \
      org.opencontainers.image.licenses="LicenseRef-XEMBRA"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    XEMBRA_CONTROL_STATE=/data \
    XEMBRA_WORKLOAD=learning \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    XEMBRA_DASHBOARD_BIND=0.0.0.0 \
    XEMBRA_DASHBOARD_PORT=8765

WORKDIR /app
COPY requirements.txt ./
COPY --from=wheels /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels \
    && useradd --uid 10001 --create-home xembra \
    && mkdir -p /data \
    && chown xembra:xembra /data

COPY --chown=xembra:xembra *.py ./
COPY --chown=xembra:xembra scripts/ ./scripts/
COPY --chown=xembra:xembra tests/ ./tests/
COPY --chown=xembra:xembra xc/ ./xc/

USER xembra
EXPOSE 8765
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/status',timeout=3)); assert d['guardian_running']"

CMD ["python", "scripts/cluster_control.py", "guardian"]
