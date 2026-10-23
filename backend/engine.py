"""Command-line entry point (used by GitHub Actions).

    python backend/engine.py bootstrap   # one-time: seed journal from ~2y history, then learn
    python backend/engine.py premarket   # ~08:00 IST: bias + options, publish
    python backend/engine.py postclose   # ~16:30 IST: scan, grade, retrain, publish
    python backend/engine.py publish     # just refresh public/*.json
"""
import json
import logging
import sys
import time

import jobs
import learning
import store


def main(task):
    store.init()
    t0 = time.time()
    if task == "bootstrap":
        out = learning.bootstrap()
        out["learn"] = jobs.learn()
        jobs.publish_all()
    elif task == "premarket":
        out = jobs.premarket()
    elif task == "postclose":
        out = jobs.postclose()
    elif task == "learn":
        out = jobs.learn()
    elif task == "live":
        import live
        out = live.run()
    elif task == "publish":
        jobs.publish_all()
        out = {"published": True}
    else:
        raise SystemExit(__doc__)
    out["seconds"] = round(time.time() - t0, 1)
    print(json.dumps(out, indent=1, default=str))


def record_error(task, exc):
    """Keep the last 30 failures in state/run_errors.json; the workflow commits it even when a run
    fails, so a failure can be read from the repo without the (login-only) Actions log."""
    import os
    import traceback
    from datetime import datetime
    from config import IST, STATE_DIR
    path = os.path.join(STATE_DIR, "run_errors.json")
    try:
        errs = json.load(open(path, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        errs = []
    errs.append({"task": task, "at": datetime.now(IST).isoformat(), "error": f"{type(exc).__name__}: {exc}"[:500],
                 "traceback": traceback.format_exc()[-4000:]})
    os.makedirs(STATE_DIR, exist_ok=True)
    json.dump(errs[-30:], open(path, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    task = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        main(task)
    except SystemExit:
        raise
    except Exception as exc:
        record_error(task, exc)
        raise
