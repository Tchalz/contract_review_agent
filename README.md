# Contract Review Agent — RAG Grounding + Human-in-the-Loop Update

This package adds two things to your contract review agent:

1. **RAG grounding** — LLM-generated risk explanations and the executive
   summary now reference a curated knowledge base (standard clause
   language, red flags, and jurisdiction-specific notes — including
   Nigeria) instead of relying purely on the model's general training
   knowledge.
2. **Human-in-the-loop review gate** — the LangGraph pipeline now genuinely
   pauses (via LangGraph's `interrupt()`) after generating explanations and
   suggested rewordings for every high/medium flagged clause, and waits
   for a human to edit and approve each one before the final report is
   assembled. This is a real pause/resume of the graph's execution, backed
   by a LangGraph checkpointer — not a UI-only trick.
3. **Bugfix** — your `.env` file was never actually being loaded (no
   `load_dotenv()` call existed anywhere in the project), so
   `OPENROUTER_API_KEY` was always empty and every LLM call was silently
   falling back to templated text. This is fixed in the packaged `app.py`.

## What's in this package

```
contract_review_agent_updates/
├── mcp_server/
│   ├── app.py              (MODIFIED — knowledge-base grounding + load_dotenv() fix)
│   └── knowledge_base.py   (NEW — reference standards, red flags, jurisdiction notes for all 11 clause types)
├── pipeline/
│   └── graph.py            (MODIFIED — jurisdiction grounding + LangGraph interrupt-based human review gate)
└── app_streamlit.py        (MODIFIED — jurisdiction dropdown + two-step review/approve UI)
```

`risk_rules.py`, `mcp_client.py`, `parsing.py`, and `client.py` are
**unchanged** — do not overwrite them.

## Before you install: one new dependency check

The human review gate uses LangGraph's `MemorySaver` checkpointer, which
ships as part of `langgraph` itself — no new package should be needed if
you already have `langgraph` installed. Confirm:
```powershell
pip show langgraph
```
If that fails, install it:
```powershell
pip install langgraph --break-system-packages
```
(or without `--break-system-packages` if you're inside your `venv`, which
you should be — check your prompt shows `(contract_review_agent)`).

## How to install

1. Back up your current project first (copy the folder, or `git commit` if
   you're using version control — you should be, see our earlier setup).

2. Copy each file below into the matching path in your project, overwriting
   the existing file where one already exists:

   | From this package                  | To your project                              |
   |-------------------------------------|-----------------------------------------------|
   | `mcp_server/app.py`                 | `mcp_server/app.py` (overwrite)                |
   | `mcp_server/knowledge_base.py`       | `mcp_server/knowledge_base.py` (new file)      |
   | `pipeline/graph.py`                  | `pipeline/graph.py` (overwrite)                |
   | `app_streamlit.py`                   | `app_streamlit.py` (overwrite, at project root)|

   In PowerShell, from inside the folder you extracted this zip to:
   ```powershell
   Copy-Item .\mcp_server\app.py "C:\Users\MSS Tech HP 02\Desktop\contract_review_agent\mcp_server\app.py" -Force
   Copy-Item .\mcp_server\knowledge_base.py "C:\Users\MSS Tech HP 02\Desktop\contract_review_agent\mcp_server\knowledge_base.py" -Force
   Copy-Item .\pipeline\graph.py "C:\Users\MSS Tech HP 02\Desktop\contract_review_agent\pipeline\graph.py" -Force
   Copy-Item .\app_streamlit.py "C:\Users\MSS Tech HP 02\Desktop\contract_review_agent\app_streamlit.py" -Force
   ```

3. Kill any running processes and restart clean:
   ```powershell
   Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
   cd "C:\Users\MSS Tech HP 02\Desktop\contract_review_agent"
   streamlit run app_streamlit.py
   ```
   The MCP server restarts automatically as a background process.

4. Confirm the `.env` fix worked — the MCP server's console output isn't
   visible when launched by Streamlit (piped to DEVNULL), so to double
   check independently:
   ```powershell
   cd mcp_server
   python app.py
   ```
   You should see:
   ```
   LLM backend: OpenRouter (openai/gpt-4o-mini)
   ```
   not "none — using rule-based fallbacks". Stop it (Ctrl+C) and go back
   to running the full Streamlit app.

5. Test the new flow: upload the sample contract, pick **Nigeria** from the
   jurisdiction dropdown, click **Run review**. The app should pause with
   a message like *"⏸️ Review paused for human approval — N clause(s) need
   your sign-off"* and show each flagged clause with an editable rewording
   textbox and an approve checkbox. Edit/approve as you like, then click
   **Submit review & finalize report** — the final report renders exactly
   as before, but each flagged clause now shows an ✅/⚠️ approval badge.

## What changed, in plain terms

- **`knowledge_base.py`** (new) — for each of your 11 clause types, stores
  standard/expected clause language, known red flags, and
  jurisdiction-specific notes (Nigeria filled in; US/EU are thinner —
  worth expanding).

- **`app.py`** — `explain_risk` and `generate_negotiation_memo` accept
  optional `clause_type`/`jurisdiction` and ground their LLM prompt in the
  matching knowledge base entry when provided. Also now calls
  `load_dotenv()` so `.env` is actually read (this was the bug causing
  identical explanation/reason/fallback text).

- **`graph.py`** — two significant changes:
  - `jurisdiction` flows through the pipeline into the grounded tool calls.
  - The graph now has a `human_review` node that calls LangGraph's
    `interrupt()` after `explain_risks`, genuinely pausing execution. New
    entry points: `start_review_sync(contract_text, jurisdiction, thread_id)`
    to run until the pause (or straight through if there's nothing to
    review), and `resume_review_sync(reviewed_flags, thread_id)` to resume
    the *same paused run* with human-provided edits/approvals. The old
    `run_review_sync` still exists for backwards compatibility (auto-
    approves everything silently, no pause) but new code should use the
    two-step functions.

- **`app_streamlit.py`** — restructured around `st.session_state` to
  handle the two-step flow across Streamlit reruns:
  - A jurisdiction dropdown (None / Nigeria / US / EU).
  - "Run review" now calls `start_review_sync`. If the graph pauses, an
    editable review form renders (one block per flagged clause: rewording
    textarea + approve checkbox).
  - "Submit review & finalize report" calls `resume_review_sync`, which
    resumes the actual paused graph, and the final report renders with
    ✅/⚠️ approval badges per clause.
  - A "Start a new review" button resets state (generates a fresh
    `thread_id`) so you can review another contract.

## Important notes on the review gate

- **The checkpointer (`MemorySaver`) is in-memory only.** If you restart
  the Streamlit/MCP processes while a review is paused mid-approval,
  that paused state is lost — you'll need to click "Run review" again
  from scratch. For a review tool used interactively in one sitting this
  is fine; if you need reviews to survive app restarts, swap
  `MemorySaver` for a persistent LangGraph checkpointer (e.g. SQLite-
  backed) in `graph.py`.
- **Low-risk flags are never paused on** — only `high`/`medium` flags are
  surfaced for review, matching `EXPLAIN_LEVELS`. Low-risk flags are
  auto-marked `human_approved: True` since they were never shown.
- **If there's nothing to review**, `start_review_sync` returns
  `{"status": "done", ...}` immediately — no pause, no extra click needed.

## Extending it further

- **Add more jurisdictions**: open `knowledge_base.py`, add a new key
  under each clause type's `jurisdiction_notes`, then add the name to
  `JURISDICTION_OPTIONS` in `app_streamlit.py`.
- **Persist paused reviews across restarts**: swap `MemorySaver` for
  `langgraph.checkpoint.sqlite.SqliteSaver` (or similar) in `graph.py`.
- **Require approval before download**: currently the final report
  downloads regardless of per-clause approval status (it just shows the
  ✅/⚠️ badge). If you want to *block* downloading until every flag is
  approved, that's a small addition to `render_final_report` — ask if you
  want this built out.

None of this is legal advice — it's reference material and a review
workflow to make the tool's output more consistent, specific, auditable,
and human-checked before anything is finalized. Always have flagged,
high-stakes contracts reviewed by a qualified lawyer.
#   c l a u s e - g r a p h  
 