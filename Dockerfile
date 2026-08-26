FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY forge/ ./forge/
COPY web/ ./web/
COPY constitution/ ./constitution/

# The Ledger lives on a volume so the Forge's history survives redeploys.
# Article II: the chain is append-only and must never be reset in place.
VOLUME ["/data"]
ENV FORGE_DB=/data/forge.db \
    FORGE_HOST=0.0.0.0 \
    FORGE_PORT=8600 \
    FORGE_TICK_SECONDS=20

EXPOSE 8600

# `run` performs genesis automatically if the Ledger is empty, then serves
# the engine and the public interface together.
CMD ["python", "-m", "forge", "run"]
