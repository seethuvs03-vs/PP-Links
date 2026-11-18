# Hosting PP Links

This archive contains the application, templates, static assets, category rules,
requirements, tests and example configuration. It does not contain passwords,
Facebook browser profiles, the original credential-bearing notebook or local logs.

## 1. Choose the server

Use one persistent Windows or Linux machine with Python and Google Chrome.
The current installation was tested with Python 3.14 on Windows. Other server
environments must be tested before production use. This is a stateful application:
do not use ephemeral storage, serverless functions, multiple replicas or multiple
worker processes against its SQLite database.

Facebook Connect opens Chrome on the SERVER, not on a visitor's computer. To use
this feature, the server needs an interactive desktop accessible to its operator.
Run Chrome and the app under the same OS account. A headless-only hosting service
cannot support the current interactive sign-in workflow without further work.

## 2. Install

Extract the ZIP and open a terminal in its PP-Links-Hosting folder.

Windows PowerShell:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

Linux (with a suitable Python installed):

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))'
```

Put the generated value in SECRET_KEY in .env. Install Chrome. Selenium Manager
normally obtains its matching driver; an offline installation must provide
CHROME_BINARY and CHROMEDRIVER_PATH.

## 3. Configure access

For an office LAN, set HOST=0.0.0.0 and add the server's actual IP or DNS name to
TRUSTED_HOSTS (comma-separated, hostnames/IPs without schemes or ports). Restrict
the firewall rule for port 5000 to the intended office network. Visitors use
http://SERVER-IP:5000, never 0.0.0.0 or their own 127.0.0.1.

For internet hosting, keep HOST=127.0.0.1 behind an HTTPS reverse proxy on the
same server. Add the public hostname to TRUSTED_HOSTS and preserve the Host header.
Require authentication at the proxy or VPN before access. The app does NOT have
user accounts, per-user permissions or an administrator role. All admitted users
can upload, edit, download and operate the shared Facebook session. Do not expose
port 5000 directly to the internet. Facebook login is not application login.

Default RESPECT_ROBOTS=true enables the advisory robots.txt check. The existing
local installation uses false by the owner's choice. Decide the deployment setting
explicitly. Login challenges and HTTP access-denial checks remain active.

## 4. Database and files

With a fresh installation, the database is created automatically. For migration,
stop the destination app first and copy the separately supplied private database
backup to data/pplinks.sqlite3. Keep the backup private: it includes saved links,
WhatsApp messages and sender information. Do not import Excel over the restored
database. The browser sign-in session is intentionally not migrated; connect again
on the server.

Keep data and outputs on persistent writable storage. The purchase master path
defaults to outputs/Purchase-Team-Master.xlsx. Existing purchase-team notes can be
migrated by copying the supplied workbook to that location before updating it.
The app preserves custom sheets and main-sheet columns N onward. App-owned sheets
and columns A-M are maintained by the app. Back up both SQLite and this workbook.

## 5. Test and start

Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe run.py
```

Linux: use .venv/bin/python for the same commands.

Run from the extracted project directory. Use this entry point (one process),
not Flask's debug server or a multi-worker WSGI command. Keep the process running
under a service supervisor or interactive desktop session appropriate to your
Facebook sign-in needs.

Open /health, /connections and /purchase-file on the configured host. Connect
Facebook on the server desktop if needed; test one link before a bulk upload.
Use Update master file once after installation or migration to create/refresh it.
Future completed jobs update that same file automatically. If Excel locks it,
close Excel and retry the update. Downloads and emailed copies are snapshots.

## Deployment acceptance checks

- Visit the app from a second permitted computer.
- Confirm the HTTPS/authentication gate if used outside the trusted LAN.
- Upload a small TXT file, then upload it again: existing links should not duplicate.
- Verify the purchase workbook and preserved purchase notes.
- Restart the app and confirm the database persists.
- Confirm the sign-in workflow on the actual host; it is not verified for an
  unspecified remote hosting environment by the local test suite.
