"""Test Agent — runs the unit + integration scenario suite and reports each one.

Three things, in one run:

  1. UNIT TESTS        — every component checked in isolation (mappers, verifiers,
                         the signal model, the bus, the L2 projection, shared infra).
  2. INTEGRATION TESTS — components wired together through the REAL pipe
                         (connector ingest/poll -> bus -> L2 sink, and the FastAPI app).
  3. COVERAGE MAP      — which components the scenarios exercise, split into unit /
                         integration, with an overall coverage percentage.

Every scenario prints what it asserts (plain English), the input it fed and the
output it produced, and PASS/FAIL — so the run reads as living documentation, not
just a pass list.

Run:
    python -m tests.agents.test_agent
    python -m tests.agents.test_agent --kind UNIT          # only unit tests
    python -m tests.agents.test_agent --source gmail       # only one component
    python -m tests.agents.test_agent --quiet              # hide input/output lines
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.agents.scenarios import (  # noqa: E402
    COMPONENTS, INTEGRATION, LIVE_STATUS, UNIT, ScenarioResult, coverage, run_all,
    run_pytest_suite,
)


def _utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # avoid cp1252 crashes on Windows
    except Exception:
        pass


def _quiet_logs() -> None:
    import logging
    logging.disable(logging.CRITICAL)


def _fmt(value: object, width: int = 90) -> str:
    s = str(value)
    return s if len(s) <= width else s[: width - 1] + "…"


_RULE = "=" * 100
_THIN = "-" * 100


class TestAgent:
    """Runs the scenario catalog and renders a sectioned, self-documenting report."""

    __test__ = False  # this is an agent, not a pytest test class — don't collect it
    name = "test-agent"

    def __init__(self, kind: str | None = None, source: str | None = None,
                 quiet: bool = False, run_pytest: bool = True) -> None:
        self.kind = kind
        self.source = source
        self.quiet = quiet
        self.run_pytest = run_pytest

    def run(self) -> list[ScenarioResult]:
        return run_all(self.kind, self.source)

    # ---- rendering -------------------------------------------------------
    def report(self, results: list[ScenarioResult]) -> int:
        print(_RULE)
        print(" L1 SignalFabric — TEST AGENT")
        print(" Agent scenarios (unit + integration) + the repo's integrated pytest suite,")
        print(" with a per-component coverage map.")
        print(_RULE)

        unit = [r for r in results if r.scenario.kind == UNIT]
        integ = [r for r in results if r.scenario.kind == INTEGRATION]

        print("\n#### PART A — AGENT SCENARIOS (in-process, OFFLINE — fake clients) ####")
        if unit:
            self._section("UNIT TESTS — each component in isolation", unit)
        if integ:
            self._section("INTEGRATION TESTS — components through the real pipe (ingest → bus → L2)", integ)

        self._coverage_map(results)
        self._live_verification()

        suite = self._pytest_section()
        self._roster(results, suite)
        return self._summary(results, unit, integ, suite)

    def _live_verification(self) -> None:
        """Honest real-tenant status. The scenarios above are OFFLINE (fake
        clients) — passing proves the code, NOT that the source is live."""
        print("\nLIVE VERIFICATION — real-tenant integration")
        print("  (the unit/integration scenarios above are OFFLINE and do NOT prove this)")
        print(_THIN)
        verified = 0
        for src in ("slack", "gmail", "outlook", "sharepoint", "notion", "database"):
            status, note = LIVE_STATUS.get(src, ("PENDING", ""))
            live = status == "LIVE"
            verified += 1 if live else 0
            print(f"  [{'x' if live else ' '}] {src:<11} {status:<8} {note}")
        print(_THIN)
        live_names = ", ".join(s.capitalize() for s, (st, _) in LIVE_STATUS.items() if st == "LIVE")
        not_live = ", ".join(s.capitalize() for s, (st, _) in LIVE_STATUS.items() if st != "LIVE")
        print(f"  live-verified sources: {verified}/{len(LIVE_STATUS)} "
              f"({live_names or 'none'} live — {not_live or 'none'} live integration NOT complete)")

    def _roster(self, results: list[ScenarioResult], suite: dict | None) -> None:
        """Every scenario across Part A + Part B, split into success and failed —
        each and every one, in one place."""
        passed: list[str] = []
        failed: list[str] = []
        for r in results:
            tag = f"[{r.scenario.kind[:4].lower():<4}] {r.id:<24} {r.scenario.title}"
            (passed if r.ok else failed).append(tag)
        if suite and suite.get("available"):
            for f, name, st in suite["tests"]:
                short = f.split("/")[-1]
                tag = f"[pyte] {short}::{name}"
                if st in ("failed", "error"):
                    failed.append(tag)
                elif st == "passed":
                    passed.append(tag)
                # skipped tests are listed separately, not as success/fail
        print("\n" + _RULE)
        print(" EVERY SCENARIO — SUCCESS & FAILED (each and every one)")
        print(_THIN)
        print(f"  ✓ SUCCESS ({len(passed)})")
        for line in passed:
            print(f"    + {line}")
        print(f"\n  ✗ FAILED ({len(failed)})")
        if failed:
            for line in failed:
                print(f"    - {line}")
        else:
            print("    (none)")

    def _pytest_section(self) -> dict | None:
        print("\n#### PART B — PROJECT TEST SUITE (the repo's integrated pytest tests) ####")
        if not self.run_pytest:
            print("  (skipped — --no-pytest)")
            return None
        if self.kind or self.source:
            print("  (skipped — filtered agent run; pytest runs on the full repo only)")
            return None
        print("  running `pytest tests` (temp L2 store, never the live data dir) …")
        suite = run_pytest_suite()
        if not suite.get("available"):
            print(f"  NOT RUN: {suite.get('note', 'pytest unavailable')}")
            files = suite.get("files", [])
            print(f"  {len(files)} integrated test file(s) discovered:")
            for f in files:
                print(f"    · {f}")
            return suite
        c = suite["counts"]
        print(f"  {c['passed']} passed · {c['failed']} failed · {c['skipped']} skipped"
              f"{(' · ' + str(c['error']) + ' error') if c['error'] else ''}"
              f"   ({suite['total']} tests across {len(suite['by_file'])} files)")
        # every individual test case, grouped by file (each and every one)
        _tag = {"passed": "PASS", "failed": "FAIL", "error": "ERR ", "skipped": "SKIP"}
        for f in sorted(suite["by_file"]):
            d = suite["by_file"][f]
            tot = d["passed"] + d["failed"] + d["skipped"] + d["error"]
            print(f"\n  ▸ {f}  ({d['passed']}/{tot})")
            print(f"  {_THIN}")
            for name, st in d["tests"]:
                print(f"    [{_tag.get(st, st.upper())}] {name}")
        if suite.get("summary"):
            print(f"\n  pytest: {suite['summary']}")
        return suite

    def _section(self, heading: str, rows: list[ScenarioResult]) -> None:
        passed = sum(1 for r in rows if r.ok)
        print(f"\n{heading}")
        print(f"  {passed}/{len(rows)} passed")
        by_comp: dict[str, list[ScenarioResult]] = defaultdict(list)
        for r in rows:
            by_comp[r.scenario.source].append(r)
        for comp in sorted(by_comp):
            crows = by_comp[comp]
            cpass = sum(1 for r in crows if r.ok)
            print(f"\n  ▸ {comp}  ({cpass}/{len(crows)})")
            print(f"  {_THIN}")
            for r in crows:
                tag = "PASS" if r.ok else "FAIL"
                # 'target' connectors are built + offline-tested; flag their LIVE
                # (real-tenant) status honestly — verified vs still pending.
                if r.scenario.target:
                    src_live = LIVE_STATUS.get(r.scenario.source, ("", ""))[0] == "LIVE"
                    flag = " ·offline (real-tenant verified)" if src_live else " ·offline (live pending)"
                else:
                    flag = ""
                print(f"    [{tag}] {r.id:<22} {r.scenario.title}{flag}")
                print(f"           asserts: {_fmt(r.scenario.asserts, 86)}")
                if not self.quiet or not r.ok:
                    print(f"           input  : {_fmt(r.input)}")
                    print(f"           output : {_fmt(r.output)}")
                if not r.ok:
                    print(f"           >> FAIL detail: {_fmt(r.detail, 84)}")

    def _coverage_map(self, results: list[ScenarioResult]) -> None:
        cov = coverage(results)
        print("\nCOVERAGE MAP — which components the scenarios exercise")
        print(_THIN)
        print(f"  {'component':<26}{'unit':>10}{'integration':>14}   description")
        both = covered = 0
        for mod, _desc in COMPONENTS:
            c = cov[mod]
            n_u, n_i = len(c["unit"]), len(c["integration"])
            has_u, has_i = n_u > 0, n_i > 0
            if has_u or has_i:
                covered += 1
            if has_u and has_i:
                both += 1
            u_cell = f"{c['unit_pass']}/{n_u}" if has_u else "—"
            i_cell = f"{c['int_pass']}/{n_i}" if has_i else "—"
            mark = "✓" if (has_u or has_i) else "✗"
            print(f"  {mark} {mod:<24}{u_cell:>10}{i_cell:>14}   {_fmt(c['desc'], 40)}")
        total = len(COMPONENTS)
        print(_THIN)
        print(f"  components with ≥1 test : {covered}/{total} "
              f"({round(100*covered/total)}%)")
        print(f"  with BOTH unit + integ : {both}/{total} "
              f"({round(100*both/total)}%)")
        uncovered = [m for m, _ in COMPONENTS if not (cov[m]["unit"] or cov[m]["integration"])]
        if uncovered:
            print(f"  NOT COVERED            : {', '.join(uncovered)}")

    def _summary(self, results: list[ScenarioResult], unit: list[ScenarioResult],
                 integ: list[ScenarioResult], suite: dict | None) -> int:
        failed = [r for r in results if not r.ok]
        targets = [r for r in results if r.scenario.target]
        print("\n" + _RULE)
        print(" SUMMARY")
        print(_THIN)
        print(" Part A — agent scenarios:")
        print(f"   unit         : {sum(1 for r in unit if r.ok)}/{len(unit)} passed")
        print(f"   integration  : {sum(1 for r in integ if r.ok)}/{len(integ)} passed")
        print(f"   subtotal     : {sum(1 for r in results if r.ok)}/{len(results)} passed")
        print(f"   connector code (offline): {sum(1 for r in targets if r.ok)}/{len(targets)} "
              "Gmail/Outlook/SharePoint capability scenarios pass — NOT live verification")
        suite_failed = 0
        if suite is None:
            print(" Part B — pytest suite: (not run)")
        elif not suite.get("available"):
            print(f" Part B — pytest suite: NOT RUN ({suite.get('note', 'unavailable')})")
        else:
            c = suite["counts"]
            suite_failed = c["failed"] + c["error"]
            print(f" Part B — pytest suite : {c['passed']}/{suite['total']} passed"
                  f"{(', ' + str(suite_failed) + ' failing') if suite_failed else ''}")
        if failed:
            print("\n  FAILED agent scenarios:")
            for r in failed:
                print(f"    - [{r.scenario.kind[:4]}] {r.id:<22} {r.scenario.title}")
                print(f"        why: {_fmt(r.detail, 88)}")
        print(_RULE)
        if failed or suite_failed:
            print(f" RESULT: {len(failed)} agent scenario(s) + {suite_failed} pytest test(s) FAILED.")
            return 1
        suite_note = f" + {suite['total']} pytest tests" if suite and suite.get("available") else ""
        print(f" RESULT: all {len(results)} agent scenarios{suite_note} PASS.")
        return 0


def main() -> int:
    _utf8_stdout()
    _quiet_logs()
    ap = argparse.ArgumentParser(description="L1 SignalFabric Test Agent")
    ap.add_argument("--kind", choices=[UNIT, INTEGRATION], help="run only unit or only integration")
    ap.add_argument("--source", help="run only scenarios for one component group (e.g. gmail, core.bus)")
    ap.add_argument("--quiet", action="store_true", help="hide input/output lines for passing scenarios")
    ap.add_argument("--no-pytest", action="store_true", help="skip the repo's pytest suite (Part B)")
    args = ap.parse_args()
    agent = TestAgent(kind=args.kind, source=args.source, quiet=args.quiet,
                      run_pytest=not args.no_pytest)
    return agent.report(agent.run())


if __name__ == "__main__":
    raise SystemExit(main())
