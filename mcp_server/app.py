"""
Contract Review Server — a standalone MCP (Model Context Protocol) server.

Exposes contract-analysis primitives (clause identification, risk flagging,
plain-language risk explanations, safer-wording suggestions, missing-clause
detection, summarization, and a negotiation-memo synthesizer) as MCP tools,
so any MCP-compatible client — including the LangGraph pipeline in
pipeline/graph.py — can call them without embedding the analysis logic in
the orchestrator itself.

Rule tables live in risk_rules.py (data separated from server logic, same
pattern as the Library MCP Server's books.json split). Reference standards,
red flags, and jurisdiction notes live in knowledge_base.py and are used to
ground the LLM-backed explanation tools so their output is anchored to a
concrete reference rather than only the model's general training
knowledge. LLM-backed tools (explain_risk, suggest_rewording,
generate_negotiation_memo) call an OpenRouter-compatible model when
OPENROUTER_API_KEY is set, and fall back to a canned/templated response
otherwise, so the server is fully runnable/testable with zero API keys.

Run it directly for local dev/testing:
    python app.py

Inspect it (schema + counts) without a client:
    fastmcp inspect app.py

Run it under the MCP Inspector (interactive browser UI):
    fastmcp dev app.py
"""

import os
import re
from typing import Optional

from dotenv import load_dotenv

MCP_HOST = os.environ.get("HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("PORT", 8002))
load_dotenv()


from mcp.server.fastmcp import FastMCP

from risk_rules import CLAUSE_SIGNALS, STANDARD_CHECKLIST, RISK_RULES, DEFAULT_RISK_LEVEL
from knowledge_base import format_reference_block

mcp = FastMCP("Contract Review Server", host=MCP_HOST, port=MCP_PORT)

# ---------------------------------------------------------------------------
# Optional LLM client (OpenRouter, OpenAI-compatible). Only used if a key is
# present — every tool below still works without it via rule-based fallback.
# ---------------------------------------------------------------------------

_client = None
_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-oss-20b:free")


def _get_client():
    global _client
    if _client is not None:
        return _client
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return None
    from openai import OpenAI
    _client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
    return _client


def _llm(prompt: str, fallback: str) -> str:
    client = _get_client()
    if client is None:
        return fallback
    try:
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=250,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:  # network/auth errors shouldn't break the tool
        return f"{fallback} (LLM explanation unavailable: {e})"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_clause_snippet(text: str, signals: list[str], window: int = 220) -> Optional[str]:
    """Finds the first sentence-ish window around any signal phrase's first match."""
    lower = text.lower()
    for signal in signals:
        idx = lower.find(signal)
        if idx != -1:
            start = max(0, idx - 40)
            end = min(len(text), idx + window)
            return text[start:end].strip()
    return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def identify_clauses(contract_text: str) -> dict:
    """
    Scans contract text and identifies which standard clause types are
    present, returning the located snippet for each. Call this first on any
    contract — its output (a dict of clause_type -> snippet) is the input
    every other tool in this server expects.

    Args:
        contract_text: The full extracted text of the contract.

    Returns:
        A dict mapping clause_type (e.g. "liability", "termination") to the
        text snippet where it was found. Clause types not detected are
        omitted from the result.
    """
    found = {}
    for clause_type, signals in CLAUSE_SIGNALS.items():
        snippet = _find_clause_snippet(contract_text, signals)
        if snippet:
            found[clause_type] = snippet
    return found


@mcp.tool()
def flag_risks(clauses: dict) -> dict:
    """
    Evaluates each identified clause against the risk ruleset and assigns a
    risk level. Call this after identify_clauses. Only flags clauses that
    match a known risk pattern — a clause type with no matching rule is
    omitted, not assumed safe (check separately if that matters).

    Args:
        clauses: The dict returned by identify_clauses (clause_type -> snippet).

    Returns:
        A dict with "flags" (list of {clause_type, level, reason, snippet})
        and "risk_score" (0-100, higher = riskier, weighted: high=25, medium=10, low=2).
    """
    flags = []
    score = 0
    weights = {"high": 25, "medium": 10, "low": 2}

    for clause_type, snippet in clauses.items():
        rules = RISK_RULES.get(clause_type, [])
        snippet_lower = snippet.lower()
        matched = False
        for phrase, level, reason in rules:
            if phrase in snippet_lower:
                flags.append({
                    "clause_type": clause_type,
                    "level": level,
                    "reason": reason,
                    "snippet": snippet,
                })
                score += weights.get(level, 0)
                matched = True
                break  # first matching rule wins per clause
        if not matched and rules:
            # rules exist for this clause type but none matched — treat as default
            pass

    return {"flags": flags, "risk_score": min(100, score)}


@mcp.tool()
def explain_risk(clause_text: str, reason: str, clause_type: str = "", jurisdiction: str = "") -> str:
    """
    Produces a plain-language explanation of why a clause is risky, suitable
    for a non-lawyer reader. When clause_type is provided, grounds the
    explanation in the knowledge_base's reference standard and red flags for
    that clause type — and in the jurisdiction-specific note too, if
    jurisdiction is also given — rather than relying purely on the model's
    general training knowledge. Uses an LLM if OPENROUTER_API_KEY is
    configured; otherwise returns the rule-based `reason` as-is. Call this
    only for clauses flag_risks marked "high" or "medium" — explaining
    low-risk clauses isn't worth the LLM call.

    Args:
        clause_text: The clause snippet to explain.
        reason: The short rule-based reason from flag_risks, used as context
            and as the fallback if no LLM is configured.
        clause_type: The clause type key (e.g. "termination", "data_privacy")
            from identify_clauses/flag_risks. Optional — if omitted, no
            knowledge-base grounding is applied and the explanation relies
            on general model knowledge only.
        jurisdiction: Optional jurisdiction name (e.g. "Nigeria", "US",
            "EU") to include a jurisdiction-specific note from the
            knowledge base, if one exists for this clause type.

    Returns:
        A 2-4 sentence plain-language explanation, grounded in the
        reference knowledge base when clause_type is available.
    """
    reference_block = format_reference_block(clause_type, jurisdiction) if clause_type else ""
    grounding_instruction = (
        "If a reference standard and red flags are given below, ground your "
        "explanation in them explicitly (e.g. name which red flag this "
        "clause matches). If a jurisdiction-specific note is given, weave "
        "it in briefly.\n\n"
        if reference_block else ""
    )
    prompt = (
        "Explain in plain, non-legal language why this contract clause is "
        f"risky. Keep it to 2-4 sentences.\n\nClause: \"{clause_text}\"\n"
        f"Known concern: {reason}\n\n{grounding_instruction}{reference_block}"
    )
    return _llm(prompt, fallback=reason)


@mcp.tool()
def suggest_rewording(clause_text: str) -> str:
    """
    Suggests safer alternative wording for a risky clause. Uses an LLM if
    OPENROUTER_API_KEY is configured; otherwise returns a generic
    recommendation to negotiate the clause. This is a suggestion for
    negotiation, not legal advice — the caller should present it as such.

    Args:
        clause_text: The risky clause snippet to reword.

    Returns:
        A suggested replacement clause, or a generic negotiation pointer if
        no LLM is available.
    """
    prompt = (
        "Rewrite this contract clause with more balanced, lower-risk "
        f"wording. Return only the rewritten clause.\n\nClause: \"{clause_text}\""
    )
    fallback = "Consider negotiating this clause with your counterparty for more balanced terms."
    return _llm(prompt, fallback=fallback)


@mcp.tool()
def detect_missing_clauses(clauses: dict) -> list[str]:
    """
    Compares the clauses found in a contract against the standard checklist
    and returns which expected clause types are absent. An absent clause
    (e.g. no confidentiality clause) can itself be a risk worth flagging to
    the user even though there's no clause text to analyze.

    Args:
        clauses: The dict returned by identify_clauses.

    Returns:
        A list of clause_type strings that were expected but not found.
    """
    return [c for c in STANDARD_CHECKLIST if c not in clauses]


@mcp.tool()
def summarize_contract(contract_text: str, clauses: dict) -> dict:
    """
    Produces a structural summary of the contract: rough length, how many
    clause types were detected, and simple heuristics for parties/dates
    where patterns are recognizable. Call this last, after clauses have
    been identified, to build the top-of-report summary block.

    Args:
        contract_text: The full extracted contract text.
        clauses: The dict returned by identify_clauses.

    Returns:
        A dict with word_count, clause_types_found, clause_types_total,
        and any dates found via simple pattern matching.
    """
    date_pattern = r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b"
    dates = re.findall(date_pattern, contract_text)

    return {
        "word_count": len(contract_text.split()),
        "clause_types_found": len(clauses),
        "clause_types_total": len(STANDARD_CHECKLIST),
        "dates_mentioned": dates[:5],
    }


@mcp.tool()
def generate_negotiation_memo(
    risk_score: int,
    flags: list[dict],
    missing_clauses: list[str],
    jurisdiction: str = "",
) -> str:
    """
    Synthesizes all flagged risks and missing clauses into a short,
    plain-language executive summary / negotiation memo — what the overall
    risk posture is, which 1-3 issues matter most, and what to prioritize
    negotiating. When jurisdiction is provided, pulls in jurisdiction-
    specific notes from the knowledge base for each flagged clause type so
    the summary reflects local context (e.g. statutory overrides) rather
    than purely general contract-law knowledge. Call this last, after
    flag_risks and detect_missing_clauses, once the full picture is
    available. Uses an LLM if OPENROUTER_API_KEY is configured; otherwise
    returns a short templated summary built directly from the flags (still
    useful, just less narrative).

    Args:
        risk_score: The overall risk score (0-100) from flag_risks.
        flags: The list of flag dicts (clause_type, level, reason, snippet)
            from flag_risks — explanations aren't required for this call.
        missing_clauses: The list of missing clause_type strings from
            detect_missing_clauses.
        jurisdiction: Optional jurisdiction name (e.g. "Nigeria") to ground
            the summary in jurisdiction-specific notes from the knowledge
            base, where available for the flagged clause types.

    Returns:
        A 4-6 sentence plain-language executive summary.
    """
    if not flags and not missing_clauses:
        return "No significant risks were flagged in this contract, and all standard clauses were present."

    flag_lines = "\n".join(
        f"- [{f['level'].upper()}] {f['clause_type'].replace('_', ' ').title()}: {f['reason']}"
        for f in sorted(flags, key=lambda f: {"high": 0, "medium": 1, "low": 2}[f["level"]])
    ) or "- None"

    missing_line = (
        f"Missing standard clauses: {', '.join(c.replace('_', ' ').title() for c in missing_clauses)}."
        if missing_clauses else "No standard clauses were missing."
    )

    reference_blocks = ""
    if jurisdiction:
        seen_types = set()
        blocks = []
        for f in flags:
            if f["clause_type"] in seen_types:
                continue
            seen_types.add(f["clause_type"])
            block = format_reference_block(f["clause_type"], jurisdiction)
            if block:
                blocks.append(block)
        if blocks:
            reference_blocks = (
                f"\nReference context for {jurisdiction}:\n" + "\n".join(blocks)
            )

    prompt = (
        "You are summarizing a contract risk review for someone about to "
        "negotiate this agreement. Write a concise executive summary "
        "(4-6 sentences, plain language, no legal jargon): state the "
        "overall risk level, name the 1-3 issues that matter most, and say "
        "what to prioritize negotiating. If jurisdiction-specific reference "
        "context is provided below, factor it in where relevant (e.g. "
        "statutory requirements that override contract silence).\n\n"
        f"Overall risk score: {risk_score}/100\n"
        f"Flagged clauses:\n{flag_lines}\n\n{missing_line}{reference_blocks}"
    )

    if flags:
        top = sorted(flags, key=lambda f: {"high": 0, "medium": 1, "low": 2}[f["level"]])[0]
        top_desc = f"most notably {top['clause_type'].replace('_', ' ').title()} ({top['level']} risk)"
    else:
        top_desc = "no flagged clauses"
    fallback = (
        f"This contract scores {risk_score}/100 for risk, with {len(flags)} clause(s) flagged, "
        f"{top_desc}. {missing_line} Review each flagged clause individually, and consider adding "
        "any missing standard clauses, before signing."
    )
    return _llm(prompt, fallback=fallback)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@mcp.resource("policy://risk-rules")
def get_risk_rules() -> str:
    """Static resource: the risk rules currently applied, for transparency/audit."""
    lines = []
    for clause_type, rules in RISK_RULES.items():
        for phrase, level, reason in rules:
            lines.append(f"[{clause_type}] '{phrase}' -> {level}: {reason}")
    return "\n".join(lines) if lines else "No risk rules configured."


@mcp.resource("policy://checklist")
def get_checklist() -> str:
    """Static resource: the standard clause checklist used for missing-clause detection."""
    return "\n".join(STANDARD_CHECKLIST)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

@mcp.prompt()
def review_focus(focus_area: str) -> str:
    """Generates a reusable prompt template for focusing a contract review on one area."""
    return (
        f"Review this contract with particular attention to '{focus_area}'. "
        "Use identify_clauses first, then flag_risks, and call explain_risk "
        f"specifically for any clause related to {focus_area} even if its "
        "risk level is low, so the user gets full visibility into that area."
    )


if __name__ == "__main__":
    print(f"Contract Review MCP Server — {len(CLAUSE_SIGNALS)} clause types, "
          f"{sum(len(v) for v in RISK_RULES.values())} risk rules loaded.")
    print(f"LLM backend: {'OpenRouter (' + _MODEL + ')' if os.environ.get('OPENROUTER_API_KEY') else 'none — using rule-based fallbacks'}")
    mcp.run(transport="streamable-http")
