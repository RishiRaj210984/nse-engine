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


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    main(sys.argv[1] if len(sys.argv) > 1 else "")
