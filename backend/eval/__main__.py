"""CLI: `uv run python -m eval` from `backend/`.

Tracing is initialized before anything imports the graph, so LangSmith
picks up the `.env` values on the first LangChain call rather than after
the run has already started untraced.
"""

import argparse
import asyncio
import sys

from .tracing import init_tracing

TRACING_ON = init_tracing()

from . import cluster  # noqa: E402  (must follow init_tracing)
from .report import print_report, summarize, write_results  # noqa: E402
from .runner import run_scenario  # noqa: E402
from .scenarios import SCENARIOS_BY_ID, get_scenarios  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m eval",
        description="Run the injected-failure eval suite against the Kind cluster.",
    )
    parser.add_argument(
        "-s",
        "--scenario",
        action="append",
        metavar="ID",
        help=f"run only this scenario (repeatable). Known: {', '.join(SCENARIOS_BY_ID)}",
    )
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="skip the LLM judge and score on the deterministic signal check alone",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="leave scenario namespaces in place after the run, for manual inspection",
    )
    parser.add_argument(
        "--setup-timeout",
        type=int,
        default=cluster.DEFAULT_TIMEOUT_SECONDS,
        help=f"seconds to wait for a scenario to reach its failure state (default {cluster.DEFAULT_TIMEOUT_SECONDS})",
    )
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    return parser.parse_args(argv)


async def main_async(args: argparse.Namespace) -> int:
    scenarios = get_scenarios(args.scenario)
    cluster.ensure_kubectl_available()

    print(f"Running {len(scenarios)} scenario(s). LangSmith tracing: {'on' if TRACING_ON else 'off'}.")
    if not args.no_judge:
        print("Scoring: deterministic signal check + LLM judge.")
    else:
        print("Scoring: deterministic signal check only (--no-judge).")

    records = []
    for index, scenario in enumerate(scenarios, start=1):
        print(f"\n[{index}/{len(scenarios)}] {scenario.id}: setting up in namespace {scenario.namespace} ...")
        record = await run_scenario(
            scenario,
            use_judge=not args.no_judge,
            keep_namespace=args.keep,
            setup_timeout=args.setup_timeout,
        )
        if record.status == "setup_failed":
            print(f"  setup failed: {record.error}")
        elif record.status == "agent_failed":
            print(f"  agent failed: {record.error.splitlines()[0]}")
        else:
            mark = "correct" if record.correct else "incorrect"
            print(f"  {mark} — {record.planner_tool_calls} planner tool call(s) in {record.duration_seconds:.0f}s")
        records.append(record)

    summary = summarize(records)
    results_path = write_results(records, summary, TRACING_ON)
    print_report(records, summary, results_path)
    return 0 if summary.scored == summary.total else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list:
        for scenario in get_scenarios(args.scenario):
            print(f"{scenario.id:<15} [{scenario.difficulty:<6}] {scenario.notes}")
        return 0
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\ninterrupted; scenario namespaces may still exist (kubectl get ns | grep eval-)")
        return 130


if __name__ == "__main__":
    sys.exit(main())
