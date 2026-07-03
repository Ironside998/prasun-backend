"""DSM MILP core. Pure compute: dict in -> dict out. No I/O side effects."""
import numpy as np
import pulp


def solve_dsm(payload: dict) -> dict:
    T  = int(payload["horizon"])
    dt = float(payload["dt"])
    customers = payload["customers"]
    N = len(customers)

    # ---- unpack arrays ----
    price = np.asarray(payload["rtp_price"], dtype=float)
    solar = np.asarray(payload.get("solar") or np.zeros(T), dtype=float)
    wind  = np.asarray(payload.get("wind")  or np.zeros(T), dtype=float)

    base_load = np.array([c["base_load"] for c in customers], dtype=float)  # [N,T]
    is_first_time = np.array([c["is_first_time"] for c in customers])
    eng_y = np.array([c["engagement_yesterday"] for c in customers], dtype=float)
    eng_t = np.array([c["engagement_today"]     for c in customers], dtype=float)

    # ---- parameters ----
    P = payload
    engagement = np.clip(0.40 * eng_y + 0.60 * eng_t, 0, 1)
    validated  = engagement >= P["validation_threshold"]

    day_ahead_required = base_load * (1 + P["day_ahead_margin_frac"])
    daily_energy = base_load.sum(axis=1) * dt

    # ---- renewable-adjusted price ----
    eps = np.finfo(float).eps
    solar_norm = solar / max(solar.max(), eps)
    wind_norm  = wind  / max(wind.max(),  eps)
    renewable_index = 0.5 * solar_norm + 0.5 * wind_norm
    effective_price = np.maximum(price - P["renewable_credit_rs"] * renewable_index, 0.0)

    sorted_price = np.sort(effective_price)
    p70 = sorted_price[max(0, int(np.ceil(0.70 * T)) - 1)]
    peak_hour = effective_price >= p70
    incentive_rate = P["base_incentive_rate"] + P["peak_incentive_bonus"] * peak_hour.astype(float)

    # ---- MILP ----
    prob = pulp.LpProblem("DSM_Business_MILP", pulp.LpMinimize)
    I_, T_ = range(N), range(T)
    x      = pulp.LpVariable.dicts("x",      (I_, T_), lowBound=0)
    uplus  = pulp.LpVariable.dicts("uplus",  (I_, T_), lowBound=0)
    uminus = pulp.LpVariable.dicts("uminus", (I_, T_), lowBound=0)
    mis    = pulp.LpVariable.dicts("mis",    (I_, T_), lowBound=0)
    yplus  = pulp.LpVariable.dicts("yplus",  (I_, T_), lowBound=0, upBound=1, cat="Binary")
    yminus = pulp.LpVariable.dicts("yminus", (I_, T_), lowBound=0, upBound=1, cat="Binary")
    peak   = pulp.LpVariable("peak", lowBound=0)

    obj = []
    for i in I_:
        discomfort = P["base_discomfort_rate"] * (1 + (1 - engagement[i]))
        for t in T_:
            obj.append(effective_price[t] * dt * x[i][t])
            obj.append((discomfort * dt + incentive_rate[t] * dt) * uplus[i][t])
            obj.append((discomfort * dt + incentive_rate[t] * dt) * uminus[i][t])
            obj.append(P["mismanagement_penalty"] * dt * mis[i][t])
    obj.append(P["peak_penalty_rate"] * peak)
    prob += pulp.lpSum(obj)

    for i in I_:
        valid = 1.0 if validated[i] else 0.0
        prob += pulp.lpSum(x[i][t] * dt for t in T_) == daily_energy[i], f"daily_energy_{i}"
        prob += pulp.lpSum(P["sat_shift_weight"] * dt * (uplus[i][t] + uminus[i][t])
                           + P["sat_mis_weight"] * dt * mis[i][t] for t in T_) \
                <= (1 - P["satisfaction_min"]) * daily_energy[i], f"satisfaction_{i}"
        for t in T_:
            prob += x[i][t] - uplus[i][t] + uminus[i][t] == base_load[i, t], f"balance_{i}_{t}"
            cap_up   = P["max_advance_frac"] * engagement[i] * valid * max(day_ahead_required[i, t], 1e-6)
            cap_down = P["max_curtail_frac"] * engagement[i] * valid * max(base_load[i, t], 1e-6)
            prob += uplus[i][t]  <= cap_up   * yplus[i][t],  f"capup_{i}_{t}"
            prob += uminus[i][t] <= cap_down * yminus[i][t], f"capdown_{i}_{t}"
            prob += yplus[i][t] + yminus[i][t] <= valid,     f"onedir_{i}_{t}"
            prob += x[i][t] - mis[i][t] <= day_ahead_required[i, t], f"mis_{i}_{t}"

    for t in T_:
        prob += pulp.lpSum(x[i][t] for i in I_) - peak <= 0, f"peak_{t}"

    prob.solve(pulp.PULP_CBC_CMD(msg=0, gapRel=1e-4, timeLimit=300))
    status = pulp.LpStatus[prob.status]
    fval = pulp.value(prob.objective) or 0.0

    # ---- extract ----
    g = np.vectorize(lambda v: v.value() if v.value() is not None else 0.0)
    x_opt      = g(np.array([[x[i][t]      for t in T_] for i in I_]))
    uplus_opt  = g(np.array([[uplus[i][t]  for t in T_] for i in I_]))
    uminus_opt = g(np.array([[uminus[i][t] for t in T_] for i in I_]))
    mis_opt    = g(np.array([[mis[i][t]    for t in T_] for i in I_]))

    baseline_agg = base_load.sum(axis=0)
    dsm_agg      = x_opt.sum(axis=0)
    shift_agg    = (uplus_opt + uminus_opt).sum(axis=0)
    mis_agg      = mis_opt.sum(axis=0)

    cust_shift = (uplus_opt + uminus_opt).sum(axis=1) * dt
    cust_mis   = mis_opt.sum(axis=1) * dt
    cust_inc   = (uplus_opt + uminus_opt) @ incentive_rate * dt
    cust_cost  = x_opt @ effective_price * dt

    raw_penalty = cust_mis * P["mismanagement_penalty"]
    actual_penalty = np.zeros(N)
    dash = []
    for i in range(N):
        if raw_penalty[i] > 0.01:
            if is_first_time[i] == 1:
                actual_penalty[i] = 0.0
                dash.append("Warning Issued: Penalty Waived (First Time)")
            else:
                actual_penalty[i] = raw_penalty[i]
                dash.append("Penalty Applied for Mismanagement")
        else:
            dash.append("Perfect: No Mismanagement")
    net_bill = cust_cost + actual_penalty - cust_inc

    satis = 100 * (1
        - P["sat_shift_weight"] * cust_shift / np.maximum(daily_energy, eps)
        - P["sat_mis_weight"]   * cust_mis   / np.maximum(daily_energy, eps)
        - P["sat_engagement_weight"] * (1 - engagement))
    satis = np.clip(satis, 0, 100)

    base_cost = float(np.sum(baseline_agg * effective_price) * dt)
    dsm_cost  = float(np.sum(dsm_agg * effective_price) * dt)

    customers_out = [{
        "customer": i + 1,
        "meter_id": customers[i]["meter_id"],
        "is_first_time": int(is_first_time[i]),
        "engagement_final": float(engagement[i]),
        "validated_for_dsm": int(validated[i]),
        "daily_energy_kwh": float(daily_energy[i]),
        "shifted_energy_kwh": float(cust_shift[i]),
        "mismanaged_energy_kwh": float(cust_mis[i]),
        "base_energy_cost_rs": float(cust_cost[i]),
        "incentives_earned_rs": float(cust_inc[i]),
        "raw_penalty_rs": float(raw_penalty[i]),
        "final_penalty_charged_rs": float(actual_penalty[i]),
        "net_bill_rs": float(net_bill[i]),
        "satisfaction_percent": float(satis[i]),
        "ui_dashboard_status": dash[i],
    } for i in range(N)]

    hourly_out = [{
        "hour": t,
        "baseline_load_kw": float(baseline_agg[t]),
        "dsm_load_kw": float(dsm_agg[t]),
        "rtp_rs_per_kwh": float(price[t]),
        "effective_rtp_rs_per_kwh": float(effective_price[t]),
        "renewable_index": float(renewable_index[t]),
        "high_price_hour": int(peak_hour[t]),
        "incentive_rs_per_kwh": float(incentive_rate[t]),
        "shifted_load_kw": float(shift_agg[t]),
        "mismanaged_load_kw": float(mis_agg[t]),
    } for t in range(T)]

    return {
        "status": status,
        "objective_value": float(fval),
        "base_cost_rs": base_cost,
        "dsm_cost_rs": dsm_cost,
        "energy_saving_rs": base_cost - dsm_cost,
        "peak_base_kw": float(baseline_agg.max()),
        "peak_dsm_kw": float(dsm_agg.max()),
        "total_incentive_rs": float(cust_inc.sum()),
        "average_satisfaction": float(satis.mean()),
        "customers": customers_out,
        "hourly": hourly_out,
    }
