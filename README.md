# PP Links

Python/Flask application for recurring WhatsApp link processing and a document library.

## Features

- Upload WhatsApp TXT exports or paste Facebook, Instagram and YouTube links.
- Preserve chat provenance, normalize URLs and detect duplicates across uploads.
- Standard Chrome/Selenium extraction with optional user-operated Facebook sign-in.
- Persistent SQLite dataset, configurable categories, manual editing, search and retries.
- Append/update the purchase-team Excel master with the existing 13-column main format.
- Upload PDF, Excel, Word and image files; deduplicate file contents and retain attachment references.
- Image gallery, enlarged previews, file details, downloads and upload history.

## Quick start (Windows)

Install Python 3.14 and Google Chrome. Open PowerShell in this repository folder:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

Set `SECRET_KEY` in `.env` to the generated value, then run:

```powershell
.\.venv\Scripts\python.exe run.py
```

Open http://127.0.0.1:5000/. Tables initialize automatically. After setup, `start.bat` starts the app in the background. Python 3.14 is the tested version. The app is a single server process with one durable worker; do not start multiple workers against the same database.

For Linux and network deployment, see [HOSTING.md](HOSTING.md). Its extracted-folder name is illustrative: run commands from this repository's root. Chrome and the app must run under the same OS account; interactive Facebook sign-in requires a desktop on the server. The optional `enable-office-access.ps1` requires a local IP via `-LocalAddress`. Review its interface and port before running it.

## Using the application

Overview TXT uploads scrape links directly. Paste links is also available. Existing URLs are not inserted again. Results distinguishes complete, partial and failed retrievals; manual details can be added. Facebook content may require sign-in or verification, may be unavailable, or may contain no caption. The application does not guarantee extraction from every post and does not solve CAPTCHA or bypass access restrictions.

Documents is a separate section for file uploads. Choose Gallery, Images only or List, and open an image for an enlarged view. An optional chat TXT can associate files with messages by filename within that upload. TXT exports contain references, not attachment bytes; supply actual files. Unmatched references remain awaiting upload. Files with identical contents are stored once; different contents with the same name remain separate. Files are stored unchanged. There is no OCR or document-content extraction.

## Persistent storage and backups

- `data/pplinks.sqlite3`: master records, job history and document index.
- `data/documents/`: uploaded document bytes (configurable with `DOCUMENTS_DIR`).
- `outputs/Purchase-Team-Master.xlsx`: default continually updated purchase export.
- Facebook's dedicated session profile stays in local application data, outside the repository.

Back up both SQLite and uploaded document files together. Keep the master workbook if it includes purchase-team notes. `.env.example` lists configuration settings. The default rendered page limit is 32 MB; document limits are 20 MB per file and 50 MB per request. Categories are in `config/categories.json` and editable in the app.

Source code alone starts with an empty collection. This repository intentionally excludes real records, documents, exports, credentials and browser sessions. Transfer business data separately through an approved private channel when migrating.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The automated suite uses temporary databases and files. `tests/live_smoke.py` is an optional network diagnostic requiring an existing dataset; it is not part of the automated suite.

## Optional maintenance utilities

`repair_facebook.py` and `restore_import_fields.py` are one-time tools for older imported datasets. They are not installation steps. Read each script first, stop the server and keep a database backup before use.

## Upload to GitHub

1. Create a repository, preferably private for an internal company application.
2. Extract the supplied ZIP and upload the **contents** of `PP-Links-GitHub`, not the ZIP itself. Include `.env.example` and `.gitignore`.
3. Commit the files. Do not add `.env`, databases, uploaded files or Facebook browser profiles.
4. For an existing repository, replace the corresponding application files and add the new document templates, module and tests.

Uploading to GitHub stores source code; it does not start or host this Python application. Ignore rules do not remove sensitive files that were already committed: review the repository before publishing. No open-source license has been assigned in this package; choose licensing according to company policy.
