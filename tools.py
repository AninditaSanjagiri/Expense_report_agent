"""
tools.py
--------
The three tools used by the Expense Report Agent graph:

  1. get_mock_expenses()   — a simulated local "database" query
  2. generate_pdf()        — renders the final report as a real PDF (fpdf2)
  3. send_expense_email()  — emails that PDF via Gmail SMTP (stdlib only)

Keeping these in their own file (separate from expense_agent.py's graph
logic) means each tool is a plain, independently testable function — you
can call any of them from a Python shell with no LangGraph involved at all,
which is a handy thing to demonstrate in a viva.
"""

import os
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fpdf import FPDF

# ---------------------------------------------------------------------------
# 1. SIMULATED LOCAL DATABASE
# ---------------------------------------------------------------------------
# A hardcoded list of dicts standing in for a real receipts database/API
# (constraint: "No Database APIs"), so the demo never depends on a live
# connection. NOTE the Taj Hotels row: `amount` is deliberately `None` —
# that's what guarantees the Anomaly Handler branch is reachable whenever
# you demo with Aditi Sharma / 2026-07.
# `receipt_id` is the supporting document reference; the Domino's row has
# `None` there, so the assistant can also report "missing supporting info".
# Expense records are stored in SQLite. The database is seeded with synthetic
# enterprise records by database.py on first startup.
from database import get_expenses as _db_get_expenses, available_months as _db_available_months
MOCK_RECEIPTS = []  # retained only for backward-compatible imports



def get_mock_expenses(employee_name: str, target_month: str) -> list:
    """Retrieve persistent expense records for an employee and YYYY-MM month."""
    return _db_get_expenses(employee_name, target_month)



def get_available_months(employee_name: str) -> list:
    """Return months with records for this employee."""
    return _db_available_months(employee_name)



# ---------------------------------------------------------------------------
# 2. PDF GENERATION
# ---------------------------------------------------------------------------
def generate_pdf(
    employee_name: str,
    target_month: str,
    category_totals: dict,
    grand_total: float,
    discrepancy_note: str = "",
    output_dir: str | None = None,
) -> str:
    """
    Renders the finished expense report as a real, physical PDF file using
    fpdf2 (the maintained fork of the original `fpdf` library — it keeps
    the same `from fpdf import FPDF` import for backward compatibility).

    Returns the absolute path of the generated PDF, which the Compiler &
    Delivery node then hands to send_expense_email() as an attachment and
    to app.py for the Streamlit download button.
    """
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(__file__), "data", "generated_reports")
    os.makedirs(output_dir, exist_ok=True)
    pdf = FPDF()
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Travel Expense Report", ln=True)

    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, f"Employee: {employee_name}", ln=True)
    pdf.cell(0, 8, f"Month: {target_month}", ln=True)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Category Breakdown", ln=True)
    pdf.set_font("Helvetica", "", 11)
    for category, total in category_totals.items():
        pdf.cell(0, 7, f"{category}: Rs. {total:,.2f}", ln=True)

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, f"Grand Total: Rs. {grand_total:,.2f}", ln=True)

    # Only render the "Flagged for Review" block when there's actually a
    # discrepancy note to show — keeps clean-run PDFs uncluttered.
    if discrepancy_note:
        pdf.ln(6)
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(200, 30, 30)
        pdf.cell(0, 8, "Flagged for Review", ln=True)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(0, 0, 0)
        pdf.multi_cell(0, 6, discrepancy_note)

    safe_name = employee_name.strip().replace(" ", "_")
    filename = f"expense_report_{safe_name}_{target_month}.pdf"
    output_path = os.path.join(output_dir, filename)
    pdf.output(output_path)
    return output_path


# ---------------------------------------------------------------------------
# 3. EMAIL DELIVERY
# ---------------------------------------------------------------------------
def send_expense_email(
    receiver_email: str,
    pdf_path: str,
    employee_name: str,
    target_month: str,
) -> dict:
    """
    Sends the generated PDF as an email attachment via Gmail's SMTP relay
    (smtp.gmail.com, port 587, STARTTLS) using ONLY Python's standard
    library — `smtplib` for the connection, `email.mime` for building a
    multipart message with an attachment. No third-party mail SDK needed.

    Reads sender credentials from environment variables (loaded from .env
    by expense_agent.py's `load_dotenv()`):
        SENDER_EMAIL        - the Gmail address sending the report
        EMAIL_APP_PASSWORD  - a Gmail "App Password", NOT the normal
                               account password (Gmail requires 2FA + an
                               App Password for third-party SMTP login)

    The whole call is wrapped in try/except so a viva demo with missing or
    incorrect credentials — or simply no internet in the exam hall — still
    finishes gracefully instead of crashing Streamlit. The returned dict
    tells the UI exactly what happened either way.
    """
    sender_email = os.getenv("SENDER_EMAIL")
    app_password = os.getenv("EMAIL_APP_PASSWORD")

    if not sender_email or not app_password:
        return {
            "success": False,
            "message": "SENDER_EMAIL / EMAIL_APP_PASSWORD not set in .env — email not sent.",
        }

    try:
        message = MIMEMultipart()
        message["From"] = sender_email
        message["To"] = receiver_email
        message["Subject"] = f"Travel Expense Report — {employee_name} — {target_month}"

        # Fixed professional template; only employee and reporting month vary.
        body = (
            "Dear Finance Team,\n\n"
            f"Please find attached the travel expense report for {employee_name} for {target_month}.\n\n"
            "Please review the attached report and follow the standard expense-processing procedure. "
            "Any items marked 'Needs Review' require verification of the supporting information.\n\n"
            "Kind regards,\nExpense Operations"
        )
        message.attach(MIMEText(body, "plain"))

        with open(pdf_path, "rb") as f:
            attachment = MIMEBase("application", "octet-stream")
            attachment.set_payload(f.read())
        encoders.encode_base64(attachment)
        attachment.add_header(
            "Content-Disposition",
            f"attachment; filename={os.path.basename(pdf_path)}",
        )
        message.attach(attachment)

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()  # upgrade the plaintext connection to TLS
            server.login(sender_email, app_password)
            server.send_message(message)

        return {"success": True, "message": f"Email sent to {receiver_email}."}

    except Exception as exc:  # noqa: BLE001 — intentionally broad for demo safety
        return {"success": False, "message": f"Email send failed: {exc}"}


# ---------------------------------------------------------------------------
# 4. CONFIRMED DELIVERY (Jury 2)
# ---------------------------------------------------------------------------
def send_confirmed_email(
    receiver_email: str,
    pdf_path: str,
    employee_name: str,
    target_month: str,
    demo_mode: bool = False,
) -> dict:
    """
    The ONLY place the app triggers an email, and it is only ever called
    after the employee explicitly confirms (button click or typing "yes").

    - demo_mode=True  -> nothing is sent; we return an honest "simulated"
                         result so the demo works offline / without
                         credentials and never claims a real send.
    - demo_mode=False -> calls the real Gmail SMTP sender above.

    Always returns {"success": bool, "status": "sent"|"failed"|"simulated",
    "message": str} so the UI can show the true outcome.
    """
    if demo_mode:
        return {
            "success": False,
            "status": "simulated",
            "message": f"Demo mode: email to {receiver_email} was NOT actually sent (simulated).",
        }
    result = send_expense_email(receiver_email, pdf_path, employee_name, target_month)
    result["status"] = "sent" if result["success"] else "failed"
    return result
