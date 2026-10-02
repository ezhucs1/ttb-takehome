# Testing, findings and fixes

What was tested, what the tests found, what the real labels found, the bugs that came
out of it and why each fix was made the way it was, and the latency and cost
discoveries along the way.

## The test suite

378 tests, all offline, in about two minutes. The model is replaced by a fake client that
returns recorded extractions; the database is a throwaway SQLite file; the web layer is
driven through FastAPI's test client.

```bash
.venv/bin/python -m pytest                           # everything
.venv/bin/python -m pytest tests/test_communication.py -v   # the two-party scenarios as a checklist
.venv/bin/ruff check .                               # lint
```

| File | Tests | What it covers |
| --- | --- | --- |
| `test_compare.py` | 94 | Every comparison rule: brand, class/type (wrapped designations, malt designations), type of product and class resolution, alcohol content per class (proof, tolerances, the tax-class line, wording), net contents per class, producer and address (trade and legal names, street against city and state), country of origin, sulfites, the filed statements, the confidence gate and uncertain reads, the roll-up |
| `test_warning.py` | 16 | The Government Health Warning: exact text, missing words, extra words, title-case heading, bold yes/no/unknown, hyphenation across lines, the word diff |
| `test_normalize.py` | 19 | Text, alcohol, volume, address and country normalizers, including the review findings ("1,000 mL", "100% Agave 40%", "West Virginia") |
| `test_rules.py` | 5 | The rulebook's shape: every class has every field, citations present, the checklist order |
| `test_extractors.py` | 28 | The Claude extractor against a fake client (prompt, panels, JSON parsing from fenced or wrapped replies, errors, timeouts), Tesseract field assignment, the registry and fallback selection, the daily budget |
| `test_gemini.py` | 20 | The Gemini extractor: schema conversion, model discovery and retirement, retries on 503 and rate limits, parsing |
| `test_preprocess.py` | 6 | EXIF rotation, downscale, transparency flattening, re-encoding |
| `test_notices.py` | 8 | The notice template, provider selection, the rewrite and its fallback |
| `test_verify.py` | 7 | End-to-end orchestration with timing and the fallback path; one test per sample label asserting the promised recommendation |
| `test_services.py` | 33 | The workflow against a database: serials, drafts and pre-checks, the status machine and its refusals, unread activity and read state, label sets and cached reads, the queue, batches and their inbox lines |
| `test_web.py` | 70 | Every HTTP route for both roles: sign-in and redirects, the applicant wizard (front and back panels, type from the label), pre-check, submit, resubmit, comments, the specialist queue, review, decisions, notices, bulk approve, batches (sample set end to end, registry files parse, scenarios applied), the API, the local fallback in the app, the filed statements on the web, and the security basics (origin check, headers, throttle, API key, role isolation) |
| `test_communication.py` | 17 | Scenarios between an applicant and a specialist over HTTP on a clean database: who sends, who receives, what shows where, when it counts as read, what the other side must never see, and that nothing is lost across a restart |
| `test_cli.py` | 3 | The command-line verify, extract and bench |

Beyond the suite, every change was walked in a real browser with Playwright scripts
(sign-in, the wizard with the image viewer, the prefill note, the statements per class,
comment and notice forms, the batch bundle on both sides, the inbox counts) and an HTTP
sweep that hits every route for both roles and drives a full case through draft, read,
pre-check, submit, comment, re-check, notice, correction request, reply, resubmit and
approval, plus a batch through upload, polling, bulk approve and the results CSV, while
the server log is checked for tracebacks. The SQLite concurrency test drives five slow
readers at once and checks from a separate connection, during each read, that a write
is never blocked.

## The registry batch: sixty real labels

The bundled samples are synthetic and exist to show each outcome. For real artwork,
sixty approved labels from TTB's public COLA registry (twenty per class, 97 panels) ship
with the app as a second batch. The CSV holds two different things, and the summary at
the top of a finished batch adds them together, so read them apart.

**Twenty demonstration rows** carry application values a person transcribed from the
label image (never values the reader produced), some then deliberately altered or
blanked. These have known answers and are the test of the engine: one batch should tell
a correct filing from a wrong one, a near miss, and a blank.

| Scenario | Rows | Application values | Expected result |
| --- | --- | --- | --- |
| `filed-correctly` | 9 (3 per class) | Transcribed by hand | Five approve. Two whisky rows are review items for the age statement (a whisky without one always is), one also for "KENTUCKEY" printed on the label; one beer is a review item for a class wrapped in descriptive words ("100% Malt Premium Beer"); one approved can is corrections needed because its warning really does depart from the statute ("woman", "risks", "drive a car or" missing). The check caught two defects the registry let through |
| `wrong-alcohol` | 2 | ABV changed (45% filed, 56.9% printed; 5.2% filed, 7.2% printed) | Corrections needed on alcohol content |
| `wrong-net-contents` | 2 | Volume changed (1 L and 1.5 L filed, 750 mL printed) | Corrections needed on net contents |
| `wrong-class` | 2 | Class changed ("Chardonnay" for a Chenin Blanc blend; "Stout" for a Belgian-style dark strong ale) | Corrections needed on class/type |
| `wrong-brand` | 1 | Another distillery's brand filed for an Nc'nean Scotch | Corrections needed on brand name |
| `near-miss-brand` | 1 | "Delto" filed for a label that reads "Delta" | Review: close but not identical, so a person decides |
| `blank-fields` | 3 (1 per class) | Brand, class, ABV and net contents left empty | Review: each blank row says what the label shows |

**Forty registry rows** (`registry-as-filed`) keep the registry's own record as the
application. Every one of these labels was approved by TTB, yet most come back
"corrections needed", and that is the check working on data that does not match the
label: the registry publishes a class code ("Other Gin", "Table White Wine") rather than
the printed designation, sometimes the permit holder's filed brand rather than the
biggest words on the label, the permit holder's legal name and street address, and no
alcohol content or net contents. So the class row mismatches, the blank rows ask for the
value, and the producer and address rows mostly match after the engine learned to accept
a street address against a city and state and a trade or legal name against whichever
is printed. These rows show the reader on real artwork and photographs.

The values and expectations live in `labelverify/testdata/cola/scenarios.json`;
`scripts/import_cola.py --csv-only` rebuilds the CSV from it. The expectations describe
a confident model read; under local OCR every row is an uncertain read and differences
become review items. "Download results (.csv)" on a finished batch lists every row with
its result, each flagged field, the application value, the label value and the reason,
which is the quickest way to compare a run with the `expected` column.

### What the real runs found, and what changed

The first full run came back 51 corrections needed, 7 review, 2 approve, with the reader
right almost everywhere; the results file showed where the engine, not the reader, was
charging labels (decisions.md, entry 26). Each line below is one row of that file.

| Finding | Fix and reasoning |
| --- | --- |
| "ALC.13.0% BY VOL." parsed as no percentage | The guard that stops "1.5 L" yielding 5% also rejected a number glued to "ALC."; it now rejects only a digit or digit-and-point before the number |
| "ONE PINT" and "5.16 U.S. Gallons" (a keg collar) were not volumes | Number words and "U.S." before the unit are |
| "BE- CAUSE", a warning hyphenated across lines, counted as wrong words | A hyphen at a line break joins the halves; a joined word that is a statutory word is accepted, "ABILILTY" next to it is still a mismatch |
| Fifty-three address rows mismatched: the registry gives the street, the label prints city and state | When every word the label prints is in the application's address, the row matches and says why; the rule asks the label only for city and state |
| The registry lists a trade and a legal name; the label prints one | The producer row tries each name, ignores entity suffixes, and reports which matched |
| The reader took a brewery name for the brand when the label also prints a product name | The reader returns a second name (`fanciful_name`); a filed brand that equals it is a match with a note, a near miss is a review item, and the producer's name alone never makes a brand |
| Two approved labels have defective warnings | Flagged, correctly; that is the point |

A second run moved the batch to 40 corrections, 18 review, 2 approve, and showed what
was left:

| Finding | Fix and reasoning |
| --- | --- |
| The reader judged five bold headings "not bold"; on the image at least three are bold | Type weight is not something a model reads reliably from a JPEG. The brief says the heading "has to be in all caps and bold", so "not bold" still sends the row to a person, and only "could not tell" is a note: a few false review items cost less than a non-bold heading passing |
| "Bottled by SVP Winery, Shandon, CA for McKelvey Vineyards, New Haven, MO" returned both parties | The prompt says the bottler is the producer; the engine compares against the part before "for"; an address the label prints more of than the application matches when the application's city and state are in it |
| A filed brand equal to the label's second name was a review item | The brand is the name the applicant designates as long as the label carries it: a match with a note |
| Rittenhouse's approved label prints "KENTUCKEY" | A 98% similarity review item; the check working |

With the reader's values from that run replayed through the engine, the nine
hand-transcribed rows come out five approve, three review and one corrections needed,
which is what the labels deserve. What did not change: the registry's class codes still
mismatch the label's wording, blank fields still ask for the value, and a whisky without
an age statement still goes to review. Those are the rules, applied to data that does not
match the label.

## Bugs found before and after deployment

From the pre-deployment review of the engine, the web backend and the front end, each
confirmed by reading the path or running it (decisions.md, entry 18), and from reviewers
using the deployed site (entry 28):

| Bug | Fix |
| --- | --- |
| "100% Agave 40% Alc./Vol." parsed as 0%; "1,000 mL" as one millilitre; "West Virginia" normalized to "w va"; "Cabernet" and "Cabernet Sauvignon" never met; transparent PNG artwork flattened to black on black | Each normalizer fixed with a spot check |
| Submitting after editing a pre-checked field kept the stale verdict | The submission re-runs the comparison on the cached read when values changed |
| A failed re-check left the previous recommendation, so a timeout could leave a case in "Ready" | A failed read is its own recommendation and lands under "Needs a look" |
| A specialist could open and comment on an applicant's unsubmitted draft | Drafts are hidden from that role |
| A batch left "processing" by a restart stayed open forever | Closed at startup; a worker that failed after creating the draft keeps the row linked |
| Zip members were inflated before their size was checked | Size-checked first |
| The review page loaded every image version's bytes twelve times over | Image bytes are a deferred column, loaded only when read; batch rows load their runs in three queries, not one per row per poll |
| Seeded submissions showed as NEW in the specialist's inbox | Events are timestamped at flush, so the seed flushes before marking them seen |
| The specialist's queue sorted clean cases first, which read as random | Newest first; the Ready tab is where the clean ones are |
| Submissions never reached the specialist's inbox; sixty batch rows would have been sixty inbox lines and sixty queue rows | Submissions are inbox items; a batch is one bundle on both sides, with its line built from grouped counts so a 300-row batch cannot crowd the single applications out of a feed limited to forty items per kind |
| Reads looked slower on the hosted site than locally | The server's read time was the same; the upload of a multi-megabyte phone photo was being counted. Photos are shrunk in the browser before upload, and the line shows the read time with the upload apart |
| The applicant could not correct a statement the reader got wrong, since statements were read-only | Every statement the label carries is an editable, prefilled field the applicant files as printed |

## Difficulties, latency and cost discoveries

- **Schema-constrained output timed out.** Passing the extraction schema as the API's
  output format compiles a grammar on first use; after the rulebook added seven fields,
  every read timed out at 20 s, including a sample that had read in four seconds the day
  before. The reader now asks for JSON in the prompt and validates it client-side; the
  constrained mode is opt-in (entry 14).
- **SQLite's single writer under concurrent reads.** Batch workers that called the model
  inside their write transaction locked each other out ("database is locked") and a
  batch could never close. The worker runs in two short transactions with the model
  call between them, and a finalizer closes the batch whatever happens (entry 7).
- **Tesseract's threads.** Five concurrent OCR reads each started a thread per core;
  twenty threads on four cores stalled the batch for minutes. One OCR thread per call
  brought the sixteen-row batch to about ten seconds. Upscaling before OCR was measured
  and rejected: six times slower for three fields gained by one sample each (entry 22).
- **Latency is the image and the output, not the model.** The three Claude tiers read the
  hardest sample within two seconds of each other; 1200 px against 1500 px saved a tenth
  of a second. Four seconds is the floor; the design meets the budget by reading once at
  upload and reusing the read (entries 1a, 28).
- **Cost.** About $0.02 per single-panel label, up to $0.06 with several panels, about
  a dollar for the sixty-label registry batch. Each engine change was evaluated by
  replaying the reader's stored values through the engine rather than paying for a new
  run; two full runs were enough to finish the rulebook. The demo asks testers to keep
  batch runs to two or three for that reason.
- **Hosting.** A free Azure account starts with zero quota for Basic and Free App
  Service instances, cloud builds are not permitted on free subscriptions, and a web
  app created from an image name without the registry host was given the wrong image.
  The deploy script builds locally, pushes to the registry, states the image explicitly,
  and documents the quota request; the demo runs in the one region that granted B1.

## How to evaluate it yourself

1. Locally, in demo mode: the fourteen samples each produce the recommendation in the
   README's table; the sample batch shows every outcome in a second or two.
2. Locally with Tesseract: upload your own artwork; expect clean artwork to read and
   photos to come back for review.
3. On the live demo: upload a photo of a real bottle for a model read; run the registry
   batch once and compare the results CSV with the `expected` column. Two or three batch
   runs at most, please: each costs about a dollar.
