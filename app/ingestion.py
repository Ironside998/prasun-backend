"""Turns raw meter/weather/price ingestion into a solver-ready payload dict."""
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from . import models, schemas


def get_active_meter_ids(db: Session, limit: int = 50):
    ids = [r[0] for r in db.query(models.CustomerProfile.meter_id).all()]
    if ids:
        return ids[:limit]
    # Fallback: nobody registered profiles yet, infer from raw readings.
    ids = [r[0] for r in db.query(models.MeterReading.meter_id).distinct().all()]
    return ids[:limit]


def hourly_base_load(db: Session, meter_id: str, end_time: datetime, hours: int = 24):
    """Aggregate 15-min power readings (kW) into `hours` hourly energy values (kWh)
    for the window (end_time - hours, end_time]. Missing hours are filled with 0.0."""
    start_time = end_time - timedelta(hours=hours)
    rows = (db.query(models.MeterReading)
            .filter(models.MeterReading.meter_id == meter_id)
            .filter(models.MeterReading.timestamp > start_time)
            .filter(models.MeterReading.timestamp <= end_time)
            .all())

    buckets = [0.0] * hours
    for r in rows:
        offset_hours = (r.timestamp - start_time).total_seconds() / 3600.0
        idx = min(max(int(offset_hours), 0), hours - 1)
        buckets[idx] += r.load_kw * 0.25  # kW over a 15-min slice -> kWh
    return buckets


def latest_weather(db: Session):
    row = db.query(models.WeatherSeries).order_by(models.WeatherSeries.id.desc()).first()
    if not row:
        return None, None
    return row.irradiance, row.wind


def latest_price(db: Session):
    row = db.query(models.PriceSeries).order_by(models.PriceSeries.id.desc()).first()
    return row.rtp_price if row else None


def build_dsm_request(db: Session, end_time: datetime = None) -> dict:
    """Assemble a DSMRequest-shaped, fully-defaulted payload from the latest
    ingested meter, weather and price data."""
    end_time = end_time or datetime.utcnow()

    meter_ids = get_active_meter_ids(db)
    if not meter_ids:
        raise ValueError("No registered meters / meter readings found.")

    solar, wind = latest_weather(db)
    rtp_price = latest_price(db)
    if rtp_price is None:
        raise ValueError("No RTP price series ingested yet.")

    customers = []
    for mid in meter_ids:
        profile = db.query(models.CustomerProfile).get(mid)
        base_load = hourly_base_load(db, mid, end_time)
        customers.append({
            "meter_id": mid,
            "is_first_time": profile.is_first_time if profile else 1,
            "engagement_yesterday": profile.engagement_yesterday if profile else 0.80,
            "engagement_today": profile.engagement_today if profile else 0.80,
            "base_load": base_load,
        })

    raw = {
        "horizon": 24,
        "dt": 1.0,
        "rtp_price": rtp_price,
        "solar": solar,
        "wind": wind,
        "customers": customers,
    }
    # Validate through the existing schema so all the business-parameter
    # defaults (penalty rates, weights, etc.) get filled in automatically.
    return schemas.DSMRequest(**raw).model_dump()