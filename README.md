# Contract Review & Risk Flagging Agent

Uploads a contract, identifies its clauses, flags risky language, explains
the risk in plain language, suggests safer wording, checks for missing
standard clauses, and produces a scored report — all not legal advice, just
a fast first pass before a qualified reviewer looks at it.

## Architecture

```
Streamlit UI  →  LangGraph pipeline  →  MCP Client  →  MCP Server (contract-analysis tools)
(app_streamlit.py)  (pipeline/graph.py)          (mcp_server/app.py)
```

- **MCP server** (`mcp_server/app.py`) owns the analysis logic as MCP tools:
  `identify_clauses`, `flag_risks`, `explain_risk`, `suggest_rewording`,
  `detect_missing_clauses`, `summarize_contract`. Risk rules and the
  standard clause checklist live in `mcp_server/risk_rules.py`, separate
  from server logic, so the ruleset can be extended without touching tool
  code.
- **LangGraph pipeline** (`pipeline/graph.py`) owns the *flow* — it calls
  the MCP tools in sequence and decides which flagged clauses are worth an
  LLM explanation call (high/medium risk only, to keep runs fast and cheap).
- **Streamlit UI** (`app_streamlit.py`) uploads a file, extracts text
  (`pipeline/parsing.py`, supports PDF/DOCX/TXT), triggers the pipeline, and
  renders the report with risk badges.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # optionally add an OPENROUTER_API_KEY
```

Without an `OPENROUTER_API_KEY`, `explain_risk` and `suggest_rewording`
still work — they fall back to the rule-based reason text instead of an
LLM-generated explanation. Everything else (clause detection, risk
scoring, missing-clause detection, summarization) is rule-based and needs
no API key at all.

## Run

**Option A — two terminals (server + client), same as the Library MCP Server:**

Terminal 1:
```bash
cd mcp_server
python app.py
```

Terminal 2:
```bash
python client.py path/to/contract.pdf
```

`client.py` connects to whatever `MCP_SERVER_URL` points to (default
`http://127.0.0.1:8002/mcp`, matching the server's default port) and prints
the report to the terminal. It does not start the server itself — if it's
not running, you'll get a clear connection error.

**Option B — Streamlit UI (auto-launches the server for you):**

```bash
streamlit run app_streamlit.py
```

**Inspecting the MCP server on its own, without any client:**

```bash
cd mcp_server
fastmcp inspect app.py   # schema + tool counts
fastmcp dev app.py       # interactive MCP Inspector UI
```

## Extending

- **New clause types / risk rules:** edit `mcp_server/risk_rules.py` only.
- **New pipeline steps** (e.g. contract version comparison, PDF report
  export): add a node to `pipeline/graph.py` and, if it needs new analysis
  logic, a matching tool in `mcp_server/app.py`.
- **Swap the LLM:** change `OPENROUTER_MODEL` in `.env`, or point
  `mcp_server/app.py`'s client at a different OpenAI-compatible base URL.

## Status

Scaffold / portfolio project — rule-based analysis is functional end-to-end
with zero API keys; LLM-backed explanations require an OpenRouter key.
Not yet deployed publicly.
