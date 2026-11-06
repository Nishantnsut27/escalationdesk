# Escalation Desk

A working local proof of concept for investigating failures across connected services.

Create an account, connect Sentry or import application logs, and generate an assessment with links to its source evidence. The normal **Investigate** action uses TypeSafe Jev through OpenRouter for advisory triage, followed by Groq for investigation and reporting. The React interface talks to a persisted Python backend. Investigations use the evidence you import.

## Run on this machine

The local dependencies and frontend build are already prepared.

~~~powershell
.\start.ps1
~~~

Open **http://127.0.0.1:8020** on this machine and create your own account. **Demo Account** accepts **`Demo@login`** and currently contains five reviewed recordings. The login screen shows the actual available count. The normal workspace opens on saved evidence, with Connections and Import logs as entry points.

Stop with Ctrl+C in the launcher, or from another terminal:

~~~powershell
.\.venv\Scripts\python.exe -m backend.launcher --stop
~~~

The launcher uses `DESK_PORT` (8020 on this machine), checks readiness and reports startup failures. It does not stop other applications occupying those ports.

## Try the complete flow

1. Create an account with a password of at least 10 characters, or sign in.
2. Open Connections to authorize Sentry, or Import logs to upload/paste application observations.
3. Select the projects and time window for a Sentry import, then review the saved evidence.
4. Click **Investigate** to run Jev triage followed by Groq.
5. Review the assessment and limitations, open its citations, and revisit the report after signing in again.

Demo Account browses saved reports without provider calls. Recorded history retains previous incidents and runs. Live fault controls are disabled in the normal workspace.

## What is implemented

- Real signup/login/logout with Argon2id password hashes, expiring server-side sessions, HttpOnly cookies, CSRF checks and authentication throttling.
- Account ownership enforced by the backend for runs, incidents, investigations and evidence.
- Sentry public-integration OAuth, encrypted account-owned connections, explicit snapshot imports and optional reference-service instrumentation. See [Sentry setup](#sentry-setup) and the verification results below for local and public acceptance.
- Controlled workload recordings remain available for reviewed demos and offline regression checks. The normal launcher starts only the workspace.
- Structured JSONL logs, measured operation spans carrying W3C trace/parent IDs, and service metric snapshots.
- Detection after three consecutive failed requests, one incident per run and verified automatic recovery.
- Read-only evidence tools, deterministic analysis, evidence-reference validation and saved reports.
- A React/TypeScript dashboard with scenario controls, service topology, incident history, evidence filtering and source-detail dialogs.
- A quota-aware Groq investigator with compact evidence, strict source citations, automatic wait/resume and saved diagnostics.
- Owned pasted/file log imports with coverage and redaction details, arbitrary service names and retained source content.
- Bounded Jev classification and observation priorities through OpenRouter, separate provider usage and visible Groq-only fallback.
- A read-only twelve-case demo gallery and offline installer; publication awaits completion of the saved-report review described below.
- Saved collections and previous incident history remain readable; new evidence comes from imports and connections.

The main interface has one investigation action. Deterministic **Evidence rules** and standalone Groq remain available through legacy APIs; comparison scripts provide standalone Jev, Groq and combined modes. Scenario names and controller flags are not passed to the investigator.

## POC implementation and product plan

This POC uses the user-approved stack retained for the next phases: Python 3.14, Starlette, HTTPX, sqlite3 and React/TypeScript/Vite.

Its connection pool holds real SQLite connections. Its trace exporter records actual operation durations and W3C parent relationships. These are lightweight POC implementations, not SQLAlchemy pooling or the OpenTelemetry SDK.

Phase 7 browser acceptance passed, user-verified on 2026-11-03. Phase 8 adds recorded demos and local live scenarios; its new interactive browser acceptance remains separate. The later roadmap adds Sentry imports, evaluation/password recovery, hosting and opt-in Sentry alert investigations. Gateway, Order and Payment remain reference/demo services.

## Recorded demos

The twelve controlled recordings cover payment pool exhaustion, slow payment processing, application exception, missing database migration, unavailable dependency, authentication rejection, incomplete evidence, failed rolling deployment, startup configuration failure, expired TLS certificate, API throttling/retries, and a background-job backlog.

**Available now:** [bundle.json](demo/v1/bundle.json) contains five reviewed reports: application exception, missing database migration, rejected upstream authentication, incomplete evidence, and API throttling. Their 2,652 log records and cited evidence are installed automatically. All twelve source recordings remain in [recordings.json](demo/v1/recordings.json); seven reports still need acceptance before publication. Rejected reports are not published. Exporting selected reviewed cases adds them to the existing bundle and rejects changes to published cases before replacing the file.

These are real local HTTP/process/SQLite/TLS/queue executions, labelled as recorded examples. They are not customer production logs or actual cloud deployments. Each case contains at least 500 distinct captured log records; spans and metrics are additional. Open the Assessment, Logs, Traces or Metrics tabs and click any source to inspect the retained observation. Browsing and searching never invokes a model.

The versioned bundle at `demo/v1/bundle.json` installs transactionally on startup without network access. Installation is idempotent, retains recorded IDs/timestamps, and allows additional reviewed cases while rejecting removal or rewriting of already installed cases. The public password is stored as an Argon2id hash. Backend permissions deny demo investigations, supplements, imports, scenario controls and other mutations.

Live scenario controls and the fixed reference topology have been removed from the normal app. Existing saved evidence is retained. Regression tests explicitly enable the development lab with `DESK_ENABLE_LAB=1`; the default is disabled.

Operator preparation is separate from visitor access:

~~~powershell
.\.venv\Scripts\python.exe scripts/prepare_demo.py capture --case all
.\.venv\Scripts\python.exe scripts/verify_demo_captures.py
.\.venv\Scripts\python.exe scripts/prepare_demo.py analyze --case all --live
.\.venv\Scripts\python.exe scripts/prepare_demo.py review --case pool --notes-file PATH_TO_REVIEW_NOTES
.\.venv\Scripts\python.exe scripts/prepare_demo.py export --case exception schema authentication
.\.venv\Scripts\python.exe scripts/prepare_demo.py install
~~~

Only `analyze --live` requests providers. Review each actual report and its cited observations before accepting it; export requires a current hash-bound review for every selected case. The exporter automatically retains previously published cases; only newly reviewed cases need to be selected. Preparation retains captures and all provider attempts under `.cache/phase8/v1`. `capture` restores shipped recordings when local snapshots are absent; `capture --recapture` archives the preceding draft capture and report before recording again. `--retry` creates a new analysis attempt. Optional `--reuse-queries` replays an existing read-only query plan for the fixed capture before fresh Jev triage and a fresh Groq report, with separate provenance; normal user investigations still select queries live. `--questions-file` accepts symptom questions, never expected answers or controller flags. `select --case CASE --attempt ID` selects an unchanged retained completed response. Never edit provider output to pass review. This curated gallery is separate from the planned comparative quality benchmark.

Detailed setup, planning and progress documents remain local and are excluded from Git; this README is the published guide. SQLAlchemy/OpenTelemetry adoption and additional connectors remain later backlog work. This launcher binds to loopback and is intended for local use.

## Import your application logs

Open **Imported logs**, paste text or choose a UTF-8 .log, .txt or .jsonl file, and optionally describe the symptom or provide a default service label. Choose **Save logs** to retain an owned collection. Importing makes no model request and creates no scenario run or failure incident.

Limits are 1 MiB of text, 5,000 observations, 32 services and 32 KiB per observation. JSONL records can supply service, timestamp, level, message, source id and correlation IDs; plain logs and multiline exceptions are also accepted. Supplied span/metric records retain recognized fields. Missing times and relationships stay unknown. The interface discloses malformed records, redactions, duplicates and missing context.

Review the retained observations before clicking **Investigate**, which sends bounded evidence to OpenRouter Jev and Groq. Common credential patterns are redacted, but arbitrary sensitive content may remain. Groq uses bounded tools and request budgets; its citations refer only to supplied saved evidence.

Raw evidence is paged and searchable separately from report sources. Identical imports return the existing collection; new investigations retain earlier attempts. Legacy scenario reports and supplements remain available. SQLite migration 3 adds collections and relaxes the required legacy run/incident links transactionally while preserving stored IDs and report fields.

For browser verification, use [examples/import-check.jsonl](examples/import-check.jsonl), an artificial test fixture. Expected coverage: **4 observations, 1 duplicate skipped, 1 missing timestamp and 1 redaction**. No analysis should start automatically. Open the auth observation to inspect redaction, click Investigate explicitly, inspect triage and cited previews, then sign out/in and revisit the saved result. Investigation consumes configured provider quota.

## Sentry setup

Create a Public Integration in Sentry's **Settings → Developer Settings** with Organization, Project and Issue & Event **Read** permissions. Enable **Verify Install**. Set the redirect URL to `https://YOUR-HOST/api/connections/sentry/callback` and the webhook URL to `https://YOUR-HOST/api/connections/sentry/webhook`. Leave alert actions disabled; only installation lifecycle notifications are handled. Until Sentry approval, the integration is limited to its creating organization.

For local verification, point a temporary HTTPS tunnel at port 8020. Put its exact origin in `DESK_PUBLIC_URL`, and the assigned `SENTRY_APP_SLUG`, `SENTRY_CLIENT_ID` and `SENTRY_CLIENT_SECRET` in ignored `backend/.env`. Remove blank overrides in the root `.env`. Generate the credential-encryption key once, then check configuration:

~~~powershell
.\.venv\Scripts\python.exe scripts/configure_sentry.py --generate-key
.\.venv\Scripts\python.exe scripts/configure_sentry.py
~~~

Retain `DESK_CONNECTION_KEY` outside the database and reuse it across restarts. Restart the app after configuring it. Sign in through the configured HTTPS origin; sessions then require Secure cookies. In **Connections**, select your organization's actual US or EU region, choose **Connect Sentry**, approve access and run **Check access**.

Send application telemetry to Sentry first; a DSN is for ingestion and does not replace the integration's read authorization. The separate test app runs through `scripts/check_sentry.py` with `SENTRY_TEST_DSN` configured. It sends three controlled integer-parsing failures and three successful requests; verify delivery in the dedicated test project before importing its matching window.

Select authorized projects and datasets, then import up to 24 hours and 5,000 observations. Dataset availability, sampling and partial imports are disclosed with the saved snapshot. Importing and browsing never invoke AI; **Investigate** explicitly sends bounded redacted evidence to Jev and Groq. Local disconnect and Sentry uninstall leave saved evidence and reports readable. Reconnect with **Connect Sentry** when needed.

## Model provider setup

Set `GROQ_API_KEY` and `OPENROUTER_API_KEY` in an ignored root `.env` or `backend/.env`, using [.env.example](.env.example) as a reference, then restart the backend. Process environment values take precedence, followed by the root file and then `backend/.env`; remove a blank root entry if you want the backend file's value to apply. Keep keys out of browser configuration and source control. No Vercel AI Gateway key is required.

Defaults are `GROQ_MODEL=openai/gpt-oss-120b` and `JEV_MODEL=typesafe/jev-1.14`. Jev uses OpenRouter's [Decisions API](https://openrouter.ai/docs/guides/community/jev-tutorial), `POST https://openrouter.ai/api/alpha/decisions`. The live provider returned `typesafe/jev-1.14-20261014`; saved results retain the actual identifier, usage and provider-reported cost. Model aliases and account availability can change. Calls use the configured provider allowances; no credits are purchased or accounts rotated by the application.

Groq is required for the main Investigate action. If Jev is unconfigured, unavailable, invalid or exceeds its allowance, the same job continues with an explicit **Groq-only fallback** notice. Groq failures remain visible. Browsing saved reports requires neither provider to be available.

Optional `GROQ_PROXY_URL` and `JEV_PROXY_URL` configure separate explicit proxies. Empty values use direct HTTPS with TLS verification; provider clients do not inherit shell HTTP proxy settings.

## Advisory triage and investigation bounds

Jev classifies up to eight representative observation groups across 16 problem families, including mixed, unknown and no clear failure. Its Choice distribution and 0–2 diagnostic-usefulness Scores guide Groq's attention without confirming a cause. The interface separates classification confidence from root-cause correctness and preserves alternatives, conflicting observations and lower-ranked sources.

Jev has a 10 KiB complete-input limit and at most two HTTP attempts within 20 seconds. Its waiting counts toward the investigation's shared 120-second waiting allowance and five-minute deadline. Jev attempts/tokens/cost are recorded separately from Groq's six-attempt budget. Its advisory context uses spare request capacity; it never evicts evidence. If even a compact hint cannot fit, diagnostics disclose its omission.

The investigator first collects a bounded cross-service baseline, including later metric windows for long collections, then lets Groq query further. Logs, metrics and spans default to 20 records (maximum 50); account, run, service, trace and inclusive time-window filters apply before limits. Original observations remain intact in SQLite. Repeated observations use cited representatives, queried trace relationships are kept together, and the prompt discloses omitted groups. Linked timing comparisons are calculations from supplied telemetry, not a rule-generated diagnosis.

For trace-linked imported exceptions, the baseline reads one matching trace and its logs/error events. Later trace selections also collect matching logs. Exception handling, span operation and HTTP status stay available to the report; an HTTP server response is distinct from a downstream response. Independent successful requests remain separate observations. Sentry normalization version 2 keeps project provenance separate from `service.name`; older snapshots need a fresh import for corrected metadata. Their saved evidence, reports, citations and timestamps are preserved.

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

The development frontend at http://127.0.0.1:5173 proxies `/api` to `DESK_PORT` from the environment or root `.env`, defaulting to port 8000 (8020 on this machine). Use either `127.0.0.1` or `localhost` consistently for browser sessions.

## Verification

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
npm test
npm run build
~~~

November 6 verification passed **123 backend tests**, **19 frontend checks** and the production build. A final focused run passed all **24 Sentry tests**, including diagnostic facts within the 4 KiB tool-result and 10 KiB complete-request limits. The dedicated Sentry app's three errors, twelve logs and twelve spans were retrieved with corrected service identity; live JWT refresh and versioned import deduplication passed. Existing collections, evidence, reports and supplements passed the history-preservation audit.

The latest live Jev/Groq report correctly identifies a handled integer-parsing `ValueError` in `record-processor`, its linked server HTTP 422 response and a separate HTTP 200 request. Its citations support the failure mechanism without inventing another service or claiming recovery. Earlier rejected reports remain unchanged. Broader evaluation in Phase 10 must still address redundant recommendations, unrelated deadline caveats and wording that confuses selected evidence with the full snapshot.

Jev tests cover strict response validation, input/redaction bounds, retries/deadlines, fallback, separate usage, saved results, ownership and preservation of evidence when advisory hints are added. Live OpenRouter Jev and four frozen Jev/Groq/combined comparisons completed. The earlier reliability milestone also passed two live original reports per failure mechanism and explicit supplement flows.

Backend tests start a separate real four-process runtime on ports 8765-8768 with isolated storage under `.cache`. They cover authentication, CSRF, account isolation, both real failure scenarios, healthy traffic, recovery, trace/time filtering before limits, grouped evidence, request-size bounds, citation integrity, quota resumption and budgets, queue limits, restart handling and saved results. Mocked provider checks consume no Groq quota.

Frontend checks cover TypeScript compilation, API CSRF/error handling, imports, triage/fallback rendering, source previews, saved-report states, Sentry regions and coverage/provenance/older-import labels. The user confirmed Phase 9 browser checks, including sign-in and retained-report retrieval, local disconnect, reauthorization and Sentry uninstall. A real signed uninstall notification and a later installation notification were received. Final connection restoration still needs reconciliation: the user reported a successful reconnect, but the last database check showed both connections disconnected. Phase 8 interactive/visual acceptance remains separate. Browser results are user-verified because the automation host is unavailable.

Local Phase 9 acceptance permits Phase 10 password recovery, broader accuracy evaluation and outstanding demo acceptance. External Sentry acceptance follows Phase 11 deployment of React on **Vercel** and Python on **Render**, with persistent SQLite and verified HTTPS sessions. Hosted callback/webhook verification, Sentry **Publish** submission, approval and second-organization isolation remain explicitly pending. Submission is not approval. Automatic Sentry investigations remain Phase 12.

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

Detailed progress and working notes are maintained locally and are not included in the repository.

