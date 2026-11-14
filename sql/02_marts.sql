-- MARTS: business logic. These are the tables Mette (via Power BI) looks at.

CREATE SCHEMA IF NOT EXISTS marts;


-- Dimension: one row per clinic (what we group and filter by)
CREATE OR REPLACE VIEW marts.dim_clinic AS
SELECT * FROM staging.clinics;


-- Fact: one row per appointment, with a clear outcome
CREATE OR REPLACE VIEW marts.fct_appointment AS
SELECT
    *,
    CASE
        WHEN status = 'completed' THEN 'completed'
        WHEN status = 'no_show'   THEN 'no_show'
        -- Cancelled after the appointment had started: possibly a no-show
        -- recorded the wrong way. Kept separate so it can be investigated.
        WHEN status = 'cancelled' AND cancelled_at >= scheduled_at THEN 'cancelled_after_start'
        WHEN status = 'cancelled' THEN 'cancelled'
        -- Appointment time has passed but the clinic hasn't recorded an outcome yet.
        -- No-shows are typically recorded ~1 day late, so many of these will become no-shows.
        WHEN status = 'booked' AND scheduled_at < now() THEN 'unresolved'
        WHEN status = 'booked' THEN 'upcoming'
        ELSE 'unknown'
    END AS outcome
FROM staging.appointments;


-- One row per clinic per day (past days only)
CREATE OR REPLACE VIEW marts.clinic_daily AS
WITH counts AS (
    SELECT
        clinic_id,
        scheduled_date,
        count(*)                                              AS appointments,
        count(*) FILTER (WHERE outcome = 'completed')         AS completed,
        count(*) FILTER (WHERE outcome = 'no_show')           AS no_shows,
        count(*) FILTER (WHERE outcome = 'cancelled')         AS cancelled,
        count(*) FILTER (WHERE outcome = 'cancelled_after_start') AS cancelled_after_start,
        count(*) FILTER (WHERE outcome = 'unresolved')        AS unresolved,
        -- Lost capacity: vet time booked for animals that never came
        coalesce(sum(duration_minutes) FILTER (WHERE outcome = 'no_show'), 0) / 60.0
                                                              AS lost_vet_hours
    FROM marts.fct_appointment
    WHERE scheduled_date < (now() AT TIME ZONE clinic_timezone)::DATE  -- before today, local time
    GROUP BY clinic_id, scheduled_date
)
SELECT
    *,
    -- No-show rate = no-shows / appointments where the animal was expected.
    -- Cancelled appointments are excluded: nobody was expected to come.
    -- Unresolved are excluded: we don't know the outcome yet.
    no_shows / nullif(completed + no_shows, 0)                AS no_show_rate,
    unresolved > 0                                            AS is_provisional
FROM counts;


-- Mette's morning view: how did each clinic do on the last working day,
-- compared to its own normal level (the 28 days before)?
-- "Last working day" (not "yesterday") so a Monday report shows Saturday, not an empty Sunday.
CREATE OR REPLACE VIEW marts.report_last_day AS
WITH last_day AS (
    SELECT max(scheduled_date) AS report_date FROM marts.clinic_daily
),
baseline AS (
    SELECT
        d.clinic_id,
        sum(d.no_shows) / nullif(sum(d.completed + d.no_shows), 0) AS no_show_rate_prev_28d
    FROM marts.clinic_daily AS d, last_day AS l
    WHERE d.scheduled_date >= l.report_date - 28
      AND d.scheduled_date <  l.report_date
    GROUP BY d.clinic_id
)
SELECT
    l.report_date,
    c.clinic_name,
    c.country,
    d.appointments,
    d.completed,
    d.no_shows,
    d.cancelled,
    d.cancelled_after_start,
    d.unresolved,
    round(d.no_show_rate, 3)             AS no_show_rate,
    round(b.no_show_rate_prev_28d, 3)    AS no_show_rate_prev_28d,
    round(d.lost_vet_hours, 1)           AS lost_vet_hours,
    d.is_provisional,
    -- Flag: clearly worse than this clinic's own normal. One day is a small
    -- sample, so require at least 2 no-shows AND 8 percentage points above normal.
    coalesce(d.no_shows >= 2
             AND d.no_show_rate >= b.no_show_rate_prev_28d + 0.08, false) AS is_above_normal
FROM marts.dim_clinic AS c
CROSS JOIN last_day AS l
LEFT JOIN marts.clinic_daily AS d
    ON d.clinic_id = c.clinic_id AND d.scheduled_date = l.report_date
LEFT JOIN baseline AS b
    ON b.clinic_id = c.clinic_id
WHERE c.is_active
ORDER BY d.no_show_rate DESC NULLS LAST;


-- Each clinic's last 28 days, compared with the 28 days before that.
-- Used to spot clinics that are persistently high, or getting worse.
CREATE OR REPLACE VIEW marts.clinic_last_28d AS
WITH last_day AS (
    SELECT max(scheduled_date) AS report_date FROM marts.clinic_daily
),
periods AS (
    SELECT
        d.clinic_id,
        sum(d.no_shows)          FILTER (WHERE d.scheduled_date >  l.report_date - 28) AS no_shows,
        sum(d.completed + d.no_shows) FILTER (WHERE d.scheduled_date > l.report_date - 28) AS expected,
        sum(d.lost_vet_hours)    FILTER (WHERE d.scheduled_date >  l.report_date - 28) AS lost_vet_hours,
        sum(d.no_shows)          FILTER (WHERE d.scheduled_date <= l.report_date - 28) AS prior_no_shows,
        sum(d.completed + d.no_shows) FILTER (WHERE d.scheduled_date <= l.report_date - 28) AS prior_expected
    FROM marts.clinic_daily AS d, last_day AS l
    WHERE d.scheduled_date > l.report_date - 56
    GROUP BY d.clinic_id
),
chain AS (
    SELECT sum(no_shows) / sum(expected) AS chain_rate FROM periods
)
SELECT
    c.clinic_name,
    c.country,
    p.no_shows,
    p.expected,
    round(p.lost_vet_hours, 1)                       AS lost_vet_hours,
    round(p.no_shows / nullif(p.expected, 0), 3)     AS no_show_rate,
    round(p.prior_no_shows / nullif(p.prior_expected, 0), 3) AS no_show_rate_prior_28d,
    round(ch.chain_rate, 3)                          AS chain_rate,
    -- Persistently high: at least 1.5x the chain-wide rate over 4 weeks
    p.no_shows / nullif(p.expected, 0) >= 1.5 * ch.chain_rate AS is_persistently_high,
    -- Getting worse: up at least 5 percentage points vs the 4 weeks before
    p.no_shows / nullif(p.expected, 0)
        >= p.prior_no_shows / nullif(p.prior_expected, 0) + 0.05 AS is_getting_worse
FROM periods AS p
JOIN marts.dim_clinic AS c USING (clinic_id)
CROSS JOIN chain AS ch
WHERE c.is_active
ORDER BY no_show_rate DESC;
