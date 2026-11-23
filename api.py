from __future__ import annotations
import os
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import analysis_agent
import database
from expense_agent import expense_graph, make_initial_state
from tools import send_confirmed_email

ROOT=Path(__file__).resolve().parent
app=FastAPI(title="Autonomous Expense Report Agent API",version="1.0")
app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:5173","http://127.0.0.1:5173"],allow_credentials=True,allow_methods=["*"],allow_headers=["*"])

class GenerateRequest(BaseModel):
    employee_name: str = Field(min_length=2)
    target_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    receiver_email: str = "finance@example.com"
    demo_mode: bool = True

class ExpenseChange(BaseModel):
    id: int
    amount: Optional[float] = None
    receipt_id: Optional[str] = None
    review_note: Optional[str] = None

class ReviewRequest(BaseModel):
    changes: list[ExpenseChange]

class SendRequest(BaseModel):
    confirmed: bool
    receiver_email: str = "anindita.sanjagiri@gmail.com"
    demo_mode: bool = False

class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    employee_name: str
    target_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    history: list[dict] = []

def report_safe(r):
    x=dict(r); x.pop("pdf_path",None); return x

@app.get("/api/health")
def health(): return {"status":"ok"}

@app.get("/api/employees")
def get_employees(): return database.employees()

@app.get("/api/expenses")
def expenses(employee_name: str, target_month: str):
    return {"employee_name":employee_name,"target_month":target_month,
            "records":database.get_expenses(employee_name,target_month),
            "available_months":database.available_months(employee_name)}

@app.get("/api/dashboard")
def dashboard(employee_name: str, target_month: str):
    records=database.get_expenses(employee_name,target_month)
    if not records: return {"employee_name":employee_name,"target_month":target_month,"count":0,"total_amount":0,"category_totals":{},"flagged":[]}
    req={"sender":"API","receiver":analysis_agent.NAME,"task":"analyze_expenses","expenses":records}
    findings=analysis_agent.handle(req,use_llm=False)["findings"]
    return {"employee_name":employee_name,"target_month":target_month,**findings}

@app.post("/api/expenses/review")
def review(payload: ReviewRequest):
    try:
        updated=database.update_expenses([x.model_dump() for x in payload.changes])
    except (ValueError,KeyError) as e:
        raise HTTPException(400,str(e))
    return {"updated_ids":updated,"message":"Changes saved."}

@app.get("/api/reports")
def reports(employee_name: Optional[str]=None):
    return [report_safe(r) for r in database.list_reports(employee_name)]

@app.post("/api/reports/generate")
def generate(payload: GenerateRequest):
    records=database.get_expenses(payload.employee_name,payload.target_month)
    if not records:
        raise HTTPException(404,"There is no expense data for this employee for the selected month. No report was generated.")
    # Generate report via the original LangGraph workflow. It never sends automatically.
    try:
        state=expense_graph.invoke(make_initial_state(payload.employee_name,payload.target_month,payload.receiver_email,payload.demo_mode))
    except Exception as e:
        raise HTTPException(500,f"Report generation failed: {e}")
    if not state.get("pdf_path") or not Path(state["pdf_path"]).exists():
        raise HTTPException(500,"The report workflow did not create a PDF.")
    needs_review = any((r.get("amount") is None) or not r.get("receipt_id") for r in records)
    saved=database.save_report(payload.employee_name,payload.target_month,state["pdf_path"],state["grand_total"],needs_review)
    saved["download_url"]=f"/api/reports/{saved['id']}/download"
    saved["message"]="Report generated. Email has not been sent."
    return saved

@app.get("/api/reports/{report_id}/download")
def download(report_id:int):
    r=database.get_report(report_id)
    if not r: raise HTTPException(404,"Generated report not found.")
    path=Path(r["pdf_path"])
    if not path.exists(): raise HTTPException(404,"PDF file is missing from disk.")
    return FileResponse(path,media_type="application/pdf",filename=path.name)

@app.post("/api/reports/{report_id}/send")
def send(report_id:int,payload:SendRequest):
    r=database.get_report(report_id)
    if not r: raise HTTPException(404,"No generated report exists for this employee and month.")
    if not payload.confirmed: return {"status":"cancelled","message":"Email was not sent."}
    employee=next((x for x in database.employees() if x["name"].lower()==r["employee_name"].lower()),None)
    receiver=payload.receiver_email.strip() or os.getenv("RECEIVER_EMAIL", "anindita.sanjagiri@gmail.com")
    if "@" not in receiver:
        raise HTTPException(400, "Enter a valid recipient email address.")
    result=send_confirmed_email(receiver,r["pdf_path"],r["employee_name"],r["target_month"],payload.demo_mode)
    status=result.get("status","failed")
    database.set_email_status(report_id, "Sent" if status=="sent" else ("Simulated" if status=="simulated" else "Send Failed"))
    return {"status":status,"message":result["message"]}

@app.post("/api/chat")
def chat(payload: ChatRequest):
    """Interpret requests with the LLM; execute only validated, confirmed actions."""
    import json, re, calendar
    from datetime import datetime
    from expense_agent import llm

    valid_employees = database.employees()
    if not any(e["name"].lower() == payload.employee_name.lower() for e in valid_employees):
        raise HTTPException(400, "Please select a valid employee.")

    # The frontend includes prior chat turns. If the assistant just asked for
    # confirmation to generate a report and the user replied yes, execute that
    # exact pending action instead of asking the LLM to classify "yes" again.
    history = payload.history or []
    # Confirmation prompts can be followed by an unrelated assistant response
    # (for example, a missing-information check). Looking only at the latest
    # assistant message loses the pending action and traps the user in a loop.
    # Search recent history for an outstanding report-generation prompt.
    assistant_messages = [str(m.get("text", "")) for m in history if m.get("role") == "assistant"]
    confirmation_prompt_seen = any(
        any(phrase in message.lower() for phrase in (
            "shall i generate and save this report", "reply yes to generate it",
            "would you like me to generate", "confirm that you would like to generate",
            "generate and save this report now", "ready to generate the report"
        ))
        for message in assistant_messages[-8:]
    )
    low_message = payload.message.strip().lower()
    affirmative = low_message in {
        "yes", "y", "yes please", "confirm", "confirmed", "go ahead",
        "do it", "proceed", "generate it", "generate the report", "yes generate it",
        "yes generate it please", "okay", "ok", "sure"
    } or (
        ("generate" in low_message and "report" in low_message)
        and any(word in low_message for word in ("yes", "okay", "ok", "go ahead", "please", "proceed"))
    )
    # If a report confirmation is already pending, an explicit renewed request
    # to generate the report also counts as confirmation rather than restarting
    # the same confirmation loop.
    explicit_generate_after_prompt = confirmation_prompt_seen and "generate" in low_message and "report" in low_message
    pending_generation = confirmation_prompt_seen and (affirmative or explicit_generate_after_prompt)

    if pending_generation:
        emp, mon = payload.employee_name, payload.target_month
        records = database.get_expenses(emp, mon)
        if not records:
            return {"intent": "question", "message": f"There are no expense records for {emp} in {mon}. I did not generate a report.", "employee_name": emp, "target_month": mon}
        try:
            state = expense_graph.invoke(make_initial_state(emp, mon, os.getenv("RECEIVER_EMAIL", "anindita.sanjagiri@gmail.com"), False))
            pdf_path = state.get("pdf_path")
            if not pdf_path or not Path(pdf_path).exists():
                raise RuntimeError("The report workflow did not create a PDF.")
            needs_review = any(r.get("amount") is None or not r.get("receipt_id") for r in records)
            saved = database.save_report(emp, mon, pdf_path, state.get("grand_total", 0), needs_review)
            saved["download_url"] = f"/api/reports/{saved['id']}/download"
            return {"intent": "report_generated", "message": f"Done — report v{saved['version']} for {emp}, {mon} has been generated and saved. " + ("It is marked Needs Review because supporting information is missing. " if needs_review else "") + "No email has been sent.", "employee_name": emp, "target_month": mon, "report_id": saved["id"], "report": report_safe(saved), "download_url": f"/api/reports/{saved['id']}/download"}
        except Exception as exc:
            raise HTTPException(500, f"I couldn't generate the report: {exc}")

    months = database.available_months(payload.employee_name)
    prompt = (
        "You are ExpenseFlow, an enterprise expense-report assistant. Interpret the latest message using the conversation history. "
        "Return ONLY JSON with keys intent, employee_name, target_month, reply. intent must be one of generate_report, send_report, missing_info, category_breakdown, question. "
        f"Selected employee: {payload.employee_name}. Selected month: {payload.target_month}. Months with records: {months}. "
        f"Recent conversation: {json.dumps(history[-8:], ensure_ascii=False)}. Latest user message: {payload.message!r}. "
        "For an explicit month, resolve it to YYYY-MM; for no month, use the selected month and say which month you mean. "
        "For report generation, ask the user to confirm employee and month before generating. Never claim an action completed. "
        "Never invent records. Email must never be sent without a separate final confirmation."
    )
    try:
        response = llm.invoke(prompt)
        raw = response.content if isinstance(response.content, str) else str(response.content)
        match = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(match.group(0)) if match else {}
        if not isinstance(data, dict): data = {}
    except Exception as exc:
        # Do not silently fake LLM success. Safe fallback handles only obvious
        # intents and clearly discloses that LLM classification was unavailable.
        low = payload.message.lower()
        intent = ("generate_report" if "generate" in low and "report" in low else
                  "send_report" if "send" in low and "report" in low else
                  "missing_info" if "missing" in low or "receipt" in low else
                  "category_breakdown" if "category" in low or "breakdown" in low else "question")
        data = {"intent": intent, "employee_name": payload.employee_name, "target_month": payload.target_month,
                "reply": f"I couldn't reach the LLM ({type(exc).__name__}). I can still help with a clearly specified request; please verify the selected month."}

    # Keep the LLM in the interpretation loop, but protect an explicit report
    # request from being downgraded to a missing-info response. Python validates
    # the selected context and controls the actual generation tool call.
    explicit_generate = bool(re.search(
        r"\b(generate|create|prepare|make)\b.{0,45}\breport\b|\breport\b.{0,45}\b(generate|create|prepare|make)\b",
        low_message,
    )) and not re.search(r"\b(don't|do not|never|cancel|stop)\b", low_message)
    if explicit_generate:
        data["intent"] = "generate_report"
        month_match = re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(20\d{2})\b", low_message)
        if month_match:
            month_number = list(calendar.month_name).index(month_match.group(1).capitalize())
            data["target_month"] = f"{month_match.group(2)}-{month_number:02d}"

    intent = data.get("intent", "question")
    emp = data.get("employee_name") or payload.employee_name
    mon = str(data.get("target_month") or payload.target_month)
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", mon): mon = payload.target_month
    if not any(e["name"].lower() == str(emp).lower() for e in valid_employees): emp = payload.employee_name
    recs = database.get_expenses(emp, mon)

    if intent == "generate_report":
        if not recs:
            return {"intent":"question","message":f"No expense records exist for {emp} in {mon}. I won't generate a report without matching data.","employee_name":emp,"target_month":mon}
        return {"intent":"generate_report","message":f"I found {len(recs)} expense records for {emp} in {calendar.month_name[int(mon[5:])]} {mon[:4]}. Shall I generate and save this report now? Reply yes to generate it. Missing information will be flagged, not block the report.","employee_name":emp,"target_month":mon,"requires_confirmation":True}
    if intent == "send_report":
        found = next((r for r in database.list_reports(emp) if r["target_month"] == mon), None)
        if not found: return {"intent":"question","message":f"There is no saved report for {emp}, {mon}. Would you like me to generate it first?","employee_name":emp,"target_month":mon}
        return {"intent":"send_report","message":f"I found report v{found['version']} for {emp}, {mon}. Open the final email confirmation to verify the recipient and send it.","report_id":found["id"],"employee_name":emp,"target_month":mon}
    if intent == "missing_info":
        flagged = [r for r in recs if r.get("amount") is None or not r.get("receipt_id")]
        details = "; ".join(f"{r['vendor']} (" + ", ".join(x for x,yes in [("missing amount",r.get('amount') is None),("missing receipt",not r.get('receipt_id'))] if yes) + ")" for r in flagged)
        return {"intent":intent,"message":("Needs attention: "+details) if flagged else "No missing amounts or receipt IDs were found.","employee_name":emp,"target_month":mon}
    if intent == "category_breakdown":
        totals = {}
        for r in recs:
            if r.get("amount") is not None: totals[r["category"]] = totals.get(r["category"], 0) + r["amount"]
        return {"intent":intent,"message":f"Category breakdown for {mon}: "+("; ".join(f"{k}: ₹{v:,.2f}" for k,v in totals.items()) if totals else "no recorded expenses."),"employee_name":emp,"target_month":mon}
    return {"intent":"question","message":str(data.get("reply") or "I can help with expenses, report generation, and sending an existing report. Which employee and month should I use?"),"employee_name":emp,"target_month":mon}
