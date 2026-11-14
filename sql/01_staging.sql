-- STAGING: clean, typed versions of the raw tables.
-- No business logic here: only types, names, and removing deleted records.

CREATE SCHEMA IF NOT EXISTS staging;

CREATE OR REPLACE VIEW staging.clinics AS
SELECT
    id          AS clinic_id,
    name        AS clinic_name,
    city,
    country,
    timezone,
    active      AS is_active,
    opened_on
FROM raw.clinics;


CREATE OR REPLACE VIEW staging.appointments AS
SELECT
    a.id                            AS appointment_id,
    a.clinic_id,
    a.vet_id,
    a.patient_ref,
    a.service,
    a.duration_minutes,
    a.status,
    a.scheduled_at::TIMESTAMPTZ     AS scheduled_at,
    -- The clinic's local calendar date. "Yesterday" should mean yesterday
    -- in Copenhagen or Stockholm, not in UTC or wherever the server runs.
    (a.scheduled_at::TIMESTAMPTZ AT TIME ZONE c.timezone)::DATE AS scheduled_date,
    c.timezone                      AS clinic_timezone,
    a.created_at::TIMESTAMPTZ       AS created_at,
    a.updated_at::TIMESTAMPTZ       AS updated_at,
    a.cancelled_at::TIMESTAMPTZ     AS cancelled_at
FROM raw.appointments AS a
JOIN raw.clinics AS c
    ON c.id = a.clinic_id
-- Deleted appointments are hidden by VetOne's own default; we treat them as never existing.
WHERE a.deleted_at IS NULL;
