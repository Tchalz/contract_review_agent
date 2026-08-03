"""
Streamlit front end for the contract review agent.

On first run, launches the MCP server (mcp_server/app.py) as a background
subprocess if it isn't already reachable, then drives the LangGraph
pipeline (pipeline/graph.py) against the uploaded contract.

Human-in-the-loop review gate
------------------------------
Running a review now happens in two steps, matching the graph's real
pause point:

  1. "Run review" calls start_review_sync(...), which runs the pipeline
     until it pauses (via LangGraph interrupt()) right after every
     high/medium flag has an LLM explanation + suggested rewording. The
     app stores the paused state (thread_id + the flags needing review)
     in st.session_state and renders an editable form: each flag's
     rewording is an editable text area with an approve checkbox.
  2. "Submit review & finalize report" calls resume_review_sync(...) with
     your edits/approvals, which resumes the *actual paused graph* — not a
     re-run — straight through to the final report.

A jurisdiction selector lets the user ground LLM-backed explanations in
jurisdiction-specific reference notes (see mcp_server/knowledge_base.py).
Selecting "None" preserves the original, ungrounded behavior.
"""

import html
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "mcp_server"))

from parsing import extract_text_with_pages, page_number_at  # noqa: E402
from graph import start_review_sync, resume_review_sync, run_comparison_sync  # noqa: E402

MCP_SERVER_SCRIPT = ROOT / "mcp_server" / "app.py"

LEVEL_BADGE = {"high": "🔴 High", "medium": "🟡 Medium", "low": "🟢 Low"}
LEVEL_COLOR = {"high": "#ef4444", "medium": "#f59e0b", "low": "#22c55e"}
LEVEL_BG = {"high": "rgba(239, 68, 68, 0.28)", "medium": "rgba(245, 158, 11, 0.28)", "low": "rgba(34, 197, 94, 0.28)"}

# Jurisdictions with reference notes in mcp_server/knowledge_base.py.
# "None" disables jurisdiction grounding entirely (original behavior).
JURISDICTION_OPTIONS = ["None", "Nigeria", "US", "EU"]

st.set_page_config(page_title="Clausegraph", page_icon="📄", layout="wide")

st.markdown("""
<style>

/* ---- Palette: ash / milk, not stark white ---- */
:root{
  --cg-page:#f5f4ef;      /* ash */
  --cg-surface:#fbfaf7;   /* milk */
  --cg-border:#ddd9cf;
  --cg-border-strong:#c8c3b6;
  --cg-text:#1c1b19;
  --cg-text-muted:#6b675f;
  --cg-accent:#d85a30;
  --cg-accent-hover:#bf4b27;
}

/* Entire app */
.stApp{background:var(--cg-page) !important;color:var(--cg-text) !important;}
.main .block-container{background:var(--cg-page) !important;color:var(--cg-text) !important;}
section[data-testid="stSidebar"]{background:var(--cg-surface) !important;}
h1,h2,h3,h4,h5,h6{color:var(--cg-text) !important;}
p,label,.stCaption{color:var(--cg-text) !important;}
.stMarkdown span:not([style*="color"]){color:var(--cg-text) !important;}

.stButton>button{background:var(--cg-accent) !important;color:#fbfaf7 !important;border:none;border-radius:8px;}
.stButton>button:hover{background:var(--cg-accent-hover) !important;}

/* File uploader: style only the actual dropzone, single clean dashed edge */
[data-testid="stFileUploader"]{background:transparent !important;border:none !important;}
[data-testid="stFileUploaderDropzone"]{
  background:var(--cg-surface) !important;
  border:1.5px dashed var(--cg-border-strong) !important;
  border-radius:12px !important;
  box-shadow:none !important;
  box-sizing:border-box;
}
[data-testid="stFileUploaderDropzone"]:hover{border-color:var(--cg-accent) !important;}
[data-testid="stFileUploaderDropzoneInstructions"] span{color:var(--cg-text-muted) !important;}

div[data-baseweb="select"]>div{background:var(--cg-surface) !important;border:1px solid var(--cg-border) !important;border-radius:8px;}
div[data-baseweb="select"] span{color:var(--cg-text) !important;}
.stTextInput input,.stTextArea textarea{background:var(--cg-surface) !important;color:var(--cg-text) !important;border:1px solid var(--cg-border) !important;}
[data-testid="metric-container"]{background:var(--cg-surface);border:1px solid var(--cg-border);border-radius:12px;padding:12px;}
[data-testid="stVerticalBlockBorderWrapper"]{background:var(--cg-surface) !important;border:1px solid var(--cg-border) !important;}

/* Theme-proof alert banners — fixed colors so they stay legible
   regardless of the viewer's OS/browser dark-mode setting */
div[data-testid="stAlertContainer"]{border-radius:10px !important;border:1px solid transparent !important;}
div[data-testid="stAlertContainer"] p{font-weight:500 !important;}
div[data-testid="stAlertContainer"][class*="warning"],
div[data-testid="stAlertContainer"]:has(svg[data-icon="warning"]){
  background:#fbead2 !important;border-color:#e2b365 !important;
}
div[data-testid="stAlertContainer"][class*="warning"] p,
div[data-testid="stAlertContainer"]:has(svg[data-icon="warning"]) p{color:#6b4a12 !important;}
</style>
""", unsafe_allow_html=True)



@st.cache_resource
def ensure_mcp_server_running():
    """Starts the MCP server once per Streamlit session if not already up."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", 8002)) == 0:
            return "already running"

    proc = subprocess.Popen(
        [sys.executable, str(MCP_SERVER_SCRIPT)],
        cwd=str(MCP_SERVER_SCRIPT.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)  # give FastMCP a moment to bind the port
    return proc


def annotate_flags_with_pages(flags: list, contract_text: str, page_starts: list) -> list:
    """
    Locates each flag's snippet in the full contract text and attaches the
    page it appears on, so every risk shown in the UI/report can be traced
    back to an exact page rather than floating unanchored. Returns a new
    list of flag dicts; flags whose snippet can't be located verbatim (or
    whose source had no page info, e.g. DOCX/TXT) get page=None.
    """
    annotated = []
    for flag in flags:
        entry = dict(flag)
        idx = contract_text.find(flag["snippet"])
        entry["page"] = page_number_at(page_starts, idx) if idx != -1 else None
        annotated.append(entry)
    return annotated


def clause_label(flag: dict) -> str:
    """Builds a display label for a flag, appending a page citation when known."""
    base = flag["clause_type"].replace("_", " ").title()
    if flag.get("page"):
        return f"{base} (Page {flag['page']})"
    return base


def risk_band(score: int):
    """Maps a 0-100 risk score to a (label, color) pair for display."""
    if score >= 60:
        return "High Risk", "#ef4444"
    if score >= 25:
        return "Moderate Risk", "#f59e0b"
    return "Low Risk", "#22c55e"


def render_risk_score(score: int):
    """Renders the overall risk score as a colored badge + progress bar."""
    label, color = risk_band(score)
    st.markdown(
        f"""
        <div style="border:1px solid {color}55; border-radius:10px; padding:16px 20px;
                    background:{color}1a; margin-bottom:8px;">
            <div style="font-size:0.85rem; color:#4a4a48; font-weight:600; margin-bottom:4px;">Overall Risk Score</div>
            <div style="display:flex; align-items:baseline; gap:12px;">
                <span style="font-size:2.2rem; font-weight:700; color:{color};">{score}/100</span>
                <span style="font-size:1rem; font-weight:600; color:{color};">{label}</span>
            </div>
            <div style="background:#e5e3dd; border-radius:6px; height:8px; margin-top:10px; overflow:hidden;">
                <div style="width:{score}%; background:{color}; height:100%;"></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def build_text_report(report: dict) -> str:
    """Builds a plain-text version of the report, suitable for download."""
    summary = report["summary"]
    lines = []
    lines.append("CONTRACT REVIEW REPORT")
    lines.append("Not legal advice — a first pass before a qualified reviewer.")
    lines.append("=" * 60)
    lines.append(f"Overall Risk Score: {report['risk_score']}/100 ({risk_band(report['risk_score'])[0]})")
    lines.append("")
    lines.append("EXECUTIVE SUMMARY")
    lines.append(report.get("negotiation_memo", ""))
    lines.append(f"Word count: {summary['word_count']}")
    lines.append(f"Clauses found: {summary['clause_types_found']}/{summary['clause_types_total']}")
    if summary.get("dates_mentioned"):
        lines.append(f"Dates mentioned: {', '.join(summary['dates_mentioned'])}")
    lines.append("")
    lines.append("-" * 60)
    lines.append("FLAGGED CLAUSES")
    lines.append("-" * 60)
    if not report["flags"]:
        lines.append("No risk-rule matches found.")
    for flag in sorted(report["flags"], key=lambda f: {"high": 0, "medium": 1, "low": 2}[f["level"]]):
        lines.append("")
        review_tag = ""
        if "human_approved" in flag:
            review_tag = " [HUMAN APPROVED]" if flag["human_approved"] else " [NOT APPROVED — PENDING REVIEW]"
        lines.append(f"[{flag['level'].upper()}] {clause_label(flag)}{review_tag}")
        lines.append(f"  Snippet: {flag['snippet']}")
        lines.append(f"  Reason: {flag['reason']}")
        if "explanation" in flag:
            lines.append(f"  Explanation: {flag['explanation']}")
            lines.append(f"  Suggested rewording: {flag['suggested_rewording']}")
    lines.append("")
    lines.append("-" * 60)
    lines.append("MISSING CLAUSES")
    lines.append("-" * 60)
    missing_clauses = report["missing_clauses"]
    if isinstance(missing_clauses, str):
        missing_clauses = [missing_clauses]
    if missing_clauses:
        lines.append(", ".join(c.replace("_", " ").title() for c in missing_clauses))
    else:
        lines.append("All standard clause types were found.")
    lines.append("")
    return "\n".join(lines)


def build_highlighted_contract_html(contract_text: str, flags: list) -> str:
    """
    Renders the full contract text as HTML with each flagged clause's
    snippet wrapped in a colored <mark> based on its risk level, so the
    risky language can be seen in context rather than only as an isolated
    snippet in the flagged-clauses list.
    """
    escaped_text = html.escape(contract_text)

    spans = []
    for flag in flags:
        escaped_snippet = html.escape(flag["snippet"])
        idx = escaped_text.find(escaped_snippet)
        if idx == -1:
            continue  # snippet couldn't be located verbatim — skip highlighting it
        spans.append((idx, idx + len(escaped_snippet), flag["level"], flag["clause_type"], flag.get("page")))

    # Sort by start position and drop overlapping spans (keep the first/earlier one)
    spans.sort(key=lambda s: s[0])
    non_overlapping = []
    last_end = -1
    for start, end, level, clause_type, page in spans:
        if start >= last_end:
            non_overlapping.append((start, end, level, clause_type, page))
            last_end = end

    pieces = []
    cursor = 0
    for start, end, level, clause_type, page in non_overlapping:
        pieces.append(escaped_text[cursor:start])
        color = LEVEL_COLOR[level]
        bg = LEVEL_BG[level]
        label = clause_type.replace("_", " ").title()
        tooltip = f"{label} ({level})" + (f", page {page}" if page else "")
        pieces.append(
            f'<mark title="{tooltip}" '
            f'style="background:{bg}; border-bottom:2px solid {color}; padding:0 2px;">'
            f"{escaped_text[start:end]}</mark>"
        )
        cursor = end
    pieces.append(escaped_text[cursor:])

    body = "".join(pieces).replace("\n", "<br>")
    return (
        '<div style="max-height:500px; overflow-y:auto; padding:16px; '
        'border:1px solid #ddd9d0; border-radius:10px; line-height:1.6; color:#161513; '
        'font-family:ui-monospace, monospace; font-size:0.9rem; white-space:pre-wrap;">'
        f"{body}</div>"
    )


def render_final_report(report: dict, contract_text: str, page_starts: list, filename_stem: str):
    """Renders the completed report (post human-review) — score, summary,
    flagged clauses with approval status, missing clauses, highlighted
    contract text, and downloads."""
    report = dict(report)
    report["flags"] = annotate_flags_with_pages(report["flags"], contract_text, page_starts)

    render_risk_score(report["risk_score"])

    st.subheader("Executive Summary")
    st.info(report["negotiation_memo"])

    summary = report["summary"]
    col1, col2, col3 = st.columns(3)
    col1.metric("Word count", summary["word_count"])
    col2.metric("Clauses found", f"{summary['clause_types_found']}/{summary['clause_types_total']}")
    col3.metric("Flagged clauses", len(report["flags"]))

    dl_col1, dl_col2 = st.columns(2)
    dl_col1.download_button(
        "⬇️ Download report (text)",
        data=build_text_report(report),
        file_name=f"{filename_stem}_review.txt",
        mime="text/plain",
        use_container_width=True,
    )
    dl_col2.download_button(
        "⬇️ Download report (JSON)",
        data=json.dumps(report, indent=2),
        file_name=f"{filename_stem}_review.json",
        mime="application/json",
        use_container_width=True,
    )

    st.subheader("Flagged Clauses")
    if not report["flags"]:
        st.write("No risk-rule matches found.")
    for flag in sorted(report["flags"], key=lambda f: {"high": 0, "medium": 1, "low": 2}[f["level"]]):
        approval_tag = ""
        if "human_approved" in flag:
            approval_tag = " ✅" if flag["human_approved"] else " ⚠️ not approved"
        with st.expander(f"{LEVEL_BADGE[flag['level']]} — {clause_label(flag)}{approval_tag}"):
            st.write(f"**Snippet:** {flag['snippet']}")
            st.write(f"**Reason:** {flag['reason']}")
            if "explanation" in flag:
                st.write(f"**Explanation:** {flag['explanation']}")
                st.write(f"**Suggested rewording (final, human-reviewed):** {flag['suggested_rewording']}")
            if "human_approved" in flag:
                st.caption("✅ Approved by human reviewer" if flag["human_approved"] else "⚠️ Not approved — flagged as pending during human review")

    st.subheader("Missing Clauses")
    missing_clauses = report["missing_clauses"]
    if isinstance(missing_clauses, str):
        missing_clauses = [missing_clauses]
    if missing_clauses:
        st.warning(", ".join(c.replace("_", " ").title() for c in missing_clauses))
    else:
        st.success("All standard clause types were found.")

    st.subheader("Contract Text with Risk Highlights")
    if not page_starts:
        st.caption("Page citations aren't available for this file type (PDF only).")
    if report["flags"]:
        legend = "".join(
            f'<span style="display:inline-flex;align-items:center;gap:6px;'
            f'background:{LEVEL_COLOR[lvl]}22;color:{LEVEL_COLOR[lvl]};'
            f'border:1px solid {LEVEL_COLOR[lvl]}55;border-radius:999px;'
            f'padding:3px 12px;font-size:0.85rem;font-weight:600;margin-right:8px;">'
            f'<span style="width:8px;height:8px;border-radius:50%;background:{LEVEL_COLOR[lvl]};"></span>'
            f'{lvl.title()}</span>'
            for lvl in ["high", "medium", "low"]
        )
        st.markdown(f'<div style="margin-bottom:10px;">{legend}</div>', unsafe_allow_html=True)
        st.markdown(build_highlighted_contract_html(contract_text, report["flags"]), unsafe_allow_html=True)
    else:
        st.write("No flagged clauses to highlight.")

    with st.expander("Full Report (JSON)"):
        st.json(report)


def render_human_review_form(flags_to_review: list, thread_id: str):
    """
    Renders the pause-point UI: one editable rewording + approve checkbox
    per high/medium flag. On submit, calls resume_review_sync to actually
    resume the paused LangGraph run (not a re-run) and stores the final
    report in session state.
    """
    st.markdown(
        f"""
        <div style="background:#fbead2; border:1px solid #e2b365; border-radius:10px;
                    padding:14px 18px; margin-bottom:16px; display:flex; align-items:center; gap:10px;">
            <span style="font-size:1.1rem;">⏸️</span>
            <span style="color:#6b4a12; font-weight:500;">
                Review paused for human approval — {len(flags_to_review)} clause(s) need your sign-off
                before the final report is generated.
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    for flag in sorted(flags_to_review, key=lambda f: {"high": 0, "medium": 1, "low": 2}[f["level"]]):
        key_base = flag["clause_type"]
        with st.container(border=True):
            st.markdown(f"**{LEVEL_BADGE[flag['level']]} — {flag['clause_type'].replace('_', ' ').title()}**")
            st.write(f"**Snippet:** {flag['snippet']}")
            st.write(f"**Reason:** {flag['reason']}")
            st.write(f"**Explanation:** {flag.get('explanation', '(none)')}")
            st.text_area(
                "Suggested rewording — edit as needed before approving",
                value=flag.get("suggested_rewording", ""),
                key=f"rewording__{key_base}",
                height=100,
            )
            st.checkbox(
                "✅ Approve this clause's rewording for the final report",
                key=f"approved__{key_base}",
            )

    if st.button("Submit review & finalize report", type="primary"):
        reviewed_flags = [
            {
                "clause_type": flag["clause_type"],
                "suggested_rewording": st.session_state.get(f"rewording__{flag['clause_type']}", flag.get("suggested_rewording", "")),
                "approved": st.session_state.get(f"approved__{flag['clause_type']}", False),
            }
            for flag in flags_to_review
        ]
        with st.spinner("Finalizing report..."):
            result = resume_review_sync(reviewed_flags, thread_id)
        st.session_state.review_result = result
        st.session_state.pending_review = None
        st.rerun()


st.title("📄 Clausegraph")
st.caption("Contract review and risk flagging, powered by a LangGraph pipeline with a human-in-the-loop review gate. Not legal advice.")

ensure_mcp_server_running()

# Session state for the two-step start/resume flow.
if "pending_review" not in st.session_state:
    st.session_state.pending_review = None  # {"thread_id", "flags", "contract_text", "page_starts", "filename_stem"}
if "review_result" not in st.session_state:
    st.session_state.review_result = None  # {"status": "done", "report": {...}}
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())

uploaded = st.file_uploader("Upload a contract", type=["pdf", "docx", "txt"])

jurisdiction = st.selectbox(
    "Jurisdiction (optional — grounds explanations in local legal context)",
    options=JURISDICTION_OPTIONS,
    index=0,
    help="Grounds LLM explanations and the executive summary in jurisdiction-specific "
         "reference notes from the knowledge base. Choose 'None' to skip this.",
)
jurisdiction_value = "" if jurisdiction == "None" else jurisdiction

if uploaded:
    tmp_path = ROOT / f"_upload{Path(uploaded.name).suffix}"
    tmp_path.write_bytes(uploaded.getvalue())

    with st.spinner("Extracting text..."):
        contract_text, page_starts = extract_text_with_pages(str(tmp_path))

    # Case 1: a review is currently paused, waiting for human input.
    if st.session_state.pending_review:
        pending = st.session_state.pending_review
        render_human_review_form(pending["flags"], pending["thread_id"])

    # Case 2: a review just completed — show the final report.
    elif st.session_state.review_result:
        render_final_report(
            st.session_state.review_result["report"],
            contract_text,
            page_starts,
            Path(uploaded.name).stem,
        )
        if st.button("Start a new review"):
            st.session_state.review_result = None
            st.session_state.thread_id = str(uuid.uuid4())
            st.rerun()

    # Case 3: nothing started yet — show the "Run review" button.
    else:
        if st.button("Run review", type="primary"):
            with st.spinner("Identifying clauses, flagging risks, generating explanations..."):
                result = start_review_sync(contract_text, jurisdiction_value, st.session_state.thread_id)

            if result["status"] == "needs_review":
                st.session_state.pending_review = {
                    "thread_id": st.session_state.thread_id,
                    "flags": result["review_payload"]["flags"],
                }
                st.rerun()
            else:
                st.session_state.review_result = result
                st.rerun()

    tmp_path.unlink(missing_ok=True)
else:
    st.info("Upload a .pdf, .docx, or .txt contract to begin.")
    st.session_state.pending_review = None
    st.session_state.review_result = None


st.divider()
st.subheader("🔁 Compare Two Contract Versions")
st.caption("See what changed between an older and newer version — added, removed, or reworded clauses, and new or resolved risks. (No human review gate in this flow.)")

comp_col1, comp_col2 = st.columns(2)
uploaded_a = comp_col1.file_uploader("Older / baseline version", type=["pdf", "docx", "txt"], key="compare_a")
uploaded_b = comp_col2.file_uploader("Newer version", type=["pdf", "docx", "txt"], key="compare_b")

if uploaded_a and uploaded_b:
    if st.button("Compare versions", type="primary"):
        tmp_a = ROOT / f"_compare_a{Path(uploaded_a.name).suffix}"
        tmp_b = ROOT / f"_compare_b{Path(uploaded_b.name).suffix}"
        tmp_a.write_bytes(uploaded_a.getvalue())
        tmp_b.write_bytes(uploaded_b.getvalue())

        with st.spinner("Extracting text from both versions..."):
            text_a, _ = extract_text_with_pages(str(tmp_a))
            text_b, _ = extract_text_with_pages(str(tmp_b))

        with st.spinner("Comparing clauses and risks..."):
            diff = run_comparison_sync(text_a, text_b)

        tmp_a.unlink(missing_ok=True)
        tmp_b.unlink(missing_ok=True)

        delta = diff["risk_score_delta"]
        if delta > 0:
            st.error(f"Risk score increased by {delta} points ({diff['risk_score_a']} → {diff['risk_score_b']}/100).")
        elif delta < 0:
            st.success(f"Risk score decreased by {abs(delta)} points ({diff['risk_score_a']} → {diff['risk_score_b']}/100).")
        else:
            st.info(f"Risk score unchanged ({diff['risk_score_b']}/100).")

        d_col1, d_col2, d_col3 = st.columns(3)
        with d_col1:
            st.markdown("**➕ Added clauses**")
            if diff["added_clause_types"]:
                for c in diff["added_clause_types"]:
                    st.write(f"- {c.replace('_', ' ').title()}")
            else:
                st.write("None")
        with d_col2:
            st.markdown("**➖ Removed clauses**")
            if diff["removed_clause_types"]:
                for c in diff["removed_clause_types"]:
                    st.write(f"- {c.replace('_', ' ').title()}")
            else:
                st.write("None")
        with d_col3:
            st.markdown("**✏️ Reworded clauses**")
            if diff["changed_clause_types"]:
                for c in diff["changed_clause_types"]:
                    st.write(f"- {c.replace('_', ' ').title()}")
            else:
                st.write("None")

        r_col1, r_col2 = st.columns(2)
        with r_col1:
            st.markdown("**🆕 New risks introduced**")
            if diff["new_risks"]:
                for r in diff["new_risks"]:
                    st.write(f"- {LEVEL_BADGE[r['level']]} {r['clause_type'].replace('_', ' ').title()}")
            else:
                st.write("None")
        with r_col2:
            st.markdown("**✅ Risks resolved**")
            if diff["resolved_risks"]:
                for r in diff["resolved_risks"]:
                    st.write(f"- {LEVEL_BADGE[r['level']]} {r['clause_type'].replace('_', ' ').title()}")
            else:
                st.write("None")
else:
    st.caption("Upload both versions above to compare them.")
