# Architecture

How LabelVerify is built: the stack, the pipeline, the workflow, the data model, the
module layout, the libraries, the configuration, and the Azure deployment. The reasoning
behind each choice is in [decisions.md](decisions.md); this page describes what is there.

## System in one paragraph

One Python process serves both roles. FastAPI renders Jinja2 pages and a small JSON API;
a verification engine with no web imports reads a label with a vision model and compares
it with the application in code; SQLAlchemy stores applications, images, runs, comments,
notices and batches in SQLite (Postgres by changing one URL); a thread pool runs batch
rows in the background. One container, one volume, no job queue, no front-end build, no
asset loaded from a third party.

```
browser ──HTML/forms/fetch──▶ FastAPI routes ──▶ services package ──▶ SQLAlchemy ──▶ SQLite
                                                   │
                                                   ▼
                                       engine: preprocess ─▶ extractor ─▶ compare ─▶ result
                                                              (Claude / Gemini / Tesseract / demo)
```

## The verification pipeline

For one check, from image bytes to a stored result:

1. **Preprocess** (`engine/preprocess.py`). EXIF rotation, downscale to 1200 px on the
   long edge (`LABELVERIFY_IMAGE_MAX_EDGE`), flatten transparency, re-encode as JPEG at
   quality 85. The browser already shrinks phone photos to 1600 px before upload so the
   upload is quick on a home connection.
2. **Extract** (`engine/extractors/`). One model call reads every panel of the label set
   (front, back, neck; up to four) and returns a `LabelExtraction`: every field as text
   exactly as printed with a confidence, the warning text with its heading flags, the
   class the label implies, and image-quality notes. Readers: Claude (default), Gemini
   (same prompt, same schema), Tesseract plus field-assignment heuristics (local
   fallback), and a demo reader that returns ground truth for the bundled samples and
   refuses anything else. A `BudgetedExtractor` wrapper can cap paid reads per day.
3. **Compare** (`engine/compare.py`, `warning.py`, `normalize.py`, `rules.py`). Pure
   code. The class is resolved (filed type, confirmed or decided by the label), the
   class's rulebook is consulted, and each field gets a `FieldResult`: verdict (match,
   needs review, mismatch, not applicable), reason, citation, confidence, and for the
   warning a word-level diff. A confidence gate softens verdicts from poor reads. The
   roll-up is approve, needs review, or request correction. No model is involved.
4. **Store** (`web/services/runs.py`). The extraction is cached on the application with
   its version and read time; the result is stored as a `VerificationRun` with its
   trigger (precheck, submit, resubmit, batch, rerun), reader name and timings. The
   pre-check and the submission reuse the cached read; a new upload or a specialist's
   re-check reads afresh.

Timing is measured around steps 1 to 3 and shown on every result (read and compare
separately), so the five-second budget is checked on every label, not estimated.

## The workflow

One `Application` record serves both roles through a status machine enforced in
`services/common.py`:

```
draft ─▶ submitted ─▶ under_review ─┬─▶ approved
                                    ├─▶ rejected
                                    └─▶ correction_requested ─▶ resubmitted ─▶ (as submitted)
```

- The applicant creates a draft by uploading the label set; the read fills the form; the
  pre-check compares; submission stores the final result and moves the record on.
- The specialist's queue lists submitted and resubmitted applications newest first, with
  tabs by recommendation. Opening one claims it (under review). Decisions are approve,
  reject, or request corrections with a notice built from the findings (template first, a
  model rewrite on top when one is configured, edited by the specialist before sending).
- Every status change is a `StatusEvent`; every message is a `Comment` on a field or on
  the application; every correction request is a `Notice`. The inbox for each role is
  derived from these records with per-item read receipts and "opened the application"
  timestamps, never from a separate notifications table.
- A batch is a `Batch` with one `BatchItem` per CSV row linking to the application it
  produced. Rows run in a five-worker thread pool, each in its own session, with the
  model call held outside any write transaction; a finalizer closes the batch whatever
  happens, and a batch left "processing" by a restart is closed at startup.

## Database schema

SQLite in WAL mode, created on first start; columns added later are appended to an
existing file at startup (`db.py`). Ids are 32-character random hex. Label image bytes
are stored in the database (deferred column, loaded only when read); production would
move them to object storage.

| Table | Purpose | Key columns |
| --- | --- | --- |
| `users` | Demo accounts | email, name, role (applicant, specialist), organization, password_hash |
| `applications` | One COLA application | serial (`COLA-2026-XXXXXX`), applicant_id, status, beverage_type (blank = decided by the label), the seven filed fields, is_import, ten nullable statement columns (qualifying phrase, warning text, sulfites, appellation, vintage, estate bottled, age, bottled in bond, blend percentage, strength claim), cached extraction (json, version, ms, extractor), latest_run_id, recommendation, risk_score, specialist_id, batch_id, timestamps |
| `label_images` | Panels of a label set | application_id, filename, media_type, data (bytes), width, height, version (upload set), panel (order) |
| `verification_runs` | Every comparison made | application_id, image_id and version, trigger, extractor, recommendation, result_json, extraction_ms, total_ms, error |
| `comments` | Field-anchored threads | application_id, field, author_id, body, resolved |
| `notices` | Correction requests and rejections | application_id, body, drafted_by (ai or template), sent_by_id |
| `status_events` | The audit trail of the status machine | application_id, from_status, to_status, actor_id, note |
| `application_views` | When a user last opened an application | user_id, application_id, seen_at |
| `activity_reads` | Per-item inbox read receipts | user_id, kind (comment, notice, status), item_id |
| `batches` | One CSV upload | applicant_id, filename, status, total, completed, failed, finished_at |
| `batch_items` | One CSV row | batch_id, row_number, brand_name, image_name, application_id, status, error |

## Module layout

```
labelverify/
  engine/                     pure verification logic, no web imports
    models.py                 ApplicationData, LabelExtraction, FieldResult, VerificationResult (pydantic)
    rules.py                  the rulebook: per-class requirements, citations, tolerances, standards of fill
    normalize.py              text, ABV, volume, producer name, address and country normalizers
    warning.py                Government Health Warning rules and word diff
    compare.py                per-field comparison and the roll-up
    preprocess.py             EXIF rotation, downscale, JPEG re-encode
    notices.py                correction notice template and the optional model rewrite
    verify.py                 orchestration: prepare, extract, fall back, compare, time
    extractors/               claude, gemini, tesseract, demo, fixture (tests), budget (daily cap)
  web/
    app.py                    FastAPI factory, hardening middleware, error pages, startup tasks
    auth.py                   password hashing, signed session cookie, sign-in throttle, API key
    db.py                     engine, session factory, column migrations
    models.py                 the SQLAlchemy tables above
    services/                 every state change, split by concern:
      common.py               constants, the status machine, risk score
      runs.py                 uploads, prefill, cached reads, storing runs
      workflow.py             draft, submit, resubmit, claim, decide, bulk approve, comments
      activity.py             unread items and the inbox feed for both roles
      queue.py                the specialist queue, batch rollups, dashboard stats
      batches.py              CSV template and parsing, background processing, results export
    routes/                   shared (sign-in, images, comments, inbox), applicant, specialist, api
    render.py, seed.py        template helpers; the demo accounts and sample applications
    templates/, static/       Jinja2 pages and partials; one stylesheet, one script, two typefaces
  samples/                    fourteen synthetic labels and their manifest
  testdata/cola/              sixty registry labels, scenarios.json, applications.csv
  cli.py                      verify, extract and bench on the command line
scripts/                      make_samples.py, import_cola.py, deploy_azure.sh
tests/                        378 offline tests
```

Routes stay thin: they parse the request, call one service function, and render. Every
state change lives in the services package so it can be tested without HTTP, and the
package's `__init__` re-exports the public names so callers write `services.<name>`.

## HTTP surface

| Area | Routes |
| --- | --- |
| Shared | `/` and `/login` (landing with the sign-in dialog), `/logout`, `/rules` (the rulebook), `/inbox`, `/inbox/open/{kind}/{id}`, `/inbox/read-all`, `/me/unread` (badge count, polled every 30 s), `/applications/{id}/images/{image}`, `/applications/{id}/comments`, `.../comments/{id}/resolve`, `/healthz` |
| Applicant (`/applicant`) | dashboard, `applications/new`, `applications` (create draft and read), `applications/{id}/read`, `/precheck`, `/submit`, `/resubmit`, `applications/{id}`, `batches` (page, template, sample and registry downloads, start), `batches/{id}` (page, `/rows` partial for polling, `/results.csv`) |
| Specialist (`/specialist`) | queue (tabs by query string), `applications/{id}` (review), `/notice` (draft), `/rerun`, `/decision`, `bulk-approve`, `batches/{id}` and its `bulk-approve` |
| API (`/api`) | `POST /verify` (image or sample id plus application JSON; a signed-in user or `LABELVERIFY_API_KEY`), `GET /samples` |

## Front end

Server-rendered HTML with one stylesheet (CSS custom properties for both themes) and
one script of plain JavaScript: async form submission with partial replacement, polling
for batch progress and the unread badge, the upload wizard (file picker with reorder and
remove, client-side shrink, read, prefill, locked steps), the image viewer (zoom and pan),
and the theme toggle applied before first paint. Inter and JetBrains Mono are served from
the app. No framework, no build step, nothing from a CDN; the Content-Security-Policy
allows scripts from this origin only.

## Libraries

| Library | Used for |
| --- | --- |
| FastAPI, Starlette, uvicorn | HTTP, routing, middleware, background tasks |
| Jinja2, python-multipart | Templates; form and file uploads |
| SQLAlchemy 2 | ORM and sessions; SQLite by default |
| pydantic 2 | The engine's models and validation of the model's JSON |
| anthropic | The Claude extractor and notice rewrite |
| Pillow | Preprocessing and sample rendering |
| pytesseract | The local OCR fallback (needs the `tesseract` binary) |
| rapidfuzz | Similarity scores for the review band |
| itsdangerous | Signed session cookies |
| python-dotenv | `.env` at startup |
| pytest, httpx, ruff, numpy (dev) | Tests, the HTTP test client, lint, the photo simulation |

The Gemini extractor uses the standard library's HTTP client so the second provider adds
no dependency.

## Configuration

Every value is optional; with none of them the app runs in demo mode.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | | Enables the Claude reader and, when chosen, the notice rewrite |
| `GEMINI_API_KEY` | | Enables the Gemini reader and, by default, Gemini wording of notices |
| `LABELVERIFY_EXTRACTOR` | `claude`, else `gemini`, else `demo`, by which key is set | `claude`, `gemini`, `tesseract`, or `demo` |
| `LABELVERIFY_MODEL` | `claude-sonnet-5-5` | Extraction model |
| `LABELVERIFY_FALLBACK` | `tesseract` | Reader used when the configured one fails; `none` turns it off |
| `LABELVERIFY_TESSERACT_CMD` | found on PATH | Full path of the tesseract executable |
| `LABELVERIFY_NOTICE_PROVIDER` | `gemini` if its key is set, else `claude`, else `template` | Who rewrites correction notices; the findings are always the engine's |
| `LABELVERIFY_NOTICE_MODEL` | `claude-sonnet-5-5` | Claude model for the rewrite |
| `LABELVERIFY_GEMINI_MODEL` | auto | Gemini model; blank picks the newest stable Flash model |
| `LABELVERIFY_EXTRACT_TIMEOUT` | `20` | Seconds before a one-image read is abandoned; each extra panel adds half again |
| `LABELVERIFY_IMAGE_MAX_EDGE` | `1200` | Long edge after preprocessing |
| `LABELVERIFY_STRUCTURED_OUTPUT` | `false` | `true` asks the API to constrain the reply to the schema (slow first call) |
| `LABELVERIFY_DAILY_READ_LIMIT` | unlimited | Paid reads allowed per UTC day |
| `LABELVERIFY_BATCH_MAX_ROWS` | `300` | Rows accepted per batch |
| `LABELVERIFY_DEMO_ACCOUNTS` | `true` | Show the demo account list in the sign-in dialog |
| `LABELVERIFY_DEMO_PASSWORD` | `labelverify` | The demo accounts' password at first seed |
| `LABELVERIFY_SECURE_COOKIES` | `false` | `true` behind HTTPS: Secure cookie and HSTS |
| `LABELVERIFY_API_KEY` | | Lets a client call `/api/verify` without a session |
| `LABELVERIFY_REPO_URL` | this repository | GitHub link on the landing page |
| `DATABASE_URL` | `sqlite:///./data/labelverify.db` | SQLAlchemy URL; Postgres works unchanged |
| `SECRET_KEY` | generated once, kept beside the database | Signs session cookies |

## Deployment

**Container.** `Dockerfile` builds a `python:3.11-slim` image with Tesseract installed,
the package installed with pip, and `/app/data` as the volume for the database and the
stored images. Any host that runs a container with a persistent directory and keeps it
awake will do; the two things a demo needs are no cold start and HTTPS.

```bash
docker build -t labelverify .
docker run -p 8000:8000 -v labelverify-data:/app/data -e SECRET_KEY=... labelverify
```

**Azure App Service** is where the live demo runs, through `scripts/deploy_azure.sh`
from a signed-in Azure CLI with Docker on the local machine:

1. Creates the resource group (`RG`, default `labelverify-rg`) in `LOCATION` (the demo
   uses `westus2`, where the subscription had B1 quota). The app name, which is the
   public hostname, is `APP`: the first run picks a random one and prints it, and it is
   kept in the local `.env` so later runs redeploy the same app. It is never derived
   from anything identifying.
2. Creates an Azure Container Registry with the admin account enabled, builds the image
   locally with Docker (free and trial subscriptions are not allowed Azure's own cloud
   build) and pushes it as `labelverify:<git short sha>`, signing Docker in with the
   registry's own credentials.
3. Creates a Linux App Service plan (`SKU`, default B1: Basic, which allows Always On)
   and a web app from the image, stating the image and registry explicitly as the
   `linux-fx-version` and `DOCKER_REGISTRY_SERVER_*` settings.
4. Sets the app settings: port 8000, App Service storage enabled so `/home` persists,
   a 240 s container start limit, `DATABASE_URL` under `/home/data`, a generated
   `SECRET_KEY`, the API key, demo accounts hidden, Secure cookies, the Tesseract
   fallback, and the optional Gemini key with Gemini as the notice provider, API key,
   demo password, daily read limit and batch limit when they are set in the shell.
5. Turns on Always On, HTTP/2, HTTPS only and the `/healthz` health check, restarts, then
   polls `/healthz` for up to 400 s and prints the URL.

Each run starts the app with a fresh, seeded database in a new directory named after
the image tag (`RESET_DATA=true`, the default), so a redeploy of the demo is always a
clean slate; `RESET_DATA=false` keeps the previous data. Re-running with the same `APP`
name rebuilds and redeploys in place: App Service pulls the new image, starts the new
container, and routes traffic to it once the health check passes. `az group delete
--name labelverify-rg` removes everything. Keys are read from the shell environment (the
developer sources a local `.env`, which is never committed) and land only in the app's
settings on Azure.

What the deployment does not have, and production would: an identity provider, a
managed database, object storage for images, a durable queue, logging and alerting,
and a FedRAMP-authorized model endpoint. See [PROTOTYPE.md](PROTOTYPE.md).
