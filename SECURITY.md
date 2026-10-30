# Security and credentials

Keep provider credentials in your local `.env`, copied from `.env.example`.
The template contains blank provider keys and public local-development database
defaults. Use your own credentials when enabling live integrations.

The API is a local development service. It has no user authentication or public
deployment hardening; keep it on `127.0.0.1`. PostgreSQL and Redis should also be
limited to your laptop. Never expose the development database to the internet.

Before committing, run:

```powershell
.\.venv\Scripts\python.exe scripts/check_publication.py
git add .
.\.venv\Scripts\python.exe scripts/check_publication.py --staged
```

Enable the same check before each local commit with
`git config core.hooksPath .githooks` (each clone enables its own hook).

The guard checks files eligible for Git, or the actual staged bytes with
`--staged`. It detects copies of local `.env` credentials, common token formats,
private-key headers, private/generated artifacts and oversized files. It prints
paths and credential names only. Pattern checks cannot detect every possible
secret, so review `git diff --cached` as well. CI repeats the candidate scan.

Do not place credentials in frontend source, screenshots, notebook outputs,
query URLs, training exports, issue descriptions or logs. Frontend build-time
variables are visible to browser users; provider keys belong in the backend.

If a credential is committed or shared, revoke/rotate it at its provider first.
Removing the current file does not remove it from Git history. Avoid publishing
the affected history until it has been cleaned and checked.

For a security issue, use the repository's private vulnerability reporting
feature if enabled. Do not include working credentials in a public issue.
