"""
Extract data from the VetOne API and load it into the raw layer in DuckDB.

Usage:
    python -m pipeline.load          # incremental: only what changed since last run
    python -m pipeline.load --full   # full reload of all appointments

Design:
  - raw.clinics       small table, fully replaced every run
  - raw.appointments  one row per appointment id (latest version), upserted
  - meta.load_runs    one row per run, for monitoring

  Incremental logic: watermark = newest updated_at already in raw.appointments.
  We ask the API for everything updated since (watermark - OVERLAP), always with
  include_deleted=true so deletions reach us too. Re-loading a row we already
  have is harmless because we upsert on id (the load is idempotent).
"""

import argparse
import datetime as dt
import json
import logging
import os
import tempfile
from pathlib import Path

import duckdb

from pipeline.api_client import VetOneClient

log = logging.getLogger("load")

DB_PATH = Path(os.environ.get("NORDVET_DB", "data/nordvet.duckdb"))
OVERLAP = dt.timedelta(hours=1)  # safety margin for records saved while we ran

# Columns exactly as the API returns them. Kept as text in the raw layer;
# converting to proper types is done in SQL in the next layer.
APPOINTMENT_COLUMNS = {
    "id": "VARCHAR",
    "clinic_id": "VARCHAR",
    "vet_id": "VARCHAR",
    "patient_ref": "VARCHAR",
    "service": "VARCHAR",
    "scheduled_at": "VARCHAR",
    "duration_minutes": "INTEGER",
    "status": "VARCHAR",
    "created_at": "VARCHAR",
    "updated_at": "VARCHAR",
    "cancelled_at": "VARCHAR",  # only present on cancelled appointments
    "deleted_at": "VARCHAR",    # only present with include_deleted=true
}


def create_tables(con):
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    con.execute("CREATE SCHEMA IF NOT EXISTS meta")
    cols = ",\n".join(f"{name} {typ}" for name, typ in APPOINTMENT_COLUMNS.items())
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS raw.appointments (
            {cols},
            _loaded_at TIMESTAMPTZ,
            PRIMARY KEY (id)
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS raw.clinics (
            id VARCHAR PRIMARY KEY, name VARCHAR, city VARCHAR, country VARCHAR,
            timezone VARCHAR, active BOOLEAN, opened_on DATE, _loaded_at TIMESTAMPTZ
        )""")
    con.execute("""
        CREATE TABLE IF NOT EXISTS meta.load_runs (
            started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, mode VARCHAR,
            updated_since VARCHAR, rows_fetched INTEGER, status VARCHAR, error VARCHAR
        )""")


def get_watermark(con):
    """Newest updated_at we already have, or None if the table is empty."""
    # updated_at is ISO text in UTC ("2026-11-06T09:15:00Z"), so text max = time max
    value = con.execute("SELECT max(updated_at) FROM raw.appointments").fetchone()[0]
    return dt.datetime.fromisoformat(value) if value else None


def load_json_rows(con, table, rows, columns):
    """Upsert a list of dicts into a table, via a temporary JSON file."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(rows, f)
        path = f.name
    try:
        col_spec = "{" + ", ".join(f"'{k}': '{v}'" for k, v in columns.items()) + "}"
        col_list = ", ".join(columns)
        con.execute(f"""
            INSERT OR REPLACE INTO {table} ({col_list}, _loaded_at)
            SELECT {col_list}, now()
            FROM read_json('{path}', format='array', columns={col_spec})
        """)
    finally:
        os.remove(path)


def run(full=False):
    started = dt.datetime.now(dt.timezone.utc)
    DB_PATH.parent.mkdir(exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    create_tables(con)
    client = VetOneClient()

    watermark = None if full else get_watermark(con)
    if watermark is None:
        mode, updated_since = "full", "1970-01-01T00:00:00+00:00"  # = everything
    else:
        mode = "incremental"
        updated_since = (watermark - OVERLAP).astimezone(dt.timezone.utc).isoformat()
    log.info("Mode: %s, fetching appointments updated since %s", mode, updated_since)

    rows_fetched = 0
    try:
        # 1. Extract everything first (in memory), so a crash halfway saves nothing
        clinics = client.get_clinics()
        appointments = []
        for i, page in enumerate(
            client.iter_appointments(updated_since=updated_since, include_deleted="true"), 1
        ):
            appointments.extend(page)
            log.info("Page %d: %d rows (total %d)", i, len(page), len(appointments))
        rows_fetched = len(appointments)

        # 2. Load everything in ONE transaction: all or nothing
        con.execute("BEGIN TRANSACTION")
        con.execute("DELETE FROM raw.clinics")
        load_json_rows(con, "raw.clinics", clinics, {
            "id": "VARCHAR", "name": "VARCHAR", "city": "VARCHAR", "country": "VARCHAR",
            "timezone": "VARCHAR", "active": "BOOLEAN", "opened_on": "DATE",
        })
        if appointments:
            load_json_rows(con, "raw.appointments", appointments, APPOINTMENT_COLUMNS)
        con.execute("COMMIT")
        status, error = "success", None
    except Exception as e:
        if con.in_transaction:
            con.execute("ROLLBACK")
        status, error = "failed", str(e)
        log.exception("Load failed")

    con.execute(
        "INSERT INTO meta.load_runs VALUES (?, now(), ?, ?, ?, ?, ?)",
        [started.isoformat(), mode, updated_since, rows_fetched, status, error],
    )
    total = con.execute("SELECT count(*) FROM raw.appointments").fetchone()[0]
    log.info("Done: %s. Fetched %d rows, raw.appointments now has %d rows",
             status, rows_fetched, total)
    con.close()
    if status != "success":
        raise SystemExit(1)  # non-zero exit code so a scheduler sees the failure


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="reload all appointments")
    run(full=parser.parse_args().full)
