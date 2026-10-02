# Prototype status

What is finished, what is deliberately left out, what should be tested before anyone
relies on it, and what a production deployment for TTB would need. Ordered roughly by
how soon each item would matter.

## What is finished

- Both roles end to end: upload, read, prefill, pre-check, submit, queue, review, comment
  on a field, correction notice, resubmission, approval and rejection, with the inbox
  and read state for each side.
- The seven fields the brief names plus the Government Health Warning, checked under the
  rules of the product's class (27 CFR parts 4, 5, 7 and 16), with the class-specific
  statements each class carries, citations on every row, and a rulebook page.
- Batch upload of up to 300 rows with live progress, individual row failures, one bundle
  on both sides, bulk approval of clean rows, and a results CSV.
- Four readers behind one interface (Claude, Gemini, Tesseract, demo), an automatic OCR
  fallback that says so, a daily read cap, and a five-second budget shown on every result.
- Fourteen synthetic sample labels and sixty real registry labels with a scenario file
  and known answers; 378 offline tests; a decision log of 29 entries.
- A deployed, always-on, HTTPS demo on Azure App Service with a one-script deployment.

## Assumptions

- Cloud model access is allowed for the prototype. The interviews say TTB's network
  blocks many outbound domains; the extractor interface, the Tesseract fallback and the
  absence of CDN assets are the mitigations, and the Azure-tenant path is below.
- The application is the applicant's statement and the label is the evidence. The
  reader's values are never treated as the filing: the prefill must be checked, and no
  test CSV carries reader output.
- Up to four images per label set, read together in one call, each field reported once.
- The registry's public record is not what the applicant typed on the form, so the forty
  registry rows are a test of the reader on real artwork, not of the engine's accuracy
  (the twenty hand-transcribed rows are that).
- Sample labels are synthetic renders; the registry images may differ from the labels as
  approved in type size and contrast, as the registry states.
- Demo accounts with a shared password stand in for identity; nothing sensitive is
  stored.

## Known limits

- **The reader is the error source.** It can take a brewery name for the brand, misjudge
  bold type, or misread small print on a photo. Each is routed to a person rather than
  decided, and the thresholds were tuned on fourteen synthetic and sixty real labels, not
  hundreds with specialist decisions.
- **Type size cannot be checked**, nor state of distillation, varietal percentages, or
  anything that needs the formula or records behind the label; they are listed as the
  specialist's checks.
- **Latency.** One panel of artwork reads inside the budget; a front-and-back photo set
  can take five to seven seconds on the model's slow tail. The UI never hides it.
- **Batches run in a thread pool inside the web process.** A restart mid-batch leaves
  rows pending; they are closed as failed at the next start rather than resumed.
- **One instance.** The sign-in throttle, the daily read cap and the session key file are
  per process. Fine for one always-on machine; a second instance needs shared state.
- **No email.** Both roles see in-app badges and markers; nothing leaves the app.
- **Opening a review page claims the application on a GET**, and the unread count is
  computed in Python over the user's items; both fine at this scale, both noted.
- **No formal Section 508 audit**, and the specialist's queue assumes a desktop screen.

## Before production: tests to run and problems to expect

- **An evaluation set with true answers.** Collect a few hundred real, de-identified
  label images with the forms as filed and the specialist's decision. Measure per-field
  precision and recall, the false-approve rate specifically, and the review rate (a tool
  that sends everything to review saves nothing). Rerun it whenever the model changes;
  pin the model id.
- **Latency under load.** Track p50 and p95 against five seconds on real traffic and on
  peak-season batches; test the five-worker pool against the provider's rate limits at
  300 rows; measure the slow tail and decide whether a faster model tier
  (`LABELVERIFY_MODEL`) is worth its signal loss.
- **The firewall.** Test the chosen model endpoint from inside TTB's network before
  anything else. Options in order of least change: Claude through Microsoft Foundry on
  the agency's Azure tenant (an endpoint inside the tenant, allow-listable); a
  FedRAMP-authorized gateway; the bundled Tesseract fallback at lower accuracy, or a
  PaddleOCR reader for an offline installation that can bundle its model files and test
  on its own hardware.
- **Rulebook currency.** Review `engine/rules.py` against eCFR on a schedule (the
  January 2025 standards of fill will change again), and extend it to the class-specific
  items listed as specialist checks where a reliable rule exists.
- **Concurrency and durability.** SQLite's single writer was already a problem under
  five concurrent reads; Postgres, a durable queue with retries, and object storage for
  images before any multi-user load.
- **Panel tagging.** Let the applicant tag each image (front, back, neck, strip) and show
  the specialist which panel each field was read from.
- **Accessibility and devices.** A Section 508 audit; the specialist's screens on the
  hardware agents use.
- **Abuse and cost.** Per-account quotas, upload scanning, and a budget alert on the
  model account; the prototype's daily cap is per process.

## What a production deployment needs

| Area | Prototype | Production |
| --- | --- | --- |
| Identity | Seeded accounts, shared demo password, signed cookie | PIV/CAC through SAML or OIDC for specialists, Login.gov for applicants, MFA |
| Model endpoint | Anthropic API directly | Claude through Microsoft Foundry on the agency's tenant, or a FedRAMP-authorized gateway; the extractor interface exists so the model can be swapped without touching the workflow |
| Hosting | One container on App Service B1 | FedRAMP-authorized hosting with an authorization to operate for any model or storage service touching real applications |
| Data | SQLite with images in the database on one volume | Postgres; object storage with server-side encryption and signed URLs; backups; a retention policy |
| Background work | Thread pool in the web process | A durable queue with retries and a dead-letter path |
| Secrets | `.env` locally, app settings on Azure, a generated session key | A secret store; key rotation |
| Audit | Status events, comments and notices by insertion | An immutable audit log; records retention; PII handling |
| Observability | Request log, timings on every result | Metrics with application ids, extractor error rates, batch completion times, alerting |
| Notifications | In-app badges and markers | Email on status changes and replies |
| COLA | Out of scope per the brief; the `Application` record mirrors Form 5100.31's fields | An import from COLAs Online would be a mapping exercise, behind its own authorization |
