"""
Build the SQL models (staging + marts) and run data quality checks.

Usage:
    python -m pipeline.transform

Runs every .sql file in the sql/ folder in name order (01_..., 02_...).
"""

import logging
from pathlib import Path

import duckdb

from pipeline.load import DB_PATH

log = logging.getLogger("transform")

SQL_DIR = Path(__file__).parent.parent / "sql"

# Data quality checks. Each query returns the rows that break a rule,
# so 0 rows = check passed.
#   "error": the data is wrong; stop and investigate
#   "warn":  worth knowing, but the report is still usable
CHECKS = {
    "appointment ids are unique": ("error", """
        SELECT id FROM raw.appointments GROUP BY id HAVING count(*) > 1"""),
    "every appointment belongs to a known clinic": ("error", """
        SELECT a.id FROM raw.appointments a
        LEFT JOIN raw.clinics c ON c.id = a.clinic_id WHERE c.id IS NULL"""),
    "only known status values": ("error", """
        SELECT id FROM raw.appointments
        WHERE status NOT IN ('booked', 'completed', 'no_show', 'cancelled')"""),
    "data is fresh (changes within the last 24h)": ("warn", """
        SELECT 1 FROM raw.appointments
        HAVING max(updated_at::TIMESTAMPTZ) < now() - INTERVAL 24 HOUR"""),
    "no appointments unresolved for more than 7 days": ("warn", """
        SELECT appointment_id FROM marts.fct_appointment
        WHERE outcome = 'unresolved' AND scheduled_at < now() - INTERVAL 7 DAY"""),
}


def build_models(con):
    """Run every .sql file in sql/ in name order, on the given database connection."""
    for sql_file in sorted(SQL_DIR.glob("*.sql")):
        log.info("Running %s", sql_file.name)
        con.execute(sql_file.read_text())


def run_checks(con):
    """Run the data quality checks. Returns the names of failed 'error' checks."""
    failed = []
    for name, (severity, query) in CHECKS.items():
        bad_rows = len(con.execute(query).fetchall())
        if bad_rows == 0:
            log.info("CHECK OK    %s", name)
        elif severity == "warn":
            log.warning("CHECK WARN  %s (%d rows)", name, bad_rows)
        else:
            log.error("CHECK FAIL  %s (%d rows)", name, bad_rows)
            failed.append(name)
    return failed


def run():
    con = duckdb.connect(str(DB_PATH))
    build_models(con)
    failed = run_checks(con)
    con.close()
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
