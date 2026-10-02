# Security

What the app does to protect itself on every host, what the public deployment adds, and
what a production deployment for TTB would need on top. The brief's guidance for the
prototype is "just don't do anything crazy" and "we're not storing anything sensitive";
the items below are the basics a public URL warrants, each small and dependency-free.

## Sign-in and sessions

- Passwords are stored as salted PBKDF2 hashes and compared in constant time.
- Ten failed attempts per address or per account in fifteen minutes and sign-in waits
  (in memory per process, which is right for one instance).
- The session is a signed, expiring cookie: HttpOnly, SameSite=Lax, and Secure with HSTS
  when `LABELVERIFY_SECURE_COOKIES=true` (the deploy script sets it behind HTTPS).
- The "next" page after sign-in is checked so it cannot send anyone off-site or to the
  other role's pages.
- The session secret is `SECRET_KEY`, or a key generated once and stored beside the
  database with owner-only permissions, so sessions survive restarts on one machine.
- The demo accounts' password can be set per deployment; the public deployment hides the
  account list from the sign-in dialog (the accounts are in the README by design, so this
  keeps them off search engines and casual visitors, not off reviewers).

## Requests that change state

- The cookie's SameSite rule keeps it off cross-site form posts, and the server also
  refuses any state-changing request outside the API whose Origin, Referer or
  Sec-Fetch-Site names another site. A request with none of those headers (a
  command-line client) passes; it carries no cookie unless its author sends one.
- CSRF tokens in every form were considered and not needed given SameSite plus the
  origin check in current browsers.

## Responses

- Every response carries a Content-Security-Policy (scripts and connections from this
  origin only, nothing in a frame; inline styles permitted because result tables and
  progress bars set widths), nosniff, a referrer policy, and HSTS on HTTPS.
- No page loads anything from a third party: fonts, scripts and the hero image are
  served from the app.
- Error pages are plain HTML without stack traces; the router's own 404 and 405 render
  the same page.

## Access

- An applicant reaches only their own applications and batches; a specialist reaches no
  drafts; every image, comment, notice and batch route checks the account.
- The JSON API takes a signed-in user or `LABELVERIFY_API_KEY` (`X-API-Key` header or a
  bearer token), so a public URL cannot be used to spend model reads.
- Paid model reads can be capped per UTC day (`LABELVERIFY_DAILY_READ_LIMIT`) so an
  open link cannot run up the bill; the demo runs without a cap and asks testers to be
  considerate.

## Uploads and data

- Uploaded files are decoded and re-encoded as images on the server; a file that is not
  an image is refused. Size (10 MB per image) and count (four per set) are limited, and
  zip members are size-checked before they are inflated.
- Templates escape everything; the database is reached through an ORM with bound
  parameters.
- Keys never enter the repository: `.env` is ignored by git, the deploy script reads keys
  from the shell and writes them only to the app's settings on Azure, and the live
  demo's data directory lives on the App Service volume, not in the image.
- The Gemini free tier may use requests for training, so it is for synthetic and public
  labels only; the demo reads with Claude and uses Gemini only to word notices built from
  the engine's findings.

## What production adds

Listed in order of how soon each would matter; see [PROTOTYPE.md](PROTOTYPE.md).

- **Identity.** The agency's identity provider (PIV/CAC through SAML or OIDC) for
  specialists and Login.gov for applicants, with MFA, instead of seeded accounts.
- **Secrets and keys.** `SECRET_KEY` and the model keys from a secret store; a model
  endpoint inside the tenant (Claude through Microsoft Foundry on the agency's Azure) or
  a FedRAMP-authorized gateway, since the prototype calls the Anthropic API directly.
- **Rate limiting and scanning.** Per-account quotas, a reverse-proxy limit on the upload
  endpoints, shared counters across instances, and antivirus scanning of uploads.
- **Audit and retention.** Status events record who did what and when, but comments and
  notices are editable only by insertion; production needs an immutable audit log and a
  records-retention policy, and PII handling once real applications are involved.
- **Storage.** Object storage with server-side encryption and signed URLs for images;
  Postgres for the relational data.
