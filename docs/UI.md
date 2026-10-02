# Interface design

The brief sets the bar in two sentences: "something my mother could figure out", 73 and
new to video calls, and "clean, obvious, no hunting for buttons", for a team where half
the agents are over 50 and one prints his emails. This page explains the choices made
to meet that, and the reasoning behind each.

## Less to look at

- **One task per screen, three numbered steps.** The applicant's page is step 1 (label
  image), step 2 (application details), step 3 (submit). Later steps are locked and
  dimmed until the earlier one is done, so there is only one thing to do at a time. The
  specialist's work is a queue, a review page, and a decision panel with three buttons.
- **The form fills itself.** After the read, step 2 is prefilled from the label with a
  note that says the applicant must check and correct it. A checklist beside the form
  was tried and removed: two lists to reconcile is one too many. The form itself follows
  the class, with a red mark on each required field and "(optional)" where the class
  does not require one, so the rule is on the field, not in a separate document.
- **The label stays in view.** The image sits beside the form and beside the result in a
  viewer that follows the page as you scroll; click to zoom, move to pan. A specialist
  confirms a finding in one look rather than switching windows.
- **Plain words.** Verdicts are MATCH, REVIEW, MISMATCH; each row says why in a sentence
  ("Similar but not identical to the application (92% similar). Confirm visually.") and
  names the regulation. The roll-up banner is a sentence, not a score. Notices read as a
  letter from a reviewer.
- **One funnel for "what is new".** An Inbox item in the sidebar with a count, an inbox
  page whose link lands on the exact field, and "New" markers on the page. Read state is
  derived from the records (a per-item receipt, or the moment the application was
  opened), so it is never wrong. Earlier versions with dots on queue rows and per-page
  read state were removed as duplicates.
- **A batch is one thing.** Sixty rows arrive as one bundle in the queue and one line in
  the inbox, with the rows under the queue's own tabs and every decision returning to the
  batch. Three hundred notifications would have been the opposite of helpful.

## Calm, dark, one accent

The look was set by reference to Anduril and SpaceX: calm, dense, technical, nothing
decorative (decisions.md, entry 10).

- **Dark by default**, light behind the toggle in the sidebar, remembered per browser
  and applied before first paint so the page never flashes. Dark suits long review
  sessions and lets the label photographs read as the only saturated thing on the page.
- **Near monochrome with one blue accent** for actions and focus; green, amber and red
  reserved for verdicts and status, so colour always means something.
- **Hairline borders, small radii, generous spacing.** Structure comes from alignment
  and whitespace, not boxes and shadows.
- **Two typefaces**, served from the app: Inter for text, with tight headings and spaced
  uppercase labels; JetBrains Mono for serials, readings, timestamps and citations, so
  anything that must be read exactly looks exactly readable.
- **Motion under half a second**, for entrances and hovers only, off under the
  reduced-motion preference.
- The landing page states what the app is and is not: "A prototype built for a TTB
  take-home exercise. Not an official TTB system." An earlier backdrop with the agency's
  seal was replaced, since federal rules restrict it.

## Honest about uncertainty

- Every result shows the read time and the reader's name, so the five-second budget is
  checked on the page rather than promised.
- An uncertain read is shown as what it is: "match · uncertain read" on a row whose values
  agree, "confirm on the image, or ask for a clearer photo" on one whose values differ. A
  table that said REVIEW on every row of a poor read hid the real mismatches; one that
  says exactly what was compared does not.
- A read that fell back to local OCR says so in three places and offers "Read again".
- Failed batch rows are listed with the reason, not dropped.

## Errors and edges

- A failed read offers "Read again" on the stored images and lets the form be filled by
  hand. An invalid form value is a 422 with a message, not a 500. Unknown pages and
  wrong methods render the same plain error page.
- A signed-out page that posts a comment gets a short "sign in again" partial, not the
  landing page injected into the thread.
- Confirmations only where a decision is irreversible and the check flagged items
  (approving a label with findings), nowhere else.

## Implementation

Server-rendered HTML with one stylesheet and one script, no framework and no build. CSS
custom properties define both themes; the markup did not change when the restyle
happened, so every test and browser check from before it still applies. Semantic
elements, keyboard-operable controls, `aria-current` on navigation, icons hidden from
screen readers, locked wizard steps marked `inert`. Not yet done: a formal Section 508
audit, and the specialist's queue assumes a desktop screen.
