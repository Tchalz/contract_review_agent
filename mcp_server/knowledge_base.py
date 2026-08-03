"""
Reference knowledge base for grounding LLM risk explanations.

Keyed by the same clause_type strings used in risk_rules.py's
CLAUSE_SIGNALS, so lookups are a direct dict access — no fuzzy matching
needed. Each entry gives explain_risk/suggest_rewording/
generate_negotiation_memo something concrete to cite instead of relying
purely on the model's general training knowledge.

Extend this over time with your own firm's playbook language, additional
jurisdictions, or more granular red flags. Nothing here is legal advice —
it's reference material to make LLM output more consistent and auditable.
"""

KNOWLEDGE_BASE: dict[str, dict] = {
    "payment_terms": {
        "standard_language": "Payment due within 30 days of invoice date (Net 30). Late payments accrue interest at a reasonable statutory or agreed rate.",
        "red_flags": [
            "Payment terms beyond Net 60 without justification",
            "No late payment interest or penalty specified",
            "Vague invoicing/payment trigger dates",
            "No dispute mechanism for contested invoices",
        ],
        "jurisdiction_notes": {
            "Nigeria": "Payment terms as written are generally enforceable under freedom of contract. Interest on late payment is enforceable if agreed, though Nigerian courts may reduce interest rates found to be punitive rather than genuinely compensatory.",
        },
    },
    "termination": {
        "standard_language": "Either party may terminate with 30 days written notice; for-cause termination allows a cure period before termination takes effect.",
        "red_flags": [
            "Unilateral termination rights favoring only one party",
            "No notice period or notice under 15 days",
            "No cure period for breach before termination",
            "Automatic renewal without a clear opt-out mechanism",
            "Termination fees disproportionate to contract value",
        ],
        "jurisdiction_notes": {
            "Nigeria": "Commercial contract termination terms are generally enforced as written under Nigerian common law (freedom of contract). For employment-adjacent agreements, the Labour Act sets minimum notice periods that can override contract silence. If the contract involves personal data, review termination-related data handling against the NDPA 2023.",
        },
    },
    "confidentiality": {
        "standard_language": "Confidential information is defined, use is restricted to the purpose of the agreement, and obligations survive termination for a reasonable, stated period (commonly 2-5 years).",
        "red_flags": [
            "No time limit on confidentiality obligations (perpetual with no carve-out)",
            "Overly broad definition of 'confidential information' with no exclusions",
            "No exceptions for independently developed or publicly available information",
        ],
        "jurisdiction_notes": {
            "Nigeria": "NDAs/confidentiality clauses are enforceable under Nigerian common law. Perpetual or extremely broad confidentiality obligations may face scrutiny if challenged as an unreasonable restraint, though courts generally respect freely negotiated terms between commercial parties.",
        },
    },
    "liability": {
        "standard_language": "Liability is capped at a stated amount (commonly fees paid in the prior 12 months), with standard carve-outs for gross negligence, willful misconduct, and confidentiality breaches.",
        "red_flags": [
            "Unlimited liability with no cap",
            "One-sided cap (protects only one party)",
            "No carve-outs for gross negligence or willful misconduct",
        ],
        "jurisdiction_notes": {
            "Nigeria": "Liability caps are generally enforceable, but Nigerian courts may refuse to enforce caps that effectively exclude all liability for fraud or gross negligence, treating such exclusions as contrary to public policy.",
        },
    },
    "indemnification": {
        "standard_language": "Indemnification is mutual and limited to third-party claims arising from breach, negligence, or IP infringement, not a blanket assumption of all risk.",
        "red_flags": [
            "One-sided indemnification favoring only the counterparty",
            "Indemnification with no cap, tied to uncapped liability",
            "Indemnity extended to the indemnifying party's own negligence",
        ],
        "jurisdiction_notes": {
            "Nigeria": "Indemnity clauses are generally enforceable under Nigerian common law, but courts may narrow or strike down indemnities seen as unconscionable, particularly where they attempt to cover the indemnified party's own gross negligence or willful misconduct.",
        },
    },
    "intellectual_property": {
        "standard_language": "Each party retains ownership of its pre-existing IP; newly created work-product ownership and license scope are explicitly stated.",
        "red_flags": [
            "Silent or ambiguous ownership of work created under the contract",
            "Overly broad assignment of pre-existing IP to the counterparty",
            "No license-back for the creating party's own use",
        ],
        "jurisdiction_notes": {
            "Nigeria": "IP ownership/licensing terms are enforceable under Nigerian law and additionally governed by the Copyright Act 2022 and the Patents and Designs Act. Because default statutory ownership rules can differ from what parties assume, explicit work-for-hire and assignment language is important.",
        },
    },
    "governing_law": {
        "standard_language": "The agreement specifies a single governing law and exclusive or non-exclusive jurisdiction for disputes.",
        "red_flags": [
            "No governing law specified (creates conflict-of-laws uncertainty)",
            "Governing law in a jurisdiction with no connection to either party, chosen unilaterally",
        ],
        "jurisdiction_notes": {
            "Nigeria": "Nigerian courts generally respect a freely negotiated foreign governing law clause, provided it doesn't conflict with Nigerian public policy or mandatory local law (e.g. matters involving land, employment, or Nigeria-specific regulatory regimes).",
        },
    },
    "dispute_resolution": {
        "standard_language": "Disputes are resolved via negotiation, then mediation, then binding arbitration or litigation in a specified venue, with each step's timeline defined.",
        "red_flags": [
            "Mandatory arbitration in a venue highly inconvenient or costly for one party",
            "No escalation steps before litigation/arbitration",
            "Waiver of the right to seek injunctive relief",
        ],
        "jurisdiction_notes": {
            "Nigeria": "Arbitration clauses are enforceable under the Arbitration and Mediation Act 2023, Nigeria's modernized framework aligned with the UNCITRAL Model Law. Nigeria is also party to the New York Convention, so foreign arbitral awards are generally enforceable domestically.",
        },
    },
    "renewal": {
        "standard_language": "Renewal requires affirmative opt-in, or if automatic, includes a clear opt-out window (commonly 30-60 days before the renewal date) and advance notice of the renewal terms.",
        "red_flags": [
            "Automatic renewal with no opt-out mechanism",
            "Short or unclear opt-out notice window",
            "Renewal terms that can change unilaterally without notice",
        ],
        "jurisdiction_notes": {
            "Nigeria": "Auto-renewal clauses are enforceable as written under freedom of contract. No specific Nigerian statute mandates opt-out notice periods for commercial contracts, so the contract's own terms control.",
        },
    },
    "data_privacy": {
        "standard_language": "The agreement specifies the legal basis for processing, data handling/security obligations, cross-border transfer terms, and breach notification timelines.",
        "red_flags": [
            "No data breach notification obligation or timeline",
            "No restriction on cross-border data transfer",
            "Silent on data processing basis or purpose limitation",
            "No provision for data deletion/return at termination",
        ],
        "jurisdiction_notes": {
            "Nigeria": "Contracts involving personal data of Nigerian residents fall under the Nigeria Data Protection Act 2023 (NDPA), enforced by the Nigeria Data Protection Commission (NDPC). Clauses should address lawful basis for processing, cross-border transfer restrictions, and breach notification — these are statutory requirements that can override contract silence, so a missing Data Privacy clause is a materially higher risk for Nigeria-connected contracts specifically.",
        },
    },
    "force_majeure": {
        "standard_language": "Force majeure events are specifically enumerated (natural disaster, war, government action, pandemic, etc.), with notice and mitigation obligations, and a defined outcome if the event persists beyond a stated period.",
        "red_flags": [
            "Vague or undefined force majeure triggers",
            "No notice obligation when invoking force majeure",
            "No termination right if the force majeure event is prolonged",
        ],
        "jurisdiction_notes": {
            "Nigeria": "Force majeure protection is recognized under Nigerian common law but must be expressly drafted — courts require the triggering event to be specifically listed or reasonably foreseeable from the clause language. There is no general statutory force majeure protection absent a written clause.",
        },
    },
}


def get_reference(clause_type: str) -> dict | None:
    """Look up reference standards for a given clause type. Returns None if unknown."""
    return KNOWLEDGE_BASE.get(clause_type)


def format_reference_block(clause_type: str, jurisdiction: str | None = None) -> str:
    """
    Builds a plain-text reference block to inject into LLM prompts.
    Returns an empty string if no reference exists for this clause type,
    so callers can safely concatenate without checking first.
    """
    ref = get_reference(clause_type)
    if not ref:
        return ""

    red_flags = "\n".join(f"- {f}" for f in ref.get("red_flags", []))
    block = (
        f"Reference standard for {clause_type.replace('_', ' ').title()}:\n"
        f"{ref['standard_language']}\n\n"
        f"Known red flags for {clause_type.replace('_', ' ').title()}:\n"
        f"{red_flags}\n"
    )

    if jurisdiction:
        note = ref.get("jurisdiction_notes", {}).get(jurisdiction)
        if note:
            block += f"\n{jurisdiction}-specific note:\n{note}\n"

    return block
