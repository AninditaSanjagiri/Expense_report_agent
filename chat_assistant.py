"""
chat_assistant.py  (Jury 2 · Unit III — Memory, Tools, Environment Interaction)
-------------------------------------------------------------------------------
The Conversational Expense Assistant.

How it works (one LangGraph loop, same idea as the original workflow):

    START -> assistant --(has tool_calls?)--> tools --> assistant ... --> END
                 |  no tool calls: final answer
                 +--------------------------------------------------> END

  * "assistant" node = the BRAIN. Live mode: ChatGroq with tools bound
    (real LLM tool calling). Demo mode: a small rule-based brain that emits
    the SAME kind of tool calls offline, so the demo needs no internet/key.
  * "tools" node     = runs the Python tools the brain asked for.
  * Conversation MEMORY = `session` dict (kept in st.session_state): the
    message history, the selected month, the retrieved expenses, the latest
    findings and the generated report. Follow-up questions reuse it.

Safety rules enforced in CODE (not just in a prompt):
  * All numbers come from Python tools / the Expense Analysis Agent.
  * No LLM-callable tool can send email. Sending happens only in
    confirm_and_send(), after the employee confirms.
  * Tool failures are returned as errors and reported honestly.
"""

import calendar
import json
import re
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages

import analysis_agent
from expense_agent import llm, expense_graph, make_initial_state
from tools import get_mock_expenses, get_available_months, send_confirmed_email

CATEGORY_WORDS = {"flight": "Flights", "meal": "Meals", "food": "Meals", "lodging": "Lodging",
                  "hotel": "Lodging", "transport": "Transport", "cab": "Transport"}
FIELD_LABEL = {"amount": "amount", "receipt_id": "receipt / supporting document",
               "vendor": "vendor", "description": "description"}
# Which findings fields each kind of question is answered from (shown in the UI).
INTENT_FIELDS = {
    "report": ["total_amount", "category_totals", "flagged_expenses"],
    "finance": ["total_amount", "flagged_expenses"],
    "missing": ["flagged_expenses", "missing_amount_count", "missing_support_count"],
    "breakdown": ["category_totals", "total_amount"],
    "highest": ["highest_category", "total_amount"],
    "category": ["items_by_category"],
    "total": ["total_amount", "total_expenses"],
    "advice": ["flagged_expenses"],
}


def rs(x: float) -> str:
    return f"₹{x:,.2f}"


def plural(n: int) -> str:
    return f"{n} expense{'s' if n != 1 else ''}"


# ===========================================================================
# 1. SESSION MEMORY
# ===========================================================================
def new_session(employee, month, receiver, demo_mode) -> dict:
    """Everything the assistant remembers during one browser session."""
    return {
        "employee": employee, "month": month, "receiver": receiver, "demo_mode": demo_mode,
        "ui_context": (employee, month, receiver),
        "messages": [],            # LangChain message history (the conversation memory)
        "expenses": None,          # last retrieved records (None = not fetched yet)
        "expenses_month": None,
        "findings": None,          # last findings from the Expense Analysis Agent
        "report": None,            # last generated report (final graph state)
        "awaiting_email": False,   # True = report prepared, waiting for confirmation
        "email_result": None,
        "last_intent": "", "last_category": "",
        "collab_log": [],          # agent-to-agent exchanges shown in the UI
        "agent_called": False,
    }


def sync_context(session, employee, month, receiver, demo_mode):
    """Copy changed top-bar inputs into the session; reset data if the employee changed."""
    session["demo_mode"] = demo_mode
    session["receiver"] = receiver
    if session["ui_context"] != (employee, month, receiver):
        if session["ui_context"][:2] != (employee, month):
            session.update(month=month, expenses=None, expenses_month=None, findings=None,
                           report=None, awaiting_email=False, email_result=None)
        session["employee"] = employee
        session["ui_context"] = (employee, month, receiver)


def normalize_month(text: str, session) -> str:
    """'July' / 'July 2026' / '2026-07' -> '2026-07' ('' if no month found)."""
    m = re.search(r"\b(\d{4})-(\d{2})\b", text)
    if m:
        return m.group(0)
    year = session["month"][:4]
    for num in range(1, 13):
        name = calendar.month_name[num].lower()
        pattern = rf"\b{name}\b" if name != "may" else r"\b(?:for|in|of|during)\s+may\b"
        if re.search(pattern, text.lower()):
            return f"{year}-{num:02d}"
    return ""


def trim_history(messages, keep_turns=6):
    """Keep only the last few user turns so prompts stay small (cuts at a user message)."""
    idx = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    return messages[idx[-keep_turns]:] if len(idx) > keep_turns else messages


# ===========================================================================
# 2. TOOLS (plain Python; the brain decides when to call them)
# ===========================================================================
def build_tools(session):
    def _fetch(month: str = "") -> dict:
        month = normalize_month(month, session) or session["month"]
        records = get_mock_expenses(session["employee"], month)
        if month != session["expenses_month"]:           # new month -> old results are stale
            session.update(findings=None, report=None, awaiting_email=False, email_result=None)
        session.update(month=month, expenses=records, expenses_month=month)
        out = {"employee": session["employee"], "month": month, "count": len(records), "records": records}
        if not records:
            out["available_months"] = get_available_months(session["employee"])
        return out

    @tool
    def get_expenses(month: str = "") -> dict:
        """Retrieve the employee's raw expense records for a month ('YYYY-MM' or a month name).
        Leave month empty to use the month currently selected."""
        return _fetch(month)

    @tool
    def analyze_expenses() -> dict:
        """Ask the separate Expense Analysis Agent to analyse the retrieved expenses. Returns
        structured findings: totals, category breakdown, highest category, missing information."""
        if session["expenses"] is None:
            _fetch()
        if not session["expenses"]:
            return {"error": "No expense records for this employee/month, nothing to analyse."}
        request = {"sender": "Chat Assistant", "receiver": analysis_agent.NAME,
                   "task": "analyze_expenses", "expenses": session["expenses"]}
        response = analysis_agent.handle(request, use_llm=not session["demo_mode"])
        session["findings"] = response["findings"]
        session["agent_called"] = True
        return response["findings"]

    @tool
    def generate_report() -> dict:
        """Generate the full expense report (Markdown + PDF) by running the existing report
        workflow. Does NOT send any email."""
        if session["expenses"] is None:
            _fetch()
        if not session["expenses"]:
            return {"generated": False, "reason": "No expense records for this employee/month."}
        try:
            state = expense_graph.invoke(make_initial_state(
                session["employee"], session["month"], session["receiver"], session["demo_mode"]))
        except Exception as exc:  # noqa: BLE001 - report the failure honestly
            return {"generated": False, "reason": f"Report workflow failed: {exc}"}
        session.update(report=state, awaiting_email=False, email_result=None)
        return {"generated": True, "pdf_file": state["pdf_path"].split("/")[-1],
                "grand_total": state["grand_total"], "has_missing_data": state["has_missing_data"],
                "email_sent": False}

    @tool
    def prepare_finance_email() -> dict:
        """Prepare (NOT send) the email to finance. Needs a generated report. The employee must
        confirm separately; this tool cannot send email."""
        if not session["report"]:
            return {"ready": False, "reason": "No report has been generated yet."}
        session["awaiting_email"] = True
        return {"ready": True, "receiver": session["report"]["receiver_email"],
                "attachment": session["report"]["pdf_path"].split("/")[-1], "email_sent": False}

    return [get_expenses, analyze_expenses, generate_report, prepare_finance_email]


# ---- confirmation step: plain Python, never reachable by the LLM -----------
def confirm_and_send(session) -> dict:
    rep = session.get("report")
    if not session["awaiting_email"] or not rep:
        return {"success": False, "status": "failed", "message": "Nothing is waiting for confirmation."}
    result = send_confirmed_email(rep["receiver_email"], rep["pdf_path"], rep["employee_name"],
                                  rep["target_month"], demo_mode=session["demo_mode"])
    session.update(awaiting_email=False, email_result=result)
    return result


def cancel_email(session):
    session.update(awaiting_email=False, email_result=None)


def email_button(session, confirm: bool) -> str:
    """Handles the Confirm / Cancel buttons in the UI and records it in the chat memory."""
    if confirm:
        reply, note = describe_email_result(confirm_and_send(session)), "[Employee pressed Confirm & send]"
    else:
        cancel_email(session)
        reply, note = "Okay - the email was **not** sent.", "[Employee pressed Cancel]"
    session["messages"] += [HumanMessage(content=note), AIMessage(content=reply)]
    return reply


def describe_email_result(r: dict) -> str:
    icon = {"sent": "✅", "simulated": "🧪", "failed": "❌"}.get(r.get("status"), "ℹ️")
    return f"{icon} {r['message']}"


# ===========================================================================
# 3. BRAINS: live LLM brain and offline demo brain (both return an AIMessage)
# ===========================================================================
def llm_brain(messages, session, tools):
    system = (
        f"You are an enterprise expense assistant for {session['employee']}. Selected month: "
        f"{session['month']} (YYYY-MM; if the user names a month like 'July', use the same year).\n"
        "Rules: use tools for all data; NEVER invent amounts, dates or vendors. analyze_expenses asks the "
        "separate Expense Analysis Agent - base your answers on its findings. To prepare the report for "
        "finance call generate_report then prepare_finance_email. You can NOT send email: say it has NOT "
        "been sent and the employee must confirm in the app. If a tool returns an error or no records, "
        "say so honestly and ask a clarifying question (e.g. which month). Never claim success after a "
        "failure. Be concise, use bullet lists, currency in ₹."
    )
    try:
        return llm.bind_tools(tools).invoke([SystemMessage(content=system)] + messages)
    except Exception as exc:  # noqa: BLE001
        return AIMessage(content=f"⚠️ I couldn't reach the language model ({type(exc).__name__}). "
                                 "Switch on **Demo mode** in the top bar to use the offline assistant. "
                                 "No action was taken.")


def detect_intent(text: str) -> str:
    t = text.lower()
    has = lambda *words: any(re.search(rf"\b{w}", t) for w in words)
    if has("finance", "send", "email", "submit"):
        return "finance"
    if has("report", "generate"):
        return "report"
    if has("missing", "support", "incomplete", "receipt", "flag", "anomal"):
        return "missing"
    if has("highest", "biggest", "largest", "most", "top"):
        return "highest"
    if any(w in t for w in CATEGORY_WORDS):
        return "category"
    if has("break", "categor", "split"):
        return "breakdown"
    if has("total", "how much", "spent", "spend"):
        return "total"
    if has("show", "list", "expense", "record"):
        return "list"
    if has("what should i do", "how do i fix", "fix", "resolve", "next step"):
        return "advice"
    if re.search(r"\b(hi|hello|hey|help)\b", t) or "what can you" in t:
        return "help"
    return "unknown"


def demo_brain(messages, session):
    """Offline stand-in for the LLM: picks tools by keywords + session memory."""
    last = messages[-1]
    if isinstance(last, ToolMessage):                      # tools just ran -> write the answer
        results = {}
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                break
            if isinstance(m, ToolMessage):
                results[m.name] = json.loads(m.content)
        return AIMessage(content=compose_answer(session, results))

    text = last.content
    intent = detect_intent(text)
    session["last_intent"] = intent
    for word, cat in CATEGORY_WORDS.items():
        if word in text.lower():
            session["last_category"] = cat
    if intent in ("help", "unknown"):
        return AIMessage(content=compose_answer(session, {}))

    month = normalize_month(text, session)
    target = month or session["month"]
    fetched = session["expenses"] is None or target != session["expenses_month"]
    calls = []
    add = lambda name, **a: calls.append({"name": name, "args": a, "id": f"demo-{len(messages)}-{len(calls)}"})
    if fetched:
        add("get_expenses", month=month)
    if fetched or session["findings"] is None:
        add("analyze_expenses")
    report_ready = bool(session["report"]) and session["report"]["target_month"] == target and not fetched
    if intent == "report" or (intent == "finance" and not report_ready):
        add("generate_report")
    if intent == "finance":
        add("prepare_finance_email")
    if not calls:                                          # follow-up answered from memory
        return AIMessage(content=compose_answer(session, {}))
    return AIMessage(content="", tool_calls=calls)


def compose_answer(session, results) -> str:
    """Demo-mode wording. Every number comes from findings / tool results."""
    intent, f = session["last_intent"], session["findings"]
    emp, month = session["employee"], session["month"]
    if intent in ("help", "unknown"):
        lead = "I can help with your expenses." if intent == "help" else "I'm not sure what you'd like me to do."
        return (f"{lead} Try:\n- *Generate my travel expense report for July*\n- *Which expenses are missing "
                "amounts or supporting information?*\n- *Break down my travel spending by category*\n"
                "- *Prepare the report for the finance team*")
    if session["expenses"] == []:
        months = ", ".join(get_available_months(emp)) or "none"
        return (f"I couldn't find any expenses for **{emp}** in **{month}**. I only have receipts for: "
                f"{months}. Which month should I use?")
    errors = [r["error"] for r in results.values() if isinstance(r, dict) and "error" in r]
    if errors:
        return f"⚠️ {errors[0]} Nothing was completed."
    if f is None:
        return "I don't have analysed expenses yet. Tell me a month (e.g. *July*) and I'll pull the records."

    flagged = f["flagged_expenses"]
    cats = "\n".join(f"- {c}: **{rs(v)}**" for c, v in f["category_totals"].items())
    flag_lines = "\n".join(
        f"- **{x['vendor']}** ({x['date']}, {x['description']}): missing "
        f"**{', '.join(FIELD_LABEL[m] for m in x['missing'])}**" for x in flagged)

    if intent in ("report", "finance"):
        rep = results.get("generate_report")
        if rep is not None and not rep.get("generated"):
            return f"⚠️ I couldn't generate the report: {rep.get('reason')}. Nothing was created or sent."
        head = (f"**Report ready for {emp} - {month}.** {plural(f['total_expenses'])}, total "
                f"**{rs(f['total_amount'])}** (receipts without an amount are excluded).\n{cats}\n")
        if flagged:
            head += f"\n⚠️ {len(flagged)} item(s) need review:\n{flag_lines}\n"
        if intent == "report":
            return head + "\n📄 PDF created. **No email has been sent.** Want me to prepare it for finance?"
        prep = results.get("prepare_finance_email")
        if not prep or not prep.get("ready"):
            return f"⚠️ I couldn't prepare the email: {(prep or {}).get('reason', 'unknown error')}."
        return (head + f"\nIt is ready to go to **{prep['receiver']}** with `{prep['attachment']}` attached.\n\n"
                "🔒 **I have not sent anything.** Type **yes** to confirm, or press the Confirm button below.")
    if intent == "missing":
        if not flagged:
            return f"✅ All {f['total_expenses']} expenses have an amount and a receipt reference."
        return (f"{len(flagged)} of {f['total_expenses']} expenses have missing information "
                f"({f['missing_amount_count']} missing amount, {f['missing_support_count']} missing "
                f"supporting document):\n{flag_lines}\n\nThe total of **{rs(f['total_amount'])}** "
                "excludes items with no amount.")
    if intent == "breakdown":
        return f"Spending by category for **{month}**:\n{cats}\n\nTotal: **{rs(f['total_amount'])}**."
    if intent == "total":
        extra = f" ({f['missing_amount_count']} without an amount are excluded)" if f["missing_amount_count"] else ""
        return f"Total for **{month}**: **{rs(f['total_amount'])}** across {plural(f['total_expenses'])}{extra}."
    if intent == "highest":
        h = f["highest_category"]
        return f"Your highest category is **{h['name']}** at **{rs(h['amount'])}** ({h['amount'] / f['total_amount']:.0%} of the total)."
    if intent == "category":
        cat = session["last_category"]
        items = f["items_by_category"].get(cat)
        if not items:
            return f"I don't see any **{cat}** expenses for {month}."
        lines = "\n".join(f"- {i['vendor']} ({i['date']}): {rs(i['amount'])}" for i in items)
        return f"**{cat}** for {month}: **{rs(f['category_totals'][cat])}**\n{lines}"
    if intent == "list":
        recs = session["expenses"]
        lines = "\n".join(f"- {r['date']} · {r['vendor']} ({r['description']}): "
                          f"**{rs(r['amount']) if r['amount'] is not None else 'amount missing'}**" for r in recs)
        return f"Here are your {len(recs)} expense record(s) for **{month}**:\n{lines}"
    if intent == "advice":
        if not flagged:
            return "Nothing needs fixing - every expense is complete."
        return ("To resolve the flagged items:\n1. Get the missing amount / receipt from the vendor or your records.\n"
                "2. Update the record, then ask me to regenerate the report.\n"
                "You can still send it now - the PDF clearly flags these items for finance.")
    return "Done."


# ===========================================================================
# 4. THE LANGGRAPH TOOL-CALLING LOOP
# ===========================================================================
class ChatState(TypedDict):
    # add_messages = reducer that APPENDS new messages to the list (conversation memory)
    messages: Annotated[list, add_messages]


def build_chat_graph(session):
    tools = build_tools(session)
    tools_by_name = {t.name: t for t in tools}

    def assistant_node(state: ChatState) -> dict:
        brain = demo_brain if session["demo_mode"] else (lambda m, s: llm_brain(m, s, tools))
        return {"messages": [brain(state["messages"], session)]}

    def tools_node(state: ChatState) -> dict:
        outputs = []
        for call in state["messages"][-1].tool_calls:
            try:
                result = tools_by_name[call["name"]].invoke(call["args"])
            except Exception as exc:  # noqa: BLE001 - a failing tool becomes an error message
                result = {"error": f"{call['name']} failed: {exc}"}
            outputs.append(ToolMessage(content=json.dumps(result, default=str),
                                       tool_call_id=call["id"], name=call["name"]))
        return {"messages": outputs}

    def should_continue(state: ChatState) -> str:
        return "tools" if getattr(state["messages"][-1], "tool_calls", None) else END

    graph = StateGraph(ChatState)
    graph.add_node("assistant", assistant_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "assistant")
    graph.add_conditional_edges("assistant", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "assistant")      # the cycle: tool results go back to the brain
    return graph.compile()


# ===========================================================================
# 5. ONE CHAT TURN (called by the Streamlit chat page)
# ===========================================================================
CONFIRM = re.compile(r"\s*(yes|y|confirm|send|send it|yes,? send( it)?|go ahead|sure)\s*[.!]*\s*", re.I)
CANCEL = re.compile(r"\s*(no|n|cancel|stop|don'?t send|do not send)\s*[.!]*\s*", re.I)


def chat_turn(session, user_text: str) -> str:
    # Explicit confirmation is handled here in plain Python - the LLM is not involved.
    if session["awaiting_email"] and (CONFIRM.fullmatch(user_text) or CANCEL.fullmatch(user_text)):
        if CONFIRM.fullmatch(user_text):
            reply = describe_email_result(confirm_and_send(session))
        else:
            cancel_email(session)
            reply = "Okay - the email was **not** sent. The report is still available on the Expense Report page."
        session["messages"] += [HumanMessage(content=user_text), AIMessage(content=reply)]
        return reply

    session["agent_called"] = False
    try:
        out = build_chat_graph(session).invoke(
            {"messages": trim_history(session["messages"]) + [HumanMessage(content=user_text)]},
            {"recursion_limit": 12})
        session["messages"] = out["messages"]
        reply = out["messages"][-1].content or "I couldn't produce an answer. No action was taken."
    except Exception as exc:  # noqa: BLE001
        reply = f"⚠️ Something went wrong ({type(exc).__name__}: {exc}). No action was taken."
        session["messages"] += [HumanMessage(content=user_text), AIMessage(content=reply)]

    if session["findings"]:                                # record the agent-to-agent exchange for the UI
        session["collab_log"].append({
            "question": user_text, "reused": not session["agent_called"],
            "findings": session["findings"], "reply": reply, "month": session["month"],
            "used": INTENT_FIELDS.get(session["last_intent"]) if session["demo_mode"] else None})
    return reply
