# TraceDesk

A working local proof of concept for investigating failures across connected services.

Create an account, connect Sentry or import application logs, and generate an assessment with links to its source evidence. The normal **Investigate** action uses TypeSafe Jev through OpenRouter for advisory triage, followed by Groq for investigation and reporting. The React interface talks to a persisted Python backend. Investigations use the evidence you import.

## Run on this machine

The local dependencies and frontend build are already prepared.

~~~powershell
.\start.ps1
~~~

Configure the backend settings in [.env.example](.env.example) first. Existing SQLite installations must follow the migration procedure below before restarting this version. Open **http://127.0.0.1:8020** on this machine and create your own account. **Demo Account** accepts **`Demo@login`**; the bundle currently contains six reviewed recordings. The login screen shows the installed count. Verified accounts land on Dashboard. Direct evidence and report links return to their intended destination after authentication.

Stop with Ctrl+C in the launcher, or from another terminal:

~~~powershell
.\.venv\Scripts\python.exe -m backend.launcher --stop
~~~

The launcher uses `DESK_PORT` (8020 on this machine), checks readiness and reports startup failures. It does not stop other applications occupying those ports.

## Try the complete flow

1. Create an account with a password of 10–128 characters, or sign in. Unverified regular accounts see a verification screen; confirm the email and check verification to enter the workspace. Existing saved history is retained.
2. Open Connections to authorize Sentry, or Import logs to upload/paste application observations.
3. Select the projects and time window for a Sentry import, then review the saved evidence.
4. Click **Investigate** to run Jev triage followed by Groq.
5. Read the concise assessment, supporting findings, next checks and limitations. Citation links open a dedicated log tab directly at the selected source and exact supplied excerpt.

Demo Account browses saved reports without provider calls. Recorded history retains previous incidents and runs. Live fault controls are disabled in the normal workspace.

## What is implemented

- Real signup/login/logout with Argon2id password hashes, expiring server-side sessions, HttpOnly cookies, CSRF checks and authentication throttling.
- Email verification and password recovery through Resend, with explicit single-use link confirmation, encrypted queued messages, bounded retries and signed delivery webhooks. Real verification/reset email delivery is user-confirmed; live webhook receipts await the signing secret.
- PostgreSQL platform storage through psycopg 3, a bounded connection pool, verified TLS and versioned migrations. The reference workloads retain separate SQLite stores.
- Account ownership enforced by the backend for runs, incidents, investigations and evidence.
- Sentry public-integration OAuth, encrypted account-owned connections, explicit snapshot imports and optional reference-service instrumentation. See [Sentry setup](#sentry-setup) and the verification results below for local and public acceptance.
- Controlled workload recordings remain available for reviewed demos and offline regression checks. The normal launcher starts only the workspace.
- Structured JSONL logs, measured operation spans carrying W3C trace/parent IDs, and service metric snapshots.
- Detection after three consecutive failed requests, one incident per run and verified automatic recovery.
- Read-only evidence tools, deterministic analysis, evidence-reference validation and saved reports.
- A dark React/TypeScript evidence dashboard, durable hash routes and dedicated stacked log workspace with severity histograms, chronological events and recorded React Flow span relationships.
- Typed LangGraph investigation and saved-evidence supplement paths, failure-focused excerpts, strict source citations, bounded quota waits and retained attempts.
- Owned pasted/file log imports with coverage and redaction details, arbitrary service names and retained source content.
- Bounded Jev classification and observation priorities through OpenRouter, separate provider usage and visible Groq-only fallback.
- A read-only demo gallery and offline installer with six published cases. Six additional captured cases are deferred for later report review.
- Saved collections and previous incident history remain readable; new evidence comes from imports and connections.

The main interface has one investigation action. Deterministic **Evidence rules** and standalone Groq remain available through legacy APIs; comparison scripts provide standalone Jev, Groq and combined modes. Scenario names and controller flags are not passed to the investigator.

## POC implementation and product plan

This POC uses Python 3.14, Starlette, HTTPX, psycopg 3/PostgreSQL and React/TypeScript/Vite. Reference workloads use sqlite3.

Its connection pool holds real SQLite connections. Its trace exporter records actual operation durations and W3C parent relationships. These are lightweight POC implementations, not SQLAlchemy pooling or the OpenTelemetry SDK.

Phase 7 browser acceptance passed, user-verified on 2026-11-03. Phase 8 adds recorded demos and local live scenarios; its new interactive browser acceptance remains separate. The later roadmap adds Sentry imports, evaluation/password recovery, hosting and opt-in Sentry alert investigations. Gateway, Order and Payment remain reference/demo services.

The current evidence-workspace redesign retains six published demos and the paused 18/36 benchmark. Deployment, automatic alert investigations, additional live/paid evaluation, extra demo publication and live infrastructure monitoring are outside this workstream.

## Recorded demos

The twelve controlled recordings cover payment pool exhaustion, slow payment processing, application exception, missing database migration, unavailable dependency, authentication rejection, incomplete evidence, failed rolling deployment, startup configuration failure, expired TLS certificate, API throttling/retries, and a background-job backlog.

**In the bundle:** [bundle.json](demo/v1/bundle.json) contains six reviewed reports: application exception, missing database migration, rejected upstream authentication, incomplete evidence, API throttling, and payment pool exhaustion. All twelve source recordings remain in [recordings.json](demo/v1/recordings.json). Slow processing, unavailable dependency, rolling deployment, startup failure, expired TLS and job backlog are deferred; their reports still need acceptance before publication. Rejected reports are retained locally. Exporting reviewed cases adds them to the bundle and rejects changes to previously published cases.

These are real local HTTP/process/SQLite/TLS/queue executions, labelled as recorded examples. They are not customer production logs or actual cloud deployments. Each case contains at least 500 distinct captured log records; spans and metrics are additional. Open a recorded assessment and follow its source links into the dedicated log workspace. Expand recorded request flow or the chronological timeline as needed. Browsing and searching never invokes a model.

The versioned bundle at `demo/v1/bundle.json` installs transactionally into PostgreSQL on startup without model-provider calls. Installation is idempotent, retains recorded IDs/timestamps, and allows additional reviewed cases while rejecting removal or rewriting of installed cases. The public password is stored as an Argon2id hash. Backend permissions deny demo investigations, supplements, imports, scenario controls and other mutations.

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

Resume the deferred cases from the project root:

~~~powershell
.\.venv\Scripts\python.exe scripts/prepare_demo.py analyze --case slow unavailable rollout startup tls backlog --live --resume
~~~

This keeps completed attempts for review and continues missing, failed or interrupted attempts. A completed but rejected report needs an explicit new attempt, for example `analyze --case tls --live --retry`. Every earlier attempt remains saved; resume does not imply acceptance or rewrite the published bundle. Retain `.cache/phase8/v1` to keep local preparation progress.

To pause either demo preparation or the benchmark, run this in a second terminal:

~~~powershell
.\.venv\Scripts\python.exe scripts/prepare_demo.py pause
~~~

The current attempt finishes and is saved before the next one is stopped. Rerun the resume command to continue. Ctrl+C interrupts the current attempt and retains its partial result when graceful cleanup can run; forcibly closing the process may leave a lock that needs inspection. Only one provider workload may run at once.

Detailed setup, planning and progress documents remain local and are excluded from Git; this README is the published guide. SQLAlchemy/OpenTelemetry adoption and additional connectors remain later backlog work. This launcher binds to loopback and is intended for local use.

## Import your application logs

Open **Import logs**, paste text or choose a UTF-8 .log, .txt or .jsonl file, and optionally describe the symptom or provide a default service label. Choose **Save logs** to retain an owned collection. Importing makes no model request and creates no scenario run or failure incident.

Limits are 1 MiB of text, 5,000 observations and 32 services. Large individual observations are retained in full within that total input bound; model requests receive explicitly marked excerpts. JSONL records can supply service, timestamp, level, message, source id and correlation IDs; plain logs and multiline exceptions are also accepted. Supplied span/metric records retain recognized fields. Missing times and relationships stay unknown. The interface discloses malformed records, redactions, duplicates and missing context.

Review the retained observations before clicking **Investigate**, which sends bounded evidence to OpenRouter Jev and Groq. Common credential patterns are redacted, but arbitrary sensitive content may remain. Groq uses bounded tools and request budgets; its citations refer only to supplied saved evidence.

Raw evidence is paged and searchable separately from report sources. Identical imports return the existing collection; new investigations retain earlier attempts. Legacy scenario reports and supplements remain available. PostgreSQL migration preserves their original serialized fields, IDs and timestamps.

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

The investigator selects explicit failures and symptom matches, preserving multiline exceptions and nested causes. It prefers matching trace/request context across services; otherwise it uses same-service observations within 30 seconds, or five adjacent observations on each side when time is unknown. Measured extremes, relevant spans and independent successful/conflicting observations are retained before repeated errors fill the selection bound. Groq can then request bounded evidence tools. Logs, metrics and spans default to 20 records (maximum 50); account, run, service, trace and inclusive time-window filters apply before limits. Original observations remain intact in PostgreSQL. Repeated observations use cited representatives, queried trace relationships are kept together, and the prompt discloses omitted groups. Linked timing comparisons are calculations from supplied telemetry, not a rule-generated diagnosis.

For trace-linked imported exceptions, the baseline reads one matching trace and its logs/error events. Later trace selections also collect matching logs. Exception handling, span operation and HTTP status stay available to the report; an HTTP server response is distinct from a downstream response. Independent successful requests remain separate observations. Sentry normalization version 3 retains full redacted source content and exception frames, while preserving the version 2 service/HTTP/exception corrections. Older snapshots need a fresh import for the updated normalization. Their saved evidence, reports, citations and timestamps are preserved.

Each tool result is capped at 4 KiB and the entire serialized request at 10 KiB, including tool/report schemas. Prompts rebuild from deduplicated evidence and compact query summaries. The model uses low reasoning effort and output limits of 1,024 tokens for selecting tools and 2,048 for the separate strict-schema report. Report citation fields are constrained to supplied source IDs and validated again by the backend.

The single worker allows four model tool rounds and six actual HTTP attempts, reserving two for the report and its possible retry. Queries that collect no new sources end exploration. A request is retried at most once for 429, temporary server errors or connection failures. Authentication and input-budget failures remain visible. A report failing structure, length, service or citation validation receives at most one correction within the existing six-attempt allowance; invalid output is never silently truncated or saved as a valid report.

When quota is low, the same investigation enters **quota waiting**. The dashboard shows the reason, expected retry and countdown while retaining completed tool activity and evidence links. The worker uses provider reset/Retry-After timing, or a 60-second fallback when a 429 gives no usable timing. Total waiting is capped at 120 seconds and execution at five minutes, excluding time in the queue. Limits requiring a longer wait produce a clear failure with the next retry time when known. Explicit retry creates a new investigation and retains the earlier attempt; restart marks unfinished waits interrupted.

Provider diagnostics include HTTP status, known limit category, input bytes, actual token usage and request duration. Scheduling estimates are separately labelled. Credentials, raw provider error bodies and private reasoning are not stored in these diagnostics.

Both investigation-create APIs accept `{"engine":"groq","triage":true}`. Omitting `triage` preserves standalone legacy behavior; it is only valid with the Groq engine. Loading a collection or saved result never requests a new investigation.

## Saved reports and further analysis

New reports have at most 250 words of explanatory prose: assessed mechanism and impact, supported/inconclusive assessment, cited supporting findings, at most two next checks and two material limitations. At most two competing hypotheses are allowed. Historical reports retain their original wording and are available in expandable details.

Citations open a new tab directly at the selected observation and exact excerpt. A known trace takes precedence over a request ID; absent correlation remains unknown. Source character ranges, source lines and selection metadata are retained per model request. The complete redacted observation remains inspectable. Further-analysis citations identify their own saved excerpts.

The log workspace stacks collection coverage and search/filters, a severity histogram, expandable recorded request flow, an expandable chronological timeline and paginated observations. Exact request/trace, service, severity, time range and detected error signature filters, selection and pagination are preserved in the URL. Reload and browser Back/Forward restore that state. Long stack traces remain searchable and expandable.

Histogram totals use the whole filtered log dataset. Missing or timezone-less timestamps stay out of time buckets and are disclosed separately. Flow edges require matching recorded trace/span/parent IDs; branches, repeated service calls and missing parents remain distinct. Severity/service/time filters do not erase intermediate operations from the labelled full-request flow. Request-only evidence shows ordered events without invented span dependencies.

Dashboard counts are observations from saved snapshots, never unique requests or failure rates. Local, versioned error signatures are separate from Jev's investigation-level categories. An OOM signature does not establish a leak, and a timeout does not explain the underlying slowness. Live infrastructure monitoring awaits a separate telemetry-source decision.

Completed Groq reports offer **Analyze further**. This explicit action reviews the same saved evidence and adds a separate supplement; it makes no telemetry queries and preserves the original report/status/timestamps. Opening or revisiting reports never calls Groq. Supplements can support, qualify, disagree with or be unable to assess the original conclusion. Inspect their citations and limitations; valid source IDs alone do not guarantee correct prose.

The single worker prioritizes fresh investigations over queued supplements. Each supplement has two HTTP attempts at most, a 10 KiB complete request, 2,048 output tokens and the same 120-second waiting/five-minute execution bounds. Original and supplemental usage are retained separately in the saved API records. Duplicate submissions return the existing result; explicit retry after failure/interruption retains the previous attempt.

## Fresh installation

Use Python 3.14 and a Node version compatible with Vite 7. The versions exercised here were Python 3.14.6 and Node 26.7.0.

Configure `DESK_DATABASE_URL` for a Neon development project before starting. Integration tests and operator preparation require `DESK_TEST_DATABASE_URL` on a separate project. Keep both URLs in ignored `backend/.env` or backend environment variables. The application strengthens Neon TLS to `verify-full`; the default pool holds at most five connections per process. There is no SQLite fallback.

## Migrate an existing SQLite installation

Keep the previous application version, frontend build, encryption keys and SQLite database together as a rollback backup. Rehearse the transfer on the separate test project first:

~~~powershell
.\.venv\Scripts\python.exe scripts/migrate_platform.py backup --source data/platform.sqlite3 --output .cache/migration-rehearsal.sqlite3
.\.venv\Scripts\python.exe scripts/migrate_platform.py rehearse --source .cache/migration-rehearsal.sqlite3 --output .cache/migration-rehearsal.json
~~~

The rehearsal retains a dedicated operator schema and compares every original field, record count and relationship. After it passes, stop the application, pause all other writers, and take a fresh backup. The working PostgreSQL target must be empty:

~~~powershell
.\.venv\Scripts\python.exe -m backend.launcher --stop
.\.venv\Scripts\python.exe scripts/migrate_platform.py backup --source data/platform.sqlite3 --output .cache/migration-cutover.sqlite3
.\.venv\Scripts\python.exe scripts/migrate_platform.py cutover --source .cache/migration-cutover.sqlite3 --output .cache/migration-cutover.json --writes-paused
npm run build
.\start.ps1
~~~

Wait for shutdown before taking the cutover backup. Use new backup filenames for subsequent rehearsals. Verify existing sign-in, saved evidence, citations, demo access and restart persistence before considering cutover complete. The transfer refuses to overwrite existing PostgreSQL records. Before new PostgreSQL writes, rollback can restore the retained previous application and its SQLite backup. After new writes, stop and preserve both stores for reconciliation; starting the old SQLite application would lose those new changes. This version never silently falls back to SQLite.

## Account email setup

Verify `mail.tracedesk.nishantraj.me` in Resend and configure `RESEND_API_KEY`, `DESK_PUBLIC_URL` and the retained encryption key from [.env.example](.env.example). Messages use `TraceDesk <notifications@mail.tracedesk.nishantraj.me>` with replies to `yournishantraj@gmail.com`; a purchased mailbox is unnecessary. Disable click and open tracking for authentication messages. The encrypted delivery queue runs with the backend and retries at most five times using stable Resend idempotency keys.

`DESK_PUBLIC_URL` is the frontend HTTPS origin used in email links. An active local HTTPS tunnel can be used before deployment; no particular temporary tunnel hostname is required. Phase 11 will set it to `https://tracedesk.nishantraj.me`, with the API hosted on Render at `https://api.tracedesk.nishantraj.me`.

Add a Resend webhook for `email.sent`, `email.delivered`, `email.delivery_delayed`, `email.failed`, `email.bounced` and `email.complained`. Its final URL is **`https://api.tracedesk.nishantraj.me/api/email/webhook`**; local testing uses the current backend tunnel origin followed by `/api/email/webhook`. Save the endpoint's signing secret in `RESEND_WEBHOOK_SECRET` and restart the backend. Sending works without this secret, but delivery receipts cannot be authenticated until it is configured. Receipts never verify accounts or change passwords.

Existing accounts retain their passwords and saved history; migration sends no bulk mail. Unverified regular accounts sign in to a dedicated verification screen with resend, check-verification and sign-out controls. Verification is sent on signup or explicit request and requires an explicit confirmation within 24 hours. Password reset links expire after 30 minutes, change the password only after submission, revoke existing sessions and establish email ownership. Reset rejects the current password without consuming the link. Password fields have show/hide controls. Recovery offers a registration link for new users while returning the same response for registered, unknown and demo addresses. Both email flows use single-use tokens submitted through POST; the frontend clears link tokens from the address bar. Reopening a used link can display its form, but another submission is rejected. Demo Account remains read-only and cannot use recovery to change its public identity.

## Install dependencies

~~~powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade "pip>=26.2"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci
npm run build
.\start.ps1
~~~

These commands require registry access. They were verified on 2026-11-07 in an isolated clean checkout with fresh Python/npm dependencies, the documented launcher and a disposable schema on the separate test database. Startup, sign-in, saved reports and restart recovery passed.

For frontend development, run the backend launcher and, in a second terminal:

~~~powershell
npm run dev
~~~

The development frontend at http://127.0.0.1:5173 proxies `/api` to `DESK_PORT` from the environment or root `.env`, defaulting to port 8000 (8020 on this machine). Use either `127.0.0.1` or `localhost` consistently for browser sessions.

## Verification

The results below describe the verified local candidate. Updated test fixtures and the browser harness remain local and are not included in this application-only publication.

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
npm test
npm run build
~~~

Local readiness passed on 2026-11-07: **164 backend tests**, **29 frontend tests**, TypeScript/production build and **21 isolated browser checks**, with zero reported axe accessibility violations or browser page errors. Browser checks cover accounts, file/paste imports, explicit investigation, report citations, unchanged original reports after Analyze further, all five filters/search/history, chart totals, and desktop/tablet/mobile keyboard and reduced-motion behavior. Loading/error/empty/disabled states and short-source focus were also inspected. Providers and account email delivery were mocked/captured; no live model requests were made.

Historical migrations and clean startup/restart preserve original evidence, reports, IDs, timestamps, ownership and source relationships. The earlier PostgreSQL cutover matched all 18 original tables. `python -m pip check`, npm audit and the Python dependency audit passed with zero known vulnerabilities after upgrading PyJWT and pip. Existing credentials, the working database, six demos and benchmark-v1 were preserved. The local candidate is ready to begin deployment setup; hosting and hosted email/Sentry acceptance remain separate work.

Earlier live-provider observations are retained below as historical evidence, not results of this local-readiness pass.

The latest live Jev/Groq report correctly identifies a handled integer-parsing `ValueError` in `record-processor`, its linked server HTTP 422 response and a separate HTTP 200 request. Its citations support the failure mechanism without inventing another service or claiming recovery. Earlier rejected reports remain unchanged. Broader evaluation in Phase 10 must still address redundant recommendations, unrelated deadline caveats and wording that confuses selected evidence with the full snapshot.

Jev tests cover strict response validation, input/redaction bounds, retries/deadlines, fallback, separate usage, saved results, ownership and preservation of evidence when advisory hints are added. Live OpenRouter Jev and four frozen Jev/Groq/combined comparisons completed. The earlier reliability milestone also passed two live original reports per failure mechanism and explicit supplement flows.

Backend tests require network access to `DESK_TEST_DATABASE_URL` and create disposable schemas on the separate test project. Runtime checks start four local processes on ports 8765–8768; reference workload files live under `.cache`. They cover authentication, CSRF, account isolation, real failure scenarios, healthy traffic, recovery, evidence limits, citations, quota handling, restart and saved results. Mocked provider checks consume no Groq quota.

Frontend checks cover TypeScript compilation, API handling, imports, evidence review/fallback rendering, sources, saved reports, Sentry metadata and account-link states. Rendered acceptance used an isolated PostgreSQL fixture with mocked providers and locally captured account email. All 21 browser checks have passing evidence across the full invocation and affected account/demo rerun. The original failed runs and final results remain in `.cache/local-readiness-20261107/`; the browser harness and updated regression tests are retained locally. Hosted email/Sentry behavior remains separately unverified.

External Sentry acceptance follows Phase 11 deployment of React on **Vercel** at `tracedesk.nishantraj.me` and Python on **Render** at `api.tracedesk.nishantraj.me`, with Neon PostgreSQL and verified HTTPS sessions. Hosted callback/webhook verification, Sentry submission/approval and second-organization isolation remain pending. Automatic investigations and incident emails remain Phase 12.

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

The four-case review establishes working integration, not improved accuracy. Jev selected plausible problem families, but both Groq and combined reports made unsupported causal claims for incomplete evidence and invented relationships for disconnected imported logs. HTTP-hop wording and unqualified retry/pool-resizing advice also need review. Initial pool/slow combined attempts omitted advice for lack of space; retained reruns include it without evicting sources. All structured citation IDs resolved, which does not establish semantic support. Quota waits and some overlapping early invocations prevent a fair latency comparison.

The separate Phase 10 benchmark freezes twelve cases: two pool, two slow, four healthy/noisy and four ambiguous/incomplete. It compares an initial-snapshot report, Groq with tools and the same investigator with Jev. Run it explicitly:

~~~powershell
.\.venv\Scripts\python.exe scripts/evaluate_accuracy.py freeze
.\.venv\Scripts\python.exe scripts/evaluate_accuracy.py run --live
.\.venv\Scripts\python.exe scripts/evaluate_accuracy.py summary
~~~

Evidence and the investigator implementation are pinned under `.cache/phase10/benchmark-v1`; operator expectations never enter model inputs. Live runs require the separate test target, share the demo-preparation lock, stop on provider cooldown and skip retained attempts. Pause with `scripts/evaluate_accuracy.py pause`, then rerun `scripts/evaluate_accuracy.py run --live` to continue from the next unattempted mode/case. `--retry` retains an additional attempt without replacing the first. Source-based semantic scores are recorded with `review --attempt PATH --scores PATH`; every citation must be assessed. Summaries retain failed and unreviewed results, actual usage, calls, latency and storage-version caveats.

The benchmark is paused at the user's request because of provider quota limits: 18 of 36 first attempts are retained, with nine completed reports and nine failures. All nine reports have source-based reviews, including incorrect HTTP attribution, mixed request traces, unsupported advice and appropriate healthy-case abstention. The remaining 18 attempts are pending; no accuracy benefit from Jev is claimed.

Run a live check explicitly, one investigation per invocation (these consume available free Groq quota):

~~~powershell
.\.venv\Scripts\python.exe scripts/live_check.py pool
.\.venv\Scripts\python.exe scripts/live_check.py slow
.\.venv\Scripts\python.exe scripts/live_check.py healthy
.\.venv\Scripts\python.exe scripts/live_check.py pool --supplement
.\.venv\Scripts\python.exe scripts/live_check.py slow --supplement
~~~

The script requires `DESK_TEST_DATABASE_URL` and starts its own isolated development lab on free loopback ports. It creates a verified fixture account only in a new operator schema on the separate test project; its password stays in memory and email delivery is disabled. It runs real service traffic, polls progress, signs back in, verifies saved results and all structured citations through the protected API, and retains the schema and report/evidence/diagnostic artifacts under `.cache/live-verification/`. `--supplement` explicitly requests one supplement after the original Groq report and verifies that both can be revisited. Every invocation uses the same configured Groq account. Healthy checks make no model call. Review the reported mechanism against the evidence; valid citation IDs alone do not establish semantic correctness.

## Storage and files

- PostgreSQL: accounts/security, sessions, imports/connections, evidence, reports, demo relationships and email delivery records.
- `data/platform.sqlite3`: retained legacy platform database; keep it as a migration backup, not an automatic fallback.
- `data/payment.sqlite3`: reference-service workload.
- `data/telemetry/`: captured JSONL service observations.
- `data/*.log`: process diagnostics.
- `backend/`: API, authentication, services, controller, evidence tools and investigators.
- `src/`: React application.
- `tests/`: isolated backend integration and frontend contract checks.

Local data, dependencies and secrets are ignored by [.gitignore](.gitignore). Retain database backups, telemetry and encryption keys together. Hosted PostgreSQL backup/restore remains Phase 11 work. Evidence is never automatically deleted to remain under free-plan limits.

Detailed progress and working notes are maintained locally and are not included in the repository.

