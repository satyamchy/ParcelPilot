"""Reads the dataset snapshot time written by ingest.py — the reference
'now' for all time-based questions (SLA elapsed hours, delay durations)."""
from datetime import datetime
from pathlib import Path

from app.config import settings

_SNAPSHOT_FILE = Path(settings.sqlite_db_path).parent / "snapshot_time.txt"


def get_snapshot_time() -> datetime:
    if _SNAPSHOT_FILE.exists():
        return datetime.fromisoformat(_SNAPSHOT_FILE.read_text().strip())
    return datetime.fromisoformat(settings.default_snapshot_time)
