from __future__ import annotations
import json, os, sqlite3
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("EXPENSE_DB_PATH", ROOT / "data" / "expenses.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

EMPLOYEES = [
    ("Aditi Sharma", "Product Engineering", "aditi.sharma@example.com"),
    ("Rohan Mehta", "Sales", "rohan.mehta@example.com"),
    ("Priya Nair", "Finance", "priya.nair@example.com"),
    ("Arjun Desai", "Customer Success", "arjun.desai@example.com"),
    ("Meera Iyer", "Product Engineering", "meera.iyer@example.com"),
    ("Kabir Shah", "Marketing", "kabir.shah@example.com"),
]
VENDORS = [
    ("IndiGo Airlines", "Flight booking", "Flights", 4500),
    ("Cafe Coffee Day", "Client lunch", "Meals", 850),
    ("Taj Hotels", "Business accommodation", "Lodging", 6200),
    ("Uber", "Airport transfer", "Transport", 620),
    ("Domino's Pizza", "Team dinner", "Meals", 1200),
    ("Ola Cabs", "Client visit cab", "Transport", 540),
    ("Vistara", "Business flight", "Flights", 7100),
    ("The Leela", "Hotel stay", "Lodging", 6100),
    ("Blue Tokai", "Client coffee", "Meals", 480),
    ("Airport Express", "Airport transit", "Transport", 350),
    ("Air India", "Return flight", "Flights", 6800),
    ("Lemon Tree Hotels", "Business accommodation", "Lodging", 3900),
    ("Barista", "Team meeting refreshments", "Meals", 620),
    ("Meru Cabs", "Office to client site", "Transport", 430),
]
def connect():
    conn=sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory=sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS employees(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, department TEXT NOT NULL, email TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS expenses(
          id INTEGER PRIMARY KEY AUTOINCREMENT, employee_name TEXT NOT NULL, expense_date TEXT NOT NULL,
          vendor TEXT NOT NULL, description TEXT NOT NULL, category TEXT NOT NULL,
          amount REAL, receipt_id TEXT, review_note TEXT, updated_at TEXT,
          FOREIGN KEY(employee_name) REFERENCES employees(name)
        );
        CREATE TABLE IF NOT EXISTS reports(
          id INTEGER PRIMARY KEY AUTOINCREMENT, employee_name TEXT NOT NULL, target_month TEXT NOT NULL,
          version INTEGER NOT NULL, pdf_path TEXT NOT NULL, grand_total REAL NOT NULL,
          needs_review INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'Generated',
          email_status TEXT NOT NULL DEFAULT 'Not sent', created_at TEXT NOT NULL,
          UNIQUE(employee_name,target_month,version)
        );
        """)
        if c.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0:
            c.executemany("INSERT INTO employees(name,department,email) VALUES(?,?,?)", EMPLOYEES)
        if c.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] == 0:
            rows=[]
            # Generate 6 employees x 9 months x 4 expenses = 216 records.
            months=[(2026, m) for m in range(1,7)] + [(2026,7),(2026,8),(2026,9)]
            eid=1000
            for emp_idx,(employee,dept,email) in enumerate(EMPLOYEES):
                for year,month in months:
                    for j in range(4):
                        vendor,desc,cat,base=VENDORS[(emp_idx*2+j+month)%len(VENDORS)]
                        day=min(3+j*5,28)
                        amount=round(base*(0.82 + ((emp_idx+j+month)%7)*0.07),2)
                        receipt=f"RCPT-{eid}"
                        # Predictable exception cases for demonstrations.
                        if employee=="Aditi Sharma" and month==7 and j==2:
                            amount=None
                        if employee=="Aditi Sharma" and month==7 and j==3:
                            receipt=None
                        if employee=="Rohan Mehta" and month==6 and j==1:
                            receipt=None
                        rows.append((employee,f"{year}-{month:02d}-{day:02d}",vendor,desc,cat,amount,receipt,None,None))
                        eid+=1
            c.executemany("""INSERT INTO expenses(employee_name,expense_date,vendor,description,category,amount,receipt_id,review_note,updated_at)
                             VALUES(?,?,?,?,?,?,?,?,?)""",rows)
init_db()

def employees():
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT name,department,email FROM employees ORDER BY name")]

def get_expenses(employee_name, target_month):
    with connect() as c:
        rows=c.execute("""SELECT id,employee_name AS employee, expense_date AS date,vendor,description,
                          category,amount,receipt_id,review_note FROM expenses
                          WHERE lower(employee_name)=lower(?) AND substr(expense_date,1,7)=?
                          ORDER BY expense_date,id""",(employee_name,target_month)).fetchall()
        return [dict(r) for r in rows]

def available_months(employee_name):
    with connect() as c:
        return [r[0] for r in c.execute("""SELECT DISTINCT substr(expense_date,1,7) FROM expenses
          WHERE lower(employee_name)=lower(?) ORDER BY 1 DESC""",(employee_name,))]

def update_expenses(changes):
    updated=[]
    with connect() as c:
        for item in changes:
            row=c.execute("SELECT id FROM expenses WHERE id=?",(int(item["id"]),)).fetchone()
            if not row: raise ValueError(f"Expense record {item['id']} does not exist.")
            amount=item.get("amount")
            if amount is not None and float(amount) <= 0: raise ValueError("Amounts must be greater than zero.")
            c.execute("""UPDATE expenses SET amount=?, receipt_id=?, review_note=?, updated_at=datetime('now')
                         WHERE id=?""",(None if amount in ("",None) else float(amount),
                         item.get("receipt_id") or None,item.get("review_note") or None,int(item["id"])))
            updated.append(int(item["id"]))
    return updated

def save_report(employee, month, pdf_path, total, needs_review):
    with connect() as c:
        version=c.execute("SELECT COALESCE(MAX(version),0)+1 FROM reports WHERE employee_name=? AND target_month=?",
                          (employee,month)).fetchone()[0]
        cur=c.execute("""INSERT INTO reports(employee_name,target_month,version,pdf_path,grand_total,needs_review,status,email_status,created_at)
                         VALUES(?,?,?,?,?,?,?,?,datetime('now'))""",
                      (employee,month,version,pdf_path,total,int(needs_review),
                       "Needs Review" if needs_review else "Ready","Not sent"))
        return dict(id=cur.lastrowid,employee_name=employee,target_month=month,version=version,pdf_path=pdf_path,
                    grand_total=total,needs_review=bool(needs_review),status="Needs Review" if needs_review else "Ready",
                    email_status="Not sent")

def list_reports(employee=None):
    with connect() as c:
        if employee:
            rows=c.execute("SELECT * FROM reports WHERE lower(employee_name)=lower(?) ORDER BY created_at DESC,id DESC",(employee,)).fetchall()
        else:
            rows=c.execute("SELECT * FROM reports ORDER BY created_at DESC,id DESC").fetchall()
        return [dict(r) for r in rows]

def get_report(report_id):
    with connect() as c:
        r=c.execute("SELECT * FROM reports WHERE id=?",(int(report_id),)).fetchone()
        return dict(r) if r else None

def set_email_status(report_id,status):
    with connect() as c:
        c.execute("UPDATE reports SET email_status=? WHERE id=?",(status,int(report_id)))
