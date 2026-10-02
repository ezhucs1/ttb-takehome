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

**Part 5 walked section by section.** After the first version, part 5 was reviewed
section by section against Excisely's summary and the eCFR table of contents. Standards
of fill are cited at 5.203 (Excisely says 5.71). Four checks were added as a result, three
of them review items: the qualifying phrase before the producer's name, with the importer
named on imports (5.66, and 4.35 and 7.66 for the other classes); an age statement on any
whisky label, since whisky under four years must state its age (5.141); the percentage
statement on a blend (5.143); and one hard finding, a "Bottled in Bond" claim at anything
other than 100 proof (5.63). Rows for the conditional checks appear only when the label
gives the engine something to check. State of distillation (5.142) and the liqueur
tolerance in 5.65 were left as listed specialist checks rather than guessed at. Two sample
labels were added to show the age statement and bottled-in-bond checks.

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

## 14. The reader asks for JSON in the prompt; the API's schema grammar is opt-in

The Claude extractor originally passed the `LabelExtraction` schema as `output_format`, so
the API constrained the reply to it. That mode compiles each new schema into a grammar on
its first use, and the compile re-runs whenever the schema changes. After the class-aware
rulebook added seven fields to the schema, every read timed out at the 20-second limit,
including the bundled sample that had read in four seconds the day before, with the
client's two attempts each cut off before the server answered.

A read has a five-second budget, so no step of it may have a latency the application does
not control. The reader now spells out the reply shape in the system prompt and validates
the JSON client-side with the same pydantic model; a reply wrapped in code fences or a
sentence is still parsed, and a reply that is not label JSON is an `ExtractionError` like
any other failed read. `LABELVERIFY_STRUCTURED_OUTPUT=true` restores the constrained call
for comparison. The CLI prints the token counts and request id of each read so a slow call
can be reported with its id.

## 15. Part 4 walked section by section: appellation, vintage, estate bottling, tax class

The same walk as part 5, against Excisely's summary cards for 27 CFR 4.25 through 4.37
(the live eCFR was again unreachable from the build environment). Sections 4.32 through
4.37 were already covered: the required items, brand name, class or type with a grape
variety allowed as the type, name and address with a qualifying phrase, alcohol content
with the table-wine exemption and the two tolerances, and metric net contents with
standards of fill. Three statements were not, and each is checkable from the label alone:

- **Vintage year (4.27).** A vintage date may be used only with an appellation of origin.
  A label that states a vintage and names no appellation is a hard finding; a vintage
  that has not happened yet is too. The harvest percentages are a records check and are
  noted, not judged.
- **Appellation of origin (4.25).** When the label names one, the row shows it with its
  citation. The grape-source percentages cannot be seen on a label, so the row informs
  rather than judges.
- **Estate Bottled (4.26; Excisely lists it under 4.35).** The claim requires a
  viticultural area appellation on the label, so a claim with no appellation is a hard
  finding. Whether the named area is a viticultural area, and whether the winery grew,
  made, and bottled the wine within it, are records checks and are noted.

One sharpening to an existing check: the ±1.5 and ±1.0 tolerances in 4.36 are not allowed
to bridge a tax class line (14, 21, and 24 percent), so a filed 13.8 against a labeled
14.2 is a mismatch with that reason rather than a within-tolerance review item. That
clause is from the section as remembered, not re-read; it only ever sharpens a row that
was already going to review, and the reason names the line so a specialist can disagree.

Nothing was added on the application side. The COLA form carries appellation, vintage,
and varietal "if on label", and comparing them would be the next step, but it means new
columns, form fields, and CSV columns, which is more change than the deliverable needs
at this point. Rows for the three checks appear only when the label gives the engine
something to check, so spirits and malt beverage results are unchanged, and the wine
samples that passed before still pass: their kickers are appellations. One sample was
added, a red wine with a 2022 vintage and no appellation, so the batch demo shows a
wine-specific finding. The reader's prompt tells the model that the bottler's city and
state are not an appellation.

## 16. The form follows the class; the checklist box is gone

A manual test showed the problem: step 2 listed "what this label must carry" as a
checklist, including items such as the qualifying phrase and the sulfite declaration that
have no field, while the read had in fact captured them. A checklist beside a form asks
the applicant to reconcile two lists. Now there is one: the form itself follows the class
the reader detected (the select says "detected from the label" and can be changed, which
re-applies the rules), every field the class requires carries a red mark, and an "also
read from the label" list under the fields shows what the read found for the items the
applicant never types, with "not found on the label" for a required one. The full
rulebook stays one click away as "Reference". The resubmit form carries the same marks.
The form's name and address labels use the rulebook's wording.

## 17. Part 7 walked section by section: designation, strength, wording, the glass

The same walk for malt beverages, against Excisely's cards for 27 CFR 7.61 through 7.70
(eCFR still unreachable from the build environment). The required items in 7.61, the
optional alcohol content with its ±0.3 tolerance (7.65), the qualifying phrase (7.66),
and fluid-ounce-or-metric net contents with no standards of fill (7.70) were already in
the rulebook. Four things were not:

- **Class designation (7.64).** A malt beverage must use a recognized designation. A
  class/type with none of beer, ale, lager, stout, porter, malt liquor and the other
  terms the category detector already knows goes to review with that note, even when it
  matches the application word for word. Wine and spirits designations are not policed
  this way; a grape variety or a standard of identity is too open a list.
- **Statements of strength (7.65).** Wording that emphasizes alcoholic strength is not
  permitted on a malt beverage. The reader reports such wording as its own field, and the
  engine also scans the brand, class, and alcohol statement. The row is a review item,
  not a finding, because "strong" inside a recognized style name (a Belgian strong ale)
  is a judgment the specialist makes. One sample was added: a lager sold as "Extra
  Strength".
- **The alcohol statement's wording.** All three parts prescribe the form "Alc. __% by
  Vol." or an abbreviation. A bare percentage with none of the words alcohol, alc, or
  abv now carries a note and goes to review. This applies to every class.
- **Net contents on the container (7.70).** A malt beverage's statement may be blown
  into the glass rather than printed, so a label with no net contents is a review item
  for a malt beverage and a finding for the other two classes.

Brand-name restrictions (misleading identity or origin, simulating a government stamp)
and the statement of composition on a flavored malt beverage are listed as notes for the
specialist; neither can be settled from a transcription.

## 18. Pre-deployment review: what three reviewers found, and what changed

Before deploying, the engine, the web backend, and the frontend were each reviewed
against the running code, with every finding confirmed by reading the path or running it.
The ones that changed behaviour:

- **Reads.** "100% Agave 40% Alc./Vol." parsed as 0% (the regex matched "00%"); "1,000 mL"
  parsed as one millilitre; "Charleston, West Virginia" normalized to "w va" because
  "virginia" was substituted first; "Cabernet" and "Cabernet Sauvignon" never met because
  the synonym was word-wise; transparent PNG artwork flattened to black on black; the
  Tesseract blend-percentage pattern could not match "51% Straight Bourbon Whiskey"; a
  proof inconsistent with its own percentage was a finding only when the percentages
  agreed closely. All fixed, each with a spot check.
- **Verdicts that could be stale.** Submitting after editing a field that had been
  pre-checked kept the pre-check's verdict; the submission now re-runs the comparison on
  the cached read when the values changed. A failed read left the previous recommendation
  in place, so a re-check that timed out could leave an application in "Ready"; a failed
  read is now its own recommendation and lands under "Needs a look".
- **Edges.** An invalid type value from the form was a 500, now a 422. The router's own
  404 and 405 now render the HTML error page. A specialist could open and comment on an
  applicant's unsubmitted draft; drafts are now hidden from that role. A batch left
  "processing" by a restart is closed at startup. A worker that failed after creating
  the draft lost the link between the row and the draft. Zip members are size-checked
  before they are inflated. A signed-out page that posts a comment now gets a short
  "sign in again" partial instead of the landing page injected into the thread.
- **Cost.** Label image bytes are loaded only when read, not with every application
  (a review page with twelve thumbnails loaded every version's bytes twelve times over);
  batch rows load their runs in three queries instead of one per row per poll; the
  worker decodes and resizes images before it takes the SQLite write lock; the class is
  resolved once per comparison instead of once per comparator; the demo reader's sample
  hash table and the Gemini schema are built once per process; static assets with a
  content hash are cached for a year, label images for a year by id; the unread badge
  does not poll in a hidden tab and stops after a sign-out; the queue's four stat cards
  are one grouped query.
- **Housekeeping.** Dead CSS, an unused JSON filter, an unused medium-weight font, the
  "kept for older imports" alias, and a few unused helpers were removed. The nav is one
  macro with `aria-current`; icons are hidden from screen readers; locked wizard steps are
  `inert`, not just unclickable.

Left as documented limits rather than changed now: the unread count is computed in
Python over the user's items (fine at this scale, a single grouped query at larger ones),
and opening a review page claims the application on a GET.

## 19. The local OCR fallback is automatic in the app, and it says so

The engine and the CLI had a Tesseract fallback from the start, but the web app only
reported a model failure and offered "Read again". Now every read the app makes (the
upload read, the pre-check, the submission, the specialist's re-check, every batch row)
falls back to local OCR when the configured model fails, and the fact is visible: the
applicant's hint names the failure and the reader, the comparison banner and the stored
run carry `tesseract (fallback after claude failed)`, and the batch rows show the same.
OCR reads have low confidence by design, so the confidence gate sends more rows to
review, which is the right bias for a degraded read. The fallback is never the same
backend as the primary, the demo reader has none, and `LABELVERIFY_FALLBACK=none` turns
it off. Without the binary there is no fallback and the failure is reported as before.

## 20. Tesseract is the local fallback, not PP-OCR, and accuracy is not why

PP-OCR (PaddleOCR) reads photographs better than Tesseract, and its mobile models run on
a CPU. It was considered for the fallback and set aside for the deliverable, for reasons
that weigh more than accuracy in the setting the brief describes:

- **It must install and run where the model cannot be reached.** The fallback exists for
  the day the firewall or the API is in the way. PaddlePaddle plus PaddleOCR is a few
  hundred megabytes of wheels, and PaddleOCR fetches its model files on first run, which
  fails on a firewalled machine unless the files are bundled by hand. Tesseract is one
  system package with its language data included, and it is in the Docker image.
- **It must run on the tester's machine, not the developer's.** The PP-OCR mobile models
  take one to three seconds per image on a current CPU and several times that on an older
  one, with roughly a gigabyte of memory. Tesseract reads a label in well under a second on
  anything that runs Python.
- **The fallback's errors are mostly not character errors.** In this path the OCR text is
  only half the job; the other half assigns raw lines to the seven fields with patterns,
  and that is where the fallback goes wrong most often. A better character reader would
  help a photo of a curved bottle, but not the field assignment, which is the same code
  for either engine.
- **Low accuracy is handled, not hidden.** The fallback reads are reported at low
  confidence on purpose, so the confidence gate turns their matches into review items and
  the applicant and specialist both see "read with local OCR" on the result. A degraded
  read that is labelled as such is safer than a better read that nobody is told about.

The reader interface is one method and the field assignment takes any block of text, so
a PaddleOCR reader is a small optional extra when a deployment can bundle its models and
has tested it on its own hardware. That is the next step for an offline installation,
after the submission, not before it.

## 21. An uncertain match is shown as a match, and the OCR reader learned where the brand is

Running the app on Tesseract alone showed two things on the result table. Every row said
"review", including rows whose two values were identical, because the confidence gate
turned a low-confidence match into a review verdict. And the brand row compared "NAPA
VALLEY" to "NAPA VALLEY": the OCR field assignment had taken the first line of text, the
kicker above the brand, and the form had been filled from that read, so the comparison
agreed with itself.

Two changes. The gate now keeps the verdict the comparison found and marks the row
"uncertain read"; the roll-up still sends the application to review and the summary
says how many matching rows came from a poor read. A table of identical values that says
"match, unverified" is honest about what was compared; a table that says "review" on every
row is not, and it hid the real mismatches among them. A genuine difference on a poor read
stays a mismatch.

The OCR field assignment no longer takes the first line as the brand. Tesseract's word
boxes give the height of every line, and the brand is the text set in the largest type on
the front panel: the tallest line seeds it and adjacent lines nearly as tall join it, so a
two-line brand is one candidate and the kicker, a medal, or a footer are not. The brand is
allowed to equal the producer's name, which it often does. Dark labels, where the brand is
light type on a dark ground, are read a second time inverted when the first pass reads
little. Address lines that name a state without a zip code now count as addresses.
Measured on the fourteen samples, the brand went from 3 to 12 of 14 right, the address
from 5 to 8, at about one second per label. The two misses are the steeply angled photo,
where Tesseract reads nothing, and a dark can where only the warning box is legible to it.

The result page also now says, in a banner, when a read came from local OCR and that the
field assignment itself can be wrong, so a mismatch may be the reader rather than the
label. The step-2 hint on a fallback read tells the applicant to correct the form to match
their application before checking.

## 22. On an uncertain read, absence is not evidence; and one OCR thread per call

Running the sample batch through Tesseract alone gave eleven "corrections needed" out of
sixteen, most of them "not found on the label" for text that is plainly on the label. OCR
misses text far more often than labels omit it, so on an uncertain read (nothing at
ordinary confidence, or nothing read at all) a required item the reader did not find is
now a review item with a note to confirm on the image, and a warning transcription a word
or two off is treated the same way. A difference in what was read stays a mismatch: a
wrong percentage, a changed warning sentence, a title-case heading. The summary says which
items were softened. The batch went to seven corrections, five of them the samples' own
defects.

The batch had also stalled for minutes under Tesseract while a single read took a second.
Tesseract starts a thread per core on every call, and the batch worker runs five reads at
once, so twenty threads fought over four cores (load average 20). The reader now sets
`OMP_THREAD_LIMIT=1` for its calls; the sixteen-row batch takes about ten seconds.

Also measured and rejected: upscaling the image two times before OCR. It lifted three
fields by one sample each and made every read six times slower.
