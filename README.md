# Autonomous Expense Report Agent — React Edition

A local enterprise-style expense-reporting application with a React UI, FastAPI backend, SQLite persistence, and your existing LangGraph expense-report workflow.

## What's included

- React + TypeScript + Vite frontend with Home, Expense Review, Generated Reports, and AI Assistant sections.
- FastAPI API backend.
- SQLite database automatically created and seeded with synthetic expense data for multiple employees and months.
- Reuses the original `expense_agent.py` LangGraph workflow and `analysis_agent.py`.
- Review/edit missing amounts, receipt IDs, and review notes.
- PDF generation and persistent report version history.
- Explicit confirmation before email delivery. Demo mode simulates sending; it does not send real email.

## Requirements

- Python 3.10+ recommended.
- Node.js 20+ recommended.
- A Groq API key is optional for this initial React UI build; database retrieval, Python calculations, synthetic seed data, and Demo mode do not require it.
- Real email delivery requires configured Gmail SMTP credentials and a finance recipient.

## Windows setup

Open two terminals in the project root.

### Terminal 1 — Python backend

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
copy .env.example .env
```

Open `.env` and fill in credentials only if you want live LLM/email functionality. Do not share the file or commit it to Git.

Start the backend:

```bat
python -m uvicorn api:app --reload --host 127.0.0.1 --port 8000
```

Check the API at `http://127.0.0.1:8000/api/health` and interactive API docs at `http://127.0.0.1:8000/docs`.

### Terminal 2 — React frontend

```bat
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Database and synthetic data


## Database and synthetic data

On first startup, `database.py` creates `data/expenses.db` and seeds multiple employees with expense records across several months. It includes deliberate missing-amount and missing-receipt cases for testing. These are synthetic records, not live corporate data.

The SQLite database persists between restarts. To reset the sample database, stop the backend, delete `data/expenses.db`, and restart the backend. This deletes local edits and generated report metadata; back up anything you need first.

PDF files are stored in `data/generated_reports/`.


The SQLite database persists between restarts. To reset the sample database, stop the backend, delete `data/expenses.db`, and restart the backend. This deletes local edits and generated report metadata; back up anything you need first.

PDF files are stored in `data/generated_reports/`.

## Core workflow

1. Select an employee and reporting month.
2. The backend retrieves only matching database records.
3. The analysis agent calculates category totals and identifies missing information.
4. Review and save corrections in Expense Review.
5. Generate a PDF. Generation does not email it.
6. Open Generated Reports to download or send a specific version.
7. Sending requires an explicit confirmation. Demo mode only simulates delivery.

Reports are versioned; regenerating creates a new version rather than overwriting an existing report.

## Important limitations of this first integration

- The current React chat has a deterministic command router for the key jury-demo requests. It is not yet connected to the full live LLM tool-calling chat graph in `chat_assistant.py`.
- The current review screen edits amount, receipt/document ID, and review note. It does not yet upload actual receipt files.
- This is a local prototype with synthetic data. Employee selection is not authentication, so it must not be exposed as a real multi-user enterprise service without server-side identity and authorization.
- SMTP delivery and live API behavior must be tested with your own credentials before claiming they work. Keep Demo mode on for the jury until real delivery has been verified.
- `app.py` remains as the previous Streamlit interface and is kept as a fallback; the new React interface is served from `frontend/`.

## Troubleshooting

- If `streamlit` is not recognized, that affects the legacy UI only. For this React app, start the API with `python -m uvicorn ...` and the frontend with `npm run dev`.
- If the UI says it cannot connect, confirm the backend is running at `http://127.0.0.1:8000`.
- If port 8000 or 5173 is busy, stop the process using it or change the configured port.
- If PDF generation fails, check that Python can write to the project `data/generated_reports/` folder.


## Live LLM and email setup

Set `GROQ_MODEL=openai/gpt-oss-120b` and `GROQ_API_KEY` in `.env`. The chat endpoint uses the configured Groq model to classify requests and identify the requested employee/month; generation is proposed in chat and requires a confirmation dialog before the PDF is created.

For real email delivery, keep Demo mode unchecked, set `SENDER_EMAIL` and `EMAIL_APP_PASSWORD` to a Gmail sender account and Gmail App Password (requires 2-Step Verification), and verify the recipient in the final confirmation dialog. The default recipient is `anindita.sanjagiri@gmail.com`. The email uses a fixed professional template and attaches the saved PDF. The UI only reports success when SMTP confirms sending; failures are surfaced as failures. Never commit `.env`.

The database seeds synthetic sample data for six employees across nine months on first run. It is a persistent local SQLite database, not a live corporate expense system. Back up `data/expenses.db` before deleting or resetting data.

## Updated chat confirmation behavior

The chat endpoint uses recent conversation turns to recognize an affirmative reply to a pending report-generation confirmation. For example, after asking to generate the May 2026 report and confirming with `yes`, the backend executes the existing LangGraph report workflow, verifies that a PDF was created, persists the report version, and returns `report_generated`. The React client then opens Generated Reports and refreshes the list. A report can be generated with unresolved receipt information; the report remains marked Needs Review. Email delivery is a separate action requiring the email confirmation dialog.

The UI stylesheet includes the Tangerine Studio editorial redesign: larger typography, wider spacing, expressive metric panels, and more spacious chat and dialog layouts.

## Final UI and chat-action update

This revision replaces the former generic hero/metric-card home page with an editorial, illustrated layout: oversized Space Grotesk headings, asymmetrical warm-paper hero, CSS-drawn report artwork, three colorful workflow tiles, a spacious statistics strip, and more deliberate spacing. Operational tables and review dialogs remain accessible, and the palette follows Tangerine Studio (ivory, orange, ink, lavender, mustard).

The chat now keeps a pending report confirmation in React state. When the assistant has proposed a specific employee/month and the user confirms, the frontend calls the existing `/api/reports/generate` Python endpoint directly, then shows the actual result and refreshes report history. A clear cancellation discards the pending action. The API also has a deterministic guard for explicit report-generation requests so a generic missing-information classification cannot override an unambiguous generation request. Python/LangGraph remains responsible for creating the PDF; generation does not send email.

### Important limitations

- Expense records are synthetic demonstration data, not company records.
- A successful SMTP send requires valid sender credentials and Gmail App Password configuration. Keep credentials in `.env`; never commit the file.
- Frontend dependency installation and browser end-to-end testing were not completed in the build environment. Run `npm install` and `npm run build` from `frontend/` locally before presenting or deploying the app.
