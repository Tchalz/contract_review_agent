"""
Streamlit front end for the contract review agent.

On first run, launches the MCP server (mcp_server/app.py) as a background
subprocess if it isn't already reachable, then drives the LangGraph
pipeline (pipeline/graph.py) against the uploaded contract.
"""

import subprocess
import sys
import time
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "mcp_server"))

from parsing import extract_text  # noqa: E402
from graph import run_review_sync  # noqa: E402

MCP_SERVER_SCRIPT = ROOT / "mcp_server" / "app.py"

LEVEL_BADGE = {"high": "🔴 High", "medium": "🟡 Medium", "low": "🟢 Low"}

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


st.title("📄 Contract Review & Risk Flagging Agent")
st.caption("LangGraph pipeline over an MCP contract-analysis server. Not legal advice.")

ensure_mcp_server_running()

uploaded = st.file_uploader("Upload a contract", type=["pdf", "docx", "txt"])

if uploaded:
    tmp_path = ROOT / f"_upload{Path(uploaded.name).suffix}"
    tmp_path.write_bytes(uploaded.getvalue())

    with st.spinner("Extracting text..."):
        contract_text = extract_text(str(tmp_path))

    if st.button("Run review", type="primary"):
        with st.spinner("Identifying clauses, flagging risks, generating explanations..."):
            report = run_review_sync(contract_text)

        score = report["risk_score"]
        st.metric("Overall Risk Score", f"{score}/100")

        summary = report["summary"]
        col1, col2, col3 = st.columns(3)
        col1.metric("Word count", summary["word_count"])
        col2.metric("Clauses found", f"{summary['clause_types_found']}/{summary['clause_types_total']}")
        col3.metric("Flagged clauses", len(report["flags"]))

        st.subheader("Flagged Clauses")
        if not report["flags"]:
            st.write("No risk-rule matches found.")
        for flag in sorted(report["flags"], key=lambda f: {"high": 0, "medium": 1, "low": 2}[f["level"]]):
            with st.expander(f"{LEVEL_BADGE[flag['level']]} — {flag['clause_type'].replace('_', ' ').title()}"):
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

        with st.expander("Full Report (JSON)"):
            st.json(report)

    tmp_path.unlink(missing_ok=True)
else:
    st.info("Upload a .pdf, .docx, or .txt contract to begin.")
