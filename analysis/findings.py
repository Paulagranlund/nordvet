"""
Reproduce every number in FINDINGS.md from the local database.

Usage:
    python -m analysis.findings

Reads data/nordvet.duckdb (run the pipeline first) and prints the results.
Uses all past appointments (before today), excluding deleted ones, like the marts.
Nothing is written anywhere: the queries are code, the data stays local.
"""

import duckdb

from pipeline.load import DB_PATH

# Shared pieces, so every query uses the same definitions as the marts
PAST = """
    FROM marts.fct_appointment f
    JOIN marts.dim_clinic c USING (clinic_id)
    WHERE f.scheduled_date < current_date
"""
EXPECTED = "count(*) FILTER (WHERE outcome IN ('no_show', 'completed'))"
RATE = f"round(100.0 * count(*) FILTER (WHERE outcome = 'no_show') / nullif({EXPECTED}, 0), 1)"
LEAD = """
    CASE WHEN date_diff('day', created_at, scheduled_at) < 1   THEN '1 same day'
         WHEN date_diff('day', created_at, scheduled_at) <= 3  THEN '2 1-3 days'
         WHEN date_diff('day', created_at, scheduled_at) <= 7  THEN '3 4-7 days'
         WHEN date_diff('day', created_at, scheduled_at) <= 14 THEN '4 8-14 days'
         ELSE '5 15+ days' END
"""
FULL_TIME_HOURS_PER_WEEK = 37  # Danish full-time week, used to express lost hours as vets


def show(con, title, sql):
    """Run a query and print the result as a simple table."""
    cur = con.execute(sql)
    cols = [c[0] for c in cur.description]
    rows = cur.fetchall()
    widths = [max(len(str(x)) for x in [col, *[r[i] for r in rows]]) for i, col in enumerate(cols)]
    print(f"\n== {title}")
    print("  " + "  ".join(str(c).ljust(w) for c, w in zip(cols, widths)))
    for r in rows:
        print("  " + "  ".join(str(x).ljust(w) for x, w in zip(r, widths)))


def main():
    con = duckdb.connect(str(DB_PATH), read_only=True)

    # The three clinics with the highest no-show rate over the whole period
    top3 = [r[0] for r in con.execute(f"""
        SELECT clinic_name {PAST} AND outcome IN ('no_show', 'completed')
        GROUP BY 1 ORDER BY {RATE} DESC LIMIT 3""").fetchall()]
    top3_sql = "(" + ", ".join(f"'{name}'" for name in top3) + ")"
    weeks = f"((SELECT max(scheduled_date) - min(scheduled_date) + 1 {PAST}) / 7.0)"

    show(con, "Period and volume", f"""
        SELECT min(scheduled_date) AS first_day, max(scheduled_date) AS last_day,
               count(*) AS past_appointments, {EXPECTED} AS expected_visits,
               count(*) FILTER (WHERE outcome = 'no_show') AS no_shows {PAST}""")

    show(con, "Overall no-show rate and lost vet-hours", f"""
        SELECT {RATE} AS no_show_rate_pct,
               round(sum(duration_minutes) FILTER (WHERE outcome = 'no_show') / 60.0) AS lost_vet_hours,
               round(sum(duration_minutes) FILTER (WHERE outcome = 'no_show') / 60.0 / {weeks})
                   AS lost_hours_per_week,
               round(sum(duration_minutes) FILTER (WHERE outcome = 'no_show') / 60.0 / {weeks} * 52)
                   AS lost_hours_per_year,
               round(sum(duration_minutes) FILTER (WHERE outcome = 'no_show') / 60.0 / {weeks}
                   / {FULL_TIME_HOURS_PER_WEEK}, 1) AS full_time_vets_equivalent
        {PAST}""")

    show(con, "Monthly rate (is it stable?)", f"""
        SELECT strftime(scheduled_date, '%Y-%m') AS month, {RATE} AS no_show_rate_pct, {EXPECTED} AS expected
        {PAST} GROUP BY 1 ORDER BY 1""")

    show(con, "Clinics: highest rates, and the rest of the chain", f"""
        SELECT clinic_name, {RATE} AS no_show_rate_pct,
               round(sum(duration_minutes) FILTER (WHERE outcome = 'no_show') / 60.0) AS lost_vet_hours
        {PAST} GROUP BY 1 ORDER BY 2 DESC LIMIT 4""")
    show(con, "Rest of the chain (all except the top 3)", f"""
        SELECT {RATE} AS no_show_rate_pct {PAST} AND clinic_name NOT IN {top3_sql}""")

    # Is the top 3's problem explained by vets, services or lead time? Compare like with like.
    show(con, "Top 3 vs rest: every vet at the top 3", f"""
        SELECT clinic_name, vet_id, {RATE} AS no_show_rate_pct, {EXPECTED} AS expected
        {PAST} AND clinic_name IN {top3_sql} GROUP BY 1, 2 ORDER BY 1, 2""")
    show(con, "Top 3 vs rest: by service", f"""
        SELECT service, clinic_name IN {top3_sql} AS is_top3, {RATE} AS no_show_rate_pct
        {PAST} GROUP BY 1, 2 ORDER BY 1, 2""")
    show(con, "Top 3 vs rest: by booking lead time", f"""
        SELECT {LEAD} AS booked_ahead, clinic_name IN {top3_sql} AS is_top3, {RATE} AS no_show_rate_pct
        {PAST} GROUP BY 1, 2 ORDER BY 1, 2""")

    show(con, "If the top 3 had the rest of the chain's rate", f"""
        WITH rest AS (
            SELECT count(*) FILTER (WHERE outcome = 'no_show') * 1.0 / {EXPECTED} AS rate
            {PAST} AND clinic_name NOT IN {top3_sql}
        ), top AS (
            SELECT {EXPECTED} AS expected, count(*) FILTER (WHERE outcome = 'no_show') AS no_shows,
                   sum(duration_minutes) FILTER (WHERE outcome = 'no_show') / 60.0 AS lost_hours
            {PAST} AND clinic_name IN {top3_sql}
        )
        SELECT top.no_shows, round(top.expected * rest.rate) AS no_shows_at_rest_rate,
               round(top.lost_hours * (1 - top.expected * rest.rate / top.no_shows)) AS hours_saved,
               round(top.lost_hours * (1 - top.expected * rest.rate / top.no_shows) / {weeks} * 52)
                   AS hours_saved_per_year
        FROM top, rest""")

    show(con, "Odense C: weekly rate", f"""
        SELECT date_trunc('week', scheduled_date)::DATE AS week, {RATE} AS no_show_rate_pct, {EXPECTED} AS expected
        {PAST} AND clinic_name = 'Nordvet Odense C' GROUP BY 1 ORDER BY 1""")
    show(con, "Odense C: per vet, before vs from 1 October", f"""
        SELECT vet_id, scheduled_date >= '2026-10-01' AS from_october, {RATE} AS no_show_rate_pct,
               {EXPECTED} AS expected
        {PAST} AND clinic_name = 'Nordvet Odense C' GROUP BY 1, 2 ORDER BY 1, 2""")

    show(con, "By booking lead time (all clinics)", f"""
        SELECT {LEAD} AS booked_ahead, {RATE} AS no_show_rate_pct, {EXPECTED} AS expected
        {PAST} GROUP BY 1 ORDER BY 1""")
    show(con, "By service", f"""
        SELECT service, {RATE} AS no_show_rate_pct,
               round(sum(duration_minutes) FILTER (WHERE outcome = 'no_show') / 60.0) AS lost_vet_hours
        {PAST} GROUP BY 1 ORDER BY 2 DESC""")
    show(con, "By weekday", f"""
        SELECT isodow(scheduled_date) AS day_no, dayname(scheduled_date) AS weekday,
               {RATE} AS no_show_rate_pct
        {PAST} GROUP BY 1, 2 ORDER BY 1""")
    show(con, "By hour of day (clinic local time)", f"""
        SELECT hour(scheduled_at AT TIME ZONE clinic_timezone) AS hour, {RATE} AS no_show_rate_pct
        {PAST} GROUP BY 1 ORDER BY 1""")

    show(con, "Repeat patients: rate given the animal's previous outcome", f"""
        WITH visits AS (
            SELECT outcome,
                   lag(outcome) OVER (PARTITION BY patient_ref ORDER BY scheduled_at) AS previous_outcome
            {PAST} AND outcome IN ('no_show', 'completed')
        )
        SELECT coalesce(previous_outcome, 'first visit') AS previous_outcome,
               {RATE} AS no_show_rate_pct, count(*) AS visits
        FROM visits GROUP BY 1 ORDER BY 1""")

    show(con, "Cancelled after start: total and spread across clinics", f"""
        WITH per_clinic AS (
            SELECT clinic_name, count(*) FILTER (WHERE outcome = 'cancelled_after_start') AS n,
                   100.0 * count(*) FILTER (WHERE outcome = 'cancelled_after_start') / count(*) AS pct
            {PAST} GROUP BY 1
        )
        SELECT sum(n) AS total, round(min(pct), 1) AS lowest_clinic_pct, round(max(pct), 1) AS highest_clinic_pct
        FROM per_clinic""")

    show(con, "How late no-shows are recorded (hours after the appointment)", f"""
        WITH per_clinic AS (
            SELECT clinic_name, median(date_diff('minute', scheduled_at, updated_at)) / 60.0 AS median_hours
            {PAST} AND outcome = 'no_show' GROUP BY 1
        )
        SELECT round((SELECT median(date_diff('minute', scheduled_at, updated_at)) / 60.0
                      {PAST} AND outcome = 'no_show'), 1) AS overall_median,
               round(min(median_hours), 1) AS fastest_clinic_median,
               round(max(median_hours), 1) AS slowest_clinic_median
        FROM per_clinic""")

    con.close()


if __name__ == "__main__":
    main()
