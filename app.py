"""
app.py
------
Streamlit front-end for the Autonomous Expense Report Agent (Jury 2 edition).

Three sections, chosen with the navigation buttons under the top bar:
  📊 Dashboard         - KPI cards, category chart, status indicators, records
  💬 Chat Assistant    - Unit III: conversational assistant + session memory,
                         with the Unit IV agent-collaboration panel beside it
  📄 Expense Report    - the ORIGINAL workflow: live thought trace (left) and
                         final Markdown report (right), via
                         `.stream(stream_mode="updates")`

Nothing is emailed automatically: sending always needs an explicit confirm.
"""

import os
from datetime import date
from html import escape

import pandas as pd
import streamlit as st

import analysis_agent
from chat_assistant import new_session, sync_context, chat_turn, email_button
from expense_agent import expense_graph, make_initial_state
from tools import get_mock_expenses, get_available_months
from ui_style import CSS, BLUE, TEAL, AMBER, GREEN, metric_card, badge

st.set_page_config(page_title="Expense Agent · Enterprise", page_icon="🧾", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

PAGES = ["📊 Dashboard", "💬 Chat Assistant", "📄 Expense Report"]
SAMPLE_PROMPTS = [
    "Generate my travel expense report for July.",
    "Which expenses are missing amounts or supporting information?",
    "Break down my travel spending by category.",
    "Prepare the report for the finance team.",
]
# Human-readable headers for each node of the ORIGINAL graph (trace display only).
NODE_LABELS = {
    "data_retriever": "Node 1 · Data Retriever",
    "categorizer_calculator": "Node 2 · Categorizer & Calculator",
    "anomaly_handler": "Node 3 · Anomaly Handler (missing-data triggered)",
    "compiler_delivery": "Node 4 · Compiler & Delivery",
}


def rs(x: float) -> str:
    return f"₹{x:,.2f}"


# ---------------------------------------------------------------------------
# HERO + TOP CONTROL BAR
# ---------------------------------------------------------------------------
has_key = bool(os.getenv("GROQ_API_KEY"))
demo_now = st.session_state.get("demo_toggle", not has_key)
mode_badge = badge("● Demo mode · offline", "teal") if demo_now else badge("● Live mode · Groq LLM", "ok")
st.markdown(
    f'<div class="hero"><div><h1>🧾 Autonomous Expense Report Agent</h1>'
    f'<p>Retrieve · categorize · flag missing data · generate a PDF · email finance - with a conversational '
    f'assistant and a dedicated analysis agent.</p></div><div>{mode_badge}</div></div>',
    unsafe_allow_html=True,
)

c1, c2, c3, c4 = st.columns([1.2, 1, 1.5, 1.1], gap="medium")
employee = c1.text_input("Employee name", value="Aditi Sharma")
month_date = c2.date_input("Target month", value=date(2026, 7, 1))
receiver = c3.text_input("Finance email (receiver)", value=os.getenv("RECEIVER_EMAIL", "finance@example.com"))
with c4:
    st.markdown("<div style='height:1.9rem'></div>", unsafe_allow_html=True)
    demo = st.toggle("Demo mode (offline)", value=not has_key, key="demo_toggle",
                     help="Offline rule-based assistant, keyword categorizer, simulated email. "
                          "Turn off to use the Groq LLM (needs GROQ_API_KEY).")
if not demo and not has_key:
    st.warning("Live mode needs GROQ_API_KEY in your .env file - switch Demo mode on to run offline.")

month = month_date.strftime("%Y-%m")

# ---------------------------------------------------------------------------
# SESSION MEMORY (lives in st.session_state for the whole browser session)
# ---------------------------------------------------------------------------
if "chat" not in st.session_state:
    st.session_state.chat = new_session(employee, month, receiver, demo)
    st.session_state.display = []      # chat bubbles shown on screen: {"role", "text"}
    st.session_state.page = PAGES[0]
session = st.session_state.chat
sync_context(session, employee, month, receiver, demo)

# ---------------------------------------------------------------------------
# NAVIGATION
# ---------------------------------------------------------------------------
def go(page: str):
    st.session_state.page = page


st.markdown("<div style='height:.6rem'></div>", unsafe_allow_html=True)
for col, name in zip(st.columns(3, gap="medium"), PAGES):
    col.button(name, key=f"nav_{name}", on_click=go, args=(name,),
               type="primary" if st.session_state.page == name else "secondary")
st.markdown("<div style='height:.8rem'></div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# SHARED HELPERS
# ---------------------------------------------------------------------------
def email_status():
    """(label, badge kind) describing the email state of the current report."""
    if not session["report"]:
        return "No report yet", "muted"
    r = session["email_result"]
    if r:
        return {"sent": ("Sent", "ok"), "simulated": ("Simulated · not sent", "info"),
                "failed": ("Failed", "err")}[r["status"]]
    return ("Awaiting your confirmation", "warn") if session["awaiting_email"] else ("Not sent", "muted")


def email_confirm_box(where: str):
    """Shown whenever a report is prepared and waiting for the employee's explicit OK."""
    if not session["awaiting_email"]:
        return
    rep = session["report"]
    st.markdown(
        f'<div class="panel warn">🔒 <b>Confirmation required</b><br>Send <code>{escape(os.path.basename(rep["pdf_path"]))}</code> '
        f'to <b>{escape(rep["receiver_email"])}</b>? Nothing is sent until you confirm.</div>',
        unsafe_allow_html=True)

    def act(confirm: bool):
        st.session_state.display.append({"role": "assistant", "text": email_button(session, confirm)})

    b1, b2 = st.columns(2)
    b1.button("✅ Confirm & send email", key=f"confirm_{where}", type="primary", on_click=act, args=(True,))
    b2.button("Cancel", key=f"cancel_{where}", on_click=act, args=(False,))


def dashboard_findings():
    """Retrieve records + ask the Expense Analysis Agent (cached per employee/month/mode)."""
    recs = get_mock_expenses(employee, month)
    cache = st.session_state.setdefault("dash_cache", {})
    key = (employee, month, demo)
    if recs and key not in cache:
        request = {"sender": "Dashboard", "receiver": analysis_agent.NAME, "expenses": recs}
        cache[key] = analysis_agent.handle(request, use_llm=not demo)["findings"]
    return recs, cache.get(key)


# ===========================================================================
# PAGE 1 - DASHBOARD
# ===========================================================================
def page_dashboard():
    st.markdown(f'<div class="section-title">Overview · {escape(employee)} · {month}</div>'
                '<div class="section-sub">Figures are computed in Python by the Expense Analysis Agent.</div>',
                unsafe_allow_html=True)
    recs, f = dashboard_findings()
    if not recs:
        months = ", ".join(get_available_months(employee)) or "none"
        st.info(f"No expenses found for {employee} in {month}. Months with data: {months}.")
        return

    flagged = len(f["flagged_expenses"])
    top = f["highest_category"]
    k1, k2, k3, k4 = st.columns(4, gap="medium")
    k1.markdown(metric_card("Total spend", rs(f["total_amount"]), "excludes receipts with no amount", BLUE),
                unsafe_allow_html=True)
    k2.markdown(metric_card("Expenses", str(f["total_expenses"]), f"{len(f['category_totals'])} categories", TEAL),
                unsafe_allow_html=True)
    k3.markdown(metric_card("Missing information", str(flagged),
                            f"{f['missing_amount_count']} no amount · {f['missing_support_count']} no receipt",
                            AMBER if flagged else GREEN), unsafe_allow_html=True)
    k4.markdown(metric_card("Top category", top["name"] if top else "-", rs(top["amount"]) if top else "", TEAL),
                unsafe_allow_html=True)
    st.markdown("<div style='height:1.2rem'></div>", unsafe_allow_html=True)

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown('<div class="section-title">Spending by category</div>', unsafe_allow_html=True)
        chart = pd.DataFrame({"Category": list(f["category_totals"]), "Amount (₹)": list(f["category_totals"].values())})
        st.bar_chart(chart.set_index("Category"), color=TEAL, height=300)
    with right:
        st.markdown('<div class="section-title">Status</div>', unsafe_allow_html=True)
        rep_badge = badge("Generated", "ok") if session["report"] else badge("Not generated", "muted")
        miss_badge = badge(f"{flagged} flagged", "warn") if flagged else badge("All complete", "ok")
        label, kind = email_status()
        st.markdown(
            f'<div class="panel"><div class="status-row"><span>📄 Report</span>{rep_badge}</div>'
            f'<div class="status-row"><span>⚠️ Missing information</span>{miss_badge}</div>'
            f'<div class="status-row"><span>📧 Email to finance</span>{badge(label, kind)}</div></div>',
            unsafe_allow_html=True)

    st.markdown('<div class="section-title">Expense records</div>', unsafe_allow_html=True)
    issues = {(x["date"], x["vendor"]): x["missing"] for x in f["flagged_expenses"]}
    cat_of = {(i["date"], i["vendor"]): c for c, items in f["items_by_category"].items() for i in items}
    rows = []
    for r in recs:
        miss = issues.get((r["date"], r["vendor"]), [])
        rows.append({"Date": r["date"], "Vendor": r["vendor"], "Description": r["description"],
                     "Category": cat_of.get((r["date"], r["vendor"]), "-"),
                     "Amount": rs(r["amount"]) if r["amount"] is not None else "—",
                     "Status": "✅ Complete" if not miss else "⚠️ Missing " + ", ".join(
                         "amount" if m == "amount" else "receipt" for m in miss)})
    st.dataframe(rows, hide_index=True)


# ===========================================================================
# PAGE 2 - CHAT ASSISTANT (+ agent collaboration panel)
# ===========================================================================
def collaboration_panel():
    st.markdown('<div class="section-title">🤝 Agent Collaboration</div>'
                '<div class="section-sub">Two agents, two responsibilities.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="agent blue"><b>💬 Chat Assistant</b><br>Talks to the employee, picks the tools, writes the answer.</div>'
        '<div class="agent teal"><b>🔎 Expense Analysis Agent</b><br>Analyses records: category totals, '
        'missing information. Returns structured findings.</div>', unsafe_allow_html=True)
    log = session["collab_log"]
    if not log:
        st.info("Ask a question - the exchange between the two agents appears here.")
        return
    e = log[-1]
    f = e["findings"]
    st.markdown(f'<div class="step"><span>①</span> Assistant → Analysis Agent</div>', unsafe_allow_html=True)
    if e["reused"]:
        st.write("Reused the findings already received earlier in this session (session memory) - no new request needed.")
    else:
        st.write(f"“Please analyse the {f['total_expenses']} expense records for {session['employee']} ({e['month']}).”")
    st.markdown('<div class="step"><span>②</span> Analysis Agent → Assistant · findings</div>', unsafe_allow_html=True)
    m1, m2, m3 = st.columns(3)
    m1.metric("Total", rs(f["total_amount"]))
    m2.metric("Categories", len(f["category_totals"]))
    m3.metric("Flagged", len(f["flagged_expenses"]))
    for x in f["flagged_expenses"]:
        st.write(f"⚠️ **{x['vendor']}** - missing {', '.join(x['missing'])}")
    with st.expander("Raw findings (JSON)"):
        st.json(f)
    st.markdown('<div class="step"><span>③</span> Assistant → Employee</div>', unsafe_allow_html=True)
    used = e["used"]
    st.write("Built the reply from: " + ", ".join(f"`{u}`" for u in used) if used
             else "The LLM read the findings JSON and wrote the reply.")
    st.caption("Reply preview: " + e["reply"].replace("\n", " ")[:170] + "…")


def page_chat():
    prompt = st.chat_input("Ask about your expenses, e.g. “Generate my travel expense report for July”")
    prompt = prompt or st.session_state.pop("queued_prompt", None)
    if prompt:                                          # one chat turn
        st.session_state.display.append({"role": "user", "text": prompt})
        with st.spinner("Assistant is working…"):
            reply = chat_turn(session, prompt)
        st.session_state.display.append({"role": "assistant", "text": reply})

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown('<div class="section-title">💬 Chat Assistant</div>'
                    '<div class="section-sub">Natural-language access to your expenses. Try a suggestion:</div>',
                    unsafe_allow_html=True)
        for pair in (SAMPLE_PROMPTS[:2], SAMPLE_PROMPTS[2:]):
            for col, text in zip(st.columns(2, gap="small"), pair):
                col.button(text, key=f"chip_{text}", on_click=lambda t=text: st.session_state.update(queued_prompt=t))
        with st.container(height=470, border=True):
            if not st.session_state.display:
                with st.chat_message("assistant", avatar="🤖"):
                    st.write(f"Hi {session['employee'].split()[0]}! I can pull your expenses, spot missing "
                             "information, break down spending and prepare your report. What would you like to do?")
            for msg in st.session_state.display:
                with st.chat_message(msg["role"], avatar="🧑‍💼" if msg["role"] == "user" else "🤖"):
                    st.markdown(msg["text"])
        email_confirm_box("chat")
    with right:
        collaboration_panel()


# ===========================================================================
# PAGE 3 - EXPENSE REPORT (the original workflow)
# ===========================================================================
def page_report():
    st.markdown('<div class="section-title">Expense Report</div>'
                '<div class="section-sub">Runs the original LangGraph workflow: retrieve → categorize → '
                '(flag missing data) → compile PDF. Email is never sent automatically.</div>', unsafe_allow_html=True)
    run_clicked = st.button("🚀 Run Expense Agent", type="primary")
    left, right = st.columns([1, 1.3], gap="large")
    with left:
        st.markdown('<div class="section-title">🧠 Live Thought Trace</div>', unsafe_allow_html=True)
        trace_box = st.container(border=True)
    with right:
        st.markdown('<div class="section-title">📋 Final Report</div>', unsafe_allow_html=True)
        result_box = st.container(border=True)

    if run_clicked:
        if not employee.strip() or not receiver.strip():
            st.error("Please fill in the employee name and receiver email.")
            st.stop()
        state = make_initial_state(employee, month, receiver, demo)
        lines = []
        with trace_box:
            placeholder = st.empty()
        with st.spinner("Agents are working…"):
            # stream_mode="updates" -> {node_name: partial_update} after EACH node finishes
            for step in expense_graph.stream(state, stream_mode="updates"):
                for node_name, node_output in step.items():
                    header = NODE_LABELS.get(node_name, node_name)
                    lines += [f"**{header}**\n\n{ln}" for ln in node_output.get("thought_trace", [])]
                    state.update({k: v for k, v in node_output.items() if k != "thought_trace"})
                placeholder.markdown("\n\n---\n\n".join(lines))
        state["thought_trace"] = lines
        session.update(report=state, awaiting_email=False, email_result=None)
    else:
        with trace_box:
            trace = (session["report"] or {}).get("thought_trace")
            st.markdown("\n\n---\n\n".join(trace)) if trace else st.info("Click **Run Expense Agent** to begin.")

    with result_box:
        rep = session["report"]
        if not rep:
            st.info("Your finalized expense report will appear here.")
            return
        label, kind = email_status()
        st.markdown(badge("Report generated", "ok") + " &nbsp; " +
                    (badge("Missing data flagged", "warn") if rep["has_missing_data"] else badge("No missing data", "ok")) +
                    " &nbsp; " + badge("Email: " + label, kind), unsafe_allow_html=True)
        st.markdown(rep["final_report_md"])
        if os.path.exists(rep["pdf_path"]):
            with open(rep["pdf_path"], "rb") as fh:
                st.download_button("⬇️ Download PDF report", data=fh.read(),
                                   file_name=os.path.basename(rep["pdf_path"]), mime="application/pdf")
        if session["awaiting_email"]:
            email_confirm_box("report")
        elif session["email_result"]:
            r = session["email_result"]
            {"sent": st.success, "simulated": st.info, "failed": st.error}[r["status"]](r["message"])
        else:
            st.button("📧 Prepare email to finance", key="prepare_email",
                      on_click=lambda: session.update(awaiting_email=True))


{PAGES[0]: page_dashboard, PAGES[1]: page_chat, PAGES[2]: page_report}[st.session_state.page]()
