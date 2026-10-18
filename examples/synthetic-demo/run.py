"""Exercise change control using synthetic data in an auto-cleaned workspace."""
from pathlib import Path
import shutil
import tempfile

from evidence_agent_core import AgentCore, CoreError


def main():
    fixtures = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="eac-synthetic-") as directory:
        root = Path(directory)
        core = AgentCore(root)
        core.init()
        transcript = root / "synthetic.jsonl"
        transcript.write_text('{"role":"user","content":"Fictional workflow experiment"}\n')
        core.capture(session_id="synthetic-session-001", transcript=transcript,
                     runtime="synthetic-demo", event="manual", auto_review=True)
        for folder in ("evidence", "evals", "proposals"):
            shutil.copytree(fixtures / folder, core.store / folder, dirs_exist_ok=True)
        proposal = "proposals/LD-001.json"
        try:
            core.verify(proposal)
        except CoreError:
            print("1. PASS: unreviewed proposal rejected")
        else:
            raise AssertionError("Unreviewed proposal was accepted")
        core.decide_review(session_id="synthetic-session-001", decision="candidate",
                           proposal=proposal, note="Synthetic example; no real approval.")
        core.verify(proposal)
        print("2. PASS: reviewed candidate and evidence checks passed")
        try:
            core.promote(proposal, "")
        except CoreError:
            print("3. PASS: missing approval label rejected")
        else:
            raise AssertionError("Missing approval label was accepted")
        # Fictional approval inside this disposable fixture, not user authorization.
        core.promote(proposal, "synthetic-demo-not-a-real-approval")
        assert core.status()["promoted_rules"] == 1
        assert core.coordination.current_mode() == "shadow"
        print("4. PASS: synthetic approval recorded; adapter generated; mode stays shadow")
    print("DONE: temporary workspace removed; no hooks or real rules changed")


if __name__ == "__main__":
    main()
