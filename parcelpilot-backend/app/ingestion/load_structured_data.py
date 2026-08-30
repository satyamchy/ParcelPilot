"""
Loads the workbook into SQLite, ONLY. Doesn't touch PDFs or Chroma — run
this independently whenever the workbook changes.

Usage:
    python -m app.ingestion.load_structured_data

Reads:  data/ParcelPilot_Assessment_Data.xlsx
Writes: data/db/parcelpilot.db          (one table per sheet)
        data/db/snapshot_time.txt        (reference "now" from the README sheet)
"""
import sqlite3
import re
from pathlib import Path

import pandas as pd

from app.config import settings


def load_workbook_to_sqlite() -> None:
    workbook_path = Path(settings.workbook_path)
    if not workbook_path.exists():
        raise FileNotFoundError(
            f"Workbook not found at {workbook_path}. Place "
            f"ParcelPilot_Assessment_Data.xlsx there first."
        )

    print(f"Reading workbook: {workbook_path}")
    xls = pd.ExcelFile(workbook_path)

    _write_snapshot_time(xls)

    conn = sqlite3.connect(settings.sqlite_db_path)
    for sheet in xls.sheet_names:
        if sheet == "README":
            continue
        df = pd.read_excel(xls, sheet_name=sheet)
        table_name = sheet.lower()
        df.to_sql(table_name, conn, if_exists="replace", index=False)
        print(f"  loaded sheet '{sheet}' -> table '{table_name}' ({len(df)} rows, "
              f"columns: {list(df.columns)})")
    conn.commit()
    conn.close()
    print(f"SQLite written to {settings.sqlite_db_path}")


def _write_snapshot_time(xls: pd.ExcelFile) -> None:
    """Reads the README sheet's 'Dataset Snapshot Time' row and caches it
    to a small file — this is the reference 'now' used for every
    time-based calculation (see app/snapshot.py)."""
    snapshot_file = Path(settings.sqlite_db_path).parent / "snapshot_time.txt"
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)

    if "README" not in xls.sheet_names:
        print("  WARNING: no README sheet found — using default snapshot "
              "time from config.py.")
        return

    # The real README sheet is a title + free-form key/value table, not a
    # clean Field/Value header — read it headerless and search for the row
    # whose first cell mentions "snapshot".
    readme = pd.read_excel(xls, sheet_name="README", header=None)
    snapshot_str = None
    for _, row in readme.iterrows():
        label = str(row[0]).strip().lower()
        if "snapshot" in label:
            snapshot_str = str(row[1]).strip()
            break

    if snapshot_str is None:
        print("  WARNING: no row mentioning 'snapshot' found in README — "
              "using default from config.py.")
        return

    # Value looks like "2026-08-16 11:00 Asia/Kolkata" — every other
    # timestamp in the workbook is a naive local time in the same zone, so
    # we just parse the date/time portion and compare naively throughout
    # rather than introduce partial timezone-awareness.
    match = re.match(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", snapshot_str)
    dt_str = match.group(1) if match else snapshot_str
    snapshot_dt = pd.to_datetime(dt_str).to_pydatetime()
    snapshot_file.write_text(snapshot_dt.isoformat())
    print(f"  snapshot time set to {snapshot_dt.isoformat()} (parsed from '{snapshot_str}')")


if __name__ == "__main__":
    print("=== Loading structured data ===\n")
    load_workbook_to_sqlite()
    print("\nDone.")
