"""
expense_agent.py
-----------------
The core agentic graph for the Autonomous Expense Report Agent.

This file defines:
  1. ExpenseState              — the typed shared "memory" every node uses
  2. Four node functions        — Data Retriever, Categorizer & Calculator,
                                   Anomaly Handler, Compiler & Delivery
  3. One conditional edge function — routes around the Anomaly Handler
  4. build_expense_graph()      — wires nodes/edges into a compiled graph

Why StateGraph (and not AgentExecutor / create_react_agent):
Those older LangChain constructs hide the control flow inside a single
opaque "agent loop." `StateGraph` makes the workflow an explicit,
inspectable directed graph over a shared state object — exactly what's
needed here, since "retrieve -> categorize -> conditionally flag -> compile
& send" is a genuine branching pipeline, not a single reasoning loop.
"""

import os
import json
import operator
from typing import TypedDict, List, Annotated

from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage
from langgraph.graph import StateGraph, START, END

from tools import get_mock_expenses, generate_pdf, send_expense_email

# Load GROQ_API_KEY / SENDER_EMAIL / EMAIL_APP_PASSWORD from a local .env
# file (see .env.example).
load_dotenv()

# A single shared LLM client, reused by every node that needs one.
# temperature=0.2 (lower than a creative-writing use case) because this
# agent's LLM calls are classification and short factual note-drafting,
# where we want consistent, low-variance output.
llm = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0.2,
    groq_api_key=os.getenv("GROQ_API_KEY"),
)

# Fixed set of categories the LLM is allowed to choose from. Constraining
# the label space up front (rather than letting the LLM invent categories
# freely) keeps category_totals predictable and easy to render/PDF.
CATEGORY_OPTIONS = ["Flights", "Lodging", "Meals", "Transport", "Other"]


# ---------------------------------------------------------------------------
# 1. STATE DEFINITION
# ---------------------------------------------------------------------------
class ExpenseState(TypedDict):
    """
    The single shared state object that flows through every node.

    Each node returns only the keys it changed; LangGraph merges that
    partial dict into the running ExpenseState. Every field is overwritten
    on update EXCEPT `thought_trace`, which is wrapped in
    `Annotated[List[str], operator.add]` — a *reducer* telling LangGraph to
    concatenate (list + list) instead of overwrite, so every node's log
    line survives instead of erasing the ones before it.
    """
    employee_name: str
    target_month: str        # "YYYY-MM", e.g. "2026-07"
    receiver_email: str

    raw_expenses: list        # every receipt pulled by the Data Retriever
    valid_expenses: list      # receipts that DO have a numeric amount
    missing_expenses: list    # receipts missing an amount
    categorized_expenses: list  # valid_expenses, each tagged with a category
    category_totals: dict     # {category: summed amount}
    grand_total: float
    has_missing_data: bool
    discrepancy_note: str     # only populated if has_missing_data

    final_report_md: str      # Markdown shown in the Streamlit right column
    pdf_path: str              # path to the generated PDF file
    email_status: dict         # {"success": bool, "message": str}

    thought_trace: Annotated[List[str], operator.add]


# ---------------------------------------------------------------------------
# 2. NODE FUNCTIONS
# Every node is a plain function: (ExpenseState) -> dict. No base class,
# no decorator — that's the entire contract LangGraph requires, which keeps
# each node trivially testable on its own.
# ---------------------------------------------------------------------------
def data_retriever_node(state: ExpenseState) -> dict:
    """
    NODE 1: Data Retriever
    ------------------------
    Pure tool call — no LLM involved. Pulls raw mock receipts for the given
    employee + month straight out of the simulated local database.
    """
    raw = get_mock_expenses(state["employee_name"], state["target_month"])

    trace = (
        f"📥 Data Retriever: pulled {len(raw)} receipt(s) for "
        f"{state['employee_name']} in {state['target_month']}."
    )
    if not raw:
        trace += " No expenses found for this employee/month."

    return {
        "raw_expenses": raw,
        "thought_trace": [trace],
    }


def _keyword_categorize(expense: dict) -> dict:
    """
    Deterministic keyword-matching fallback, used ONLY if the LLM
    categorization call below fails or returns malformed JSON. Guarantees
    the graph can always produce a categorized report, even with no
    internet access during a live viva.
    """
    text = f"{expense['vendor']} {expense['description']}".lower()
    if any(k in text for k in ("flight", "airlines", "indigo", "spicejet", "air india")):
        category = "Flights"
    elif any(k in text for k in ("hotel", "lodging", "taj", "leela", "night")):
        category = "Lodging"
    elif any(k in text for k in ("cafe", "restaurant", "lunch", "dinner", "pizza", "food")):
        category = "Meals"
    elif any(k in text for k in ("uber", "ola", "cab", "taxi", "transfer")):
        category = "Transport"
    else:
        category = "Other"

    tagged = dict(expense)
    tagged["category"] = category
    return tagged


def _categorize_with_llm(valid_expenses: list) -> list:
    """
    Helper used by categorizer_calculator_node (not a graph node itself).
    Asks the LLM to assign one of CATEGORY_OPTIONS to each expense, and
    requests STRICT JSON back so the result is trivially parseable —
    no free-text parsing, no regex.

    Falls back to `_keyword_categorize` if the LLM call throws or the
    response isn't valid JSON. Same "never crash the demo" philosophy as
    the try/except in tools.send_expense_email.
    """
    if not valid_expenses:
        return []

    items_desc = "\n".join(
        f"{i}. vendor='{e['vendor']}', description='{e['description']}'"
        for i, e in enumerate(valid_expenses)
    )

    system_prompt = (
        "You are an expense-categorization assistant. For each numbered "
        f"expense below, assign EXACTLY ONE category from this fixed "
        f"list: {CATEGORY_OPTIONS}.\n\n{items_desc}\n\n"
        "Respond with ONLY a JSON array (no prose, no markdown fences), "
        "one object per expense, in the same order, in this exact shape: "
        '[{"index": 0, "category": "Flights"}, ...]'
    )

    try:
        response = llm.invoke([SystemMessage(content=system_prompt)])
        # Strip common wrapping artifacts (markdown code fences, a leading
        # "json" language tag) before parsing, since LLMs sometimes add
        # these even when explicitly told not to.
        cleaned = response.content.strip().strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
        parsed = json.loads(cleaned)

        categorized = []
        for entry in parsed:
            idx = entry["index"]
            category = entry["category"]
            if category not in CATEGORY_OPTIONS:
                category = "Other"
            tagged = dict(valid_expenses[idx])
            tagged["category"] = category
            categorized.append(tagged)
        return categorized

    except Exception:  # noqa: BLE001 — broad on purpose, see docstring
        return [_keyword_categorize(e) for e in valid_expenses]


def categorizer_calculator_node(state: ExpenseState) -> dict:
    """
    NODE 2: Categorizer & Calculator
    -----------------------------------
    Deliberately split into a deterministic half and an LLM half:

      1. DETERMINISTIC (Python): separate expenses that have a real
         numeric `amount` from ones missing it. This is a factual check,
         not a judgment call, so `has_missing_data` is decided here in
         plain code — never left to an LLM to "notice."
      2. LLM: only the *valid* expenses are sent to the LLM, asked to
         assign each a spending category. Turning a vendor + description
         into "Meals" vs "Flights" is a judgment call LLMs are well-suited
         for.
      3. DETERMINISTIC (Python): once categories come back, the actual
         SUMS are computed with plain `sum()` in Python — never by asking
         the LLM to do arithmetic. LLMs are unreliable at precise math, so
         we only trust the model for the category label, not the totals.
    """
    raw_expenses = state["raw_expenses"]

    valid_expenses = [e for e in raw_expenses if e.get("amount") is not None]
    missing_expenses = [e for e in raw_expenses if e.get("amount") is None]
    has_missing_data = len(missing_expenses) > 0

    categorized_expenses = _categorize_with_llm(valid_expenses)

    category_totals: dict = {}
    for expense in categorized_expenses:
        cat = expense["category"]
        category_totals[cat] = category_totals.get(cat, 0.0) + expense["amount"]
    grand_total = sum(category_totals.values())

    trace = (
        f"🧮 Categorizer & Calculator: categorized {len(categorized_expenses)} "
        f"expense(s) into {len(category_totals)} categories — grand total "
        f"₹{grand_total:,.2f}."
    )
    if has_missing_data:
        trace += f" ⚠️ {len(missing_expenses)} expense(s) missing an amount."

    return {
        "valid_expenses": valid_expenses,
        "missing_expenses": missing_expenses,
        "has_missing_data": has_missing_data,
        "categorized_expenses": categorized_expenses,
        "category_totals": category_totals,
        "grand_total": grand_total,
        "thought_trace": [trace],
    }


def anomaly_handler_node(state: ExpenseState) -> dict:
    """
    NODE 3: Anomaly Handler
    --------------------------
    Only reached when has_missing_data == True (see route_after_categorizing
    below). Asks the LLM to draft a short, professional discrepancy note
    describing which receipts are missing an amount, for a human finance
    reviewer to chase up. The LLM is explicitly told not to invent amounts
    — its job is to *describe* the gap, not fill it in.
    """
    missing_list = "\n".join(
        f"- {e['vendor']} on {e['date']}: \"{e['description']}\" (amount missing)"
        for e in state["missing_expenses"]
    )

    system_prompt = (
        "You are drafting a short, professional discrepancy note for a "
        "finance team. The following receipts are missing an amount and "
        f"need manual follow-up:\n\n{missing_list}\n\n"
        "Write 2-4 sentences flagging these for human review. Do not "
        "invent or guess any amounts."
    )

    response = llm.invoke([SystemMessage(content=system_prompt)])

    return {
        "discrepancy_note": response.content,
        "thought_trace": [
            f"🚩 Anomaly Handler: flagged {len(state['missing_expenses'])} "
            "receipt(s) with missing amounts for human review."
        ],
    }


def compiler_delivery_node(state: ExpenseState) -> dict:
    """
    NODE 4: Compiler & Delivery
    -------------------------------
    Terminal node. Builds the final Markdown report, generates a real PDF
    via tools.generate_pdf, then emails it via tools.send_expense_email.
    Both tool calls are deterministic Python/stdlib work — formatting a
    table and sending bytes over SMTP don't need an LLM.
    """
    lines = [
        f"# Travel Expense Report — {state['employee_name']}",
        f"**Month:** {state['target_month']}",
        "",
        "| Category | Total |",
        "|---|---|",
    ]
    for category, total in state["category_totals"].items():
        lines.append(f"| {category} | ₹{total:,.2f} |")
    lines.append(f"| **Grand Total** | **₹{state['grand_total']:,.2f}** |")

    if state.get("has_missing_data"):
        lines += ["", "### ⚠️ Flagged for Review", state["discrepancy_note"]]

    final_report_md = "\n".join(lines)

    pdf_path = generate_pdf(
        employee_name=state["employee_name"],
        target_month=state["target_month"],
        category_totals=state["category_totals"],
        grand_total=state["grand_total"],
        discrepancy_note=state.get("discrepancy_note", ""),
        output_dir=".",  # Forces the PDF to save in the current project folder
    )

    email_status = send_expense_email(
        receiver_email=state["receiver_email"],
        pdf_path=pdf_path,
        employee_name=state["employee_name"],
        target_month=state["target_month"],
    )

    trace = f"📄 Compiler & Delivery: generated PDF at '{pdf_path}'. "
    trace += (
        f"✅ {email_status['message']}"
        if email_status["success"]
        else f"❌ {email_status['message']}"
    )

    return {
        "final_report_md": final_report_md,
        "pdf_path": pdf_path,
        "email_status": email_status,
        "thought_trace": [trace],
    }


# ---------------------------------------------------------------------------
# 3. CONDITIONAL EDGE FUNCTION
# ---------------------------------------------------------------------------
def route_after_categorizing(state: ExpenseState) -> str:
    """
    `add_conditional_edges` expects a plain function that reads current
    state and returns a STRING key, which is then looked up in a mapping
    (see build_expense_graph below) to pick the next node. This is what
    makes the graph a genuine state machine — whether the Anomaly Handler
    runs depends on runtime data (were any receipts missing an amount?),
    not on a fixed pipeline order.
    """
    return "anomaly_handler" if state["has_missing_data"] else "compiler_delivery"


# ---------------------------------------------------------------------------
# 4. GRAPH ASSEMBLY
# ---------------------------------------------------------------------------
def build_expense_graph():
    """
    Builds and compiles the branching StateGraph described in the brief:

        START -> data_retriever -> categorizer_calculator
                                        -[conditional]-> anomaly_handler -> compiler_delivery -> END
                                        \\----------------------------------> compiler_delivery -> END
    """
    graph = StateGraph(ExpenseState)

    # --- Register nodes ---------------------------------------------------
    graph.add_node("data_retriever", data_retriever_node)
    graph.add_node("categorizer_calculator", categorizer_calculator_node)
    graph.add_node("anomaly_handler", anomaly_handler_node)
    graph.add_node("compiler_delivery", compiler_delivery_node)

    # --- Fixed edges --------------------------------------------------------
    graph.add_edge(START, "data_retriever")
    graph.add_edge("data_retriever", "categorizer_calculator")

    # --- The conditional edge ------------------------------------------------
    # route_after_categorizing returns "anomaly_handler" or
    # "compiler_delivery"; the dict below maps each key to the actual node
    # name to jump to. This single call is what makes has_missing_data
    # actually control the graph's path at runtime.
    graph.add_conditional_edges(
        "categorizer_calculator",
        route_after_categorizing,
        {
            "anomaly_handler": "anomaly_handler",
            "compiler_delivery": "compiler_delivery",
        },
    )

    # Anomaly Handler always feeds into Compiler & Delivery once it's done.
    graph.add_edge("anomaly_handler", "compiler_delivery")
    graph.add_edge("compiler_delivery", END)

    # .compile() turns the declarative graph definition into a runnable
    # object exposing .invoke() / .stream().
    return graph.compile()


# Module-level compiled graph — imported directly by app.py so it's built
# exactly once per process, not rebuilt on every Streamlit rerun.
expense_graph = build_expense_graph()
