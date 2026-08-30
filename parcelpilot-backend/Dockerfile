FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


COPY . .

# Build SQLite + Chroma during Docker image creation
RUN python -m app.ingestion.load_structured_data && \
    python -m app.ingestion.embed_documents

EXPOSE 10000

EXPOSE 10000

EXPOSE 10000

CMD ["sh", "-c", "echo '=== STARTING PARCELPILOT FASTAPI ===' && uvicorn main:app --host 0.0.0.0 --port 10000 --log-level debug"]
