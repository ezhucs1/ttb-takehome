# The AI model: where it sits, what it costs, how long it takes

LabelVerify uses a vision model for exactly one job on the critical path: reading the
label. Everything that decides something is code. This page explains that placement,
the prompt, the model choice and its measurements, the cost per label and per batch,
the time per label and per batch, and the local OCR fallback.

## Where the model is, and where it is not

| Step | Who does it | Why |
| --- | --- | --- |
| Read the label panels into fields | Vision model (Claude Sonnet 5.5 by default; Gemini behind the same interface) | Reading stylized type, curved bottles, glare and angled photos is what the model is good at and what Jenny Park asked for |
| Decide which class the product is | Code, from the class/type words; the model's judgment is used only when the words do not settle it | The class decides the rules, so it must be explainable |
| Compare each field with the application | Code (`engine/compare.py`) under the class's rulebook | A regulator needs to know exactly why a field failed; a model asked "does this match" is non-deterministic and untestable |
| Word-for-word Government Health Warning check | Code (`engine/warning.py`), a word diff against the statute | Exactness is the requirement; a model cannot be trusted to notice one missing word |
| Roll-up to approve, review, or request correction | Code | Same |
| Draft the correction notice | Code writes the template from the findings; a model (Gemini when its key is present, else Claude) rewrites the wording; the specialist edits it | The content is fixed before any model sees it; the prompt forbids adding or removing findings; the rewrite is off the five-second path and falls back to the template on any failure |
| Inbox, read state, queue, decisions | Code | Bookkeeping must be exact and instant |

The model never sends anything, decides anything, or fills the applicant's form with
authority: the prefill is a convenience the applicant must check, because the check
compares what the applicant files against the label, and a value copied unchecked from
the reader would only compare the reader with itself. The same rule holds for the test
data: no CSV in the repository carries values the reader produced.

## The read

One request per label set, however many panels (up to four). The system prompt
(`engine/extractors/claude.py`) tells the model to transcribe exactly as printed, never
to correct or complete text, to report each field once from the most legible panel, to
set a field to null with confidence 0 when it is not visible, and to report confidence as
its certainty that the transcription is exactly right (1.0 for crisp text, about 0.5
under glare or blur). It defines each field in the COLA form's own terms, including
`fanciful_name` for a second product name, the "Bottled by A for B" rule (A is the
producer), the appellation (not the bottler's city), and the health warning's heading
flags: all caps is a yes or no; bold is yes, no, or null when the type is too small or
rotated to tell. It also asks for image-quality notes ("glare across the bottom third").

The reply is JSON whose shape is spelled out in the prompt and validated client-side with
the same pydantic model the engine uses. The API's schema-constrained output mode is
opt-in (`LABELVERIFY_STRUCTURED_OUTPUT`): it compiles each new schema into a grammar on
first use, and after the rulebook added seven fields that compile pushed every read past
the 20-second timeout (decisions.md, entry 14). A read has a five-second budget, so no
step may have a latency the application does not control.

Confidence from the read sets the strength of the verdict (decisions 21 to 23): a
difference read confidently is a mismatch; a difference from a low-confidence field is a
review item that says "confirm on the image, or ask for a clearer photo"; a
low-confidence agreement is a match marked "uncertain read" that still sends the
application to review. On the OCR path every value is low confidence, so the engine never
issues a correction request on its own.

## Model choice, measured

Measured on the hardest bundled sample (an angled, glary phone photo), median of three
runs from a home connection:

| Provider and model | Read time | Read correct? | Confidence reporting |
| --- | --- | --- | --- |
| Claude Sonnet 5.5 (default) | 4.1 s | yes, all fields | calibrated |
| Claude Haiku 4.5 | 3.9 s (one run 19.5 s) | yes, all fields | calibrated, but named no image issues |
| Claude Opus 5.5 | 6.0 s | yes, all fields | calibrated; named angle, lighting, blur |
| Gemini Flash (free tier) | 13.9 s | yes, all fields | 1.0 on every field, no issues named |

Sonnet meets the five-second budget with the same extraction as the larger model.
Haiku's gain is marginal, with a long-tail outlier and less image-quality signal. Opus is
slower and costs several times more, so it was dropped from the configuration. Gemini
reports full confidence on every field, which would disable the review gate; it stays
as a zero-cost evaluation path (and the free tier may use requests for training, so it is
for synthetic and public labels only).

The near-identical times across Claude tiers show the fixed cost is the image payload
and the output length (the JSON includes the full warning transcription), not the model.
Image size is a minor factor: 1200 px measured 3.97 s against 4.08 s at 1500 px with no
loss on the samples, so 1200 px is the default. About four seconds is the floor for this
design.

## Time per label and per batch

| Case | Measured | Notes |
| --- | --- | --- |
| One panel, Sonnet 5.5 | 4.1 s | The read; the comparison adds milliseconds |
| Front and back, one call | 5.5 s | Each extra panel adds about a second and a half |
| Slow tail | 5 to 7 s | A hard photo or a busy API; the result shows the real time |
| Pre-check and submission | instant | They reuse the read cached at upload; a new read happens only when the images change or a specialist re-checks |
| Local OCR (Tesseract) | about 1 s per label | One OCR thread per call, so five workers do not contend |
| Sample batch of 16, demo mode | 1 to 2 s | No reads |
| Sample batch of 16, OCR | about 10 s | |
| Registry batch of 60 (97 panels), model | a few minutes | Five concurrent reads; about 12 rounds of 5 to 7 s plus the API's tail. The batch page prints the measured total and per-label time |

The browser shrinks a phone photo to 1600 px before upload so the upload itself is a
fraction of a second; the result line shows the server's read time with the upload
apart, since that depends on the connection.

## Cost per label and per batch

Cost scales with panels and image size, not rows. Observed on the live demo:

| Unit | Cost |
| --- | --- |
| One label, one panel, 1200 px | about $0.02 |
| One label with back and neck panels, or a large photo | up to about $0.06 |
| The sixty-label registry batch (97 panels) | about $1 per run |
| The sample batch of sixteen | about $0.30 |
| A notice rewrite | a fraction of a cent (Gemini free tier on the demo) |
| Demo-mode reads, OCR reads | free |

This is why the README asks testers to run the batch uploads two or three times at most.
A deployment can cap paid reads per UTC day with `LABELVERIFY_DAILY_READ_LIMIT` (after
the cap, uploads get a clear message and the samples keep working); the demo runs
without a cap and relies on the note.

## The local OCR fallback

Every read the app makes falls back to Tesseract on the server when the configured model
fails (timeout, network, rejected key, exhausted budget), and the result says so in the
step-1 status, the step-2 hint, the comparison banner, and the stored run (`tesseract
(fallback after claude failed)`). `LABELVERIFY_EXTRACTOR=tesseract` makes it the reader
outright, which is how a machine without a key reads its own images.

Tesseract was chosen over PaddleOCR because it installs as one package with no model
download, runs in under a second on an old CPU, and because the fallback's errors come
mostly from assigning raw text to fields, which is the same code for either engine
(decisions.md, entry 20). The field assignment takes the brand from the largest type on
the front panel, reads dark labels a second time inverted, and treats address lines
without a zip code as addresses. Measured on the fourteen samples:

| Field | Right | Notes |
| --- | --- | --- |
| Brand name | 12 of 14 | misses the steeply angled photo and one dark can |
| Class / type | 12 of 14 | the same two |
| Net contents | 12 of 14 | |
| Alcohol content | 10 of 14 | small print on the photos |
| Producer name | 9 of 14 | |
| Address | 8 of 14 | |
| Warning present | 13 of 14 | the word-for-word check still runs on what was read |

On an uncertain read a required item the reader did not find is a review item with a note
to confirm on the image, not a finding, because OCR misses text far more often than labels
omit it; a difference in what was read stays a mismatch. On photographs of real bottles
expect most rows to come back for review on the OCR path, which is the brief's own
fallback: ask for a better image, with the reader's guess shown beside it.

## Command-line tools for anyone with a key

These are for developers evaluating readers; the web app needs none of them. Run them
with the virtual environment activated and the key in `.env`.

```bash
python -m labelverify.cli extract labelverify/samples/old-tom-angled-photo.jpg --extractor claude
python -m labelverify.cli extract front.jpg back.jpg --extractor gemini          # a set, read as one
python -m labelverify.cli verify labelverify/samples/old-tom-bourbon.jpg --application app.json --fallback
python -m labelverify.cli bench labelverify/samples/old-tom-angled-photo.jpg --extractors claude,gemini --runs 3
python -m labelverify.cli bench front.jpg back.jpg --extractors claude --runs 3 --timeout 120
```

`extract` prints the raw extraction with each panel's size after preprocessing, the token
counts and the request id; `bench` prints median latency per extractor, which is how the
numbers above were taken. `--model` and `--timeout` apply to that run only.
