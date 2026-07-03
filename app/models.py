# app/models.py
from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base


class DSMRun(Base):
    __tablename__ = "dsm_runs"
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    run_type = Column(String, default="INTRADAY")   # "DAY_AHEAD" or "INTRADAY"
    status = Column(String)
    objective_value = Column(Float)
    base_cost_rs = Column(Float)
    dsm_cost_rs = Column(Float)
    energy_saving_rs = Column(Float)
    average_satisfaction = Column(Float)
    request_payload = Column(JSON)
    result_payload = Column(JSON)
    customers = relationship("CustomerRow", back_populates="run",
                             cascade="all, delete-orphan")


class CustomerRow(Base):
    __tablename__ = "customer_results"
    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("dsm_runs.id"))
    meter_id = Column(String, index=True)
    engagement_final = Column(Float)
    daily_energy_kwh = Column(Float)
    shifted_energy_kwh = Column(Float)
    mismanaged_energy_kwh = Column(Float)
    net_bill_rs = Column(Float)
    incentives_earned_rs = Column(Float)
    final_penalty_charged_rs = Column(Float)
    satisfaction_percent = Column(Float)
    ui_dashboard_status = Column(String)
    run = relationship("DSMRun", back_populates="customers")


class CustomerProfile(Base):
    """Static-ish per-customer info that smart meters don't report themselves."""
    __tablename__ = "customer_profiles"
    meter_id = Column(String, primary_key=True)
    is_first_time = Column(Integer, default=1)          # 0/1
    engagement_yesterday = Column(Float, default=0.80)
    engagement_today = Column(Float, default=0.80)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MeterReading(Base):
    """One row per 15-min reading posted by a smart meter."""
    __tablename__ = "meter_readings"
    id = Column(Integer, primary_key=True, index=True)
    meter_id = Column(String, index=True, nullable=False)
    timestamp = Column(DateTime, index=True, nullable=False)
    load_kw = Column(Float, nullable=False)   # avg power over the 15-min interval
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("meter_id", "timestamp", name="uq_meter_ts"),)


class WeatherSeries(Base):
    """A full 24-hour irradiance + wind series, posted at once."""
    __tablename__ = "weather_series"
    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, unique=True, index=True)  # 'YYYY-MM-DD'
    irradiance = Column(JSON)  # 24 values
    wind = Column(JSON)        # 24 values
    created_at = Column(DateTime, default=datetime.utcnow)


class PriceSeries(Base):
    """A full 24-hour RTP price series, posted at once."""
    __tablename__ = "price_series"
    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, unique=True, index=True)
    rtp_price = Column(JSON)  # 24 values
    created_at = Column(DateTime, default=datetime.utcnow)