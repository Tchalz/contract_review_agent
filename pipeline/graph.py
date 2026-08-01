"""
LangGraph orchestration for the contract review pipeline.

The graph owns *flow* (what runs, in what order, and which risky clauses
get the expensive LLM explain/reword calls) while mcp_server/app.py owns
the actual analysis logic, called here as MCP tools. Swapping or extending
the analysis (new clause types, smarter risk rules, a real classifier)
never requires touching this file.

Flow:
    identify_clauses -> flag_risks -> explain_flagged_risks
                      -> detect_missing_clauses -> summarize -> assemble_report
"""

import asyncio
from typing import TypedDict

from langgraph.graph import StateGraph, END

from mcp_client import mcp_session, call_tool

# Risk levels worth spending an LLM call to explain/reword. Low-risk
# clauses are reported without an explanation to keep runs fast and cheap.
EXPLAIN_LEVELS = {"high", "medium"}


class ContractState(TypedDict, total=False):
    contract_text: str
    clauses: dict
    risk_flags: list
    risk_score: int
    explained_flags: list
    missing_clauses: list
    summary: dict
    report: dict


def build_graph(session):
    """Builds the StateGraph, closing over an open MCP ClientSession so
    every node can call tools without re-negotiating a connection."""

    async def identify_clauses_node(state: ContractState) -> dict:
        clauses = await call_tool(session, "identify_clauses", {"contract_text": state["contract_text"]})
        return {"clauses": clauses}

    async def flag_risks_node(state: ContractState) -> dict:
        result = await call_tool(session, "flag_risks", {"clauses": state["clauses"]})
        return {"risk_flags": result["flags"], "risk_score": result["risk_score"]}

    async def explain_risks_node(state: ContractState) -> dict:
        explained = []
        for flag in state["risk_flags"]:
            entry = dict(flag)
            if flag["level"] in EXPLAIN_LEVELS:
                entry["explanation"] = await call_tool(
                    session, "explain_risk",
                    {"clause_text": flag["snippet"], "reason": flag["reason"]},
                )
                entry["suggested_rewording"] = await call_tool(
                    session, "suggest_rewording", {"clause_text": flag["snippet"]},
                )
            explained.append(entry)
        return {"explained_flags": explained}

    async def missing_clauses_node(state: ContractState) -> dict:
        missing = await call_tool(session, "detect_missing_clauses", {"clauses": state["clauses"]})
        return {"missing_clauses": missing}

    async def summarize_node(state: ContractState) -> dict:
        summary = await call_tool(
            session, "summarize_contract",
            {"contract_text": state["contract_text"], "clauses": state["clauses"]},
        )
        return {"summary": summary}

    async def assemble_report_node(state: ContractState) -> dict:
        report = {
            "risk_score": state["risk_score"],
            "summary": state["summary"],
            "flags": state["explained_flags"],
            "missing_clauses": state["missing_clauses"],
            "clause_types_found": list(state["clauses"].keys()),
        }
        return {"report": report}

    graph = StateGraph(ContractState)
    graph.add_node("identify_clauses", identify_clauses_node)
    graph.add_node("flag_risks", flag_risks_node)
    graph.add_node("explain_risks", explain_risks_node)
    graph.add_node("detect_missing", missing_clauses_node)
    graph.add_node("summarize", summarize_node)
    graph.add_node("assemble_report", assemble_report_node)

    graph.set_entry_point("identify_clauses")
    graph.add_edge("identify_clauses", "flag_risks")
    graph.add_edge("flag_risks", "explain_risks")
    graph.add_edge("explain_risks", "detect_missing")
    graph.add_edge("detect_missing", "summarize")
    graph.add_edge("summarize", "assemble_report")
    graph.add_edge("assemble_report", END)

    return graph.compile()


async def run_review(contract_text: str) -> dict:
    """Runs the full pipeline against contract_text and returns the report dict."""
    async with mcp_session() as session:
        compiled = build_graph(session)
        final_state = await compiled.ainvoke({"contract_text": contract_text})
        return final_state["report"]


def run_review_sync(contract_text: str) -> dict:
    """Sync wrapper for callers (e.g. Streamlit) that aren't in an event loop."""
    return asyncio.run(run_review(contract_text))
