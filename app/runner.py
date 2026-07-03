"""Shared 'solve + persist' logic, used by both manual, scheduled, and event-triggered runs."""
from sqlalchemy.orm import Session
from . import models
from .solver import solve_dsm


def execute_and_store(db: Session, payload: dict, run_type: str = "INTRADAY") -> models.DSMRun:
    run = models.DSMRun(status="RUNNING", request_payload=payload, run_type=run_type)
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        result = solve_dsm(payload)
    except Exception:
        run.status = "ERROR"
        db.commit()
        raise

    run.status = result["status"]
    run.objective_value = result["objective_value"]
    run.base_cost_rs = result["base_cost_rs"]
    run.dsm_cost_rs = result["dsm_cost_rs"]
    run.energy_saving_rs = result["energy_saving_rs"]
    run.average_satisfaction = result["average_satisfaction"]
    run.result_payload = result

    for c in result["customers"]:
        db.add(models.CustomerRow(
            run_id=run.id,
            meter_id=c["meter_id"],
            engagement_final=c["engagement_final"],
            daily_energy_kwh=c["daily_energy_kwh"],
            shifted_energy_kwh=c["shifted_energy_kwh"],
            mismanaged_energy_kwh=c["mismanaged_energy_kwh"],
            net_bill_rs=c["net_bill_rs"],
            incentives_earned_rs=c["incentives_earned_rs"],
            final_penalty_charged_rs=c["final_penalty_charged_rs"],
            satisfaction_percent=c["satisfaction_percent"],
            ui_dashboard_status=c["ui_dashboard_status"],
        ))
    db.commit()
    db.refresh(run)
    return run