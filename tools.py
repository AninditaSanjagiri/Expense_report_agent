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
from email import encoders, message
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from time import time

from fpdf import FPDF

# ---------------------------------------------------------------------------
# 1. SIMULATED LOCAL DATABASE
# ---------------------------------------------------------------------------

MOCK_RECEIPTS = [
    # Aditi Sharma - July 2026 (Contains an anomaly)
    {"employee": "Aditi Sharma", "date": "2026-07-03", "vendor": "IndiGo Airlines", "description": "Flight PNQ-BLR", "amount": 4500.0},
    {"employee": "Aditi Sharma", "date": "2026-07-03", "vendor": "Cafe Coffee Day", "description": "Client lunch", "amount": 850.0},
    {"employee": "Aditi Sharma", "date": "2026-07-05", "vendor": "Taj Hotels", "description": "2 nights lodging", "amount": None}, # Anomaly
    {"employee": "Aditi Sharma", "date": "2026-07-06", "vendor": "Uber", "description": "Airport transfer", "amount": 620.0},
    {"employee": "Aditi Sharma", "date": "2026-07-10", "vendor": "Domino's Pizza", "description": "Team dinner", "amount": 1200.0},
    {"employee": "Aditi Sharma", "date": "2026-07-12", "vendor": "WeWork", "description": "Day pass for coworking", "amount": 900.0},
    
    # Aditi Sharma - August 2026 (Clean run, no missing data)
    {"employee": "Aditi Sharma", "date": "2026-08-01", "vendor": "Vistara", "description": "Flight BLR-BOM", "amount": 5100.0},
    {"employee": "Aditi Sharma", "date": "2026-08-02", "vendor": "Trident Hotel", "description": "1 night stay", "amount": 7500.0},
    {"employee": "Aditi Sharma", "date": "2026-08-02", "vendor": "Starbucks", "description": "Breakfast meeting", "amount": 650.0},
    {"employee": "Aditi Sharma", "date": "2026-08-04", "vendor": "Ola Cabs", "description": "City transit", "amount": 450.0},

    # Rohan Mehta - July 2026 (Contains multiple anomalies)
    {"employee": "Rohan Mehta", "date": "2026-07-08", "vendor": "SpiceJet", "description": "Flight BLR-DEL", "amount": 5200.0},
    {"employee": "Rohan Mehta", "date": "2026-07-09", "vendor": "The Leela", "description": "1 night lodging", "amount": 6100.0},
    {"employee": "Rohan Mehta", "date": "2026-07-10", "vendor": "Bukhara", "description": "Client dinner", "amount": None}, # Anomaly
    {"employee": "Rohan Mehta", "date": "2026-07-11", "vendor": "Meru Cabs", "description": "Hotel to Airport", "amount": None}, # Anomaly

    # Ankit Prasad - July 2026 (Clean run)
    {"employee": "Ankit Prasad", "date": "2026-07-14", "vendor": "Air India", "description": "Flight DEL-HYD", "amount": 6300.0},
    {"employee": "Ankit Prasad", "date": "2026-07-15", "vendor": "ITC Kakatiya", "description": "2 nights stay", "amount": 14000.0},
    {"employee": "Ankit Prasad", "date": "2026-07-16", "vendor": "Paradise Biryani", "description": "Team lunch", "amount": 2100.0},
    {"employee": "Ankit Prasad", "date": "2026-07-17", "vendor": "Hyderabad Metro", "description": "Transit card recharge", "amount": 500.0}
]


def get_mock_expenses(employee_name: str, target_month: str) -> list:
    """
    Simulated "Retrieve expenses" + "Filter date" tool.

    employee_name: matched case-insensitively against MOCK_RECEIPTS.
    target_month:  a "YYYY-MM" string, e.g. "2026-07".

    Doing the employee + month filtering here, in plain Python, rather
    than asking the LLM to filter a list, keeps retrieval 100%
    deterministic — exactly the kind of task a normal function should do,
    not something delegated to a language model.
    """
    return [
        receipt
        for receipt in MOCK_RECEIPTS
        if receipt["employee"].strip().lower() == employee_name.strip().lower()
        and receipt["date"].startswith(target_month)
    ]


# ---------------------------------------------------------------------------
# 2. PDF GENERATION
# ---------------------------------------------------------------------------
def generate_pdf(
    employee_name: str,
    target_month: str,
    category_totals: dict,
    grand_total: float,
    discrepancy_note: str = "",
    output_dir: str = ".",
) -> str:
    """
    Renders the finished expense report as a real, physical PDF file using
    fpdf2 (the maintained fork of the original `fpdf` library — it keeps
    the same `from fpdf import FPDF` import for backward compatibility).

    Returns the absolute path of the generated PDF, which the Compiler &
    Delivery node then hands to send_expense_email() as an attachment and
    to app.py for the Streamlit download button.
    """
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
        
        # Sanitize LLM text to prevent FPDF Unicode crashes
        clean_note = (
            discrepancy_note.replace(" ", " ")
            .replace("—", "-")
            .replace("’", "'")
            .replace("“", '"')
            .replace("”", '"')
        )
        # Strip any remaining unsupported characters silently
        clean_note = clean_note.encode('latin-1', 'ignore').decode('latin-1')
        
        pdf.multi_cell(0, 6, clean_note)

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
        import time
        current_time = time.strftime("%H:%M:%S")
        message["Subject"] = f"Travel Expense Report — {employee_name} ({target_month}) [Run: {current_time}]"

        body = (
            f"Hi Finance team,\n\n"
            f"Please find attached the travel expense report for "
            f"{employee_name}, covering {target_month}.\n\n"
            f"Regards,\n{employee_name} (sent by Autonomous Expense Agent)"
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
