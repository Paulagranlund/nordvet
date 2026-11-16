# Nordvet: a first slice of their data platform

A small pipeline that pulls appointments from the VetOne API, models them in SQL and produces
a morning no-show view per clinic for Nordvet.

```
VetOne API ──► load.py ──► raw ──► staging ──► marts ──► dashboard.html  (this demo)
  (Python)                  └────── DuckDB, plain SQL ──────┘ └─► Power BI   (proposed for Nordvet)
```

Main findings are listed in **[FINDINGS.md](FINDINGS.md)**.

## How to run it

Requires Python 3.11+.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env              # then fill in VETONE_CLIENT_ID and VETONE_CLIENT_SECRET

.venv/bin/python -m pipeline.run  # load -> transform + checks -> dashboard
open output/dashboard.html
```

The first run does a full load, which takes about 6 minutes, because of the API's rate limits. After that each run is incremental and takes a few seconds. `--full` forces a full reload.

The steps can also be run on their own: `python -m pipeline.load`, `pipeline.transform`, `pipeline.dashboard`.

## Project structure

```
pipeline/
  api_client.py     Talks to VetOne: token refresh, retries, pagination, throttling
  load.py           Extract + load into the raw layer (full or incremental)
  transform.py      Runs the SQL files in sql/ in order, then data quality checks
  dashboard.py      Reads the marts and writes output/dashboard.html
  run.py            Runs all of the above; the one command a scheduler would call
sql/
  01_staging.sql    Types, names, removes deleted appointments
  02_marts.sql      Business logic: outcomes, daily clinic metrics, morning report, flags
analysis/
  findings.py       Reproduces every number in FINDINGS.md
tests/
  test_models.py    SQL models and loading on a small made-up dataset
  test_api_client.py  API client against a fake API: pagination, retries, errors
.github/workflows/
  ci.yml            Lint + tests on every push and pull request
  daily.yml         Runs the pipeline every morning (demo of scheduling)
```

## Tests and CI

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check .    # lint
.venv/bin/pytest          # tests
```

The tests use made-up data and a fake API, so they need no credentials and no network.
They check that each appointment gets the right outcome, that the daily counts and no-show
rate are right, that deleted appointments are excluded, that days follow the clinic's local
time, that re-loading a row replaces it instead of duplicating it, and that the API client
follows pagination, retries on 429/500 and fails fast on 400.

GitHub Actions runs the same lint and tests on every push to `main` and on every pull request.

## Daily run (demo)

`.github/workflows/daily.yml` runs the pipeline every morning and on demand (Actions → Daily pipeline → Run workflow). The database is kept between runs in the Actions cache so runs stay incremental, credentials come from repository secrets, and the dashboard is attached to each run. It demonstrates scheduling only; for Nordvet the same command would run in an Azure Function with Power BI (see below).

## Design decisions

**Daily batch, not real-time.** No-shows are recorded in VetOne a median of 26 hours after the
appointment, sometimes up to 5 days later. A real-time pipeline would show the same incomplete
numbers faster, at higher cost and complexity. Running once each morning (or twice a day, if needed) matches how quickly outcomes are recorded in VetOne, and how often Nordvet needs the numbers.

**Incremental loading with a watermark.** Each run asks for appointments updated since the
newest `updated_at` already loaded, minus a 1-hour overlap, with `include_deleted=true`.
Status changes (including late no-shows) and deletions both update `updated_at`, so they arrive
in the next run. `updated_at` was verified not to be backdated by comparing two snapshots taken 14 hours apart. The watermark is derived from the data itself, so it can never get out of sync.

**Idempotent, all-or-nothing loads.** Rows are upserted on `id` (`INSERT OR REPLACE`), so
re-running is harmless. Everything is extracted first and written in a single transaction: the
API returns pages ordered by appointment time, not `updated_at`, so saving page by page and
crashing halfway could move the watermark past rows that were never saved.

**Layers: raw → staging → marts.** Raw is exactly what the API returned, so business logic can
be changed and rebuilt without calling the client's API again. Staging only cleans (types, names,
deleted rows). Marts hold every business rule in one place: what counts as a no-show, how the rate
is calculated, and when a clinic is flagged.

**Plain SQL, DuckDB locally.** IT team is two people who know SQL and Power BI, so all logic
they would maintain is in `.sql` files. DuckDB runs the demo with no server. The SQL is standard
apart from DuckDB's `count(*) FILTER (...)` and `AT TIME ZONE` syntax, which translate directly
to `SUM(CASE ...)` and `AT TIME ZONE` in Azure SQL.

**Views, not tables.** At about 70,000 rows, views are instant and always current. With much more
data, the marts should be materialised as tables, rebuilt after each load.

**Data quality checks on every run.** Duplicate ids, unknown clinics or unknown statuses stop the
pipeline, so the dashboard is never built on data we don't understand. Stale data, or
appointments with no outcome after 7 days, log a warning. Each step exits non-zero on failure,
so a scheduler can alert.

**Clinic level only, no per-vet view.** The data has `vet_id`, and is used to investigate
Odense C. A dashboard ranking individual vets would be sensitive and invites blame for something
(owners not showing up) that is more likely a process issue.

## Definitions

| Term | Definition |
|---|---|
| No-show rate | no-shows ÷ (completed + no-shows). Cancelled and not-yet-recorded appointments are excluded |
| Lost vet-hours | sum of `duration_minutes` of no-show appointments ÷ 60 |
| No outcome yet | appointment time has passed but the status is still `booked` |
| Cancelled after start | `cancelled_at` is after `scheduled_at`. Kept separate, not counted as a no-show |
| Day | the clinic's local calendar date (`Europe/Copenhagen` or `Europe/Stockholm`) |
| Last working day | the latest past date with appointments, so a Monday report shows Saturday |
| Flags | *Above normal*: ≥ 2 no-shows and ≥ 8 points above the clinic's previous 28 days. *Persistently high*: 4-week rate ≥ 1.5× chain. *Getting worse*: 4-week rate ≥ 5 points above the 4 weeks before |

## API findings

- The token expires after about an hour, so the client refreshes it.
- It rate-limits with `429` and a `Retry-After` header, and returns occasional `500`s. The client waits and retries, and also paces requests at 0.5 s.
- `limit` is silently capped at 500, so the client always follows `next_cursor`.
- Deleted appointments are hidden unless `include_deleted=true` (about 950 rows), so the load always sends it.
- `+` in a timezone offset in `updated_since` must be URL-encoded, otherwise the API returns `400`.
- Appointments have more fields than documented: `vet_id`, `patient_ref`, `duration_minutes`, `created_at`, `updated_at`, `cancelled_at`, `deleted_at`.
- There are 39 clinics, not 38. Ystad (`CL-420`) is inactive with no appointments after 10 September. It is kept in history but left out of daily reports.
- Data starts on 6 August 2026 and bookings run about a month ahead.

## Assumptions

**Given**
- All clinics use VetOne, and the API is the only source.
- Nordvet is on Microsoft 365 and Azure, and the team that will run this knows SQL and Power BI.

**Observed** 
- `updated_at` changes on every status change and deletion, and is not backdated.
- Each service has a fixed duration (consultation 20 min, vaccination and follow-up 15, dental 45, surgery 90).
- Each `vet_id` belongs to exactly one clinic.
- Every clinic records no-shows equally late (median 24–28 hours), so comparisons between clinics are fair.

**Assumed** 
- `deleted_at` means the appointment should not count at all, for example because it was booked by mistake.
- `vet_id` is a veterinarian.
- A `cancelled` appointment with `cancelled_at` after the start time is not a no-show. 
- Booked duration is a fair measure of lost capacity. There are no prices in the data, so money
  is lost vet-hours × whatever a vet-hour is worth to Nordvet.

## Left out on purpose

- **Change history.** Only the latest version of each appointment is kept. A history table would show exactly when no-shows get registered and allow reproducing old reports. This would be a priority when doing a bigger report. 
- **Real-time and streaming**, for the reasons above.
- **Orchestration tooling** (Airflow, Data Factory and so on). One scheduled command is enough at this size.
- **dbt.** The staging/marts structure follows the same idea, and dbt is a natural next step if the SQL grows.
- **Money.** There are no prices in the data, and I didn't invent them.
- **Per-vet reporting**, see above.

## Where it would run for Nordvet

```
Azure Function (Python, timer trigger, daily 06:00)
   │  runs pipeline.run (load + SQL models + checks); secrets from Azure Key Vault
   ▼
Azure SQL Database ── raw / staging / marts schemas (the SQL files, adapted)
   │
   ▼
Power BI (Nordvet's M365 tenant) ── scheduled refresh after the load
```

- **Azure Function, consumption plan:** costs practically nothing at one short run a day. It needs no servers to look after.
- **Azure SQL Database, small tier:** cheap at this data size, and Emil's team can query it from tools they already know. Power BI connects to it natively.
- **Key Vault** holds the VetOne credentials. Function logs and alerts go to Application Insights, and a failed run or failed check sends an email.
- **Power BI** replaces `dashboard.html`. The marts are already shaped for it: one fact table and one clinic dimension, plus the report views.
- Total running cost should be a small fraction of the cost of a single day's no-shows.

