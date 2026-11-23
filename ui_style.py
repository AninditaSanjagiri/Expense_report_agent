"""
ui_style.py
-----------
All visual styling in one place: a block of custom CSS plus two tiny helpers
that return HTML snippets. No UI library, no JavaScript - just Streamlit's
built-in components with a consistent navy / blue / teal / white look.
"""
from html import escape

BLUE, TEAL, AMBER, GREEN, RED = "#2F7DF6", "#14B8A6", "#F59E0B", "#22C55E", "#EF4444"

CSS = """
<style>
html, body, [class*="css"] { font-family: Inter, "Segoe UI", system-ui, -apple-system, sans-serif; }
.stApp { background: radial-gradient(1100px 600px at 8% -10%, #14346a 0%, #0A1A33 55%); }
.block-container { padding: 2rem 3rem 6rem; max-width: 1320px; }
#MainMenu, footer { visibility: hidden; }
header[data-testid="stHeader"] { background: transparent; }

/* ---- hero banner ---- */
.hero { background: linear-gradient(120deg, #12356b 0%, #1d5fd0 58%, #14B8A6 135%);
        border-radius: 20px; padding: 28px 36px; margin-bottom: 22px;
        box-shadow: 0 12px 32px rgba(0,0,0,.35); display: flex; justify-content: space-between;
        align-items: center; gap: 24px; }
.hero h1 { font-size: 2.1rem; margin: 0 0 6px; padding: 0; color: #fff; font-weight: 750; letter-spacing: -.01em; }
.hero p  { margin: 0; color: #DCE8FF; font-size: 1.05rem; }

/* ---- headings ---- */
.section-title { font-size: 1.4rem; font-weight: 700; color: #F8FAFC; margin: 10px 0 4px; }
.section-sub   { color: #94A3B8; font-size: .98rem; margin: 0 0 14px; }

/* ---- metric cards & panels ---- */
.card { background: #12294D; border: 1px solid rgba(148,163,184,.18); border-left: 5px solid #2F7DF6;
        border-radius: 16px; padding: 18px 22px; box-shadow: 0 6px 18px rgba(0,0,0,.25); height: 100%; }
.card-label { color: #94A3B8; font-size: .8rem; text-transform: uppercase; letter-spacing: .08em; font-weight: 600; }
.card-value { color: #fff; font-size: 2rem; font-weight: 750; margin: 6px 0 2px; line-height: 1.15; }
.card-sub   { color: #B6C4DA; font-size: .9rem; }
.panel { background: #12294D; border: 1px solid rgba(148,163,184,.18); border-radius: 16px; padding: 18px 22px; margin-bottom: 12px; }
.panel.warn { border-color: rgba(245,158,11,.6); background: #2a2a2e; }
.status-row { display: flex; justify-content: space-between; align-items: center; padding: 10px 0;
              border-bottom: 1px solid rgba(148,163,184,.12); color: #E2E8F0; font-size: 1rem; }
.status-row:last-child { border-bottom: none; }

/* ---- status badges ---- */
.badge { display: inline-block; padding: 4px 12px; border-radius: 999px; font-size: .85rem; font-weight: 650; }
.badge.ok   { background: rgba(34,197,94,.16);  color: #4ADE80; }
.badge.warn { background: rgba(245,158,11,.18); color: #FBBF24; }
.badge.err  { background: rgba(239,68,68,.18);  color: #F87171; }
.badge.info { background: rgba(47,125,246,.20); color: #7FB0FF; }
.badge.teal { background: rgba(20,184,166,.20); color: #5EEAD4; }
.badge.muted{ background: rgba(148,163,184,.16); color: #CBD5E1; }

/* ---- agent collaboration ---- */
.agent { border-radius: 14px; padding: 14px 18px; margin-bottom: 10px; color: #F1F5F9; font-size: .98rem; }
.agent.blue { background: rgba(47,125,246,.16); border: 1px solid rgba(47,125,246,.5); }
.agent.teal { background: rgba(20,184,166,.14); border: 1px solid rgba(20,184,166,.5); }
.step { font-weight: 700; color: #fff; margin: 16px 0 6px; font-size: 1.02rem; }
.step span { color: #5EEAD4; }

/* ---- chat ---- */
[data-testid="stChatMessage"] { background: #12294D; border: 1px solid rgba(148,163,184,.18);
        border-radius: 16px; padding: 14px 18px; margin-bottom: 10px; }
[data-testid="stChatMessage"]:has([data-testid*="user" i]) { background: #1B3F7A; border-color: rgba(47,125,246,.55); }
[data-testid="stChatMessage"] p, [data-testid="stChatMessage"] li { font-size: 1.03rem; line-height: 1.65; }
[data-testid="stChatInput"] { border-radius: 16px; }
[data-testid="stChatInput"] textarea { font-size: 1.05rem; }

/* ---- buttons / inputs ---- */
div.stButton > button, div.stDownloadButton > button { width: 100%; border-radius: 12px; padding: .65rem 1.1rem;
        font-weight: 650; font-size: 1rem; border: 1px solid rgba(148,163,184,.28); }
div.stButton > button[kind="primary"], div.stButton > button[data-testid="stBaseButton-primary"],
div.stDownloadButton > button[kind="primary"] { background: linear-gradient(90deg, #2F7DF6, #14B8A6);
        border: none; color: #fff; }
div[data-baseweb="input"] > div, div[data-baseweb="base-input"] { border-radius: 10px; }
label p { font-size: .95rem !important; font-weight: 600; color: #CBD5E1; }
</style>
"""


def metric_card(label: str, value: str, sub: str = "", accent: str = BLUE) -> str:
    """One KPI card as HTML (dynamic text is escaped)."""
    return (f'<div class="card" style="border-left-color:{accent}"><div class="card-label">{escape(label)}</div>'
            f'<div class="card-value">{escape(value)}</div><div class="card-sub">{escape(sub)}</div></div>')


def badge(text: str, kind: str = "info") -> str:
    """A coloured status pill. kind: ok | warn | err | info | teal | muted."""
    return f'<span class="badge {kind}">{escape(text)}</span>'
