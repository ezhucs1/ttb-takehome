# LabelVerify

AI-assisted alcohol label verification for TTB COLA review.

**Live demo:** add the address that `scripts/deploy_azure.sh` prints here before
submitting. Sign in with the accounts under "Run it locally"; the dialog hides them on a
public URL.

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
  The prefill is a convenience: a note on the form says the applicant must check and
  correct it, because the check compares the applicant's values against the label, and a
  value copied unchecked from the reader would only compare the reader with itself.
- Run a pre-check before submitting and fix problems while they are cheap.
- See correction requests as plain-language notices, reply on the exact field in
  question, and resubmit with a revised label. The application keeps its number.
- Know when the other side has written. An Inbox in the sidebar carries a live count of
  unread items; the inbox lists each reply, notice, and decision with a link that lands on
  the exact field; new items are marked on the application page. Opening an item reads
  that item only; opening the application from a list reads everything on it. Works for
  both roles.
- Batch upload: a CSV of applications plus a zip of images, up to 300 at a time, checked
  in the background with live progress, and a results CSV to download when it is done.

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
| Health warning | Word for word against 27 CFR 16.21; `GOVERNMENT WARNING` must be all caps and bold. A reader that says the heading is not bold sends the row to review; a reader that cannot tell adds a note, since type weight is hard to judge from an image, and the specialist has the image beside the table | exact statutory text |
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

Per-field verdicts are match, needs review, mismatch, or not applicable. The strength of
a verdict follows the confidence of the read. A match from a low-confidence read keeps
its verdict (the values do agree) but is marked "uncertain read", and the application
goes to review rather than approval. A difference from a low-confidence read is a review
item that says "confirm on the image, or ask for a clearer photo", not a mismatch: the
interviews say that when an agent cannot read a label the practice is to ask for a better
image, not to reject it for a mismatch, and a reader's slip must not become a correction
request. A difference read with confidence is a mismatch. The roll-up is approve, needs
review, or request correction.

## How it measures against the brief

The brief's interview notes set the bar: results in about five seconds, exact matching
with judgment where TTB allows it, batches of 200 to 300, an interface a novice can use,
a network that blocks most cloud services, and "a reliable core application over
incomplete feature sets, with transparent documentation of trade-offs made". Measured
against each, with the shortfalls stated:

| The brief asks for | What is here | Where it falls short |
| --- | --- | --- |
| Results in about 5 seconds; the vendor pilot took 30 to 40 | One model call reads a label set; the comparison is code and takes milliseconds. Measured on the hardest sample (angled, glary phone photo): Claude Sonnet 5.5 reads one panel in 4.1 s, a front-and-back set in 5.5 s. The set is read once at upload; the pre-check and the submission reuse that read, so "Check before submitting" is instant. Every result shows its read time, and a batch shows elapsed time per label | A two-panel set or a hard photograph can take 5 to 7 s on the model's slow tail. The budget is met for one panel of artwork and missed by a second or two for multi-panel photos; the UI never hides it |
| Exact matching: brand, class/type, ABV, net contents, producer, origin, and a word-for-word Government Warning with an all-caps bold heading | The warning is diffed word by word against the statutory text with the differences shown inline; caps and bold are checked. Text fields match only when identical after normalization; a near miss goes to a person; numbers are parsed and compared under each class's tolerance. 362 offline tests, one per rule and one per sample label. On real labels: the 8 deliberately wrong rows in the registry batch are all caught, the 9 correct rows come back 5 approve and 4 explained review items, and the check found two approved labels with defective warnings and one with "KENTUCKEY" | The reader, not the engine, is the error source: it can take a brewery name for the brand or misjudge bold type. Both are routed to review rather than decided. Type size cannot be checked at all |
| Judgment where TTB allows it ("STONE'S THROW" vs "Stone's Throw") | Case, punctuation, spacing, standard abbreviations and synonyms are folded; a street address matches the label's city and state; a trade or legal name matches whichever is printed; a filed class wrapped in descriptive words, and a filed brand printed as the second name, are accepted or sent to a person with the reason | The thresholds are tuned on fourteen synthetic labels and sixty real ones, not on hundreds with specialist decisions |
| Batch uploads of 200 to 300 | A CSV plus a zip, up to 300 rows, read with bounded concurrency in the background, live progress, a summary, every readable row in the specialist's queue, and a results CSV. Rows fail individually; a missing or unreadable image is reported, not dropped. Sixty real labels with 97 panels run in one batch for about a dollar of model reads | The queue is a thread pool in the web process; a restart mid-batch leaves rows pending (they are closed as failed at the next start). Cost scales with panels, not rows |
| Usable by a novice | Three numbered steps; the form fills itself from the label with a note that says to check it; a red mark on required fields; the label stays beside the form and the result, zoomable; verdicts in plain words with the rule behind each; correction notices in plain language with a thread on the exact field; an inbox that says what is new | No formal Section 508 audit, no email, and the specialist's queue assumes a desktop screen |
| Firewalls that block cloud services | Every read falls back to Tesseract on the server when the model fails, and the result says so; nothing on any page loads from a CDN; the model endpoint is one setting, with the Azure-tenant path in `docs/production.md` | Tesseract reads the brand, class and net contents on clean artwork and misses small print on photographs; its accuracy table is in "If the model is unavailable" |
| A reliable core and documented trade-offs | The seven fields and the warning work end to end for both roles, with the correction loop, in demo mode without any key. Twenty-six decision entries record what was chosen, what was considered, and what two runs on real labels changed | Production needs identity, FedRAMP-authorized hosting, object storage, a durable queue and an evaluation set; `docs/production.md` lists them in order |
| A README with setup, and a deployed URL | This file, `docs/user-guide.md` for the screens, and `scripts/deploy_azure.sh` for the URL | |

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
all fourteen sample cases.

The `beverage_type` column may be left blank in a batch CSV, and the type may be left on
"Detect from the label" in the form: the reader decides the class and the record says the
type was taken from the label.

**Trying the batch upload** needs a CSV and a zip of images. The batch page offers two
ready-made sets. The first is a sixteen-row CSV and a zip of the sample labels. Fourteen rows are the bundled
samples; two are deliberately broken, a photo that is not a label and a row whose image is
missing from the zip, so the summary shows failures next to the AI's findings. Download
them, choose them in the form, and start the batch; the page shows progress and every
readable row lands in the specialist's queue. In demo mode it finishes in a second or two. With a real API key the
same batch makes fourteen model reads, which is a fair test of the concurrency and the
per-label timing.

The second set is real: sixty approved labels from TTB's public COLA registry, twenty of
each class, many with back and neck panels (`labelverify/testdata/cola/`, provenance in
its README). It needs a live reader; demo mode cannot read it. The CSV holds two
different things, and the summary at the top of a finished batch adds them together, so
read them apart.

**Twenty demonstration rows** carry application values a person transcribed from the
label image (never values the reader produced, so the check is not comparing the reader
with itself), some then deliberately altered or blanked. These have known answers and
are the test of the engine: one batch should tell a correct filing from a wrong one, a
near miss, and a blank, which is where the time saving is. The specialist opens the rows
the batch flagged and skims the rest.

**Forty registry rows** keep the registry's own record as the application. Every one of
these labels was approved by TTB, yet most come back "corrections needed", and that is
the check working on data that does not match the label, not a reading error. The
public registry does not publish what the applicant typed on the form. It publishes:

- **Brand name**, usually as printed, but sometimes what the permit holder filed rather
  than the biggest words on the label. The Spartan Select can is registered under the
  brand "Saugatuck Brewing Co.", and TTB accepted that.
- **Class/type as a category code**, such as "Other Gin" or "Table White Wine", not the
  designation printed on the label. TTB approved "American Gin" on the label under the
  code "Other Gin"; both are correct, but they are not the same text. This is the row
  that makes most registry rows "corrections needed".
- **The permit holder's legal name and street address**, which is often not the name on
  the label. Delicato Vineyards holds the COLA for the Francis Ford Coppola Winery label.
  The engine accepts a street address against the label's city and state, and a trade
  name or legal name against whichever the label prints, so this row now mostly matches.
- **No alcohol content and no net contents.** The registry does not publish them, so
  those rows say the application left them blank and come back for review.

What the forty rows show is the reader on real artwork and real photographs, and that
the engine says precisely which value differs and why. The `scenario` column names each
row's case and `expected` says what the check should report:

| Scenario | Rows | Application values | Expected result |
| --- | --- | --- | --- |
| `filed-correctly` | 9 (3 per class) | Transcribed from the label by hand | Five approve. Two whisky rows are review items for the age statement (a whisky without one always is), one of them also for "KENTUCKEY" printed on the label; one beer is a review item for a class designation wrapped in descriptive words ("100% Malt Premium Beer"); and one approved can is corrections needed because its Government Health Warning really does depart from the statutory text ("woman", "risks", "drive a car or" missing). The check caught two defects the registry let through |
| `wrong-alcohol` | 2 | Transcribed, then the ABV changed (45% filed, 56.9% printed; 5.2% filed, 7.2% printed) | Corrections needed on alcohol content |
| `wrong-net-contents` | 2 | Transcribed, then the volume changed (1 L and 1.5 L filed, 750 mL printed) | Corrections needed on net contents |
| `wrong-class` | 2 | Transcribed, then the class changed ("Chardonnay" for a Chenin Blanc blend; "Stout" for a Belgian-style dark strong ale) | Corrections needed on class/type |
| `wrong-brand` | 1 | Another distillery's brand filed for an Nc'nean Scotch | Corrections needed on brand name |
| `near-miss-brand` | 1 | "Delto" filed for a label that reads "Delta" | Review: close but not identical, so a person decides rather than the label being charged |
| `blank-fields` | 3 (1 per class) | Brand, class, ABV and net contents left empty | Review: each blank row says what the label shows, so the applicant can fill it in |
| `registry-as-filed` | 40 | The registry's record: brand, class code, permit holder with street address; ABV and net contents are not published | Mostly corrections needed, on the class row: the registry's code ("Other Gin", "Table White Wine") is not the label's wording. The street address matches the label's city and state, a trade name or legal name matches whichever is printed, and the blank ABV and net contents are review items. The reader is right on these rows; the filed values are the registry's, so this is the check doing its job on data that does not match the label |

The values and the expected results are in `labelverify/testdata/cola/scenarios.json`;
`scripts/import_cola.py --csv-only` rebuilds the CSV from it. The expectations describe a
confident model read. Under the local OCR fallback every row is an uncertain read and
differences become review items, as described above. "Download results (.csv)" on the
finished batch lists every row with its result and each flagged field, the application
value, the label value and the reason, which is the quickest way to compare a run with
the `expected` column.

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

About one second per label, and the sample batch of sixteen in about ten seconds. Every
OCR value is reported at low confidence, so the rows that agree with the application show
"match · uncertain read", and the application goes to review rather than approval. On
such a read, a required item the reader did not find is a review item with a note to
confirm on the image, not a finding, because OCR misses text far more often than labels
omit it; a difference in what was read (a wrong percentage, a changed warning) stays a
mismatch when read confidently. Because every OCR value is low confidence, a difference
it reports is a review item asking for a look at the image or a clearer photo, not a
correction request; only the specialist, looking at the image, turns it into one. On
photographs of real bottles, with glare, curvature and stylized type, expect most rows to
come back for review on the OCR path. That is the brief's own fallback ("ask for a better
image"), with the reader's guess shown beside the image so the specialist decides in one
look. The form in step 2 is filled
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
| Harvest Moon Red | Vintage date with no appellation of origin | Request correction |
| North Shore Lager | "Extra Strength", a statement of alcoholic strength on a malt beverage | Needs review |
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

The app is one container: Python, the engine, Tesseract for the fallback, SQLite in a
directory. Any host that runs a container with a persistent directory and keeps it
awake will do. Two things matter for a demo that reviewers will open cold: no cold
start (the first request must answer in seconds, the vendor-pilot failure from the
interviews) and HTTPS (the session cookie is marked Secure on a public URL).

**Azure App Service** (recommended; the script does everything from a signed-in `az`):

```bash
az login
ANTHROPIC_API_KEY=sk-ant-... GEMINI_API_KEY=... scripts/deploy_azure.sh
```

Claude reads the labels; with the Gemini key present, Gemini words the specialist's
correction notices (the findings are the engine's either way), and without it Claude
does. The script builds the image with Docker on your machine and pushes it to Azure
Container Registry (free and trial subscriptions are not allowed Azure's own cloud
build), creates a B1 Linux plan with Always On, a web app from the image, and the
settings for a public URL, then waits for `/healthz` and prints the address. Docker on
Ubuntu: `sudo apt install docker.io`; the script uses `sudo docker` when your user is
not in the docker group. The database, the stored images and the
session secret live under `/home`, which App Service persists across restarts and
deployments. Re-running the script with the same `APP` name rebuilds and redeploys.
`az group delete --name labelverify-rg` removes everything. On a free Azure account the
B1 plan comes out of the credit; App Service's free tier would also run it, but it
sleeps after twenty minutes idle and the first request after that takes long enough to
fail the brief's five-second test.

**Docker**, anywhere:

```bash
docker build -t labelverify .
docker run -p 8000:8000 -v labelverify-data:/app/data -e ANTHROPIC_API_KEY=... -e SECRET_KEY=... labelverify
```

**Fly.io** (always-on machine, persistent volume): see `fly.toml`. Railway or Render work
the same way; use a paid or always-on tier so the demo does not hit a cold start.

Every public deployment sets the same guards, as environment variables any host can set:
the sign-in dialog shows no demo credentials (reviewers take them from this README),
paid model reads are capped per day so an open link cannot run up the API bill, and the
session cookie is Secure.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | | Enables the vision extractor and notice rewriting |
| `GEMINI_API_KEY` | | Enables the Gemini extractor and, by default, Gemini wording of correction notices |
| `LABELVERIFY_NOTICE_PROVIDER` | `gemini` if its key is set, else `claude`, else `template` | Who rewrites correction notices; the findings always come from the engine |
| `LABELVERIFY_GEMINI_MODEL` | auto | Gemini model; blank picks the newest stable Flash model from Google's model list, and a retired name falls back to the replacement Google suggests |
| `LABELVERIFY_EXTRACTOR` | `claude`, else `gemini`, else `demo`, by which key is set | `claude`, `gemini`, `tesseract`, or `demo` |
| `LABELVERIFY_MODEL` | `claude-sonnet-5-5` | Extraction model. Measured at 4.1 s on the hardest sample; `claude-haiku-4-5` is faster if its reads hold up |
| `LABELVERIFY_NOTICE_MODEL` | `claude-sonnet-5-5` | Claude model for the notice rewrite when the provider is `claude` |
| `LABELVERIFY_EXTRACT_TIMEOUT` | `20` | Seconds before a one-image read is abandoned; each extra panel adds half again (a front-and-back set gets 30 s). A failed read in the wizard offers "Read again" on the stored images |
| `LABELVERIFY_DAILY_READ_LIMIT` | unlimited | Paid model reads allowed per UTC day; after that uploads get a clear message and the sample labels still work. Set it on any public URL |
| `LABELVERIFY_DEMO_ACCOUNTS` | `true` | Show the demo account list in the sign-in dialog. Set `false` on a public URL; reviewers use the accounts in this README |
| `LABELVERIFY_REPO_URL` | this repository | GitHub link on the landing page |
| `LABELVERIFY_TESSERACT_CMD` | found on PATH | Full path of the tesseract executable when the app cannot find it on its own |
| `LABELVERIFY_FALLBACK` | `tesseract` | Reader used when the configured one fails on a read; `none` turns the fallback off |
| `LABELVERIFY_SECURE_COOKIES` | `false` | `true` marks the session cookie Secure; set it behind HTTPS (the Fly config does) |
| `LABELVERIFY_STRUCTURED_OUTPUT` | `false` | `true` asks the API to constrain the reply to the extraction schema. Off by default: the API compiles a new schema into a grammar on first use, and that compile can take longer than a read is allowed to. The default asks for JSON in the prompt and validates it here |
| `LABELVERIFY_IMAGE_MAX_EDGE` | `1200` | Long edge in pixels after preprocessing; smaller is faster and cheaper, larger keeps more small-print detail (1500 measured as no more accurate on the samples) |
| `DATABASE_URL` | `sqlite:///./data/labelverify.db` | SQLAlchemy URL; Postgres works unchanged |
| `SECRET_KEY` | generated once, kept beside the database | Signs session cookies; set it explicitly in any shared deployment |

## How it is built

```
labelverify/
  engine/                 pure verification logic, no web imports
    models.py             ApplicationData, LabelExtraction, VerificationResult
    rules.py              the rulebook: what each class must carry, citations, tolerances, standards of fill
    normalize.py          text, ABV, volume, producer name, address, country normalizers
    warning.py            health warning rules and word diff
    compare.py            per-field rules and roll-up; nothing here calls a model
    preprocess.py         EXIF rotation, 1200 px downscale, JPEG re-encode
    notices.py            correction notice template and optional model rewrite
    verify.py             orchestration with timing and fallback
    extractors/           claude and gemini (vision), tesseract (local OCR), demo (samples), fixture (tests), budget (daily cap)
  web/
    app.py                FastAPI factory, session middleware, error handlers
    auth.py, db.py        sign-in and cookies; SQLite engine and session factory
    models.py             SQLAlchemy tables: users, applications, images, runs, comments, events, notices, views, batches
    services.py           every state change: create, verify, submit, review, decide, resubmit, batch
    render.py, seed.py    template helpers and labels; the demo accounts and sample applications
    routes/               shared (auth, images, comments), applicant, specialist, api
    templates/            Jinja2 pages and partials
    static/               one stylesheet, one script, two self-hosted typefaces, no external assets
  samples/                bundled labels and manifest
  testdata/cola/          sixty registry labels, the scenario file, the batch CSV
  cli.py                  command-line verify, extract and bench for latency checks
scripts/make_samples.py   renders the sample labels
scripts/import_cola.py    builds the registry set from downloads; --csv-only applies scenarios.json
scripts/deploy_azure.sh   App Service deployment
docs/                     decisions.md, production.md, user-guide.md, screenshots
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
  and the label call for them, the sulfite declaration, appellation, vintage and estate
  bottling (wine), the age statement, bottled-in-bond proof and blend percentage
  (spirits), and statements of strength (malt). Rules the engine cannot judge from a read
  (state of distillation, varietal and grape-source percentages, type sizes) are listed
  on the rules page as the specialist's checks.
- Bold detection on the warning heading is a visual judgment and is surfaced for review
  rather than failed automatically.
- Sample labels are synthetic renders, not real COLA images.
