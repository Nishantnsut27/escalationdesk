# Escalation Desk

A working local proof of concept for investigating failures across connected services.

Create an account, import application logs or run a real service scenario, and generate an assessment with links to its source evidence. The normal **Investigate** action uses TypeSafe Jev through OpenRouter for advisory triage, followed by Groq for investigation and reporting. The React interface talks to a persisted Python backend and three independent HTTP service processes.

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
4. With provider credentials configured below, select the incident and click **Investigate**.
5. Read the assessed cause, supported/inconclusive status, recommended checks and limitations. Expand **Initial triage** to review Jev's preliminary classification.
6. Open a cited observation preview, or inspect the Logs, Traces and Metrics tabs.
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
- Owned pasted/file log imports with coverage and redaction details, arbitrary service names and retained source content.
- Bounded Jev classification and observation priorities through OpenRouter, separate provider usage and visible Groq-only fallback.

The main interface has one investigation action. Deterministic **Evidence rules** and standalone Groq remain available through legacy APIs; comparison scripts provide standalone Jev, Groq and combined modes. Scenario names and controller flags are not passed to the investigator.

## POC implementation and product plan

This POC uses the user-approved stack retained for the next phases: Python 3.14, Starlette, HTTPX, sqlite3 and React/TypeScript/Vite.

Its connection pool holds real SQLite connections. Its trace exporter records actual operation durations and W3C parent relationships. These are lightweight POC implementations, not SQLAlchemy pooling or the OpenTelemetry SDK.

Phase 6 imports and earlier browser acceptance are complete, user-verified on 2026-11-03. Phase 7 Jev integration is implemented and tested with live OpenRouter access; its new interactive browser checks remain pending. The later roadmap adds a read-only demo with seven saved Groq cases, Sentry imports, evaluation/password recovery, hosting and opt-in Sentry alert investigations. Gateway, Order and Payment remain reference/demo services. There is currently no shared demo login.

The local planning documents are [prd.md](prd.md), [phase.md](phase.md), [status.md](status.md) and [agent.md](agent.md). They are intentionally ignored by Git and are unavailable in a fresh clone unless supplied separately. SQLAlchemy/OpenTelemetry adoption and additional connectors remain later backlog work. This launcher binds to loopback and is intended for local use.

## Import your application logs

Open **Imported logs**, paste text or choose a UTF-8 .log, .txt or .jsonl file, and optionally describe the symptom or provide a default service label. Choose **Save logs** to retain an owned collection. Importing makes no model request and creates no scenario run or failure incident.

Limits are 1 MiB of text, 5,000 observations, 32 services and 32 KiB per observation. JSONL records can supply service, timestamp, level, message, source id and correlation IDs; plain logs and multiline exceptions are also accepted. Supplied span/metric records retain recognized fields. Missing times and relationships stay unknown. The interface discloses malformed records, redactions, duplicates and missing context.

Review the retained observations before clicking **Investigate**, which sends bounded evidence to OpenRouter Jev and Groq. Common credential patterns are redacted, but arbitrary sensitive content may remain. Groq uses bounded tools and request budgets; its citations refer only to supplied saved evidence.

Raw evidence is paged and searchable separately from report sources. Identical imports return the existing collection; new investigations retain earlier attempts. Legacy scenario reports and supplements remain available. SQLite migration 3 adds collections and relaxes the required legacy run/incident links transactionally while preserving stored IDs and report fields.

For browser verification, use [examples/import-check.jsonl](examples/import-check.jsonl), an artificial test fixture. Expected coverage: **4 observations, 1 duplicate skipped, 1 missing timestamp and 1 redaction**. No analysis should start automatically. Open the auth observation to inspect redaction, click Investigate explicitly, inspect triage and cited previews, then sign out/in and revisit the saved result. Investigation consumes configured provider quota.

## Model provider setup

Set `GROQ_API_KEY` and `OPENROUTER_API_KEY` in an ignored root `.env` or `backend/.env`, using [.env.example](.env.example) as a reference, then restart the backend. Process environment values take precedence, followed by the root file and then `backend/.env`; remove a blank root entry if you want the backend file's value to apply. Keep keys out of browser configuration and source control. No Vercel AI Gateway key is required.

Defaults are `GROQ_MODEL=openai/gpt-oss-120b` and `JEV_MODEL=typesafe/jev-1.14`. Jev uses OpenRouter's [Decisions API](https://openrouter.ai/docs/guides/community/jev-tutorial), `POST https://openrouter.ai/api/alpha/decisions`. The live provider returned `typesafe/jev-1.14-20261014`; saved results retain the actual identifier, usage and provider-reported cost. Model aliases and account availability can change. Calls use the configured provider allowances; no credits are purchased or accounts rotated by the application.

Groq is required for the main Investigate action. If Jev is unconfigured, unavailable, invalid or exceeds its allowance, the same job continues with an explicit **Groq-only fallback** notice. Groq failures remain visible. Browsing saved reports requires neither provider to be available.

Optional `GROQ_PROXY_URL` and `JEV_PROXY_URL` configure separate explicit proxies. Empty values use direct HTTPS with TLS verification; provider clients do not inherit shell HTTP proxy settings.

## Advisory triage and investigation bounds

Jev classifies up to eight representative observation groups across 16 problem families, including mixed, unknown and no clear failure. Its Choice distribution and 0–2 diagnostic-usefulness Scores guide Groq's attention without confirming a cause. The interface separates classification confidence from root-cause correctness and preserves alternatives, conflicting observations and lower-ranked sources.

Jev has a 10 KiB complete-input limit and at most two HTTP attempts within 20 seconds. Its waiting counts toward the investigation's shared 120-second waiting allowance and five-minute deadline. Jev attempts/tokens/cost are recorded separately from Groq's six-attempt budget. Its advisory context uses spare request capacity; it never evicts evidence. If even a compact hint cannot fit, diagnostics disclose its omission.

The investigator first collects a bounded cross-service baseline, then lets Groq query further. Logs, metrics and spans default to 20 records (maximum 50); account, run, service, trace and inclusive time-window filters apply before limits. Original observations remain intact in SQLite. Repeated observations use cited representatives, queried trace relationships are kept together, and the prompt discloses omitted groups. Linked timing comparisons are calculations from supplied telemetry, not a rule-generated diagnosis.

Each tool result is capped at 4 KiB and the entire serialized request at 10 KiB, including tool/report schemas. Prompts rebuild from deduplicated evidence and compact query summaries. The model uses low reasoning effort and output limits of 1,024 tokens for selecting tools and 2,048 for the separate strict-schema report. Report citation fields are constrained to supplied source IDs and validated again by the backend.

The single worker allows four model tool rounds and six actual HTTP attempts, reserving two for the report and its possible retry. Queries that collect no new sources end exploration. A request is retried at most once for 429, temporary server errors or connection failures. Authentication, oversized input and invalid output fail visibly with diagnostics.

When quota is low, the same investigation enters **quota waiting**. The dashboard shows the reason, expected retry and countdown while retaining completed tool activity and evidence links. The worker uses provider reset/Retry-After timing, or a 60-second fallback when a 429 gives no usable timing. Total waiting is capped at 120 seconds and execution at five minutes, excluding time in the queue. Limits requiring a longer wait produce a clear failure with the next retry time when known. Explicit retry creates a new investigation and retains the earlier attempt; restart marks unfinished waits interrupted.

Provider diagnostics include HTTP status, known limit category, input bytes, actual token usage and request duration. Scheduling estimates are separately labelled. Credentials, raw provider error bodies and private reasoning are not stored in these diagnostics.

Both investigation-create APIs accept `{"engine":"groq","triage":true}`. Omitting `triage` preserves standalone legacy behavior; it is only valid with the Groq engine. Loading a collection or saved result never requests a new investigation.

## Saved reports and further analysis

Open a saved investigation to inspect its hypotheses, individual citations and evidence timeline. The timeline uses that report's recorded supplied sources, with a labelled citation-only fallback for older reports. Missing sources/trace links remain visible. Raw run evidence stays in the separate log/trace/metric tabs. The service map separates reported impact, observed errors and current reachability.

Completed Groq reports offer **Analyze further**. This explicit action reviews the same saved evidence and adds a separate supplement; it makes no telemetry queries and preserves the original report/status/timestamps. Opening or revisiting reports never calls Groq. Supplements can support, qualify, disagree with or be unable to assess the original conclusion. Inspect their citations and limitations; valid source IDs alone do not guarantee correct prose.

The single worker prioritizes fresh investigations over queued supplements. Each supplement has two HTTP attempts at most, a 10 KiB complete request, 2,048 output tokens and the same 120-second waiting/five-minute execution bounds. Original and supplemental usage are shown separately. Duplicate submissions return the existing result; explicit retry after failure/interruption retains the previous attempt. SQLite migration 2 adds separate supplement storage without rewriting original reports.

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

Verified on 2026-11-03: **83 backend tests**, **13 frontend checks** and the production build passed. Jev tests cover strict response validation, input/redaction bounds, retries/deadlines, fallback, separate usage, saved results, ownership and preservation of evidence when advisory hints are added. Live OpenRouter Jev and four frozen Jev/Groq/combined comparisons completed. The earlier reliability milestone also passed two live original reports per failure mechanism and explicit supplement flows.

Backend tests start a separate real four-process runtime on ports 8765-8768 with isolated storage under `.cache`. They cover authentication, CSRF, account isolation, both real failure scenarios, healthy traffic, recovery, trace/time filtering before limits, grouped evidence, request-size bounds, citation integrity, quota resumption and budgets, queue limits, restart handling and saved results. Mocked provider checks consume no Groq quota.

Frontend checks cover TypeScript compilation, API CSRF/error handling, imports, triage/fallback rendering, source previews and saved-report states. Earlier browser acceptance was confirmed by the user; the new Phase 7 interactive/visual checks remain pending because the browser automation host is unavailable. API and rendered-component checks do not substitute for browser acceptance.

Run an explicit live Jev probe:

~~~powershell
.\.venv\Scripts\python.exe scripts/check_jev.py
~~~

Freeze a comparison input without making provider requests, then explicitly compare modes:

~~~powershell
.\.venv\Scripts\python.exe scripts/compare_triage.py imported
.\.venv\Scripts\python.exe scripts/compare_triage.py imported --mode all --live
~~~

For `pool`, `slow` or `incomplete`, supply `--source PATH_TO_LIVE_CHECK_JSON` when first freezing a case. `incomplete` retains only Gateway/Order logs. Modes are `jev`, `groq`, `combined` and `all`. Snapshots are hashed and retained under `.cache/phase7/comparison/`; evaluation databases are isolated from normal app data. Run live jobs sequentially using the same configured accounts. `--live` and the Jev probe consume provider quota.

The four-case review establishes working integration, not improved accuracy. Jev selected plausible problem families, but both Groq and combined reports made unsupported causal claims for incomplete evidence and invented relationships for disconnected imported logs. HTTP-hop wording and unqualified retry/pool-resizing advice also need review. Initial pool/slow combined attempts omitted advice for lack of space; retained reruns include it without evicting sources. All structured citation IDs resolved, which does not establish semantic support. Quota waits and some overlapping early invocations prevent a fair latency comparison. The broader quality benchmark remains future work.

Run a live check explicitly, one investigation per invocation (these consume available free Groq quota):

~~~powershell
.\.venv\Scripts\python.exe scripts/live_check.py pool
.\.venv\Scripts\python.exe scripts/live_check.py slow
.\.venv\Scripts\python.exe scripts/live_check.py healthy
.\.venv\Scripts\python.exe scripts/live_check.py pool --supplement
.\.venv\Scripts\python.exe scripts/live_check.py slow --supplement
~~~

The script creates a local verification account with its password kept only in memory. It runs real service traffic, polls progress, signs back in, verifies saved results and all structured citations through the protected API, and writes report/evidence/diagnostic artifacts under `.cache/live-verification/`. `--supplement` explicitly requests one supplement after the original Groq report and verifies that both can be revisited. Every invocation uses the server's same configured Groq account. Healthy checks make no model call. Review the reported mechanism against the evidence; valid citation IDs alone do not establish semantic correctness.

## Storage and files

- `data/platform.sqlite3`: accounts, session digests, runs, incidents, evidence collections, observations, investigations and supplements.
- `data/payment.sqlite3`: reference-service workload.
- `data/telemetry/`: captured JSONL service observations.
- `data/*.log`: process diagnostics.
- `backend/`: API, authentication, services, controller, evidence tools and investigators.
- `src/`: React application.
- `tests/`: isolated backend integration and frontend contract checks.

Local data, dependencies and secrets are ignored by [.gitignore](.gitignore). Stop the runtime before making a backup; retain the platform database and telemetry together. This POC has no verified hosted backup/restore workflow.

Use [status.md](status.md) for current progress, [phase.md](phase.md) for the full implementation checklist, and [agent.md](agent.md) for working rules.

