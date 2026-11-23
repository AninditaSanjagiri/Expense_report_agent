"""
analysis_agent.py  (Jury 2 · Unit IV — Multi-Agent Collaboration)
-----------------------------------------------------------------
The Expense Analysis Agent: a small, SEPARATE agent with ONE responsibility —
analyse expense records and return structured findings.

Division of labour between the two agents:
  * Chat Assistant (chat_assistant.py)  -> talks to the employee, decides
    which tool to call, writes the final answer in natural language.
  * Expense Analysis Agent (this file)  -> does the analysis. It never talks
    to the employee and never writes the reply.

They exchange plain dict "messages":
    request  = {"sender", "receiver", "task", "expenses"}
    response = {"sender", "receiver", "findings"}

All arithmetic is done here in Python (never by an LLM).
"""

from expense_agent import _categorize_with_llm  # reuse the existing categorizer

NAME = "Expense Analysis Agent"

# Fields every receipt should have; a missing one is reported as "missing info".
REQUIRED_FIELDS = ["amount", "vendor", "description", "receipt_id"]


def handle(request: dict, use_llm: bool = False) -> dict:
    """Receive a request message from another agent, return a response message."""
    expenses = request["expenses"]

    # --- 1. Identify missing information (pure Python checks) ---------------
    flagged = []
    for e in expenses:
        missing = [f for f in REQUIRED_FIELDS if e.get(f) in (None, "")]
        if missing:
            flagged.append(
                {"date": e["date"], "vendor": e["vendor"],
                 "description": e["description"], "missing": missing}
            )

    # --- 2. Categorize (reuses the original categorizer: LLM, or keyword
    #        rules in demo mode) and total (pure Python) ----------------------
    valid = [e for e in expenses if e.get("amount") is not None]
    categorized = _categorize_with_llm(valid, use_llm=use_llm)

    category_totals: dict = {}
    for e in categorized:
        category_totals[e["category"]] = category_totals.get(e["category"], 0.0) + e["amount"]

    items_by_category: dict = {}
    for e in categorized:
        items_by_category.setdefault(e["category"], []).append(
            {"vendor": e["vendor"], "date": e["date"], "amount": e["amount"]}
        )

    highest = max(category_totals.items(), key=lambda kv: kv[1]) if category_totals else None

    # --- 3. Structured findings returned to the requesting agent ------------
    findings = {
        "total_expenses": len(expenses),
        "total_amount": round(sum(category_totals.values()), 2),
        "category_totals": category_totals,
        "items_by_category": items_by_category,
        "highest_category": {"name": highest[0], "amount": highest[1]} if highest else None,
        "missing_amount_count": sum("amount" in f["missing"] for f in flagged),
        "missing_support_count": sum("receipt_id" in f["missing"] for f in flagged),
        "flagged_expenses": flagged,
        "needs_review": len(flagged) > 0,
        "note": "Totals exclude receipts with no amount.",
    }
    return {"sender": NAME, "receiver": request["sender"], "findings": findings}
