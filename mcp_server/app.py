"""
Contract Review Server — a standalone MCP (Model Context Protocol) server.

Exposes contract-analysis primitives (clause identification, risk flagging,
plain-language risk explanations, safer-wording suggestions, missing-clause
detection, and summarization) as MCP tools, so any MCP-compatible client —
including the LangGraph pipeline in pipeline/graph.py — can call them
without embedding the analysis logic in the orchestrator itself.

Rule tables live in risk_rules.py (data separated from server logic, same
pattern as the Library MCP Server's books.json split). LLM-backed tools
(explain_risk, suggest_rewording) call an OpenRouter-compatible model when
OPENROUTER_API_KEY is set, and fall back to a canned rule-based explanation
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

from mcp.server.fastmcp import FastMCP

from risk_rules import CLAUSE_SIGNALS, STANDARD_CHECKLIST, RISK_RULES, DEFAULT_RISK_LEVEL

mcp = FastMCP("Contract Review Server", port=8002)

# ---------------------------------------------------------------------------
# Optional LLM client (OpenRouter, OpenAI-compatible). Only used if a key is
# present — every tool below still works without it via rule-based fallback.
# ---------------------------------------------------------------------------

_client = None
_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")


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
def explain_risk(clause_text: str, reason: str) -> str:
    """
    Produces a plain-language explanation of why a clause is risky, suitable
    for a non-lawyer reader. Uses an LLM if OPENROUTER_API_KEY is configured;
    otherwise returns the rule-based `reason` as-is. Call this only for
    clauses flag_risks marked "high" or "medium" — explaining low-risk
    clauses isn't worth the LLM call.

    Args:
        clause_text: The clause snippet to explain.
        reason: The short rule-based reason from flag_risks, used as context
            and as the fallback if no LLM is configured.

    Returns:
        A 2-4 sentence plain-language explanation.
    """
    prompt = (
        "Explain in plain, non-legal language why this contract clause is "
        f"risky. Keep it to 2-4 sentences.\n\nClause: \"{clause_text}\"\n"
        f"Known concern: {reason}"
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
