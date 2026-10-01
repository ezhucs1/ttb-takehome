# From prototype to production

What this prototype leaves out on purpose, and what a production deployment for TTB would
need. Ordered roughly by how soon each would matter.

## Security and identity

- **Authentication.** Seeded accounts with a shared demo password and a signed cookie.
  Production would use the agency's identity provider (PIV/CAC through SAML or OIDC) for
  specialists and Login.gov for applicants, with MFA.
- **Session secret.** With `SECRET_KEY` unset, a key is generated once and stored beside
  the database, which is enough for one machine. A deployment with more than one instance,
  or one that must survive losing its volume, sets `SECRET_KEY` from its secret store.
- **Rate limiting and upload scanning.** Only a process-wide daily cap on model reads.
  Add per-account quotas, a reverse-proxy limit on the upload endpoints, and antivirus
  scanning of uploaded files.
- **Audit trail.** Status events record who did what and when, but comments and notices
  are editable only by insertion, not deletion; a production system needs a formal,
  immutable audit log and records-retention policy.

## Network and hosting

- **Model endpoint.** The prototype calls the Anthropic API directly. Inside TTB's
  network, Marcus's firewall would block that. Options, in order of least change: Claude
  through Microsoft Foundry on the agency's Azure tenant (endpoint inside the tenant,
  allow-listable); a FedRAMP-authorized gateway; or the bundled Tesseract fallback at
  lower accuracy.
- **FedRAMP.** Any hosted model or storage service used for real applications needs an
  authorization to operate. The extractor interface exists so the model behind it can be
  swapped without touching the workflow.
- **Storage.** Images live in SQLite. Production would use object storage with
  server-side encryption and signed URLs, and Postgres for the relational data.

## Accuracy and operations

- **Evaluation set.** The ten bundled labels are synthetic. Before rollout, collect a few
  hundred real, de-identified label images with specialist decisions and measure
  per-field precision and recall, plus the false-approve rate specifically.
- **Latency budget.** The UI shows extraction time on every check. Track p50 and p95
  against the five-second target and switch to a faster model tier if needed
  (`LABELVERIFY_MODEL`).
- **Model drift.** Pin the model ID, keep the ground-truth set, and rerun it when the model
  changes.
- **Monitoring.** No metrics or alerting. Add request logging with application IDs,
  extractor error rates, and batch completion times.
- **Background jobs.** Batches run in a thread pool inside the web process; a restart
  mid-batch leaves rows pending. Production would use a durable queue with retries.

## Product gaps

- **Beverage-specific rules.** Only the seven common fields are checked. TTB rules differ
  by class (wine appellation and varietal percentages, malt beverage exemptions, spirits
  age statements). The comparison module is organized per field so these slot in.
- **Label panels.** Up to four images per set are read together. Production would let
  the applicant tag each image (front, back, neck, strip) and show the specialist which
  panel each field was read from.
- **COLA integration.** Out of scope per the brief. The `Application` record mirrors the
  Form 5100.31 fields so an import from COLAs Online would be a mapping exercise.
- **Notifications.** Both roles see in-app badges and "New" markers for activity they have
  not opened, but nothing leaves the app. Add email on status changes and on replies.
- **Accessibility.** Semantic markup and keyboard-operable controls, but no formal
  Section 508 audit yet.
