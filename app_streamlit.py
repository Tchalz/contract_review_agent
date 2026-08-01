"""
Streamlit front end for the contract review agent.

On first run, launches the MCP server (mcp_server/app.py) as a background
subprocess if it isn't already reachable, then drives the LangGraph
pipeline (pipeline/graph.py) against the uploaded contract.

A jurisdiction selector lets the user ground LLM-backed explanations in
jurisdiction-specific reference notes (see mcp_server/knowledge_base.py).
Selecting "None" preserves the original, ungrounded behavior.
"""

import html
import json
import subprocess
import sys
import time
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "mcp_server"))

from parsing import extract_text_with_pages, page_number_at  # noqa: E402
from graph import run_review_sync, run_comparison_sync  # noqa: E402

MCP_SERVER_SCRIPT = ROOT / "mcp_server" / "app.py"

LEVEL_BADGE = {"high": "🔴 High", "medium": "🟡 Medium", "low": "🟢 Low"}
LEVEL_COLOR = {"high": "#ef4444", "medium": "#f59e0b", "low": "#22c55e"}
LEVEL_BG = {"high": "rgba(239, 68, 68, 0.28)", "medium": "rgba(245, 158, 11, 0.28)", "low": "rgba(34, 197, 94, 0.28)"}

# Jurisdictions with reference notes in mcp_server/knowledge_base.py.
# "None" disables jurisdiction grounding entirely (original behavior).
JURISDICTION_OPTIONS = ["None", "Nigeria", "US", "EU"]

st.set_page_config(page_title="Contract Review Agent", page_icon="📄", layout="wide")


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
            <div style="font-size:0.85rem; opacity:0.8; margin-bottom:4px;">Overall Risk Score</div>
            <div style="display:flex; align-items:baseline; gap:12px;">
                <span style="font-size:2.2rem; font-weight:700; color:{color};">{score}/100</span>
                <span style="font-size:1rem; font-weight:600; color:{color};">{label}</span>
            </div>
            <div style="background:#ffffff22; border-radius:6px; height:8px; margin-top:10px; overflow:hidden;">
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
        lines.append(f"[{flag['level'].upper()}] {clause_label(flag)}")
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
        'border:1px solid #ffffff22; border-radius:10px; line-height:1.6; '
        'font-family:ui-monospace, monospace; font-size:0.85rem; white-space:pre-wrap;">'
        f"{body}</div>"
    )


st.title("📄 Contract Review & Risk Flagging Agent")
st.caption("LangGraph pipeline over an MCP contract-analysis server. Not legal advice.")

ensure_mcp_server_running()

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

    if st.button("Run review", type="primary"):
        with st.spinner("Identifying clauses, flagging risks, generating explanations..."):
            report = run_review_sync(contract_text, jurisdiction_value)
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
            file_name=f"{Path(uploaded.name).stem}_review.txt",
            mime="text/plain",
            use_container_width=True,
        )
        dl_col2.download_button(
            "⬇️ Download report (JSON)",
            data=json.dumps(report, indent=2),
            file_name=f"{Path(uploaded.name).stem}_review.json",
            mime="application/json",
            use_container_width=True,
        )

        st.subheader("Flagged Clauses")
        if not report["flags"]:
            st.write("No risk-rule matches found.")
        for flag in sorted(report["flags"], key=lambda f: {"high": 0, "medium": 1, "low": 2}[f["level"]]):
            with st.expander(f"{LEVEL_BADGE[flag['level']]} — {clause_label(flag)}"):
                st.write(f"**Snippet:** {flag['snippet']}")
                st.write(f"**Reason:** {flag['reason']}")
                if "explanation" in flag:
                    st.write(f"**Explanation:** {flag['explanation']}")
                    st.write(f"**Suggested rewording:** {flag['suggested_rewording']}")

        st.subheader("Missing Clauses")
        missing_clauses = report["missing_clauses"]
        if isinstance(missing_clauses, str):
            # The backend returns a bare string instead of a list when
            # exactly one clause is missing — normalize here so it isn't
            # iterated character-by-character (e.g. "renewal" -> "r","e",...).
            missing_clauses = [missing_clauses]
        if missing_clauses:
            st.warning(", ".join(c.replace("_", " ").title() for c in missing_clauses))
        else:
            st.success("All standard clause types were found.")

        st.subheader("Contract Text with Risk Highlights")
        if not page_starts:
            st.caption("Page citations aren't available for this file type (PDF only).")
        if report["flags"]:
            legend = "  ".join(
                f'<span style="color:{LEVEL_COLOR[lvl]};">●</span> {lvl.title()}'
                for lvl in ["high", "medium", "low"]
            )
            st.markdown(legend, unsafe_allow_html=True)
            st.markdown(build_highlighted_contract_html(contract_text, report["flags"]), unsafe_allow_html=True)
        else:
            st.write("No flagged clauses to highlight.")

        with st.expander("Full Report (JSON)"):
            st.json(report)

    tmp_path.unlink(missing_ok=True)
else:
    st.info("Upload a .pdf, .docx, or .txt contract to begin.")


st.divider()
st.subheader("🔁 Compare Two Contract Versions")
st.caption("See what changed between an older and newer version — added, removed, or reworded clauses, and new or resolved risks.")

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
