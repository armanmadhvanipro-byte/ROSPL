# Purchase & Warranty Manager

Business purchase, invoice, asset, warranty and service tracking — BE SEM 7 ROSPL mini project
(Atharva College of Engineering, IT). See `Purchase_Warranty_Manager_Report.docx` for the full report.

**Lifecycle:** Purchase → Invoice → Asset Registration → Warranty Tracking → Service/Maintenance → Warranty Expiry

## Features
- Login with roles: **admin** (everything, incl. deletes and user management) and **staff** (add/edit).
- Vendors with contact and GSTIN; purchases with invoice no, date, amount, tax, payment status and invoice upload (PDF/image).
- Assets linked to their invoice, with serial no, category, department, location and status.
- Warranty expiry calculated automatically from start + months (start defaults to the purchase date); Active / Expiring Soon / Expired / Pending status with an adjustable expiry window.
- AMC tracking (provider, start, end).
- Service history per asset; an open case marks the asset *Under Repair*, and resolving it puts the asset back *In Use*.
- Dashboard: totals, warranty counts, items needing attention, vendor-wise and category-wise spend, upcoming expiries, service and AMC renewals due.
- Reports: purchase register, vendor summary, asset & warranty register, category summary, service log — filterable, with CSV and PDF export.
- Anomaly checks from the report: duplicate invoice per vendor, duplicate serial no, invalid date sequences, missing invoice document, missing warranty info, unresolved service cases.
- CSRF protection on every form, hashed passwords, upload type/size checks, safe file names.
- **Yappr, the AI assistant** (bottom-right, or Ctrl+K): an animated companion (ported from the Katachi call desk) that answers questions about your data and drives the app. Ask "which warranties expire this month?", "purchases from TechWorld missing invoices" or "export the asset register as PDF", and it opens the right page with the filters applied. Ask "how do I upload an invoice?" and it flies to that field, rings it and says what to do. "Open X", "add a purchase" and how/where questions work instantly without a model key; data questions need `GEMINI_API_KEY` or `GROQ_API_KEY`. The model runs server-side through read-only tools, so keys never reach the browser.
- Motion everywhere: page cross-fades, staggered entrances, counting numbers, growing bars, ripples and an animated sign-in page. All of it switches off under the OS "reduce motion" setting.

## Run locally
```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m flask --app app seed   # optional demo data
.venv\Scripts\python app.py                    # http://127.0.0.1:5000
```
First start creates the user **admin / admin@123**. Change it under **Users** right away.

Self-check: `.venv\Scripts\python test_app.py`

## Configuration (environment variables)
| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///pwm.db` (in `instance/`) | e.g. `mysql+pymysql://user:pass@host/pwm` for MySQL |
| `SECRET_KEY` | dev value | **Set in production** |
| `ADMIN_PASSWORD` | `admin@123` | Password for the auto-created first admin |
| `EXPIRING_DAYS` | `30` | Default "expiring soon" window |
| `GEMINI_API_KEY` | — | Yappr's model (Gemini Flash-Lite, tried first) |
| `GROQ_API_KEY`, `GROQ_API_KEY_2` | — | Yappr's fallback model (Groq) |
| `GEMINI_AGENT_MODELS`, `GROQ_AGENT_MODEL` | `gemini-3.5-flash-lite,gemini-3.1-flash-lite`, `qwen/qwen3.8-27b` | Override the model names |

## Deploy (Render)
- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app`
- Set `SECRET_KEY`, `ADMIN_PASSWORD` and `DATABASE_URL` (MySQL). Uploaded invoices go to `instance/uploads`; attach a persistent disk there.

## Project layout
```
app.py            models, business rules, routes, reports, Yappr's model loop and tools, seed command
templates/        Jinja pages (macros.html holds shared form/badge helpers)
static/style.css  UI styles and motion (light/dark, responsive)
static/app.js     page motion: stagger order, count-up numbers, ripples
static/yappr/     the companion: yappr.js (chat, local commands, pointing), yappr.css, avatar.json (faces and moods)
test_app.py       end-to-end self-check
```
