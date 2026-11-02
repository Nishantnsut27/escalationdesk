# Escalation Desk

A working local proof of concept for investigating failures across connected services.

Create an account, run a real service scenario, inspect the resulting incident, and generate an assessment with links to its source evidence. The React interface talks to a persisted Python backend and three independent HTTP service processes.

## Run on this machine

The local dependencies and frontend build are already prepared.

~~~powershell
.\start.ps1
~~~

Open **http://127.0.0.1:8000** and create your own account. No shared/default account is created.

Stop with Ctrl+C in the launcher, or from another terminal:

~~~powershell
.\.venv\Scripts\python.exe -m backend.launcher --stop
~~~

The launcher uses ports 8000-8003, checks service readiness and reports startup failures. It does not stop other applications occupying those ports.

## Try the complete flow

1. Create an account with a password of at least 10 characters.
2. Run **Connection pool exhaustion** or **Slow Payment processing**.
3. Wait for three checkout requests and automatic recovery. Downstream spans are collected even when the caller has already timed out.
4. Select the incident and click **Investigate** with **Evidence rules** selected.
5. Read the assessment, recommended checks and limitations.
6. Open a supporting evidence ID, or inspect the Logs, Traces and Metrics tabs.
7. Sign out and back in to revisit stored incidents and reports.

A **Healthy checkout** run captures evidence without creating a false incident. Recent runs remain inspectable. Another account cannot read or reset your runs or access your reports and evidence.

## What is implemented

- Real signup/login/logout with Argon2id password hashes, expiring server-side sessions, HttpOnly cookies, CSRF checks and authentication throttling.
- Account ownership enforced by the backend for runs, incidents, investigations and evidence.
- Gateway -> Order -> Payment over actual HTTP, with separate platform and Payment SQLite files.
- A pool of two real SQLite connections, one-second acquisition timeout and a controller that holds connections to reproduce exhaustion.
- Four-second Payment processing with a two-second Order timeout.
- Structured JSONL logs, measured operation spans carrying W3C trace/parent IDs, and service metric snapshots.
- Detection after three consecutive failed requests, one incident per run and verified automatic recovery.
- Read-only evidence tools, deterministic analysis, evidence-reference validation and saved reports.
- A React/TypeScript dashboard with scenario controls, service topology, incident history, evidence filtering and source-detail dialogs.
- A quota-aware Groq investigator with compact evidence, strict source citations, automatic wait/resume and saved diagnostics.

The default **Evidence rules** engine derives findings from observed logs, timings and metrics. It does not call a model, and the interface labels it accordingly. Scenario names and controller flags are not passed to the investigator.

## POC implementation and product plan

This POC uses the user-approved available stack: Python 3.14, Starlette, HTTPX, sqlite3 and React/TypeScript/Vite. The planned Python 3.13 environment could not be downloaded in this session.

Its connection pool holds real SQLite connections. Its trace exporter records actual operation durations and W3C parent relationships. These are lightweight POC implementations, not SQLAlchemy pooling or the OpenTelemetry SDK.

The complete product requirements remain in [prd.md](prd.md). Password recovery, Jev integration, SQLAlchemy/OpenTelemetry adoption, the full 12-case evaluation, deployed sessions and hosting are not implemented in this POC. This launcher binds to loopback and is intended for local use.

## Optional Groq setup

Create a local `.env` using [.env.example](.env.example), set `GROQ_API_KEY`, and restart the backend. Keep the key out of browser configuration and source control. The default model is `openai/gpt-oss-120b`.

Select **Groq** when investigating an incident. This sends scoped operational evidence to the one configured Groq account. Evidence rules remains a separate selectable engine; a failed Groq call is never replaced by a rule-generated report. No paid upgrade, purchased credits or account rotation is needed or performed.

The investigator first collects a bounded cross-service baseline, then lets Groq query further. Logs, metrics and spans default to 20 records (maximum 50); account, run, service, trace and inclusive time-window filters apply before limits. Original observations remain intact in SQLite. Repeated observations use cited representatives, queried trace relationships are kept together, and the prompt discloses omitted groups. Linked timing comparisons are calculations from supplied telemetry, not a rule-generated diagnosis.

Each tool result is capped at 4 KiB and the entire serialized request at 10 KiB, including tool/report schemas. Prompts rebuild from deduplicated evidence and compact query summaries. The model uses low reasoning effort and output limits of 1,024 tokens for selecting tools and 2,048 for the separate strict-schema report. Report citation fields are constrained to supplied source IDs and validated again by the backend.

The single worker allows four model tool rounds and six actual HTTP attempts, reserving two for the report and its possible retry. Queries that collect no new sources end exploration. A request is retried at most once for 429, temporary server errors or connection failures. Authentication, oversized input and invalid output fail visibly with diagnostics.

When quota is low, the same investigation enters **quota waiting**. The dashboard shows the reason, expected retry and countdown while retaining completed tool activity and evidence links. The worker uses provider reset/Retry-After timing, or a 60-second fallback when a 429 gives no usable timing. Total waiting is capped at 120 seconds and execution at five minutes, excluding time in the queue. Limits requiring a longer wait produce a clear failure with the next retry time when known. Explicit retry creates a new investigation and retains the earlier attempt; restart marks unfinished waits interrupted.

Provider diagnostics include HTTP status, known limit category, input bytes, actual token usage and request duration. Scheduling estimates are separately labelled. Credentials, raw provider error bodies and private reasoning are not stored in these diagnostics. Jev remains unconnected.

## Fresh installation

Use Python 3.14 and a Node version compatible with Vite 7. The versions exercised here were Python 3.14.6 and Node 26.7.0.

~~~powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci
npm run build
.\start.ps1
~~~

These are the standard fresh-install commands and require registry access. This session used existing local packages because network downloads were unavailable; a clean online installation has not been verified.

For frontend development, run the backend launcher and, in a second terminal:

~~~powershell
npm run dev
~~~

The development frontend at http://127.0.0.1:5173 proxies `/api` to port 8000. Use either `127.0.0.1` or `localhost` consistently for browser sessions.

## Verification

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
npm test
npm run build
~~~

Verified on 2026-11-02: 35 backend tests, 3 frontend checks, Python compilation and the production frontend build. Two live reports for each failure mechanism passed source/citation review. Each used two HTTP attempts, resumed from a 24 to 30 second quota wait and finished in 30 to 36 seconds. A separate healthy run produced zero failures and zero incidents. Earlier incorrect or rejected attempts are retained; see [status.md](status.md) for case IDs, actual usage and limitations.

Backend tests start a separate real four-process runtime on ports 8765-8768 with isolated storage under `.cache`. They cover authentication, CSRF, account isolation, both real failure scenarios, healthy traffic, recovery, trace/time filtering before limits, grouped evidence, request-size bounds, citation integrity, quota resumption and budgets, queue limits, restart handling and saved results. Mocked provider checks consume no Groq quota.

Frontend checks cover TypeScript compilation, API CSRF/error handling, and rendered waiting/failed investigations with countdowns, diagnostics and evidence links. The browser automation host is unavailable in this session, so interactive browser and visual verification remain pending. API-based live checks do not count as browser verification.

Run a live check explicitly, one investigation per invocation (these consume available free Groq quota):

~~~powershell
.\.venv\Scripts\python.exe scripts/live_check.py pool
.\.venv\Scripts\python.exe scripts/live_check.py slow
.\.venv\Scripts\python.exe scripts/live_check.py healthy
~~~

The script creates a local verification account with its password kept only in memory. It runs real service traffic, polls progress, signs back in, verifies saved results and all structured citations through the protected API, and writes report/evidence/diagnostic artifacts under `.cache/live-verification/`. Every invocation uses the server's same configured Groq account. Healthy checks make no model call. Review the reported mechanism against the evidence; valid citation IDs alone do not establish semantic correctness.

## Storage and files

- `data/platform.sqlite3`: accounts, session digests, runs, incidents, evidence and investigations.
- `data/payment.sqlite3`: reference-service workload.
- `data/telemetry/`: captured JSONL service observations.
- `data/*.log`: process diagnostics.
- `backend/`: API, authentication, services, controller, evidence tools and investigators.
- `src/`: React application.
- `tests/`: isolated backend integration and frontend contract checks.

Local data, dependencies and secrets are ignored by [.gitignore](.gitignore). Stop the runtime before making a backup; retain the platform database and telemetry together. This POC has no verified hosted backup/restore workflow.

Use [status.md](status.md) for current progress, [phase.md](phase.md) for the full implementation checklist, and [agent.md](agent.md) for working rules.

