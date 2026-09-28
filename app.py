"""
app.py
------
Streamlit dashboard for the Autonomous Expense Report Agent.

Layout:
  - Top row: 3 columns of inputs — Employee Name, Target Month, Receiver
    Email — followed by a full-width "Run Expense Agent" button.
  - Main area, 2 columns:
      Left  — live, step-by-step "Thought Trace" as the graph executes.
      Right — the final Markdown report, a PDF download button, and the
              email delivery status.

Dark theme is set declaratively in `.streamlit/config.toml` (the officially
supported way to theme a Streamlit app) rather than injected via custom CSS.

Why `.stream(..., stream_mode="updates")` instead of `.invoke()`:
`.invoke()` blocks until the ENTIRE graph finishes and only returns the
final state — the UI would just show one spinner with no visibility into
which node is running. `.stream(stream_mode="updates")` yields a
`{node_name: partial_state_update}` dict after EACH node finishes, which is
what lets us render the trace node-by-node in near real time.
"""

import os
from datetime import date

import streamlit as st
from expense_agent import expense_graph

st.set_page_config(
    page_title="Autonomous Expense Report Agent",
    page_icon="🧾",
    layout="wide",
)

st.title("🧾 Autonomous Expense Report Agent")
st.caption(
    "A LangGraph agent that retrieves receipts, categorizes and totals "
    "them, flags missing data for human review, then generates and emails "
    "a PDF expense report."
)

if not os.getenv("GROQ_API_KEY"):
    st.warning(
        "⚠️ GROQ_API_KEY not found. Copy `.env.example` to `.env` and add "
        "your free Groq key before running the agent.",
        icon="⚠️",
    )

# ---------------------------------------------------------------------------
# TOP ROW — 3-column inputs
# ---------------------------------------------------------------------------
input_col1, input_col2, input_col3 = st.columns(3)

with input_col1:
    employee_name = st.text_input("Employee Name", value="Aditi Sharma")

with input_col2:
    # A date_input is friendlier than free-text; we only use it to derive
    # a "YYYY-MM" string, since that's the exact format the mock DB's
    # `date` field is filtered against in tools.get_mock_expenses.
    target_month_date = st.date_input("Target Month", value=date(2026, 7, 1))

with input_col3:
    receiver_email = st.text_input(
        "Receiver Email (Finance)",
        value=os.getenv("RECEIVER_EMAIL", "finance@example.com"),
    )

target_month = target_month_date.strftime("%Y-%m")

run_clicked = st.button(
    "🚀 Run Expense Agent", type="primary", use_container_width=True
)

st.divider()

# ---------------------------------------------------------------------------
# MAIN AREA — 2 columns
# ---------------------------------------------------------------------------
left_col, right_col = st.columns([1, 1.3])

with left_col:
    st.subheader("🧠 Live Thought Trace")
    trace_container = st.container(border=True)

with right_col:
    st.subheader("📋 Final Report")
    result_container = st.container(border=True)

# Human-readable headers for each node, used only for the trace display.
NODE_LABELS = {
    "data_retriever": "Node 1 · Data Retriever",
    "categorizer_calculator": "Node 2 · Categorizer & Calculator",
    "anomaly_handler": "Node 3 · Anomaly Handler (missing-data triggered)",
    "compiler_delivery": "Node 4 · Compiler & Delivery",
}

if run_clicked:
    if not employee_name.strip() or not receiver_email.strip():
        st.error("Please fill in the employee name and receiver email.")
        st.stop()

    # The initial state fed into START. Every ExpenseState key the graph
    # will ever read/write should have a sane starting value here.
    initial_state = {
        "employee_name": employee_name,
        "target_month": target_month,
        "receiver_email": receiver_email,
        "raw_expenses": [],
        "valid_expenses": [],
        "missing_expenses": [],
        "categorized_expenses": [],
        "category_totals": {},
        "grand_total": 0.0,
        "has_missing_data": False,
        "discrepancy_note": "",
        "final_report_md": "",
        "pdf_path": "",
        "email_status": {},
        "thought_trace": [],
    }

    # `accumulated_state` mirrors LangGraph's own internal state merging:
    # start from initial_state, then merge each node's partial-update dict
    # on top of it, key by key. This lets the right-hand column read
    # `final_report_md` / `pdf_path` once Compiler & Delivery has run,
    # without invoking the graph a second time.
    accumulated_state = dict(initial_state)

    with trace_container:
        trace_placeholder = st.empty()
    displayed_trace_lines = []

    with st.spinner("Agents are working..."):
        # stream_mode="updates" -> each `step` is {node_name: node_output}
        # for whichever node just finished running.
        for step in expense_graph.stream(initial_state, stream_mode="updates"):
            for node_name, node_output in step.items():
                header = NODE_LABELS.get(node_name, node_name)

                for line in node_output.get("thought_trace", []):
                    displayed_trace_lines.append(f"**{header}**\n\n{line}")

                for key, value in node_output.items():
                    if key != "thought_trace":
                        accumulated_state[key] = value

            trace_placeholder.markdown("\n\n---\n\n".join(displayed_trace_lines))

    with result_container:
        if accumulated_state.get("final_report_md"):
            st.markdown(accumulated_state["final_report_md"])

            pdf_path = accumulated_state.get("pdf_path")
            if pdf_path and os.path.exists(pdf_path):
                with open(pdf_path, "rb") as f:
                    st.download_button(
                        "⬇️ Download PDF Report",
                        data=f.read(),
                        file_name=os.path.basename(pdf_path),
                        mime="application/pdf",
                        use_container_width=True,
                    )

            email_status = accumulated_state.get("email_status", {})
            if email_status.get("success"):
                st.success(email_status["message"])
            elif email_status:
                st.error(email_status["message"])
        else:
            st.warning(
                "The graph finished but no report was produced — check the "
                "thought trace on the left for details."
            )
else:
    with trace_container:
        st.info("Fill in the inputs above and click **Run Expense Agent** to begin.")
    with result_container:
        st.info("Your finalized expense report will appear here.")
