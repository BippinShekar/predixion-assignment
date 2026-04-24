import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()


def _validate_env() -> None:
    missing = [k for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY") if not os.environ.get(k)]
    if missing:
        print(f"Error: missing required environment variables: {', '.join(missing)}")
        print("Copy .env.example to .env and fill in your API keys.")
        sys.exit(1)


def _print_pretty(result) -> None:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"

    confidence_color = {
        "high": GREEN,
        "medium": YELLOW,
        "low": RED,
    }.get(result.confidence, RESET)

    print(f"\n{'=' * 60}")
    print(f"{BOLD}QUESTION{RESET}")
    print(f"  {result.question}")

    print(f"\n{BOLD}ANSWER{RESET}")
    print(f"  {result.answer}")

    print(f"\n{BOLD}CONFIDENCE{RESET}  {confidence_color}{result.confidence.upper()}{RESET}")

    if result.key_findings:
        print(f"\n{BOLD}KEY FINDINGS{RESET}")
        for i, finding in enumerate(result.key_findings, 1):
            print(f"  {i}. {finding.claim}")
            for url in finding.source_urls:
                print(f"     {DIM}{url}{RESET}")

    if result.sources:
        print(f"\n{BOLD}SOURCES{RESET}")
        for source in result.sources:
            bar = int(source.relevance_score * 10)
            score_display = f"[{'#' * bar}{'.' * (10 - bar)}] {source.relevance_score:.2f}"
            print(f"  {CYAN}{source.title}{RESET}")
            print(f"  {DIM}{source.url}{RESET}")
            print(f"  {DIM}{score_display}{RESET}")

    if result.limitations:
        print(f"\n{BOLD}LIMITATIONS{RESET}")
        for item in result.limitations:
            print(f"  - {item}")

    if result.assumptions:
        print(f"\n{BOLD}ASSUMPTIONS{RESET}")
        for item in result.assumptions:
            print(f"  - {item}")

    if result.next_steps:
        print(f"\n{BOLD}NEXT STEPS{RESET}")
        for item in result.next_steps:
            print(f"  -> {item}")

    if result.judge_verdict:
        v = result.judge_verdict
        status_color = GREEN if v.passed else RED
        status_label = "PASSED" if v.passed else "FAILED"
        print(f"\n{BOLD}JUDGE{RESET}  {status_color}{status_label}{RESET}  "
              f"groundedness {v.groundedness_score:.2f}")
        if v.reasoning:
            print(f"  {DIM}{v.reasoning}{RESET}")
        if v.flagged_claims:
            print(f"  {YELLOW}Flagged:{RESET}")
            for claim in v.flagged_claims:
                print(f"    ! {claim}")

    print(f"{'=' * 60}\n")


def main() -> None:
    from utils.logger import CallTracker, setup_logger
    from agent.agent import run_agent

    parser = argparse.ArgumentParser(
        prog="research-agent",
        description="AI research agent that answers questions with source-backed structured output.",
    )
    parser.add_argument(
        "question",
        nargs="?",
        type=str,
        help="The research question to answer.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted text.",
    )
    parser.add_argument(
        "--no-trace",
        action="store_true",
        help="Suppress the token/cost trace summary.",
    )
    args = parser.parse_args()

    if not args.question:
        parser.print_help()
        sys.exit(1)

    _validate_env()
    setup_logger()

    tracker = CallTracker()

    try:
        result = run_agent(args.question, tracker)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        _print_pretty(result)

    if not args.no_trace:
        tracker.print_summary()


if __name__ == "__main__":
    main()
