# Bakes the data pack ingestion into the image at build time, so the
# running container never depends on a persistent writable volume for
# SQLite/Chroma — important on free hosting tiers where the filesystem
# resets on redeploy/restart.
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code AND the real data pack (data/pdfs/*.pdf,
# data/ParcelPilot_Assessment_Data.xlsx must exist before building this
# image — see README).
COPY . .

# Pre-download the sentence-transformers model and run both ingestion
# steps now, so the image ships with data/db/ already populated.
RUN python -m app.ingestion.load_structured_data && \
    python -m app.ingestion.embed_documents

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
