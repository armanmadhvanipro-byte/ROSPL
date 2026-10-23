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

## Project Highlights

- 🧾 **Purchase & Invoice Management** — Record purchases, invoices, payment status and supporting documents.
- 🏢 **Vendor & Asset Tracking** — Maintain vendor details and connect assets with their purchase invoices.
- 🛡️ **Warranty & AMC Management** — Monitor warranty periods, expiry status and AMC renewals.
- 🔧 **Service History** — Track asset repairs, service cases and maintenance activities.
- 📊 **Reports & Dashboard** — View business summaries and export filtered reports in CSV or PDF format.
- 🤖 **AI-Powered Yappr Assistant** — Use natural-language commands to navigate the application and retrieve data insights.

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m flask --app app seed   # optional demo data
.venv\Scripts\python app.py                    # http://127.0.0.1:5000
