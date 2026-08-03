"""
Contract Review Client — connects to the MCP server (run separately as
`python app.py` in mcp_server/) and runs the LangGraph pipeline against a
contract file.

Run the server first, in its own terminal:
    cd mcp_server
    python app.py

Then, in a second terminal, run this client:
    python client.py path/to/contract.pdf

Or, to compare two versions of a contract instead of reviewing one:
    python client.py path/to/old_version.pdf path/to/new_version.pdf

The client does not start or manage the server — if it isn't running (or
MCP_SERVER_URL doesn't match where it's listening), this will fail to
connect with a clear error rather than silently launching one, same as the
Library MCP Server client/server split.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "pipeline"))

from parsing import extract_text_with_pages, page_number_at  # noqa: E402
from graph import run_review_sync, run_comparison_sync  # noqa: E402

LEVEL_BADGE = {"high": "🔴 HIGH", "medium": "🟡 MEDIUM", "low": "🟢 LOW"}


def annotate_flags_with_pages(flags: list, contract_text: str, page_starts: list) -> list:
    """
    Locates each flag's snippet in the full contract text and attaches the
    page it appears on, so every risk printed can be traced back to an
    exact page rather than floating unanchored. Flags whose snippet can't
    be located verbatim (or whose source had no page info, e.g. DOCX/TXT)
    get page=None.
    """
    annotated = []
    for flag in flags:
        entry = dict(flag)
        idx = contract_text.find(flag["snippet"])
        entry["page"] = page_number_at(page_starts, idx) if idx != -1 else None
        annotated.append(entry)
    return annotated


def clause_label(flag: dict) -> str:
    """Builds a display label for a flag, appending a page citation when known."""
    base = flag["clause_type"].replace("_", " ").title()
    if flag.get("page"):
        return f"{base} (Page {flag['page']})"
    return base


def print_report(report: dict) -> None:
    summary = report["summary"]
    print("\n" + "=" * 60)
    print(f"OVERALL RISK SCORE: {report['risk_score']}/100")
    print("=" * 60)
    if report.get("negotiation_memo"):
        print("\nEXECUTIVE SUMMARY")
        print(report["negotiation_memo"])
    print(f"Word count: {summary['word_count']}")
    print(f"Clauses found: {summary['clause_types_found']}/{summary['clause_types_total']}")
    if summary["dates_mentioned"]:
        print(f"Dates mentioned: {', '.join(summary['dates_mentioned'])}")

    print("\n--- FLAGGED CLAUSES ---")
    if not report["flags"]:
        print("No risk-rule matches found.")
    for flag in sorted(report["flags"], key=lambda f: {"high": 0, "medium": 1, "low": 2}[f["level"]]):
        print(f"\n[{LEVEL_BADGE[flag['level']]}] {clause_label(flag)}")
        print(f"  Snippet: {flag['snippet'][:150]}...")
        print(f"  Reason: {flag['reason']}")
        if "explanation" in flag:
            print(f"  Explanation: {flag['explanation']}")
            print(f"  Suggested rewording: {flag['suggested_rewording']}")

    print("\n--- MISSING CLAUSES ---")
    missing_clauses = report["missing_clauses"]
    if isinstance(missing_clauses, str):
        # The backend returns a bare string instead of a list when exactly
        # one clause is missing — normalize here so it isn't iterated
        # character-by-character (e.g. "renewal" -> "r","e","n",...).
        missing_clauses = [missing_clauses]
    if missing_clauses:
        print(", ".join(c.replace("_", " ").title() for c in missing_clauses))
    else:
        print("All standard clause types were found.")
    print()


def print_comparison(diff: dict) -> None:
    print("\n" + "=" * 60)
    print("CONTRACT VERSION COMPARISON")
    print("=" * 60)
    delta = diff["risk_score_delta"]
    arrow = "increased" if delta > 0 else "decreased" if delta < 0 else "unchanged"
    print(f"Risk score: {diff['risk_score_a']} -> {diff['risk_score_b']}/100 ({arrow}, delta {delta:+d})")

    print("\n--- ADDED CLAUSES ---")
    print(", ".join(c.replace("_", " ").title() for c in diff["added_clause_types"]) or "None")

    print("\n--- REMOVED CLAUSES ---")
    print(", ".join(c.replace("_", " ").title() for c in diff["removed_clause_types"]) or "None")

    print("\n--- REWORDED CLAUSES ---")
    print(", ".join(c.replace("_", " ").title() for c in diff["changed_clause_types"]) or "None")

    print("\n--- NEW RISKS INTRODUCED ---")
    if diff["new_risks"]:
        for r in diff["new_risks"]:
            print(f"  [{r['level'].upper()}] {r['clause_type'].replace('_', ' ').title()}")
    else:
        print("None")

    print("\n--- RISKS RESOLVED ---")
    if diff["resolved_risks"]:
        for r in diff["resolved_risks"]:
            print(f"  [{r['level'].upper()}] {r['clause_type'].replace('_', ' ').title()}")
    else:
        print("None")
    print()


def main():
    if len(sys.argv) not in (2, 3):
        print("Usage: python client.py path/to/contract.pdf")
        print("   or: python client.py path/to/old_version.pdf path/to/new_version.pdf")
        sys.exit(1)

    if len(sys.argv) == 3:
        path_a, path_b = sys.argv[1], sys.argv[2]
        if not Path(path_a).exists():
            print(f"Error: file not found: {path_a}")
            sys.exit(1)
        if not Path(path_b).exists():
            print(f"Error: file not found: {path_b}")
            sys.exit(1)

        print(f"Extracting text from {path_a} and {path_b}...")
        text_a, _ = extract_text_with_pages(path_a)
        text_b, _ = extract_text_with_pages(path_b)

        print("Connecting to MCP server and comparing versions...")
        try:
            diff = run_comparison_sync(text_a, text_b)
        except Exception as e:
            print(f"\nError: could not complete the comparison — {e}")
            print("Is the MCP server running? Start it in another terminal with:")
            print("  cd mcp_server && python app.py")
            sys.exit(1)

        print_comparison(diff)
        return

    file_path = sys.argv[1]
    if not Path(file_path).exists():
        print(f"Error: file not found: {file_path}")
        sys.exit(1)

    print(f"Extracting text from {file_path}...")
    contract_text, page_starts = extract_text_with_pages(file_path)
    if not page_starts:
        print("(No page citations available for this file type — PDF only.)")

    print("Connecting to MCP server and running review pipeline...")
    try:
        report = run_review_sync(contract_text)
    except Exception as e:
        print(f"\nError: could not complete the review — {e}")
        print("Is the MCP server running? Start it in another terminal with:")
        print("  cd mcp_server && python app.py")
        sys.exit(1)

    report["flags"] = annotate_flags_with_pages(report["flags"], contract_text, page_starts)
    print_report(report)


if __name__ == "__main__":
    main()
