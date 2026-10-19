"""FastAPI engine (optional live mode).

The app works without this server by reading the JSON snapshots that GitHub
Actions publishes. Run this when you want live, on-demand refreshes:

    uvicorn main:app --host 0.0.0.0 --port 8000
Set RUN_SCHEDULER=1 to also run the daily jobs from this process.
"""
import math
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import jobs
import learning
import macro
import news
import screener
import store
from config import IST


@asynccontextmanager
async def lifespan(app):
    store.init()
    sched = None
    if os.getenv("RUN_SCHEDULER") == "1":
        from apscheduler.schedulers.background import BackgroundScheduler
        sched = BackgroundScheduler(timezone=IST)
        sched.add_job(jobs.premarket, "cron", day_of_week="mon-fri", hour=8, minute=0)
        sched.add_job(jobs.postclose, "cron", day_of_week="mon-fri", hour=16, minute=30)
        sched.start()
    yield
    if sched:
        sched.shutdown()


app = FastAPI(title="NSE Momentum & Options Engine", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health():
    return {"ok": True, "time": store.now_ist().isoformat(), "model": learning.model_info(store.latest_state())}


@app.get("/api/global-bias")
def global_bias(refresh: bool = False):
    try:
        return {**macro.global_bias(force=refresh), "disclaimer": jobs.DISCLAIMER}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))


@app.get("/api/volume-breakouts")
def volume_breakouts():
    return {**screener.scan(), "disclaimer": jobs.DISCLAIMER}


@app.get("/api/plan")
def plan():
    return jobs.build_plan(macro.global_bias(), screener.scan())


@app.get("/api/performance")
def performance():
    return learning.performance()


@app.get("/api/news")
def market_news():
    return news.market_news()


class PositionIn(BaseModel):
    capital: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    risk_pct: float = Field(1.0, gt=0, le=10)
    target: Optional[float] = None


def position_size(capital, entry_price, stop_loss, risk_pct=1.0, target=None):
    rps = abs(entry_price - stop_loss)
    if rps == 0:
        raise ValueError("Entry and stop-loss cannot be equal")
    risk_budget = capital * risk_pct / 100
    by_risk = math.floor(risk_budget / rps)
    by_cash = math.floor(capital / entry_price)
    qty = max(0, min(by_risk, by_cash))
    out = {
        "quantity": qty, "capital_used": round(qty * entry_price, 2),
        "max_loss": round(qty * rps, 2), "risk_budget": round(risk_budget, 2),
        "risk_per_share": round(rps, 2), "limited_by": "capital" if by_cash < by_risk else "risk",
        "note": "Excludes brokerage, STT, GST and slippage. Gaps can exceed the stop-loss.",
    }
    if target:
        out["reward_at_target"] = round(qty * abs(target - entry_price), 2)
        out["reward_risk"] = round(abs(target - entry_price) / rps, 2)
    return out


@app.post("/api/position-calculator")
def position_calculator(body: PositionIn):
    try:
        return position_size(body.capital, body.entry_price, body.stop_loss, body.risk_pct, body.target)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/position-calculator")
def position_calculator_get(capital: float, entry_price: float, stop_loss: float,
                            risk_pct: float = 1.0, target: Optional[float] = None):
    return position_calculator(PositionIn(capital=capital, entry_price=entry_price, stop_loss=stop_loss,
                                          risk_pct=risk_pct, target=target))


@app.post("/api/jobs/{name}")
def run_job(name: str):
    fn = {"premarket": jobs.premarket, "postclose": jobs.postclose, "learn": jobs.learn}.get(name)
    if not fn:
        raise HTTPException(404, "unknown job")
    return fn()
