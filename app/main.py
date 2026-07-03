"""FastAPI backend: ingest meter/weather/price data, solve MILP, serve results."""
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .database import engine, SessionLocal, Base
from . import models, schemas, ingestion, runner
from .scheduler import start_scheduler

Base.metadata.create_all(bind=engine)

app = FastAPI(title="DSM Business-Model Backend", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.on_event("startup")
def on_startup():
    start_scheduler()


@app.get("/health")
def health():
    return {"status": "ok"}


# ------------------------------------------------------------------
# Ingestion
# ------------------------------------------------------------------

@app.post("/api/meters/ingest")
def ingest_meter_reading(reading: schemas.MeterReadingIn, db: Session = Depends(get_db)):
    existing = (db.query(models.MeterReading)
                .filter_by(meter_id=reading.meter_id, timestamp=reading.timestamp).first())
    if existing:
        existing.load_kw = reading.load_kw
    else:
        db.add(models.MeterReading(
            meter_id=reading.meter_id, timestamp=reading.timestamp, load_kw=reading.load_kw
        ))
    db.commit()
    return {"status": "ok"}


@app.post("/api/meters/ingest/batch")
def ingest_meter_batch(batch: schemas.MeterReadingBatchIn, db: Session = Depends(get_db)):
    for r in batch.readings:
        existing = (db.query(models.MeterReading)
                    .filter_by(meter_id=r.meter_id, timestamp=r.timestamp).first())
        if existing:
            existing.load_kw = r.load_kw
        else:
            db.add(models.MeterReading(meter_id=r.meter_id, timestamp=r.timestamp, load_kw=r.load_kw))
    db.commit()
    return {"status": "ok", "count": len(batch.readings)}


@app.post("/api/customers/register")
def register_customer(profile: schemas.CustomerProfileIn, db: Session = Depends(get_db)):
    existing = db.query(models.CustomerProfile).get(profile.meter_id)
    if existing:
        existing.is_first_time = profile.is_first_time
        existing.engagement_yesterday = profile.engagement_yesterday
        existing.engagement_today = profile.engagement_today
    else:
        db.add(models.CustomerProfile(**profile.model_dump()))
    db.commit()
    return {"status": "ok"}


def _run_day_ahead_background():
    """Fired once/day when tomorrow's weather forecast is posted."""
    db = SessionLocal()
    try:
        payload = ingestion.build_dsm_request(db)
        run = runner.execute_and_store(db, payload, run_type="DAY_AHEAD")
        print(f"[weather-triggered] Day-ahead run {run.id} completed: {run.status}")
    except Exception as e:
        print(f"[weather-triggered] Day-ahead run failed: {e}")
    finally:
        db.close()


@app.post("/api/weather/ingest")
def ingest_weather(series: schemas.WeatherSeriesIn, background_tasks: BackgroundTasks,
                    db: Session = Depends(get_db)):
    existing = db.query(models.WeatherSeries).filter_by(date=series.date).first()
    if existing:
        existing.irradiance = series.irradiance
        existing.wind = series.wind
    else:
        db.add(models.WeatherSeries(date=series.date, irradiance=series.irradiance, wind=series.wind))
    db.commit()

    # New forecast in -> immediately kick off the day-ahead solve in the background,
    # so this endpoint returns fast and doesn't block the meter/weather source on a solve.
    background_tasks.add_task(_run_day_ahead_background)
    return {"status": "ok", "day_ahead_run_triggered": True}


@app.post("/api/price/ingest")
def ingest_price(series: schemas.PriceSeriesIn, db: Session = Depends(get_db)):
    existing = db.query(models.PriceSeries).filter_by(date=series.date).first()
    if existing:
        existing.rtp_price = series.rtp_price
    else:
        db.add(models.PriceSeries(date=series.date, rtp_price=series.rtp_price))
    db.commit()
    return {"status": "ok"}


# ------------------------------------------------------------------
# Solve endpoints
# ------------------------------------------------------------------

@app.post("/api/dsm/run", response_model=schemas.DSMResponse)
def run_dsm(req: schemas.DSMRequest, db: Session = Depends(get_db)):
    """Manual run: caller supplies the full payload directly."""
    payload = req.model_dump()
    try:
        run = runner.execute_and_store(db, payload, run_type="MANUAL")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Solver failed: {e}")
    return {"run_id": run.id, "run_type": run.run_type, **run.result_payload}


@app.post("/api/dsm/run-latest", response_model=schemas.DSMResponse)
def run_latest(run_type: str = Query("INTRADAY", pattern="^(DAY_AHEAD|INTRADAY)$"),
                db: Session = Depends(get_db)):
    """Manually trigger a solve from the latest ingested data (for testing/ops)."""
    try:
        payload = ingestion.build_dsm_request(db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        run = runner.execute_and_store(db, payload, run_type=run_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Solver failed: {e}")
    return {"run_id": run.id, "run_type": run.run_type, **run.result_payload}


@app.get("/api/dsm/runs")
def list_runs(run_type: Optional[str] = None, db: Session = Depends(get_db)):
    q = db.query(models.DSMRun)
    if run_type:
        q = q.filter(models.DSMRun.run_type == run_type)
    rows = q.order_by(models.DSMRun.id.desc()).all()
    return [{
        "run_id": r.id, "run_type": r.run_type, "created_at": r.created_at, "status": r.status,
        "energy_saving_rs": r.energy_saving_rs,
        "average_satisfaction": r.average_satisfaction,
    } for r in rows]


@app.get("/api/dsm/runs/{run_id}", response_model=schemas.DSMResponse)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.query(models.DSMRun).get(run_id)
    if not run or not run.result_payload:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run_id": run.id, "run_type": run.run_type, **run.result_payload}


# ------------------------------------------------------------------
# Frontend read endpoints
# Default to the latest run of ANY type (freshest number available);
# pass ?run_type=DAY_AHEAD to pin to the official committed schedule instead.
# ------------------------------------------------------------------

def _latest_run(db: Session, run_type: Optional[str] = None) -> models.DSMRun:
    q = db.query(models.DSMRun).filter(models.DSMRun.result_payload.isnot(None))
    if run_type:
        q = q.filter(models.DSMRun.run_type == run_type)
    run = q.order_by(models.DSMRun.id.desc()).first()
    if not run:
        raise HTTPException(status_code=404, detail="No completed DSM run yet")
    return run


@app.get("/api/dsm/customers", response_model=List[schemas.CustomerMetricsOut])
def customer_metrics(run_type: Optional[str] = None, db: Session = Depends(get_db)):
    run = _latest_run(db, run_type)
    return [{
        "meter_id": c["meter_id"],
        "engagement_score": c["engagement_final"],
        "daily_energy_kwh": c["daily_energy_kwh"],
        "shifted_energy_kwh": c["shifted_energy_kwh"],
        "mismanaged_energy_kwh": c["mismanaged_energy_kwh"],
        "incentive_earned_rs": c["incentives_earned_rs"],
        "penalty_charged_rs": c["final_penalty_charged_rs"],
        "net_bill_rs": c["net_bill_rs"],
        "satisfaction_percent": c["satisfaction_percent"],
    } for c in run.result_payload["customers"]]


@app.get("/api/dsm/customers/{meter_id}", response_model=schemas.CustomerMetricsOut)
def customer_metrics_one(meter_id: str, run_type: Optional[str] = None, db: Session = Depends(get_db)):
    run = _latest_run(db, run_type)
    for c in run.result_payload["customers"]:
        if c["meter_id"] == meter_id:
            return {
                "meter_id": c["meter_id"],
                "engagement_score": c["engagement_final"],
                "daily_energy_kwh": c["daily_energy_kwh"],
                "shifted_energy_kwh": c["shifted_energy_kwh"],
                "mismanaged_energy_kwh": c["mismanaged_energy_kwh"],
                "incentive_earned_rs": c["incentives_earned_rs"],
                "penalty_charged_rs": c["final_penalty_charged_rs"],
                "net_bill_rs": c["net_bill_rs"],
                "satisfaction_percent": c["satisfaction_percent"],
            }
    raise HTTPException(status_code=404, detail="Customer not found in latest run")


@app.get("/api/dsm/overview/load-shift", response_model=List[schemas.LoadShiftPoint])
def load_shift_overview(run_type: Optional[str] = None, db: Session = Depends(get_db)):
    run = _latest_run(db, run_type)
    return [{
        "hour": h["hour"],
        "baseline_load_kw": h["baseline_load_kw"],
        "dsm_load_kw": h["dsm_load_kw"],
        "shifted_load_kw": h["shifted_load_kw"],
        "mismanaged_load_kw": h["mismanaged_load_kw"],
    } for h in run.result_payload["hourly"]]


@app.get("/api/dsm/overview/price-renewable", response_model=List[schemas.PriceRenewablePoint])
def price_renewable_overview(run_type: Optional[str] = None, db: Session = Depends(get_db)):
    run = _latest_run(db, run_type)
    return [{
        "hour": h["hour"],
        "rtp_rs_per_kwh": h["rtp_rs_per_kwh"],
        "effective_rtp_rs_per_kwh": h["effective_rtp_rs_per_kwh"],
        "renewable_index": h["renewable_index"],
        "high_price_hour": h["high_price_hour"],
    } for h in run.result_payload["hourly"]]


@app.get("/api/dsm/overview/satisfaction", response_model=schemas.SatisfactionOverview)
def satisfaction_overview(run_type: Optional[str] = None, db: Session = Depends(get_db)):
    run = _latest_run(db, run_type)
    customers = [{
        "meter_id": c["meter_id"],
        "satisfaction_percent": c["satisfaction_percent"],
    } for c in run.result_payload["customers"]]
    return {"average_satisfaction": run.average_satisfaction, "customers": customers}