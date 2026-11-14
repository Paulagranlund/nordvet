"""
Generate a static HTML dashboard from the marts.

Usage:
    python -m pipeline.dashboard      # writes output/dashboard.html

Reads only from the marts schema, so it sees the same numbers Power BI would.
The HTML is self-contained (no internet needed): data is embedded as JSON
and drawn with a little plain JavaScript.
"""

import datetime as dt
import decimal
import json
import logging
from pathlib import Path

import duckdb

from pipeline.load import DB_PATH

log = logging.getLogger("dashboard")

OUTPUT = Path("output/dashboard.html")
TEMPLATE = Path(__file__).parent / "dashboard_template.html"


def rows(con, sql):
    """Run a query and return a list of dicts (JSON friendly)."""
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    def clean(v):
        if isinstance(v, (dt.date, dt.datetime)):
            return v.isoformat()
        if isinstance(v, decimal.Decimal):
            return float(v)
        return v
    return [{c: clean(v) for c, v in zip(cols, r)} for r in cur.fetchall()]


def collect(con):
    expected = "count(*) FILTER (WHERE outcome IN ('no_show','completed'))"
    rate = f"count(*) FILTER (WHERE outcome = 'no_show') / nullif({expected}, 0)"
    return {
        "data_as_of": con.execute(
            "SELECT strftime(max(updated_at::TIMESTAMPTZ) AT TIME ZONE 'Europe/Copenhagen', '%Y-%m-%d %H:%M') "
            "FROM raw.appointments").fetchone()[0],
        "morning": rows(con, "SELECT * FROM marts.report_last_day"),
        "last_28d": rows(con, "SELECT * FROM marts.clinic_last_28d"),
        # Weekly no-show rate per clinic + chain, for the trend chart
        "weekly": rows(con, """
            SELECT c.clinic_name, date_trunc('week', d.scheduled_date)::DATE AS week,
                   sum(d.no_shows) AS no_shows, sum(d.completed + d.no_shows) AS expected
            FROM marts.clinic_daily d JOIN marts.dim_clinic c USING (clinic_id)
            WHERE c.is_active GROUP BY ALL ORDER BY week"""),
        # Drivers over the whole period
        "by_lead_time": rows(con, f"""
            SELECT CASE WHEN date_diff('day', created_at, scheduled_at) < 1 THEN 'Same day'
                        WHEN date_diff('day', created_at, scheduled_at) <= 3 THEN '1–3 days'
                        WHEN date_diff('day', created_at, scheduled_at) <= 7 THEN '4–7 days'
                        WHEN date_diff('day', created_at, scheduled_at) <= 14 THEN '8–14 days'
                        ELSE '15+ days' END AS label,
                   min(date_diff('day', created_at, scheduled_at)) AS sort_key,
                   {rate} AS rate, {expected} AS n
            FROM marts.fct_appointment WHERE outcome IN ('no_show', 'completed')
            GROUP BY 1 ORDER BY sort_key"""),
        "by_service": rows(con, f"""
            SELECT service AS label, {rate} AS rate, {expected} AS n,
                   sum(duration_minutes) FILTER (WHERE outcome = 'no_show') / 60.0 AS lost_vet_hours
            FROM marts.fct_appointment WHERE outcome IN ('no_show', 'completed')
            GROUP BY 1 ORDER BY rate DESC"""),
    }


def run():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    data = collect(con)
    con.close()
    html = TEMPLATE.read_text().replace("__DATA__", json.dumps(data, default=str))
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(html)
    log.info("Dashboard written to %s", OUTPUT.resolve())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
