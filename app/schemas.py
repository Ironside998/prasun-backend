"""Pydantic models = the exact JSON shape exchanged with the frontend."""
from typing import List, Optional
from pydantic import BaseModel, Field, conlist


class CustomerInput(BaseModel):
    meter_id: str = Field(..., examples=["Meter 2"])
    is_first_time: int = Field(1, ge=0, le=1)
    engagement_yesterday: float = Field(0.80, ge=0, le=1)
    engagement_today: float = Field(0.80, ge=0, le=1)
    # 24 hourly base-load values (kWh). This is the customer's declared profile.
    base_load: conlist(float, min_length=24, max_length=24)


class DSMRequest(BaseModel):
    """Full payload posted by the frontend to start one DSM run."""
    horizon: int = Field(24, description="Scheduling hours")
    dt: float = Field(1.0, description="Time step in hours")

    # Environment series (24 values each)
    rtp_price: conlist(float, min_length=24, max_length=24)
    solar: Optional[conlist(float, min_length=24, max_length=24)] = None
    wind: Optional[conlist(float, min_length=24, max_length=24)] = None

    customers: List[CustomerInput]

    # Optional overrides for the business parameters (sensible defaults applied)
    day_ahead_margin_frac: float = 0.05
    max_advance_frac: float = 0.35
    max_curtail_frac: float = 0.35
    validation_threshold: float = 0.60
    peak_penalty_rate: float = 0.20
    base_discomfort_rate: float = 0.08
    mismanagement_penalty: float = 5.00
    renewable_credit_rs: float = 0.20
    base_incentive_rate: float = 0.10
    peak_incentive_bonus: float = 0.20
    satisfaction_min: float = 0.80
    sat_shift_weight: float = 0.25
    sat_mis_weight: float = 1.00
    sat_engagement_weight: float = 0.10


class CustomerResult(BaseModel):
    customer: int
    meter_id: str
    is_first_time: int
    engagement_final: float
    validated_for_dsm: int
    daily_energy_kwh: float
    shifted_energy_kwh: float
    mismanaged_energy_kwh: float
    base_energy_cost_rs: float
    incentives_earned_rs: float
    raw_penalty_rs: float
    final_penalty_charged_rs: float
    net_bill_rs: float
    satisfaction_percent: float
    ui_dashboard_status: str


class HourlyResult(BaseModel):
    hour: int
    baseline_load_kw: float
    dsm_load_kw: float
    rtp_rs_per_kwh: float
    effective_rtp_rs_per_kwh: float
    renewable_index: float
    high_price_hour: int
    incentive_rs_per_kwh: float
    shifted_load_kw: float
    mismanaged_load_kw: float


class DSMResponse(BaseModel):
    run_id: int
    run_type: Optional[str] = None
    status: str
    objective_value: float
    base_cost_rs: float
    dsm_cost_rs: float
    energy_saving_rs: float
    peak_base_kw: float
    peak_dsm_kw: float
    total_incentive_rs: float
    average_satisfaction: float
    customers: List[CustomerResult]
    hourly: List[HourlyResult]
# --- append to app/schemas.py ---
from datetime import datetime


class MeterReadingIn(BaseModel):
    meter_id: str = Field(..., examples=["Meter 2"])
    timestamp: datetime
    load_kw: float = Field(..., description="Average power over the 15-min interval, in kW")


class MeterReadingBatchIn(BaseModel):
    readings: List[MeterReadingIn]


class WeatherSeriesIn(BaseModel):
    date: str = Field(..., examples=["2026-07-03"])
    irradiance: conlist(float, min_length=24, max_length=24)
    wind: conlist(float, min_length=24, max_length=24)


class PriceSeriesIn(BaseModel):
    date: str = Field(..., examples=["2026-07-03"])
    rtp_price: conlist(float, min_length=24, max_length=24)


class CustomerProfileIn(BaseModel):
    meter_id: str
    is_first_time: int = Field(1, ge=0, le=1)
    engagement_yesterday: float = Field(0.80, ge=0, le=1)
    engagement_today: float = Field(0.80, ge=0, le=1)


class CustomerMetricsOut(BaseModel):
    meter_id: str
    engagement_score: float
    daily_energy_kwh: float
    shifted_energy_kwh: float
    mismanaged_energy_kwh: float
    incentive_earned_rs: float
    penalty_charged_rs: float
    net_bill_rs: float
    satisfaction_percent: float


class LoadShiftPoint(BaseModel):
    hour: int
    baseline_load_kw: float
    dsm_load_kw: float
    shifted_load_kw: float
    mismanaged_load_kw: float


class PriceRenewablePoint(BaseModel):
    hour: int
    rtp_rs_per_kwh: float
    effective_rtp_rs_per_kwh: float
    renewable_index: float
    high_price_hour: int


class SatisfactionPoint(BaseModel):
    meter_id: str
    satisfaction_percent: float


class SatisfactionOverview(BaseModel):
    average_satisfaction: float
    customers: List[SatisfactionPoint]