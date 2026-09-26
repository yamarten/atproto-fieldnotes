"""Run the normal CLI or worker with deterministic response providers only."""
from pathlib import Path
import os
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from enrich_overlap_support import Responses
from project_registry_core import cli, worker


if __name__ == "__main__":
    mode = sys.argv.pop(1)
    responses = Responses(llm_delay=.02)
    if os.getenv("ENRICH_TEST_SCENARIO") == "slow_second":
        def slow(url):
            if "overlap-2" in url:
                Path(os.environ["ENRICH_TEST_MARKER"]).touch()
                time.sleep(.4)
        responses.on_probe = slow
    with responses.patches():
        raise SystemExit(worker.main() if mode == "worker" else cli.main())
