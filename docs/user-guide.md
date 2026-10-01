# User guide

How to use LabelVerify, what every screen is for, and what each tag and symbol means.
The README covers installation and configuration; this page is for the people using it.

## Who uses it

| Role | Demo account | What they do |
| --- | --- | --- |
| Applicant (a producer or a label-compliance agent) | maria@alvarezlabels.com | Uploads a label, fills in the application, runs the check, submits, answers questions, resubmits after a correction request |
| Labeling specialist (TTB) | sarah.chen@ttb.gov | Reviews the queue, confirms the AI check against the image, approves, or sends a correction request |

The demo password for both is `labelverify`. A public deployment hides the account list
on the landing page; the accounts still work.

## Which reader is active

The sidebar, bottom left, names the reader the app is using:

| Sidebar says | Meaning |
| --- | --- |
| Claude vision model | The configured model reads the label; this is the normal mode |
| Gemini vision model | The alternative model is configured |
| Local OCR (Tesseract) | The app was started with local OCR as the reader. No network is used |
| Demo mode | No API key is set. Only the fourteen bundled sample labels can be read |

When the model fails on a read (a timeout, a network error, a rejected key, the daily
budget), the app reads the label with local OCR instead and says so: the step-1 status
and the step-2 hint name the failure and the reader, and the result carries a "Read with
local OCR" banner. The reader's name is stored on the run, so the specialist's page and
the batch rows show it too.

## Applicant: a new application

**Step 1, label image.** Drop the front label, then the back and neck labels if the
product has them, up to four images. Or pick a bundled sample. "Read the label" stores
the images and reads them once. The status line shows how long the read took and which
reader did it. If the read fails, the status offers "Read again" and the form can be
filled by hand.

**Step 2, application details.** The form is filled from the read. It is a convenience,
not the truth: make every field say what your application says, then run the check.

- The type of product is detected from the label ("detected from the label" next to the
  select). Change it if it is wrong; the rules below follow the type.
- A red asterisk marks a field the class requires. "(optional)" marks one it does not,
  such as alcohol content on a malt beverage. Country of origin becomes required when
  "Imported product" is ticked.
- "Also read from the label" lists what the label itself must carry that you never type:
  the qualifying phrase before the producer's name, the Government Health Warning, and
  the class's own statements (sulfites, appellation and vintage for wine; age, bottled in
  bond and blend percentage for spirits; a strength claim for malt beverages). A green
  check and a value mean the read found it; hover for the full text. "Not found on the
  label" in amber means a required item was not read; "not on the label" in grey means a
  conditional item was not read and may simply not apply. These cannot be edited here
  because the label is the evidence: if one is wrong, the artwork changes, or the
  specialist confirms it from the image.
- "Reference" opens the rulebook for all three classes in a new tab, with citations.

**Check before submitting.** Runs the comparison and shows the result table (below). The
read is reused, so the check is instant. Fix the form and check again as often as needed.

**Step 3, submit.** Sends the application to the specialist queue with its result. You can
submit with open items; the specialist sees them.

## The result table

Each row is one field. The columns are the application value, the value read from the
label, and the result.

| Tag | Meaning |
| --- | --- |
| MATCH | Identical after normalization: case, punctuation, spacing, and standard abbreviations (CA for California) are ignored; numbers are compared as numbers |
| REVIEW | Similar but not identical, within a labeling tolerance, or something only a person can settle; the reason says which, with the similarity percentage when there is one |
| MISMATCH | A difference the rules do not allow, or a required item missing from the label |
| N/A | Not applicable here, for example country of origin on a domestic product |
| uncertain read | Added to a MATCH when the reader was not confident of its transcription (every OCR read, or a blurred photo). The values agree, but look at the image. The application goes to review, not approval |
| conditional | In the small print under the field name: required only in a stated circumstance (imports, sulfites, a vintage date) |
| 27 CFR x.xx | The regulation the row rests on; the Reference page has the text behind each |
| the bar and "n% read" | How confident the reader was in that value, from the model's own estimate or the OCR engine's; it is not the similarity score |

The banner above the table gives the roll-up and the rules applied. Approve means every
row matched on a confident read. Review means at least one row needs a look. Corrections
needed means at least one mismatch.

The Government Health Warning row compares the statement word for word with the
statutory text and shows the differences inline, red for missing words, green for extra
ones. "As printed" expands the transcription.

## Applicant: after submitting

The application page shows the status, the result, and a thread under every field where
the specialist can ask a question and you can answer. The inbox lists everything the
other side did that you have not read; the badge on it counts the unread items, and
opening an item marks only that item read.

A correction request arrives as a notice that lists the findings. The "Fix and resubmit"
form on the application page takes corrected values and, if the artwork changed, new
images; the label is checked again and goes back to the same specialist.

## Applicant: batch upload

A CSV and a zip of images, up to 300 rows. The CSV has the same columns for every class;
"What each column means" on the batch page says which the class requires, and the
template has one example row per class. Leave the type blank to let the label decide.
Each row becomes an application with its result, straight into the queue; the batch page
shows progress and a summary. A row whose image is missing or unreadable is reported,
not dropped. The sample CSV and zip cover every outcome.

## Specialist: the queue and a review

The queue has four cards: open items, those ready to approve (every row matched), those
needing a look, and those awaiting the applicant's correction. "Approved" lists the
decided ones. Opening an item claims it.

The review page shows the label image beside the result table. Every row has a thread
for a question to the applicant. "Re-check label" reads the images again with the
current reader. The decision panel offers:

- **Approve label**, with a confirmation when the check flagged items.
- **Request corrections**: "Draft the notice" writes a plain-language notice from the
  findings (a model rewrites the wording when one is configured; the content is always the
  engine's). Edit it, then "Send correction request". The applicant gets the notice and a
  thread on each flagged field. "Discard draft" throws the draft away.
- **Reject**, with a reason.

While an application awaits the applicant there is nothing to decide; the page says so
and keeps the threads open.

## When the model is unavailable

Nothing stops. Reads fall back to local OCR, every value comes back as an uncertain read,
and the result says "Read with local OCR" at the top. Expect the brand, class, net
contents and the warning to be read on clean artwork, and small print on photographs to
be missed; the field assignment can also be wrong, so a mismatch on an OCR read may be
the reader rather than the label. Use "Read again" once the model is back, or correct the
form by hand and let the specialist confirm against the image. The measured accuracy on
the bundled samples is in the README.
