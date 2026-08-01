"""
Rule tables for clause identification and risk scoring.

Kept separate from app.py (same pattern as the Library MCP Server's
books.json/generate_books.py split) so the ruleset can be extended,
swapped for a real legal-review policy, or loaded from a database later
without touching server/tool logic.
"""

# Each clause type maps to keyword/phrase signals used to locate it in the
# contract text. Real system would use an LLM or a trained classifier here;
# keyword matching gives a fast, dependency-free first pass and a fallback
# when no LLM API key is configured.
CLAUSE_SIGNALS: dict[str, list[str]] = {
    "payment_terms": ["payment", "invoice", "net 30", "net 60", "due date", "fees"],
    "termination": ["terminate", "termination", "cancellation", "notice period"],
    "confidentiality": ["confidential", "non-disclosure", "nda", "proprietary information"],
    "liability": ["liability", "liable", "damages", "limitation of liability"],
    "indemnification": ["indemnify", "indemnification", "hold harmless"],
    "intellectual_property": ["intellectual property", "copyright", "trademark", "patent", "ip rights"],
    "governing_law": ["governing law", "jurisdiction", "venue"],
    "dispute_resolution": ["arbitration", "mediation", "dispute resolution"],
    "renewal": ["renew", "renewal", "auto-renew", "automatic renewal"],
    "data_privacy": ["data protection", "gdpr", "personal data", "privacy"],
    "force_majeure": ["force majeure", "act of god"],
}

# Every clause type a well-formed commercial contract is generally expected
# to cover. Used by detect_missing_clauses.
STANDARD_CHECKLIST = list(CLAUSE_SIGNALS.keys())

# Risk rules: a clause type maps to a list of (trigger phrase, level, reason).
# Trigger phrases are checked against the clause's extracted text.
# Level is one of "high", "medium", "low".
RISK_RULES: dict[str, list[tuple[str, str, str]]] = {
    "liability": [
        ("without limitation", "high", "Unlimited liability exposure — no cap on damages."),
        ("unlimited", "high", "Unlimited liability exposure — no cap on damages."),
        ("limitation of liability", "low", "Liability appears to be capped."),
    ],
    "renewal": [
        ("automatic", "medium", "Auto-renewal may extend the contract without active review."),
        ("auto-renew", "medium", "Auto-renewal may extend the contract without active review."),
    ],
    "termination": [
        ("at any time", "medium", "One-sided or unrestricted termination right."),
        ("sole discretion", "medium", "Termination left to one party's sole discretion."),
        ("30 days", "low", "Termination requires reasonable written notice."),
        ("written notice", "low", "Termination requires reasonable written notice."),
    ],
    "indemnification": [
        ("indemnify", "medium", "Broad indemnification may shift another party's risk onto you."),
    ],
    "confidentiality": [],  # presence alone is low risk; absence is handled by missing-clause check
    "payment_terms": [
        ("net 60", "medium", "Extended payment terms may create cash-flow risk."),
        ("net 90", "high", "Long payment terms create significant cash-flow risk."),
    ],
}

DEFAULT_RISK_LEVEL = "low"
