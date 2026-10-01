# Engineering decisions

Each entry records what was chosen, what was considered, and why. Later entries may
revise earlier ones; the earlier entry stays so the reasoning trail is visible.

## 1. Two roles, one workflow, instead of a single verification screen

**Chose:** an applicant portal and a specialist workbench sharing one application record,
with a status pipeline (draft, submitted, under review, correction requested,
resubmitted, approved, rejected) and comment threads anchored to individual label fields.

**Considered:** a single "upload and compare" screen for specialists only, which is the
minimum the brief asks for.

**Why:** the interviews describe two costs: specialists drowning in routine matching, and
applicants waiting days to learn a label was rejected for a title-case heading. Running
the same check at submission time removes most of the routine work before it reaches a
specialist, and anchoring the conversation to a field keeps the correction loop short.
The single-screen verifier still exists as `POST /api/verify` and as the pre-check step.

## 1a. Default model chosen by measurement: Claude Sonnet 5.5

**Chose:** ``claude-sonnet-5-5`` at low effort as the default extraction model.

**Measured** (angled, glary photo sample, 1114x1500 after preprocessing, from a home
connection, median of three runs):

| Provider and model | Read time | Read correct? | Confidence reporting |
| --- | --- | --- | --- |
| Claude Opus 5.5, low effort | 6.0 s | yes, all fields | calibrated; named angle, lighting, blur |
| Claude Sonnet 5.5, low effort | 4.1 s | yes, all fields | calibrated |
| Claude Haiku 4.5 | 3.9 s (one run 19.5 s) | yes, all fields | calibrated, but named no image issues |
| Gemini 3.8 Flash (free tier) | 13.9 s | yes, all fields | 1.0 on every field, no issues named |

**Why:** the brief's budget is five seconds and the vendor pilot failed at thirty. Sonnet
meets the budget with identical extraction on the hardest sample. Opus stays available by
setting ``LABELVERIFY_MODEL`` for cases where accuracy on unusual labels matters more than
speed. Haiku 4.5 measured only marginally faster with a long-tail outlier and less
image-quality reporting, so it is not worth the loss of signal as the default; it remains
a candidate for high-volume batch runs. The near-identical times across the three Claude
tiers suggest the fixed cost is the image payload and output length rather than the
model, which is why the preprocessing size is configurable
(``LABELVERIFY_IMAGE_MAX_EDGE``) for further measurement.

Measured: Opus 5.5 at 1200 px read the same sample correctly in 5.2 s versus 6.0 s at
1500 px, but its confidence in the health warning fell from 0.85 to 0.75 and it flagged
the small print as blurred. The small print is exactly where rejections happen, so 1500 px
stays the default and 1200 px is an opt-in for speed on clean artwork. Sonnet 5.5 at
1200 px measured 3.97 s against 4.08 s at 1500 px, confirming that image size is a minor
factor: the remaining time is output generation (the JSON includes the full warning
transcription, which the comparison needs) plus the round trip. About four seconds is
the floor for this design, and it meets the brief.

Gemini's free tier also produced 503 "high demand" errors and a timeout during the same
session, and it reports full confidence on every field, which would disable the workflow's
low-confidence review gate. It stays as the zero-cost evaluation path, not the default.

Two-panel sets (front and back read in one call) measured 5.5 s on Sonnet, against about
4.1 s for one panel. Before this entry the applicant flow paid that twice: once to fill
the form at upload, and again for the pre-check. The read is now stored with the
application and reused by the pre-check and the submission, so the applicant waits for one
read, the comparison takes milliseconds, and the model bill per application halves. A
specialist's "Re-check label" always reads afresh; a new upload always reads afresh.

## 2. One vision-model call, then deterministic comparison

**Chose:** a single Claude request that returns every required field as structured JSON
(the `LabelExtraction` schema), followed by rule-based comparison in plain Python.

**Considered:** (a) OCR (Tesseract or a cloud OCR API) plus a text model to classify
fields; (b) asking the model directly "does this label match the application?"

**Why:** (a) adds a second vendor and a second failure mode, and OCR degrades sharply on
angled or glary photos, which Jenny asked for specifically. (b) is non-deterministic and
untestable; a regulator needs to know exactly why a field failed. Keeping comparison in
code means the rules (word-for-word warning text, proof equals twice ABV, unit
conversion) are unit tested and explainable, and the model is only trusted to read.

## 3. "Match" means identical after normalization, nothing looser

**Chose:** text fields match only when they are identical after case, punctuation,
whitespace, and synonym folding. Anything else is "needs review" (above a similarity
threshold) or "mismatch" (below it).

**Considered:** treating high fuzzy similarity (say 92 percent) as a match.

**Why:** the first draft did that, and a unit test showed it approving "DISTILERY" as a
match for "DISTILLERY". A missing letter in a brand name is a real defect. Dave's
"STONE'S THROW" versus "Stone's Throw" still matches because normalization makes them
identical; a typo goes to a human.

## 4. Extractors behind one interface, with a local fallback and a demo mode

**Chose:** `Extractor` protocol with three implementations: Claude vision (primary),
Tesseract plus regex heuristics (offline fallback), and a demo extractor that returns
ground truth for the bundled samples.

**Why:** Marcus's firewall comment. The prototype runs on our hosting so the model API is
reachable, but a production deployment inside TTB's network might not be. The fallback
proves the workflow does not depend on one endpoint. Demo mode exists so reviewers can
exercise every screen without an API key; it refuses unknown images rather than
inventing results.

## 4a. A second vision provider (Gemini) behind the same interface

**Chose:** a Gemini extractor that speaks the same ``LabelExtraction`` schema, using the
REST API through the standard library, selected by ``LABELVERIFY_EXTRACTOR=gemini`` or
automatically when only a Gemini key is configured.

**Why:** two reasons. The free tier makes it possible to evaluate the tool at zero cost,
and having two providers proves the extractor boundary is real: the comparison engine,
workflow, and UI did not change. The same system prompt is shared, so accuracy
differences come from the model, not the instructions. Free-tier requests may be used by
the provider for training, so it is for synthetic and public labels, not real
submissions.

## 5. Server-rendered HTML with a small script, no front-end framework

**Chose:** FastAPI plus Jinja2 templates, one stylesheet, and about 250 lines of plain
JavaScript for async form submission, polling, the upload wizard, and the image viewer.

**Considered:** Next.js or a React front end with a JSON API; HTMX.

**Why:** the reviewers' network blocks many outbound domains, so the page must not load
scripts or fonts from CDNs, and a build pipeline is extra surface for a prototype. HTMX
was the first choice for the interactive bits but its CDN was unreachable from the build
environment, and the needed behaviour fit comfortably in a small self-hosted script.

## 6. SQLite with images stored in the database

**Chose:** SQLAlchemy 2 with SQLite (WAL mode) by default; label images are stored as
prepared JPEG bytes in a table. `DATABASE_URL` switches to Postgres without code changes.

**Why:** one file on one volume is the simplest thing to deploy and back up for a
prototype, and images are downscaled to about 1500 px before storage, so a 300-label
batch is tens of megabytes, not gigabytes. Production would move images to object
storage; see `production.md`.

## 7. Batch processing in a thread pool, not a job queue

**Chose:** `BackgroundTasks` plus a `ThreadPoolExecutor` with five workers, each row in
its own database session, with atomic counters for progress.

**Considered:** Celery or RQ with Redis.

**Why:** a queue adds a service to deploy and monitor. Five concurrent model calls keep a
300-label batch inside the peak-season expectations from the interviews while staying
under typical API rate limits. The first version lost counter updates across threads;
switching to `UPDATE ... SET completed = completed + 1` fixed it.

A second fault only appeared with a real model: each worker inserted the application and
its images, then called the model while still inside that write transaction. SQLite has
one writer, a read takes seconds, and the other workers timed out with "database is
locked"; when that error hit the handler's own commit, the batch never closed and rows
sat at "pending". The worker now runs three steps in two short transactions, with the
model call in between holding no lock, and a finalizer closes the batch whatever happens.
A test drives five slow readers at once and checks, from a separate connection during
each read, that a write is never blocked.

## 8. Notices are drafted from findings, then edited by a human

**Chose:** a deterministic template that lists each flagged field, what the label shows,
what the application says, and what to do; when an API key is present the model rewrites
it in plainer language. The specialist edits either version before sending.

**Why:** Dave's point that review needs judgment. The engine never sends anything on its
own. The template guarantees a usable notice offline; the rewrite improves tone without
changing facts because the prompt forbids adding or removing findings.

## 8a. Notices can be worded by a different provider than the one that reads labels

**Chose:** ``LABELVERIFY_NOTICE_PROVIDER`` selects who rewrites the correction notice,
defaulting to Gemini when its key is present, while ``LABELVERIFY_EXTRACTOR`` keeps
label reading on Claude.

**Why:** the two jobs have different constraints. Reading a label is on the five-second
path and needs calibrated confidence, so it stays on the measured Claude configuration.
Drafting a notice happens when a specialist clicks a button, a few seconds is acceptable,
and the content is fixed by the engine's findings before any model sees it. That makes it
safe to hand the wording to a free tier: if the call fails or is rate limited, the
specialist gets the template, which already lists every finding and fix.

## 9. Unread activity is derived from a "last opened" timestamp, not per-item flags

**Chose:** each thing the other party does (a comment, a notice, a decision, a
resubmission) is an inbox item with its own read state. An item is read when the user
clicks it in the inbox, when they open its application directly from a list, or when they
mark all as read. Reaching the application through an inbox link consumes only the clicked
item, so three replies are three clicks or one "mark all". It surfaces as a funnel with
one entry point: an Inbox item in the sidebar whose count refreshes every thirty seconds,
the inbox page with a link that lands on the exact field, and "New" markers on the
application page. Two earlier versions were replaced: dots on queue rows duplicated the
inbox and only refreshed with the page, and tracking read state per application made one
click clear every item on that application.

**Considered:** marking items read as they scroll into view; a notifications table
populated on every action; email; asking a model to decide what is worth notifying.

**Why:** the interviews describe the pain as not knowing something is waiting, not as
losing track inside a page. Read state is derived from the records themselves (a
per-item receipt or the moment the application was opened) so it never drifts out of sync
with the comments and notices it describes, and scroll-based reading is too easy to trigger
by accident. A submission is not
counted for specialists, because it is queue work rather than a message and the queue
already shows it. Email belongs in production and is listed there. A model has no place
in the mechanism: "what happened after you last looked" is bookkeeping that must be exact
and instant, and a model call would add latency and a chance of being wrong. Where a
model could help later is summarizing a long thread in the inbox, not deciding what is
in it.

## 10. Visual design: dark, hairline, one accent, motion that is brief

**Chose:** a near-monochrome dark interface as the default (light remains a toggle), one
blue accent, hairline borders with small corner radii, a variable grotesk (Inter) with
tight headings and spaced uppercase labels, a monospace (JetBrains Mono) for serials,
readings, and timestamps, and entrance and hover motion of under half a second that is
disabled under the reduced-motion preference. Both typefaces are served from the app under
the Open Font License, and the login hero is one of the bundled label photographs rather
than stock imagery.

**Considered:** a component framework; a CSS framework from a CDN; keeping the USWDS-style
light interface as the default.

**Why:** the reference points were Anduril and SpaceX: calm, dense, technical, with
nothing decorative. That look is mostly typography, spacing, and restraint, which costs
nothing to deploy. A CDN would break on the reviewers' network, and a framework adds a
build step to a project whose whole front end is one stylesheet and one script. Dark as
the default suits long review sessions and lets label photographs read as the only
saturated thing on the page. The restyle changed no markup or class names, so every
server and browser check from before it still applies.

## 11. A public URL gets a daily read budget, not a login wall

**Chose:** two environment switches for deployment. ``LABELVERIFY_DEMO_ACCOUNTS=false``
removes the credential list from the sign-in dialog, and ``LABELVERIFY_DAILY_READ_LIMIT``
caps paid model reads per UTC day, after which uploads return a clear message while the
bundled samples keep working. The landing page also says it is a prototype and not an
official TTB system; an earlier backdrop carried the agency's seal, which federal rules
restrict, and was replaced by a night photograph of the Lincoln Memorial.

**Considered:** a captcha; per-account quotas; keeping the account list and trusting the
URL to stay private.

**Why:** the brief asks for a deployed URL that reviewers can use, and the credentials
are in the README by design, so hiding them from the page only keeps them off search
engines and casual visitors. The thing that actually bounds the cost is the cap: a
process-local counter is enough for one always-on machine and adds no datastore.
Per-account quotas would be the production answer and are noted in production.md.

## 12. One rulebook per commodity class, consulted by every comparison

**Chose:** a rulebook module with one entry per class (27 CFR part 5 for distilled
spirits, part 4 for wine, part 7 for malt beverages) stating, per field, whether it is
required, optional, or conditional, the section it rests on, the alcohol tolerance, whether
proof is permitted, whether net contents must be metric, and the standards of fill. The
reader reports the class it sees; the engine confirms it from the class/type words,
falls back to the reader's judgment when the words do not settle it, and applies that
class's rules. The type on the application and in the batch CSV became optional: when it
is absent the label decides, and the record says so. The same table renders the
applicant's step-2 checklist, the citation under every result row, and the `/rules` page.

**Considered:** keeping one set of rules for all three classes with a few special cases
in the comparators; encoding the rules in the model prompt and letting it judge.

**Why:** the classes genuinely differ on the fields the brief asks about. A malt beverage
need not state alcohol content, a wine between 7 and 14 percent may say "Table Wine"
instead of a number, malt beverages may use fluid ounces while the other two must be
metric, and wine carries a sulfite declaration the others do not. Scattering those as
special cases had already produced one wrong verdict (wine treated as optional for
alcohol content). A table the UI and the engine share cannot drift. The model is still
only trusted to read: it reports the class it sees as one more field with a confidence,
and the deterministic rules decide what that class requires.

**Validation:** Excisely's curated regulation list was used as a cross-check for the
section numbers and the per-class differences; the live eCFR could not be reached from
the build environment, so the citations are by section number and the rules page says to
read the full text for anything consequential. Tolerances: ±0.15 (5.65), ±1.5 and ±1.0
(4.36), ±0.3 (7.65). A difference inside a tolerance is a review item, not a match,
because the tolerance governs actual versus labeled content, while the application
should carry the labeled figure exactly.

## 13. Bold detection is a reviewed judgment, not a hard fail

**Chose:** the model reports whether the warning heading looks bolder than the body; a
"no" or "unsure" produces "needs review", never "mismatch".

**Why:** weight is a visual judgment that varies with print quality and photo exposure.
Capitalization and wording are unambiguous and are hard failures.
