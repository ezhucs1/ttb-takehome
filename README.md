# LabelVerify

AI-assisted alcohol label verification for TTB COLA review.

Applicants upload label artwork, the engine reads it and compares every required field
against the application, and a labeling specialist confirms the result. The tool
recommends; a person decides.

![Specialist review screen](docs/screenshots/review.png)

## What it does

**For applicants**

- Upload the label first, front and back panels together if the product has both. Images
  can be added one at a time, removed individually, and reordered; the application form
  fills itself from what is printed across all panels, including the type of product,
  and step 2 shows what that class of label must carry, with the regulation for each item.
- Run a pre-check before submitting and fix problems while they are cheap.
- See correction requests as plain-language notices, reply on the exact field in
  question, and resubmit with a revised label. The application keeps its number.
- Know when the other side has written. An Inbox in the sidebar carries a live count of
  unread items; the inbox lists each reply, notice, and decision with a link that lands on
  the exact field; new items are marked on the application page. Opening an item reads
  that item only; opening the application from a list reads everything on it. Works for
  both roles.
- Batch upload: a CSV of applications plus a zip of images, up to 300 at a time, checked
  in the background with live progress.

**For labeling specialists**

- A queue sorted so applications where every field matched come first, with tabs for
  "ready", "needs a look", "awaiting applicant", and "approved".
- A review screen with the label image, a field-by-field comparison with confidence per
  field, a word-level diff of the Government Health Warning, and a comment thread on
  every field.
- One-click approve, bulk approve for clean applications, or a correction request whose
  notice is drafted from the findings and edited before it goes out. A drafted notice can be
  discarded, which puts the panel back exactly as it was.

Both roles get a dark theme by default and a light theme behind the toggle in the sidebar,
remembered per browser. Type is Inter and JetBrains Mono, served from the app itself:
nothing on any page loads from a CDN.

**The engine** (no web dependency, fully unit tested)

| Field | Strategy | Example that matches |
| --- | --- | --- |
| Brand name, class/type, producer | Identical after normalization (case, punctuation, spacing, synonyms). Similar but different goes to a human. | `STONE'S THROW` = `Stone's Throw`; `Whisky` = `Whiskey` |
| Alcohol content | Parsed to % ABV; proof converted; label proof must equal 2 × ABV | `45% Alc./Vol. (90 Proof)` = `45` = `90 proof` |
| Net contents | Parsed to mL; standard-of-fill sizes noted | `750 mL` = `0.75 L` = `75 cL` |
| Country of origin | Required for imports only; aliases folded | `Product of Scotland` = `United Kingdom` |
| Type of product | The class the label implies must be the class filed; if none was filed, the label decides and that class's rules apply | `Straight Bourbon Whiskey` filed as wine is a mismatch |
| Sulfite declaration | Wine only: "Contains sulfites" is required at 10 ppm or more, so a missing statement goes to review | wine label without the statement |
| Qualifying phrase | The words before the producer's name ("Distilled by", "Produced and bottled by", "Brewed by"); imports must also name the importer | label with no such phrase |
| Age statement | Whisky only: required when aged under four years, so a whisky label without one goes to review | bourbon with no "Aged" statement |
| Bottled in Bond | Only when the label claims it: must be 100 proof, a hard finding | "Bottled in Bond" at 90 proof |
| Blend percentage | Only when the class/type says blended: the percentage of straight whisky must appear | "Blended Bourbon" with no percentage |
| Appellation of origin | Wine only, when the label names one: shown with its citation; the grape-source percentages are a records check | "Napa Valley" |
| Vintage year | Wine only, when the label states one: it must sit beside an appellation of origin and be a real past year | a 2022 vintage with no appellation |
| Estate Bottled | Wine only, when the label claims it: there must be a viticultural area appellation on the label | "Estate Bottled" with no appellation |
| Statement of strength | Malt beverages only, when the label emphasizes alcoholic strength: goes to review | "Extra Strength Lager" |

**The rules differ by class**, and the engine applies the class's own (27 CFR part 5 for
distilled spirits, part 4 for wine, part 7 for malt beverages; part 16 for the warning):

| | Distilled spirits | Wine | Malt beverage |
| --- | --- | --- | --- |
| Alcohol content | Required; proof allowed; ±0.15 | Required; "Table Wine" may replace the number at 7 to 14%; ±1.5 up to 14%, ±1.0 above, never across the 14% tax class line | Optional federally; ±0.3 when stated |
| Net contents | Metric; standards of fill | Metric; standards of fill | Fluid ounces or metric; no standards of fill; may be blown into the glass, so a label without one goes to review |
| Sulfites | Not applicable | Required at 10 ppm or more | Not applicable |
| Class / type | A class or type from the standards of identity | A class or type; a grape variety may serve | Must contain a recognized designation (beer, ale, lager, stout, porter, malt liquor ...) or it goes to review |
| Class-specific statements | Age, bottled in bond, blend percentage | Appellation, vintage, estate bottled | Statements of strength |

A difference inside the class's labeling tolerance is a review item rather than a
mismatch, because the application should carry the labeled figure. Standards of fill are
advisory (a note, never a verdict) because TTB's list changes; the engine carries the
January 2025 sizes. The rulebook lives in one module, `labelverify/engine/rules.py`, and
drives the comparisons, the applicant's step-2 checklist, the citations on every result
row, and the reference page at `/rules` (linked as "Reference" from the form). The form
itself follows the detected class: a red mark on every field the class requires, and an
"also read from the label" list of the items the applicant never types (qualifying
phrase, sulfites, appellation, vintage, age statement) with what the read found, so they
are reviewed before the check. Changing the type re-applies the rules.
| Health warning | Word for word against 27 CFR 16.21; `GOVERNMENT WARNING` must be all caps; bold is a visual judgment that goes to review when uncertain | exact statutory text |

Per-field verdicts are match, needs review, mismatch, or not applicable. A match from a
low-confidence read keeps its verdict (the values do agree) but is marked "unverified
read", and the application goes to review rather than approval. The roll-up is approve,
needs review, or request correction.

## Run it locally

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/ezhucs1/ttb-takehome && cd ttb-takehome
uv venv && uv pip install -e ".[dev]"
cp .env.example .env                                          # optional: add ANTHROPIC_API_KEY (read at startup)
.venv/bin/uvicorn labelverify.web.app:serve --factory --reload  # http://127.0.0.1:8000
```

Open the landing page, choose Sign in, and use one of the demo accounts (password
`labelverify`). Locally the dialog lists them; a public deployment hides the list.

| Role | Account | What to try |
| --- | --- | --- |
| Labeling specialist | sarah.chen@ttb.gov | Open the queue, review a "needs a look" item, draft a correction notice, answer the reply waiting in the inbox |
| Applicant | maria@alvarezlabels.com | Start a new application from a sample label, run the pre-check, submit; fix the one awaiting correction; batch upload with the template CSV and the sample images |

Maria is a label-compliance agent filing for several producers, so her one account holds
all ten sample cases.

The `beverage_type` column may be left blank in a batch CSV, and the type may be left on
"Detect from the label" in the form: the reader decides the class and the record says the
type was taken from the label.

**Trying the batch upload** needs a CSV and a zip of images. The batch page offers both
ready-made: a sixteen-row CSV and a zip of the sample labels. Fourteen rows are the bundled
samples; two are deliberately broken, a photo that is not a label and a row whose image is
missing from the zip, so the summary shows failures next to the AI's findings. Download
them, choose them in the form, and start the batch; the page shows progress and every
readable row lands in the specialist's queue. In demo mode it finishes in a second or two. With a real API key the
same batch makes fourteen model reads, which is a fair test of the concurrency and the
per-label timing.

The database is seeded on first start with fourteen sample applications in a mix of states
so every screen has content.

**Without an API key** the app runs in demo mode: the fourteen bundled sample labels work end
to end, and uploading your own image gives a clear message instead of a made-up result.

**With `ANTHROPIC_API_KEY` in `.env`** (or the environment) the vision extractor reads any label you upload, and the
correction notice is rewritten by the model before the specialist edits it. Every result
shows the read time so you can check it against the five-second budget. A label set is
read once, when it is uploaded; the pre-check and the submission compare against that
read in milliseconds, and a new read happens only when the images change or a specialist
asks for a re-check.

**With `GEMINI_API_KEY` instead** the same flow runs on Google Gemini (the free tier is
enough to try it). Set `LABELVERIFY_EXTRACTOR=gemini` to force it when both keys are
present. The free tier allows a small number of requests per minute, so batch uploads
will pace themselves with retries.

### If the model is unavailable

Every read the app makes has a local fallback: when the configured vision model fails
(timeout, network, rejected key, exhausted daily budget), the label is read with
Tesseract OCR on the server instead, and the result says so in three places. The step-1
status and the step-2 hint say "The vision model was unavailable (...), so the label was
read with local OCR (Tesseract), which is less accurate"; the comparison banner names the
reader as `tesseract (fallback after claude failed)`; and the same name is stored on the
run, so the specialist's page and the batch rows show it too. OCR reads carry lower
confidence, so more rows land in review than with the model, which is the right bias for
a degraded read.

The fallback needs the `tesseract` binary (`apt install tesseract-ocr` or `brew install
tesseract`; the Docker image installs it). The app looks on its PATH, then in the usual
install locations (Homebrew, snap, apt, the Windows installer). If it still says the
binary was not found, the process that runs the app has a different PATH from your
shell: put the full path in `LABELVERIFY_TESSERACT_CMD` (what `which tesseract` prints).
Without the binary there is no fallback and the failure is reported with a "Read again"
button.

To try it:

```bash
# Force local OCR for every read (the sidebar then says "Local OCR (Tesseract)")
LABELVERIFY_EXTRACTOR=tesseract .venv/bin/uvicorn labelverify.web.app:serve --factory

# Simulate a model outage with the model configured: a rejected key fails fast
ANTHROPIC_API_KEY=sk-ant-not-a-real-key .venv/bin/uvicorn labelverify.web.app:serve --factory

# Or the same on the command line
.venv/bin/python -m labelverify.cli extract labelverify/samples/old-tom-bourbon.jpg --extractor tesseract
.venv/bin/python -m labelverify.cli verify labelverify/samples/old-tom-bourbon.jpg --application app.json --fallback
```

`LABELVERIFY_FALLBACK=none` turns the automatic fallback off, for a deployment that
would rather report the outage than show a lower-quality read.

What to expect from an OCR read, measured on the fourteen bundled samples (artwork and
three simulated photos), after the brand is taken from the largest type on the front
panel and dark labels are read inverted:

| Field | Right | Notes |
| --- | --- | --- |
| Brand name | 12 of 14 | misses the steeply angled photo and one dark can |
| Class / type | 12 of 14 | the same two |
| Net contents | 12 of 14 | |
| Alcohol content | 10 of 14 | small print on the photos |
| Producer name | 9 of 14 | |
| Address | 8 of 14 | |
| Warning present | 13 of 14 | the word-for-word check still runs on what was read |

About one second per label. Every OCR value is reported at low confidence, so the rows
that agree with the application show "match · uncertain read", the ones that differ show
a mismatch, and the application goes to review either way. The form in step 2 is filled
from the read: correct the fields that are wrong before checking, exactly as you would
check a model read against your application.

Compare extractors on the same label from the command line:

```bash
.venv/bin/python -m labelverify.cli extract labelverify/samples/old-tom-angled-photo.jpg --extractor claude
.venv/bin/python -m labelverify.cli extract labelverify/samples/old-tom-angled-photo.jpg --extractor gemini
.venv/bin/python -m labelverify.cli extract ~/Pictures/my-bottle.jpg --extractor gemini   # any photo of yours
.venv/bin/python -m labelverify.cli extract front.jpg back.jpg --extractor claude            # a front-and-back set, read as one

# Median latency per extractor on one image (what the five-second budget is measured against)
.venv/bin/python -m labelverify.cli bench labelverify/samples/old-tom-angled-photo.jpg --extractors claude,gemini --runs 3
.venv/bin/python -m labelverify.cli bench labelverify/samples/old-tom-angled-photo.jpg --extractors claude --model claude-haiku-4-5
# A read that times out: wait longer to learn the real latency, then decide what to change
.venv/bin/python -m labelverify.cli bench front.jpg back.jpg --extractors claude --runs 3 --timeout 120
```

Both commands print each panel's size after preprocessing and, on a failed read, how long they
waited. `--timeout` and `--model` apply to that run only and override `.env`.

### Sample labels

Fourteen labels rendered in four visual styles, three of them passed through a photo
simulation (bottle curvature, perspective, glare, grain, blur). Each demonstrates one
outcome:

| Label | Demonstrates | Expected |
| --- | --- | --- |
| Old Tom Bourbon, clean artwork | Everything matches | Approve |
| Old Tom Bourbon, title-case warning | `Government Warning:` instead of caps | Request correction |
| Old Tom Bourbon, bottle photo | Label 40% vs application 45% | Request correction |
| Old Tom Bourbon, angled phone photo | Low confidence on small print, bold unknown | Needs review |
| Stone's Throw Cabernet | `STONE'S THROW` vs `Stone's Throw` | Approve |
| Sunset Ridge Rosé | Warning reworded ("can cause health issues") | Request correction |
| Glen Aldie Scotch | Import, `Product of Scotland` vs `United Kingdom` | Approve |
| Harbor Light IPA | No warning statement at all | Request correction (seeded as rejected, with notice) |
| Harbor Light IPA, can photo | 16 fl oz on label vs 12 fl oz on application | Request correction |
| Copper Ridge Rye | Brand printed `COPPER RIGDE` | Needs review |
| Old Tom Bourbon, no age statement | Whisky with no statement of age | Needs review |
| Copper Ridge Rye, bonded at 90 proof | "Bottled in Bond" at 45% alc/vol | Request correction |

Regenerate them with `.venv/bin/python scripts/make_samples.py`.

### Tests

```bash
.venv/bin/python -m pytest     # all offline, with a fake API client
.venv/bin/ruff check .         # lint
```

Coverage: normalizers and parsers, every comparison rule, the health-warning rules and
diff, image preprocessing, all three extractors (Claude against a fake client), the
workflow services against a throwaway SQLite database, every HTTP route for both roles,
batch processing, and one test per sample label asserting the promised recommendation.

**Communication scenarios.** `tests/test_communication.py` plays out situations between
an applicant and a specialist over HTTP on a clean database (demo users, no seeded
applications): who sends, who receives, what shows where, when it counts as read, what
the other side must never see, and that nothing is lost across a restart. Run it on its
own with verbose names so the output reads as a checklist:

```bash
.venv/bin/python -m pytest tests/test_communication.py -v
```

## Deploy

**Docker**

```bash
docker build -t labelverify .
docker run -p 8000:8000 -v labelverify-data:/app/data -e ANTHROPIC_API_KEY=... -e SECRET_KEY=... labelverify
```

**Fly.io** (always-on machine, persistent volume): see `fly.toml`. Railway or Render work
the same way; use a paid or always-on tier so the demo does not hit a cold start, which is
exactly the vendor-pilot failure from the interviews.

`fly.toml` sets two guards for a public URL: the sign-in dialog shows no demo credentials
(reviewers take them from this README), and paid model reads are capped per day so an
open link cannot run up the API bill. Both are plain environment variables, so any host
can set them.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | | Enables the vision extractor and notice rewriting |
| `GEMINI_API_KEY` | | Enables the Gemini extractor and, by default, Gemini wording of correction notices |
| `LABELVERIFY_NOTICE_PROVIDER` | `gemini` if its key is set, else `claude`, else `template` | Who rewrites correction notices; the findings always come from the engine |
| `LABELVERIFY_GEMINI_MODEL` | auto | Gemini model; blank picks the newest stable Flash model from Google's model list, and a retired name falls back to the replacement Google suggests |
| `LABELVERIFY_EXTRACTOR` | `claude`, else `gemini`, else `demo`, by which key is set | `claude`, `gemini`, `tesseract`, or `demo` |
| `LABELVERIFY_MODEL` | `claude-sonnet-5-5` | Extraction model. Measured: Sonnet 5.5 4.1 s, Opus 5.5 6.0 s on the hardest sample; `claude-haiku-4-5` is faster if its reads hold up |
| `LABELVERIFY_NOTICE_MODEL` | `claude-opus-5-5` | Claude model for the notice rewrite when the provider is `claude` |
| `LABELVERIFY_EXTRACT_TIMEOUT` | `20` | Seconds before a one-image read is abandoned; each extra panel adds half again (a front-and-back set gets 30 s). A failed read in the wizard offers "Read again" on the stored images |
| `LABELVERIFY_DAILY_READ_LIMIT` | unlimited | Paid model reads allowed per UTC day; after that uploads get a clear message and the sample labels still work. Set it on any public URL |
| `LABELVERIFY_DEMO_ACCOUNTS` | `true` | Show the demo account list in the sign-in dialog. Set `false` on a public URL; reviewers use the accounts in this README |
| `LABELVERIFY_REPO_URL` | this repository | GitHub link on the landing page |
| `LABELVERIFY_TESSERACT_CMD` | found on PATH | Full path of the tesseract executable when the app cannot find it on its own |
| `LABELVERIFY_FALLBACK` | `tesseract` | Reader used when the configured one fails on a read; `none` turns the fallback off |
| `LABELVERIFY_SECURE_COOKIES` | `false` | `true` marks the session cookie Secure; set it behind HTTPS (the Fly config does) |
| `LABELVERIFY_STRUCTURED_OUTPUT` | `false` | `true` asks the API to constrain the reply to the extraction schema. Off by default: the API compiles a new schema into a grammar on first use, and that compile can take longer than a read is allowed to. The default asks for JSON in the prompt and validates it here |
| `LABELVERIFY_IMAGE_MAX_EDGE` | `1500` | Long edge in pixels after preprocessing; smaller is faster, larger keeps more small-print detail |
| `DATABASE_URL` | `sqlite:///./data/labelverify.db` | SQLAlchemy URL; Postgres works unchanged |
| `SECRET_KEY` | generated once, kept beside the database | Signs session cookies; set it explicitly in any shared deployment |

## How it is built

```
labelverify/
  engine/                 pure verification logic, no web imports
    models.py             ApplicationData, LabelExtraction, VerificationResult
    normalize.py          text, ABV, volume, address, country normalizers
    warning.py            health warning rules and word diff
    compare.py            per-field rules and roll-up
    preprocess.py         EXIF rotation, 1500 px downscale, JPEG re-encode
    notices.py            correction notice template and optional model rewrite
    verify.py             orchestration with timing and fallback
    extractors/           claude and gemini (vision), tesseract (local OCR), demo (samples), fixture (tests)
  web/
    app.py                FastAPI factory, session middleware, error handlers
    models.py             SQLAlchemy tables: users, applications, images, runs, comments, events, notices, views, batches
    services.py           every state change: create, verify, submit, review, decide, resubmit, batch
    routes/               shared (auth, images, comments), applicant, specialist, api
    templates/            Jinja2 pages and partials
    static/               one stylesheet, one script, two self-hosted typefaces, no external assets
  samples/                bundled labels and manifest
  cli.py                  command-line verify/extract for latency checks
scripts/make_samples.py   renders the sample labels
docs/                     decisions.md, production.md, screenshots
tests/                    pytest suite
```

Request flow for one check: image bytes → preprocess → extractor → `LabelExtraction` →
compare against `ApplicationData` → `VerificationResult` stored as a run on the
application. No model is involved in the comparison step.

## Screens

| Specialist queue | Applicant pre-check | Correction loop |
| --- | --- | --- |
| ![Queue](docs/screenshots/queue.png) | ![New application](docs/screenshots/new-application.png) | ![Applicant detail](docs/screenshots/applicant-detail.png) |

## Documentation

- [docs/decisions.md](docs/decisions.md): engineering decisions with alternatives and reasoning
- [docs/production.md](docs/production.md): what a production deployment would need (identity, FedRAMP, firewall, storage, evaluation)
- `docs/user-guide.md`: how each role uses the app, screen by screen, and what every tag, badge, and symbol on the result means.

## Assumptions and limitations

- Cloud model access is allowed for the prototype. The interviews flag that TTB's network
  blocks many outbound domains; the extractor interface, the Tesseract fallback, and the
  absence of any CDN-loaded assets are the mitigations, and `docs/production.md` names
  the Azure-hosted path.
- The local fallback is Tesseract, a less accurate reader than PP-OCR, chosen because it
  installs as one system package with no model download, runs in under a second on an old
  CPU, and because the fallback's errors come mostly from assigning text to fields, which
  is the same code for either engine. Its reads are reported at low confidence so the
  confidence gate sends them to review and the result says "read with local OCR". The
  reasoning is in `docs/decisions.md` (entry 20); a PaddleOCR reader is a small optional
  extra for an offline installation that can bundle its models.
- Up to four images per label set (front, back, neck). They are read together in one
  model call and each field is reported once.
- The seven fields named in the brief are checked under the rules of the product's class,
  plus: the type-of-product consistency check, the qualifying phrase, and, where the class
  and the label call for them, the sulfite declaration (wine), the age statement (whisky),
  the bottled-in-bond proof, and the blend percentage. Rules the engine cannot judge from
  a read (state of distillation, appellations, vintage, varietal percentages, type sizes)
  are listed on the rules page as the specialist's checks.
- Bold detection on the warning heading is a visual judgment and is surfaced for review
  rather than failed automatically.
- Sample labels are synthetic renders, not real COLA images.
