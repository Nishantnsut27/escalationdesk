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
- An optional bounded Groq tool-calling/report adapter.

The default **Evidence rules** engine derives findings from observed logs, timings and metrics. It does not call a model, and the interface labels it accordingly. Scenario names and controller flags are not passed to the investigator.

## POC implementation and product plan

This POC uses the user-approved available stack: Python 3.14, Starlette, HTTPX, sqlite3 and React/TypeScript/Vite. The planned Python 3.13 environment could not be downloaded in this session.

Its connection pool holds real SQLite connections. Its trace exporter records actual operation durations and W3C parent relationships. These are lightweight POC implementations, not SQLAlchemy pooling or the OpenTelemetry SDK.

The complete product requirements remain in [prd.md](prd.md). Password recovery, Jev integration, SQLAlchemy/OpenTelemetry adoption, the full 12-case evaluation, deployed sessions and hosting are not implemented in this POC. This launcher binds to loopback and is intended for local use.

## Optional Groq setup

Create a local \`.env\` using [.env.example](.env.example), set \`GROQ_API_KEY\`, and restart the backend. Keep the key out of browser configuration and source control. The default model is \`openai/gpt-oss-120b\`.

Select **Groq** when investigating an incident. That action sends the incident's scoped operational evidence to Groq. Tool calling and final structured output use separate requests; the worker allows at most four tool rounds and six total requests.

No Groq key was available during development. Mocked contract checks passed, but authenticated model access, current model compatibility and live RCA behavior still require verification. Provider errors remain visible; the app does not substitute a rule-generated result for a failed model call. Jev is not connected.

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

The development frontend at http://127.0.0.1:5173 proxies \`/api\` to port 8000. Use either \`127.0.0.1\` or \`localhost\` consistently for browser sessions.

## Verification

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
npm test
npm run build
~~~

Verified: 11 backend tests, 2 frontend checks and the production frontend build.

Backend tests start a separate real four-process runtime on ports 8765-8768 with isolated storage under \`.cache\`. They verify authentication, CSRF, account isolation, both real failure scenarios, trace relationships, healthy traffic, recovery, report citations, query bounds, session expiry, login throttling, persistence after restart and shutdown cleanup. Groq contract tests use explicitly mocked responses and consume no provider credits.

Frontend checks cover TypeScript compilation and the API client's CSRF/error behavior. Browser automation was unavailable in this session, so visual layout and interactive browser flows still need manual verification.

## Storage and files

- \`data/platform.sqlite3\`: accounts, session digests, runs, incidents, evidence and investigations.
- \`data/payment.sqlite3\`: reference-service workload.
- \`data/telemetry/\`: captured JSONL service observations.
- \`data/*.log\`: process diagnostics.
- \`backend/\`: API, authentication, services, controller, evidence tools and investigators.
- \`src/\`: React application.
- \`tests/\`: isolated backend integration and frontend contract checks.

Local data, dependencies and secrets are ignored by [.gitignore](.gitignore). Stop the runtime before making a backup; retain the platform database and telemetry together. This POC has no verified hosted backup/restore workflow.

Use [status.md](status.md) for current progress, [phase.md](phase.md) for the full implementation checklist, and [agent.md](agent.md) for working rules.

