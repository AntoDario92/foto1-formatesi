# FormaTesi

Independent academic-support portal in Italian: public pages, email-verified student accounts, a personal workspace and protected administrative delivery workflow. This directory is separate from the existing repository root page, which is preserved.

## Production status

The public site works without configuration. Until permanent PostgreSQL, email and business/privacy configuration are supplied, registration and uploads return an explicit setup message. No accounts, password or documents are stored on ephemeral Render disk. `/anteprima` is a clearly labeled read-only example, never a simulated authenticated account.

## Deploy on Render

Free Python web service, repository `AntoDario92/foto1-formatesi`, branch `main`:

- Build: `pip install -r formatesi/requirements.txt`
- Start: `gunicorn --chdir formatesi app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 60`
- Health path: `/health`

Set variables from `.env.example`. The server will refuse local SQLite storage in production. Use a permanent external PostgreSQL database, not Render's 30-day free database. Free Render apps sleep after inactivity and have account-wide usage limits. No paid resource is provisioned by this repository.

Email uses Brevo's transactional API over HTTPS, which also works on Render's free web service. Set `BREVO_API_KEY`, a verified `MAIL_FROM` address and optionally `MAIL_FROM_NAME` (defaults to `FormaTesi`). Failed messages remain in the database outbox and can be retried explicitly from the administrator dashboard. No credentials or message text is logged.

`ADMIN_EMAIL` becomes administrator only after completing email verification for that exact address; first registrants never gain administrator rights. Use the registration and verification flow to create the administrator. Password reset tokens expire after one hour and invalidate previous sessions. Set `LEGAL_READY=yes` only after the operator confirms the actual privacy providers, retention policy and business details. Do not enable student registrations while those values are placeholders.

The public contact buttons use the provided FormaTesi WhatsApp number, +39 350 581 5735, with a prefilled request for the free consultation. Optional `WHATSAPP_NUMBER` controls the contact shown inside authenticated work pages and must contain digits only. Official university cover sheets are not included: the live preview is explicitly illustrative.

Facebook Login uses the server-side OAuth authorization-code flow. Configure `FACEBOOK_APP_ID` and `FACEBOOK_APP_SECRET` only in Render, and register `https://formatesi.onrender.com/facebook/callback` as an exact valid OAuth redirect URI in the Meta app. FormaTesi requests only `public_profile` and `email`; Facebook supplies name, surname and email, while the student still enters the university matricola and accepts the site terms. Existing accounts are matched by verified email or Facebook ID. The app secret never reaches the browser.

The university marks displayed on the public page identify the supported study paths. They remain the property of their respective owners, do not imply affiliation, and are accompanied by an explicit independence notice.

## Work lifecycle

`waiting → delivered → revision_requested → revised → revision_requested → revised …`

First delivery is version 0. Revision number increments only on delivery after a revision request. Immutable events and file versions preserve history. Stale or repeated delivery requests fail with 409. Per-user authorization is checked on every work action and every download. One free request per account is enforced by a database unique index. Similarity across matricola+ateneo, titles, outlines and file hashes is visible only to administrators and never automatically accuses or blocks someone.

Proposals include a total amount and a description of scope, times and included revisions. The student can express interest. There is no online payment and the UI explicitly states that confirmation does not conclude an order. A legally reviewed purchase flow and payment provider must be configured separately before collecting money online.

## Storage and security

Data and base64 attachments (max 5 MB each) are in PostgreSQL, with foreign keys and atomic transactions. Delivery and quote actions lock their project row. HTML escapes all content; no uploaded document is rendered inline. Only PDF, DOCX without VBA and UTF-8 TXT are allowed. File type checks are not antivirus scanning: integrate malware scanning before opening untrusted attachments operationally. CSRF tokens, origin checks, HttpOnly/Secure/SameSite cookies, scrypt hashes, parameterized SQL and persistent rate limiting protect the portal. Rate limiting uses the trusted server remote address; no untrusted forwarded address headers are accepted.

Operators must arrange database backups, deletion handling and storage monitoring. This application does not claim an automated backup/retention process. Do not store sensitive personal data in thesis attachments. Business policies and provider agreements require operator review before real collection begins.

## Verify locally

Python standard library only for tests:

`python -m unittest discover -s formatesi/tests -v`

Tests cover public launch gate, account verification, login, CSRF, work submission, first delivery, numbered revisions, cross-account access denial, duplicate free trial, stale delivery and password reset. Test SQLite is explicitly enabled using `TESTING=1`; it cannot be enabled by a browser request.
