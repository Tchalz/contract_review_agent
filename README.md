Contract Review Agent — RAG Grounding + Human-in-the-Loop Update

This update adds two major capabilities to your contract review agent, plus a bugfix.

What's new
RAG grounding — LLM-generated risk explanations and the executive summary now draw on a curated knowledge base (standard clause language, red flags, and jurisdiction-specific notes, including Nigeria) instead of relying solely on the model's general training knowledge.
Human-in-the-loop review gate — the LangGraph pipeline now genuinely pauses, via LangGraph's interrupt(), after generating explanations and suggested rewordings for every high- or medium-risk flagged clause. It waits for a human to edit and approve each one before the final report is assembled. This is a real pause/resume of the graph's execution, backed by a LangGraph checkpointer — not just a UI-level trick.
Bugfix — the project was never actually loading .env (no load_dotenv() call existed anywhere), so OPENROUTER_API_KEY was always empty and every LLM call silently fell back to templated text. This is now fixed in app.py.
Package contents
contract_review_agent_updates/
├── mcp_server/
│   ├── app.py              (MODIFIED — knowledge-base grounding + load_dotenv() fix)
│   └── knowledge_base.py   (NEW — reference standards, red flags, jurisdiction notes for all 11 clause types)
├── pipeline/
│   └── graph.py             (MODIFIED — jurisdiction grounding + interrupt-based human review gate)
└── app_streamlit.py         (MODIFIED — jurisdiction dropdown + two-step review/approve UI)

risk_rules.py, mcp_client.py, parsing.py, and client.py are unchanged — do not overwrite them.

Before you install: dependency check

The human review gate uses LangGraph's MemorySaver checkpointer, which ships with langgraph itself — no new package should be needed if langgraph is already installed. Confirm with:

powershell
pip show langgraph

If that fails, install it:

powershell
pip install langgraph --break-system-packages

(Omit --break-system-packages if you're inside your venv — check that your prompt shows (contract_review_agent).)

Installation

1. Back up your current project. Copy the folder, or git commit if you're using version control.

2. Copy each file into your project, overwriting where a file already exists:

From this package	To your project
mcp_server/app.py	mcp_server/app.py (overwrite)
mcp_server/knowledge_base.py	mcp_server/knowledge_base.py (new file)
pipeline/graph.py	pipeline/graph.py (overwrite)
app_streamlit.py	app_streamlit.py (overwrite, at project root)

From inside the folder you extracted this zip to, in PowerShell:

powershell
Copy-Item .\mcp_server\app.py "C:\Users\MSS Tech HP 02\Desktop\contract_review_agent\mcp_server\app.py" -Force
Copy-Item .\mcp_server\knowledge_base.py "C:\Users\MSS Tech HP 02\Desktop\contract_review_agent\mcp_server\knowledge_base.py" -Force
Copy-Item .\pipeline\graph.py "C:\Users\MSS Tech HP 02\Desktop\contract_review_agent\pipeline\graph.py" -Force
Copy-Item .\app_streamlit.py "C:\Users\MSS Tech HP 02\Desktop\contract_review_agent\app_streamlit.py" -Force

3. Restart clean:

powershell
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
cd "C:\Users\MSS Tech HP 02\Desktop\contract_review_agent"
streamlit run app_streamlit.py

The MCP server restarts automatically as a background process.

4. Confirm the .env fix worked. The MCP server's console output isn't visible when launched by Streamlit (it's piped to DEVNULL), so check independently:

powershell
cd mcp_server
python app.py

You should see:

LLM backend: OpenRouter (openai/gpt-4o-mini)

not none — using rule-based fallbacks. Stop it with Ctrl+C, then go back to running the full Streamlit app.

5. Test the new flow. Upload the sample contract, pick Nigeria from the jurisdiction dropdown, and click Run review. The app should pause with a message like "⏸️ Review paused for human approval — N clause(s) need your sign-off" and show each flagged clause with an editable rewording box and an approve checkbox. Edit and approve as needed, then click Submit review & finalize report — the final report renders as before, but each flagged clause now carries a ✅/⚠️ approval badge.

What changed, in plain terms
knowledge_base.py (new) — for each of the 11 clause types, stores standard/expected clause language, known red flags, and jurisdiction-specific notes. Nigeria is filled in; US and EU are thinner and worth expanding.
app.py — explain_risk and generate_negotiation_memo now accept optional clause_type/jurisdiction arguments and ground their LLM prompt in the matching knowledge base entry when provided. Also now calls load_dotenv(), so .env is actually read — this was the bug causing identical explanation/reason/fallback text.
graph.py — two significant changes:
jurisdiction now flows through the pipeline into the grounded tool calls.
A new human_review node calls LangGraph's interrupt() after explain_risks, genuinely pausing execution. Two new entry points support this: start_review_sync(contract_text, jurisdiction, thread_id) runs until the pause (or straight through if nothing needs review), and resume_review_sync(reviewed_flags, thread_id) resumes that same paused run with human-provided edits and approvals. run_review_sync still exists for backwards compatibility (auto-approves everything silently, no pause), but new code should use the two-step functions.
app_streamlit.py — restructured around st.session_state to handle the two-step flow across Streamlit reruns:
A jurisdiction dropdown (None / Nigeria / US / EU).
"Run review" calls start_review_sync. If the graph pauses, an editable review form renders — one block per flagged clause, with a rewording textarea and an approve checkbox.
"Submit review & finalize report" calls resume_review_sync, resuming the actual paused graph; the final report then renders with ✅/⚠️ approval badges per clause.
A "Start a new review" button resets state (generates a fresh thread_id) so you can review another contract.
Notes on the review gate
The checkpointer (MemorySaver) is in-memory only. If you restart the Streamlit/MCP processes while a review is paused mid-approval, that paused state is lost — you'll need to click "Run review" again from scratch. This is fine for interactive use in one sitting; if reviews need to survive app restarts, swap MemorySaver for a persistent checkpointer (e.g. SQLite-backed) in graph.py.
Low-risk flags are never paused on — only high/medium flags are surfaced for review, matching EXPLAIN_LEVELS. Low-risk flags are auto-marked human_approved: True since they were never shown.
If there's nothing to review, start_review_sync returns {"status": "done", ...} immediately — no pause, no extra click needed.
Extending it further
Add more jurisdictions: open knowledge_base.py, add a new key under each clause type's jurisdiction_notes, then add the name to JURISDICTION_OPTIONS in app_streamlit.py.
Persist paused reviews across restarts: swap MemorySaver for langgraph.checkpoint.sqlite.SqliteSaver (or similar) in graph.py.
Require approval before download: currently the final report downloads regardless of per-clause approval status (it just shows the ✅/⚠️ badge). Blocking download until every flag is approved is a small addition to render_final_report — ask if you'd like this built out.

None of this is legal advice — it's reference material and a review workflow meant to make the tool's output more consistent, specific, auditable, and human-checked before anything is finalized. Always have flagged, high-stakes contracts reviewed
