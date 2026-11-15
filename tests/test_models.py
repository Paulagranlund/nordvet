"""
Tests for the loading logic and the SQL models, on a small made-up dataset.

No API calls and no real data: each test builds a fresh, temporary DuckDB
database, loads a few hand-written appointments and checks the results.
"""

import duckdb
import pytest

from pipeline.load import APPOINTMENT_COLUMNS, create_tables, load_json_rows
from pipeline.transform import build_models, run_checks

CLINIC_COLUMNS = {"id": "VARCHAR", "name": "VARCHAR", "city": "VARCHAR", "country": "VARCHAR",
                  "timezone": "VARCHAR", "active": "BOOLEAN", "opened_on": "DATE"}

CLINICS = [
    {"id": "CL-001", "name": "Test Clinic", "city": "Copenhagen", "country": "DK",
     "timezone": "Europe/Copenhagen", "active": True, "opened_on": "2020-01-01"},
]


def appt(id, status, scheduled_at="2026-01-05T10:00+01:00", duration=20, **extra):
    """Build one appointment like the API returns it. Unknown fields default to None."""
    row = {col: None for col in APPOINTMENT_COLUMNS}
    row.update(id=id, clinic_id="CL-001", vet_id="V0011", patient_ref="P1", service="consultation",
               scheduled_at=scheduled_at, duration_minutes=duration, status=status,
               created_at="2026-01-01T09:00:00Z", updated_at="2026-01-05T12:00:00Z")
    row.update(extra)
    return row


# One day (Monday 5 Jan 2026) at one clinic, with one appointment of each kind
APPOINTMENTS = [
    appt("a1", "completed"),
    appt("a2", "completed"),
    appt("a3", "no_show"),
    appt("a4", "cancelled", cancelled_at="2026-01-04T08:00:00Z"),   # cancelled the day before
    appt("a5", "cancelled", cancelled_at="2026-01-05T11:00:00Z"),   # cancelled after the 10:00 start
    appt("a6", "booked"),                                           # in the past, no outcome yet
    appt("a7", "completed", deleted_at="2026-01-05T13:00:00Z"),     # deleted: must be ignored
    appt("a8", "booked", scheduled_at="2099-01-05T10:00+01:00"),    # far in the future
]


@pytest.fixture
def con():
    """A fresh in-memory database with the test data loaded and models built."""
    con = duckdb.connect(":memory:")
    create_tables(con)
    load_json_rows(con, "raw.clinics", CLINICS, CLINIC_COLUMNS)
    load_json_rows(con, "raw.appointments", APPOINTMENTS, APPOINTMENT_COLUMNS)
    build_models(con)
    yield con
    con.close()


def outcomes(con):
    return dict(con.execute(
        "SELECT appointment_id, outcome FROM marts.fct_appointment").fetchall())


def test_each_appointment_gets_the_right_outcome(con):
    assert outcomes(con) == {
        "a1": "completed",
        "a2": "completed",
        "a3": "no_show",
        "a4": "cancelled",
        "a5": "cancelled_after_start",
        "a6": "unresolved",
        "a8": "upcoming",
    }


def test_deleted_appointments_are_excluded(con):
    assert "a7" not in outcomes(con)


def test_daily_counts_and_no_show_rate(con):
    row = con.execute("""
        SELECT appointments, completed, no_shows, cancelled, cancelled_after_start,
               unresolved, no_show_rate, lost_vet_hours, is_provisional
        FROM marts.clinic_daily WHERE scheduled_date = '2026-01-05'
    """).fetchone()
    appointments, completed, no_shows, cancelled, late_cancel, unresolved, rate, lost, provisional = row
    assert (appointments, completed, no_shows, cancelled, late_cancel, unresolved) == (6, 2, 1, 1, 1, 1)
    # rate = no-shows / (completed + no-shows): cancellations and unresolved are left out
    assert rate == pytest.approx(1 / 3)
    assert lost == pytest.approx(20 / 60)   # one 20-minute no-show
    assert provisional is True              # a6 has no outcome yet


def test_day_is_the_clinics_local_date(con):
    # 00:15 in Copenhagen on 6 Jan is still 5 Jan in UTC: it must count as 6 Jan
    load_json_rows(con, "raw.appointments",
                   [appt("tz1", "completed", scheduled_at="2026-01-06T00:15+01:00")],
                   APPOINTMENT_COLUMNS)
    date = con.execute("SELECT scheduled_date FROM marts.fct_appointment "
                       "WHERE appointment_id = 'tz1'").fetchone()[0]
    assert str(date) == "2026-01-06"


def test_upsert_replaces_instead_of_duplicating(con):
    # The same appointment arrives again, now with a new status
    load_json_rows(con, "raw.appointments",
                   [appt("a6", "no_show", updated_at="2026-01-06T09:00:00Z")],
                   APPOINTMENT_COLUMNS)
    rows = con.execute("SELECT status FROM raw.appointments WHERE id = 'a6'").fetchall()
    assert rows == [("no_show",)]


def test_quality_checks_pass_on_good_data(con):
    assert run_checks(con) == []


def test_quality_checks_catch_unknown_status(con):
    load_json_rows(con, "raw.appointments", [appt("bad1", "rescheduled")], APPOINTMENT_COLUMNS)
    assert "only known status values" in run_checks(con)
