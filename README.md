# 🧾 Autonomous Expense Report Agent

A LangGraph agent that retrieves last month's travel receipts, categorizes
and totals them, flags any receipt missing an amount for human review, then
generates a real PDF and emails it to finance — all visualized in a
dark-themed Streamlit dashboard with a live "thought trace."

## Project structure

```
expense-report-agent/
├── requirements.txt          # pinned dependencies
├── .env.example                # copy to .env and fill in real values
├── .streamlit/
│   └── config.toml              # dark theme (declarative, no CSS hacks)
├── tools.py                     # mock receipts DB, PDF generator, SMTP sender
├── expense_agent.py             # TypedDict state, 4 nodes, conditional edge, StateGraph
├── app.py                        # Streamlit dashboard
└── README.md
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env:
#   GROQ_API_KEY        - free key from https://console.groq.com/keys
#   SENDER_EMAIL         - a Gmail address
#   EMAIL_APP_PASSWORD   - a Gmail "App Password" (not your normal password —
#                          Google Account -> Security -> 2-Step Verification
#                          -> App Passwords)
#   RECEIVER_EMAIL       - finance's inbox (also editable in the UI)
streamlit run app.py
```

No real database or paid API is used anywhere: receipts come from a
hardcoded list in `tools.py`, so the demo never depends on network access
to a database. If `SENDER_EMAIL` / `EMAIL_APP_PASSWORD` aren't set (or the
send fails for any reason), `send_expense_email` fails gracefully and the
UI shows why — the app keeps running either way.

## Architecture at a glance

```
START → Data Retriever → Categorizer & Calculator ──(no missing data)──→ Compiler & Delivery → END
                                    │
                                    └──(has_missing_data)──→ Anomaly Handler ──→ Compiler & Delivery → END
```

| Node | Calls the LLM? | Calls a tool? | Purpose |
|---|---|---|---|
| **Data Retriever** | ❌ | ✅ `get_mock_expenses` | Pulls receipts for the employee + month from the mock DB |
| **Categorizer & Calculator** | ✅ (categorization only) | — | Splits valid vs. missing-amount receipts (Python), categorizes valid ones (LLM), sums totals (Python) |
| **Anomaly Handler** | ✅ | — | Only runs if `has_missing_data`; drafts a discrepancy note for finance |
| **Compiler & Delivery** | ❌ | ✅ `generate_pdf`, ✅ `send_expense_email` | Formats the Markdown report, renders the PDF, emails it |

The demo is guaranteed to hit the Anomaly Handler branch by default: the
mock "Taj Hotels" receipt for Aditi Sharma / July 2026 has `amount: None`.

## Viva cheat sheet — key design decisions

- **`StateGraph(ExpenseState)`** — an explicit, inspectable directed graph
  over a typed state object, instead of an opaque `AgentExecutor` loop.
- **`Annotated[List[str], operator.add]`** on `thought_trace` — a
  *reducer*. Tells LangGraph to concatenate each node's new trace lines
  onto the list instead of overwriting it.
- **Missing-data detection is deterministic, not LLM-judged.** In
  `categorizer_calculator_node`, `has_missing_data` is set by a plain
  Python check (`amount is None`) — a factual condition should never
  depend on a probabilistic model "noticing" it.
- **The LLM only categorizes; Python does the arithmetic.** Category
  labels (a judgment call) come from the LLM; category totals and the
  grand total are computed with plain `sum()`. This is a deliberate
  design choice to avoid trusting an LLM with precise math.
- **Two independent fallback layers**, both there so a live demo never
  crashes: `_categorize_with_llm` falls back to `_keyword_categorize` if
  the LLM call or its JSON parsing fails; `send_expense_email` catches
  any SMTP/network error and returns a `{"success": False, ...}` dict
  instead of raising.
- **`add_conditional_edges`** — `route_after_categorizing` reads
  `state["has_missing_data"]` at runtime and returns a string key, looked
  up in a `{key: node_name}` map to pick the next node. This is what
  makes the Anomaly Handler branch conditional rather than always-on.
- **`.stream(state, stream_mode="updates")`** — used in `app.py` instead
  of `.invoke()` so the UI can render each node's output as soon as it's
  ready, producing the live "thought trace."
- **Real PDF, real email, both via well-known free tooling** — `fpdf2`
  (imported as `fpdf`) for the PDF; Python's own `smtplib` +
  `email.mime` for the email, no third-party mail SDK.
