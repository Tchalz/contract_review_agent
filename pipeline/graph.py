"""
LangGraph orchestration for the contract review pipeline.

The graph owns *flow* (what runs, in what order, and which risky clauses
get the expensive LLM explain/reword calls, and where the pipeline pauses
for human review) while mcp_server/app.py owns the actual analysis logic,
called here as MCP tools. Swapping or extending the analysis (new clause
types, smarter risk rules, a real classifier) never requires touching this
file.

An optional `jurisdiction` string flows through ContractState from
start_review/start_review_sync into the explain_risk and
generate_negotiation_memo tool calls, so the MCP server can ground its
LLM-backed explanations in jurisdiction-specific reference notes (see
mcp_server/knowledge_base.py) when one is supplied. Leave it empty/omitted
to fall back to the original ungrounded behavior.

Human-in-the-loop review gate
------------------------------
After explanations/rewordings are generated for every high/medium flag,
the graph genuinely pauses (via LangGraph's interrupt()) at the
human_review node, surfacing those flags to the caller. The caller (e.g.
Streamlit) shows an editable UI, collects edited rewordings + approval
per flag, and resumes the graph with that data — only then does it
continue to missing-clause detection, summarization, and the final memo.

This requires a checkpointer (MemorySaver here — in-process, cleared on
restart; swap for a persistent checkpointer if you need reviews to survive
an app restart) and a thread_id per review session, so the graph knows
which paused run to resume.

Two-call usage pattern:
    result = start_review_sync(contract_text, jurisdiction, thread_id)
    # result["status"] == "needs_review" -> show result["review_payload"]["flags"]
    # ... human edits/approves in the UI ...
    result = resume_review_sync(reviewed_flags, thread_id)
    # result["status"] == "done" -> result["report"] is the final report

If there are no high/medium flags to review, the graph runs straight
through to completion in a single start_review_sync call (status "done"
immediately) — no empty pause.

Single-contract review flow:
    identify_clauses -> flag_risks -> explain_risks -> human_review (pause)
                      -> detect_missing_clauses -> summarize
                      -> generate_memo -> assemble_report

Two-contract comparison flow (build_comparison_graph / run_comparison_sync):
    identify_both -> flag_both -> diff -> assemble_comparison
Both contracts are identified and flagged in parallel (two MCP calls at
once per stage) since they're independent of each other until the diff
step, which is pure Python — no MCP round-trip needed. The comparison flow
does not include a human review gate.
"""

import asyncio
from typing import TypedDict

from langgraph.graph import StateGraph, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import MemorySaver

from mcp_client import mcp_session, call_tool

# Risk levels worth spending an LLM call to explain/reword, and worth
# pausing for human review. Low-risk clauses are reported without an
# explanation or a review step, to keep runs fast and cheap.
EXPLAIN_LEVELS = {"high", "medium"}

# In-process checkpointer shared across all review sessions in this run of
# the app. Keyed internally by thread_id, so multiple concurrent reviews
# (e.g. different browser tabs/sessions) don't collide. Lost on restart —
# an interrupted review that hasn't been resumed before the app restarts
# will need to start over.
_checkpointer = MemorySaver()


class ContractState(TypedDict, total=False):
    contract_text: str
    jurisdiction: str
    clauses: dict
    risk_flags: list
    risk_score: int
    explained_flags: list
    missing_clauses: list
    summary: dict
    negotiation_memo: str
    report: dict


def build_graph(session):
    """Builds the StateGraph, closing over an open MCP ClientSession so
    every node can call tools without re-negotiating a connection.
    Compiled with a checkpointer so the graph can pause at human_review
    and be resumed later, potentially via a different session/connection."""

    async def identify_clauses_node(state: ContractState) -> dict:
        clauses = await call_tool(session, "identify_clauses", {"contract_text": state["contract_text"]})
        return {"clauses": clauses}

    async def flag_risks_node(state: ContractState) -> dict:
        result = await call_tool(session, "flag_risks", {"clauses": state["clauses"]})
        return {"risk_flags": result["flags"], "risk_score": result["risk_score"]}

    async def explain_risks_node(state: ContractState) -> dict:
        """
        Explains and rewords every flagged clause that meets EXPLAIN_LEVELS,
        concurrently rather than one at a time. Passes clause_type and
        jurisdiction into explain_risk so the MCP server can ground its
        explanation in the knowledge base when a jurisdiction is set in
        state. Every flag not meeting EXPLAIN_LEVELS passes through
        unmodified — it won't be shown in the human review step either.
        """
        async def process(flag: dict) -> dict:
            entry = dict(flag)
            if flag["level"] in EXPLAIN_LEVELS:
                explanation, rewording = await asyncio.gather(
                    call_tool(session, "explain_risk", {
                        "clause_text": flag["snippet"],
                        "reason": flag["reason"],
                        "clause_type": flag["clause_type"],
                        "jurisdiction": state.get("jurisdiction", ""),
                    }),
                    call_tool(session, "suggest_rewording", {"clause_text": flag["snippet"]}),
                )
                entry["explanation"] = explanation
                entry["suggested_rewording"] = rewording
            return entry

        explained = await asyncio.gather(*(process(flag) for flag in state["risk_flags"]))
        return {"explained_flags": list(explained)}

    async def human_review_node(state: ContractState) -> dict:
        """
        Pauses the graph (if there's anything to review) and surfaces every
        high/medium flag to the caller for editing and approval. Resumes
        with a list of {clause_type, suggested_rewording, approved} dicts,
        one per reviewed flag, which get merged back into explained_flags.

        Flags below EXPLAIN_LEVELS (low risk, no explanation/rewording)
        pass through untouched and are marked human_approved=True by
        default, since they were never surfaced for review.
        """
        needs_review = [f for f in state["explained_flags"] if f["level"] in EXPLAIN_LEVELS]

        if not needs_review:
            # Nothing to review — mark everything approved and continue
            # straight through without pausing.
            passthrough = [dict(f, human_approved=True) for f in state["explained_flags"]]
            return {"explained_flags": passthrough}

        reviewed = interrupt({
            "type": "review_flags",
            "flags": needs_review,
        })
        # `reviewed` is whatever the caller passes to Command(resume=...):
        # expected shape is a list of dicts with clause_type,
        # suggested_rewording, and approved keys, one per flag in
        # needs_review (order not required to match).
        reviewed_by_type = {r["clause_type"]: r for r in reviewed}

        merged = []
        for f in state["explained_flags"]:
            if f["clause_type"] in reviewed_by_type:
                r = reviewed_by_type[f["clause_type"]]
                entry = dict(f)
                entry["suggested_rewording"] = r.get("suggested_rewording", f.get("suggested_rewording"))
                entry["human_approved"] = bool(r.get("approved", False))
                merged.append(entry)
            else:
                # Below EXPLAIN_LEVELS, never surfaced for review.
                merged.append(dict(f, human_approved=True))
        return {"explained_flags": merged}

    async def missing_clauses_node(state: ContractState) -> dict:
        missing = await call_tool(session, "detect_missing_clauses", {"clauses": state["clauses"]})
        return {"missing_clauses": missing}

    async def summarize_node(state: ContractState) -> dict:
        summary = await call_tool(
            session, "summarize_contract",
            {"contract_text": state["contract_text"], "clauses": state["clauses"]},
        )
        return {"summary": summary}

    async def generate_memo_node(state: ContractState) -> dict:
        memo = await call_tool(
            session, "generate_negotiation_memo",
            {
                "risk_score": state["risk_score"],
                "flags": state["explained_flags"],
                "missing_clauses": state["missing_clauses"],
                "jurisdiction": state.get("jurisdiction", ""),
            },
        )
        return {"negotiation_memo": memo}

    async def assemble_report_node(state: ContractState) -> dict:
        report = {
            "risk_score": state["risk_score"],
            "summary": state["summary"],
            "flags": state["explained_flags"],
            "missing_clauses": state["missing_clauses"],
            "clause_types_found": list(state["clauses"].keys()),
            "negotiation_memo": state["negotiation_memo"],
        }
        return {"report": report}

    graph = StateGraph(ContractState)
    graph.add_node("identify_clauses", identify_clauses_node)
    graph.add_node("flag_risks", flag_risks_node)
    graph.add_node("explain_risks", explain_risks_node)
    graph.add_node("human_review", human_review_node)
    graph.add_node("detect_missing", missing_clauses_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("generate_memo", generate_memo_node)
    graph.add_node("assemble_report", assemble_report_node)

    graph.set_entry_point("identify_clauses")
    graph.add_edge("identify_clauses", "flag_risks")
    graph.add_edge("flag_risks", "explain_risks")
    graph.add_edge("explain_risks", "human_review")
    graph.add_edge("human_review", "detect_missing")
    graph.add_edge("detect_missing", "summarize")
    graph.add_edge("summarize", "generate_memo")
    graph.add_edge("generate_memo", "assemble_report")
    graph.add_edge("assemble_report", END)

    return graph.compile(checkpointer=_checkpointer)


def _interrupt_payload(result: dict):
    """Extracts the value passed to interrupt() from an ainvoke result, or
    None if the graph run completed without pausing."""
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None
    return interrupts[0].value


async def start_review(contract_text: str, jurisdiction: str = "", thread_id: str = "default") -> dict:
    """
    Starts (or restarts) the review pipeline for a given thread_id and runs
    until it either pauses for human review or completes outright (when
    there are no high/medium flags to review).

    jurisdiction (optional): e.g. "Nigeria", "US", "EU" — passed through to
    the MCP server's knowledge-base-grounded explanation tools.

    thread_id: identifies this review session to the checkpointer. Use a
    fresh, unique value per new contract/review (e.g. a UUID generated
    once per Streamlit session or per upload) — reusing a thread_id resumes
    or restarts a previous run's checkpointed state.

    Returns:
        {"status": "needs_review", "review_payload": {"flags": [...]}} if
        paused, or {"status": "done", "report": {...}} if it ran straight
        through.
    """
    async with mcp_session() as session:
        compiled = build_graph(session)
        config = {"configurable": {"thread_id": thread_id}}
        result = await compiled.ainvoke(
            {"contract_text": contract_text, "jurisdiction": jurisdiction}, config
        )

    payload = _interrupt_payload(result)
    if payload is not None:
        return {"status": "needs_review", "review_payload": payload}
    return {"status": "done", "report": result["report"]}


async def resume_review(reviewed_flags: list, thread_id: str = "default") -> dict:
    """
    Resumes a paused review with human-reviewed flag data and runs to
    completion (detect_missing -> summarize -> generate_memo -> assemble_report).

    reviewed_flags: a list of dicts, one per flag that was in
    review_payload["flags"], each with:
        {"clause_type": str, "suggested_rewording": str, "approved": bool}

    thread_id: must match the thread_id used in the corresponding
    start_review call.

    Returns:
        {"status": "done", "report": {...}} — resuming should always
        complete the run, since human_review is the only pause point.
    """
    async with mcp_session() as session:
        compiled = build_graph(session)
        config = {"configurable": {"thread_id": thread_id}}
        result = await compiled.ainvoke(Command(resume=reviewed_flags), config)

    payload = _interrupt_payload(result)
    if payload is not None:
        # Shouldn't normally happen (only one pause point), but handle
        # gracefully rather than assuming "report" exists.
        return {"status": "needs_review", "review_payload": payload}
    return {"status": "done", "report": result["report"]}


def start_review_sync(contract_text: str, jurisdiction: str = "", thread_id: str = "default") -> dict:
    """Sync wrapper for callers (e.g. Streamlit) that aren't in an event loop."""
    return asyncio.run(start_review(contract_text, jurisdiction, thread_id))


def resume_review_sync(reviewed_flags: list, thread_id: str = "default") -> dict:
    """Sync wrapper for callers (e.g. Streamlit) that aren't in an event loop."""
    return asyncio.run(resume_review(reviewed_flags, thread_id))


# Backwards-compatible aliases: any other script still importing
# run_review/run_review_sync gets the old straight-through behavior, with
# human review auto-approved silently (no pause). New code should prefer
# start_review_sync + resume_review_sync directly to get a real review gate.
async def run_review(contract_text: str, jurisdiction: str = "") -> dict:
    import uuid
    thread_id = str(uuid.uuid4())
    result = await start_review(contract_text, jurisdiction, thread_id)
    if result["status"] == "needs_review":
        auto_approved = [
            {"clause_type": f["clause_type"], "suggested_rewording": f.get("suggested_rewording", ""), "approved": True}
            for f in result["review_payload"]["flags"]
        ]
        result = await resume_review(auto_approved, thread_id)
    return result["report"]


def run_review_sync(contract_text: str, jurisdiction: str = "") -> dict:
    return asyncio.run(run_review(contract_text, jurisdiction))


# ---------------------------------------------------------------------------
# Contract version comparison
# ---------------------------------------------------------------------------

class ComparisonState(TypedDict, total=False):
    text_a: str
    text_b: str
    clauses_a: dict
    clauses_b: dict
    risk_a: dict
    risk_b: dict
    diff: dict
    comparison_report: dict


def build_comparison_graph(session):
    """
    Builds a second, independent graph that compares two contract versions:
    which clause types were added/removed/reworded, and which risk flags
    are new or resolved between version A and version B. identify_clauses
    and flag_risks run on both contracts concurrently at each stage (they
    don't depend on each other), and the diff itself is pure Python — no
    MCP call needed for that step. No human review gate in this flow.
    """

    async def identify_both_node(state: ComparisonState) -> dict:
        clauses_a, clauses_b = await asyncio.gather(
            call_tool(session, "identify_clauses", {"contract_text": state["text_a"]}),
            call_tool(session, "identify_clauses", {"contract_text": state["text_b"]}),
        )
        return {"clauses_a": clauses_a, "clauses_b": clauses_b}

    async def flag_both_node(state: ComparisonState) -> dict:
        risk_a, risk_b = await asyncio.gather(
            call_tool(session, "flag_risks", {"clauses": state["clauses_a"]}),
            call_tool(session, "flag_risks", {"clauses": state["clauses_b"]}),
        )
        return {"risk_a": risk_a, "risk_b": risk_b}

    async def diff_node(state: ComparisonState) -> dict:
        clauses_a, clauses_b = state["clauses_a"], state["clauses_b"]
        types_a, types_b = set(clauses_a), set(clauses_b)

        added_clause_types = sorted(types_b - types_a)
        removed_clause_types = sorted(types_a - types_b)
        changed_clause_types = sorted(
            t for t in (types_a & types_b)
            if clauses_a[t].strip() != clauses_b[t].strip()
        )

        flags_a = {(f["clause_type"], f["level"]) for f in state["risk_a"]["flags"]}
        flags_b = {(f["clause_type"], f["level"]) for f in state["risk_b"]["flags"]}
        new_risks = sorted(flags_b - flags_a)
        resolved_risks = sorted(flags_a - flags_b)

        diff = {
            "risk_score_a": state["risk_a"]["risk_score"],
            "risk_score_b": state["risk_b"]["risk_score"],
            "risk_score_delta": state["risk_b"]["risk_score"] - state["risk_a"]["risk_score"],
            "added_clause_types": added_clause_types,
            "removed_clause_types": removed_clause_types,
            "changed_clause_types": changed_clause_types,
            "new_risks": [{"clause_type": c, "level": lvl} for c, lvl in new_risks],
            "resolved_risks": [{"clause_type": c, "level": lvl} for c, lvl in resolved_risks],
        }
        return {"diff": diff}

    async def assemble_comparison_node(state: ComparisonState) -> dict:
        return {"comparison_report": state["diff"]}

    graph = StateGraph(ComparisonState)
    graph.add_node("identify_both", identify_both_node)
    graph.add_node("flag_both", flag_both_node)
    graph.add_node("diff", diff_node)
    graph.add_node("assemble_comparison", assemble_comparison_node)

    graph.set_entry_point("identify_both")
    graph.add_edge("identify_both", "flag_both")
    graph.add_edge("flag_both", "diff")
    graph.add_edge("diff", "assemble_comparison")
    graph.add_edge("assemble_comparison", END)

    return graph.compile()


async def run_comparison(text_a: str, text_b: str) -> dict:
    """Runs the comparison graph between two contract versions (A = older/baseline, B = newer) and returns the diff report."""
    async with mcp_session() as session:
        compiled = build_comparison_graph(session)
        final_state = await compiled.ainvoke({"text_a": text_a, "text_b": text_b})
        return final_state["comparison_report"]


def run_comparison_sync(text_a: str, text_b: str) -> dict:
    """Sync wrapper for callers (e.g. Streamlit) that aren't in an event loop."""
    return asyncio.run(run_comparison(text_a, text_b))
