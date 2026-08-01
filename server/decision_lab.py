"""Synchronous decision-lab computations extracted without behavior changes.

This module owns no HTTP, jobs, persistence, recovery, migration, or lifecycle
state.  It is an internal seam; ``server.app`` re-exports its compatibility
surface for existing callers and routes.
"""
from __future__ import annotations

import copy

import engine_v98 as ENG

SWEEP_CAP = 8000                    # per-point cap for sweeps


SENS_CAP = 5000                     # per-run cap for sensitivity


def _get_path(d: dict, path: str):
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _set_path(d: dict, path: str, v) -> dict:
    ks = path.split(".")
    cur = d
    for k in ks[:-1]:
        if not isinstance(cur.get(k), dict):
            cur[k] = {}
        cur = cur[k]
    cur[ks[-1]] = v
    return d


def _base_cfg(cfg: dict) -> dict:
    """A deep copy with relocation forced OFF — the clean home-only scenario
    used for sweeps and sensitivity so a single parameter is isolated."""
    c = copy.deepcopy(cfg)
    c.setdefault("relocation", {})["enabled"] = False
    return c


def _scale_portfolio(c: dict, f: float) -> dict:
    for k in ("pretax_401k", "roth_ira", "hsa", "taxable"):
        v = _get_path(c, "initial." + k)
        if v:
            _set_path(c, "initial." + k, v * f)
    return c


def _select_roth_best(points: list) -> dict:
    """Prefer solvency, then unconditional after-tax terminal wealth."""
    if not points:
        raise ValueError("Roth optimization produced no candidates")
    return max(points, key=lambda x: (
        x["lifetime_success"],
        x.get("terminal_after_tax_real_p50") or 0.0))


def run_sweep(cfg: dict, param: str, values: list, paths: int, seed: int) -> dict:
    """One-dimensional sweep: for each value of `param`, re-run the home-only
    scenario and report the headline metrics. Used for the SWR and Social
    Security claim-age sections."""
    n = max(500, min(int(paths), SWEEP_CAP))
    pts = []
    for v in values:
        c = _set_path(_base_cfg(cfg), param, v)
        s = ENG.summary(c, n, seed, relocation_on=False)
        s["value"] = v
        pts.append(s)
    return {"param": param, "n_paths": n, "seed": seed, "points": pts}


GOALSEEK_METRICS = {"lifetime_success": ">=", "fire_age_p50": "<=",
                    "terminal_real_p50": ">=", "cons_p50": ">="}


def _set_equity(c, v):
    v = max(0.0, min(float(v), 1.0))
    _set_path(c, "glide.equity_start", v)
    return _set_path(c, "glide.equity_end", v)


GOALSEEK_LEVERS = {
    "expenses": ("state.expenses_y0",
                 lambda c, v: _set_path(c, "state.expenses_y0", float(v))),
    "salary": ("contributions.base_salary_pre",
               lambda c, v: _set_path(c, "contributions.base_salary_pre", float(v))),
    "swr": ("state.swr_pref",
            lambda c, v: _set_path(c, "state.swr_pref", float(v))),
    "equity": ("glide.equity_start", _set_equity),
}


class _GsCancelled(Exception):
    pass


def _gs_validate(goal: dict, levers: list):
    """Shared request validation — raises ValueError on bad input. Called
    synchronously by the route (so bad requests 400) AND by run_goalseek."""
    metric = str(goal.get("metric"))
    if metric not in GOALSEEK_METRICS:
        raise ValueError(f"unknown goal metric: {metric!r}")
    target = float(goal.get("value"))
    if len(levers) != 2:
        raise ValueError("exactly two levers required")
    keys, los, his = [], [], []
    for lv in levers:
        k = str(lv.get("key"))
        if k not in GOALSEEK_LEVERS:
            raise ValueError(f"unknown lever: {k!r}")
        keys.append(k)
        los.append(float(lv["min"]))
        his.append(float(lv["max"]))
    if keys[0] == keys[1]:
        raise ValueError("levers must differ")
    return metric, target, keys, los, his


def run_goalseek(cfg: dict, goal: dict, levers: list, paths: int, seed: int,
                 grid: int = 8, cb=None, cancelled=None) -> dict:
    """Feasible-set search. Returns the coarse grid (z + feasibility), the
    boundary-refinement points, the caller's current position, and the
    nearest feasible point in normalized lever space."""
    metric, target, keys, los, his = _gs_validate(goal, levers)
    op = GOALSEEK_METRICS[metric]
    ok = ((lambda v: v is not None and v >= target) if op == ">="
          else (lambda v: v is not None and v <= target))
    grid = max(4, min(int(grid), 10))
    n = max(500, min(int(paths), 2000))

    xs = [los[0] + (his[0] - los[0]) * i / (grid - 1) for i in range(grid)]
    ys = [los[1] + (his[1] - los[1]) * j / (grid - 1) for j in range(grid)]
    set1, set2 = GOALSEEK_LEVERS[keys[0]][1], GOALSEEK_LEVERS[keys[1]][1]

    evals = [0]
    total_est = grid * grid + 2 * grid + 1

    def ev(x, y):
        if cancelled and cancelled():
            raise _GsCancelled()
        c = _base_cfg(cfg)
        set1(c, x)
        set2(c, y)
        v = ENG.summary(c, n, seed, relocation_on=False)[metric]
        evals[0] += 1
        if cb:
            cb(min(evals[0] / total_est, 0.99), f"{evals[0]}/{total_est}")
        return v

    z = [[ev(x, y) for x in xs] for y in ys]
    feas = [[bool(ok(v)) for v in row] for row in z]

    # boundary refinement: midpoints wherever feasibility flips between
    # neighbors, capped at 2×grid extra evaluations
    cand = []
    for j in range(grid):
        for i in range(grid - 1):
            if feas[j][i] != feas[j][i + 1]:
                cand.append(((xs[i] + xs[i + 1]) / 2, ys[j]))
    for i in range(grid):
        for j in range(grid - 1):
            if feas[j][i] != feas[j + 1][i]:
                cand.append((xs[i], (ys[j] + ys[j + 1]) / 2))
    refined = []
    for (x, y) in cand[:2 * grid]:
        v = ev(x, y)
        refined.append({"x": x, "y": y, "v": v, "ok": bool(ok(v))})

    # current position + nearest feasible point (normalized lever distance)
    cx = _get_path(cfg, GOALSEEK_LEVERS[keys[0]][0])
    cy = _get_path(cfg, GOALSEEK_LEVERS[keys[1]][0])
    cur_v = ev(cx, cy) if (cx is not None and cy is not None) else None
    pts = [{"x": xs[i], "y": ys[j], "v": z[j][i], "ok": feas[j][i]}
           for j in range(grid) for i in range(grid)] + refined
    span1, span2 = max(his[0] - los[0], 1e-9), max(his[1] - los[1], 1e-9)
    feas_pts = [p for p in pts if p["ok"]]
    nearest = None
    if feas_pts and cx is not None and cy is not None:
        nearest = min(feas_pts, key=lambda p: ((p["x"] - cx) / span1) ** 2
                                              + ((p["y"] - cy) / span2) ** 2)
    return {"goal": {"metric": metric, "op": op, "value": target},
            "levers": [{"key": keys[0], "values": xs},
                       {"key": keys[1], "values": ys}],
            "z": z, "feasible": feas, "refined": refined,
            "current": ({"x": cx, "y": cy, "v": cur_v,
                         "ok": bool(ok(cur_v)) if cur_v is not None else None}
                        if cx is not None and cy is not None else None),
            "nearest": nearest,
            "n_paths": n, "seed": seed, "evals": evals[0]}


def _dominates(a: dict, b: dict) -> bool:
    """a dominates b: >= on cons & success, <= on fire age, > somewhere.
    Points that never reach FI (fire_age None) are dominated by definition."""
    if b["fire_age_p50"] is None:
        return a["fire_age_p50"] is not None
    if a["fire_age_p50"] is None:
        return False
    ge = (a["cons_p50"] >= b["cons_p50"]
          and a["lifetime_success"] >= b["lifetime_success"]
          and a["fire_age_p50"] <= b["fire_age_p50"])
    gt = (a["cons_p50"] > b["cons_p50"]
          or a["lifetime_success"] > b["lifetime_success"]
          or a["fire_age_p50"] < b["fire_age_p50"])
    return ge and gt


def run_frontier(cfg: dict, paths: int, seed: int, grid: int = 7,
                 ranges: dict = None, cb=None, cancelled=None) -> dict:
    n = max(500, min(int(paths), 2000))
    grid = max(4, min(int(grid), 10))
    rg = ranges or {}
    e_cur = float(_get_path(cfg, "state.expenses_y0") or 45_000)
    s_cur = float(_get_path(cfg, "state.swr_pref") or 0.0333)
    e_lo = float(rg.get("expenses_min", e_cur * 0.6))
    e_hi = float(rg.get("expenses_max", e_cur * 1.3))
    s_lo = float(rg.get("swr_min", 0.028))
    s_hi = float(rg.get("swr_max", 0.055))

    exps = [e_lo + (e_hi - e_lo) * i / (grid - 1) for i in range(grid)]
    swrs = [s_lo + (s_hi - s_lo) * j / (grid - 1) for j in range(grid)]
    evals = [0]
    total = grid * grid + 1

    def ev(e, w):
        if cancelled and cancelled():
            raise _GsCancelled()
        c = _base_cfg(cfg)
        _set_path(c, "state.expenses_y0", e)
        _set_path(c, "state.swr_pref", w)
        s = ENG.summary(c, n, seed, relocation_on=False)
        evals[0] += 1
        if cb:
            cb(min(evals[0] / total, 0.99), f"{evals[0]}/{total}")
        return {"expenses": e, "swr": w,
                "cons_p50": s["cons_p50"], "fire_age_p50": s["fire_age_p50"],
                "lifetime_success": s["lifetime_success"],
                "terminal_real_p50": s["terminal_real_p50"]}

    pts = [ev(e, w) for w in swrs for e in exps]
    cur = ev(e_cur, s_cur)

    for p in pts:
        p["frontier"] = not any(_dominates(q, p) for q in pts if q is not p)
    cur["dominated_by"] = sum(1 for q in pts if _dominates(q, cur))

    # nearest frontier point in the normalized outcome space
    front = [p for p in pts if p["frontier"]]
    nearest = None
    if front and cur["fire_age_p50"] is not None:
        cs = [p["cons_p50"] for p in pts if p["cons_p50"] is not None]
        fa = [p["fire_age_p50"] for p in pts if p["fire_age_p50"] is not None]
        sc = max(cs) - min(cs) or 1.0
        sa = max(fa) - min(fa) or 1.0

        def dist(p):
            return (((p["cons_p50"] - cur["cons_p50"]) / sc) ** 2
                    + ((p["fire_age_p50"] - cur["fire_age_p50"]) / sa) ** 2
                    + (p["lifetime_success"] - cur["lifetime_success"]) ** 2)
        nearest = min(front, key=dist)
    return {"points": pts, "current": cur, "nearest_frontier": nearest,
            "expenses": exps, "swrs": swrs,
            "n_paths": n, "seed": seed, "evals": evals[0]}


def run_sensitivity(cfg: dict, paths: int, seed: int) -> dict:
    """Tornado of terminal-real-P50 swings for ±perturbations of each key
    assumption (common random numbers: same seed everywhere), plus a return-μ
    uncertainty band (the whole regime mixture shifted ±1.5pp). One parameter is
    perturbed at a time on the home-only scenario."""
    n = max(500, min(int(paths), SENS_CAP))
    base = ENG.summary(_base_cfg(cfg), n, seed, relocation_on=False)
    center = base["terminal_real_p50"]

    def term(mut) -> float:
        c = _base_cfg(cfg)
        mut(c)
        return ENG.summary(c, n, seed, relocation_on=False)["terminal_real_p50"]

    def term_mu(shift) -> float:
        return ENG.summary(_base_cfg(cfg), n, seed, relocation_on=False,
                           mu_shift=shift)["terminal_real_p50"]

    def bump(path, d):
        return lambda c: _set_path(c, path, (_get_path(c, path) or 0) + d)

    def mul(path, f):
        return lambda c: _set_path(c, path, (_get_path(c, path) or 0) * f)

    # Rows carry a stable `key`; the frontend maps keys to localized labels
    # (audit P1-2 — no display language baked into the API).
    rows = [{"key": "mu", "lo": term_mu(-0.015), "hi": term_mu(0.015)}]
    specs = [
        ("expenses", mul("state.expenses_y0", 0.90),
                     mul("state.expenses_y0", 1.10)),
        ("swr", bump("state.swr_pref", -0.005),
                bump("state.swr_pref", 0.005)),
        ("salary", mul("contributions.base_salary_pre", 0.90),
                   mul("contributions.base_salary_pre", 1.10)),
        ("portfolio", lambda c: _scale_portfolio(c, 0.90),
                      lambda c: _scale_portfolio(c, 1.10)),
        ("inflation", bump("returns.inflation_mu", -0.005),
                      bump("returns.inflation_mu", 0.005)),
        ("salary_growth", bump("contributions.salary_growth_pre", -0.01),
                          bump("contributions.salary_growth_pre", 0.01)),
    ]
    for key, lo_fn, hi_fn in specs:
        rows.append({"key": key, "lo": term(lo_fn), "hi": term(hi_fn)})

    base_shift = float((_base_cfg(cfg).get("returns") or {}).get("equity_mu_shift", 0.0) or 0.0)
    band = []
    for shift in (-0.015, -0.0075, 0.0, 0.0075, 0.015):
        s = ENG.summary(_base_cfg(cfg), n, seed, relocation_on=False, mu_shift=shift)
        band.append({"mu": ENG.BASE_MU + base_shift + shift,
                     "terminal_real_p50": s["terminal_real_p50"],
                     "lifetime_success": s["lifetime_success"]})

    return {"n_paths": n, "seed": seed, "center": center,
            "base": base, "rows": rows, "mu_band": band}


def run_backtest(cfg: dict, retire_age, seed: int) -> dict:
    """Deterministic sequence-of-returns stress test via the v9.8 retirement
    engine (see engine_v98.backtest). Stylized adverse openings, NOT literal
    index data."""
    return ENG.backtest(cfg, retire_age, seed)


__all__ = [
    "SWEEP_CAP", "SENS_CAP", "_get_path", "_set_path", "_base_cfg",
    "_scale_portfolio", "_select_roth_best", "GOALSEEK_METRICS",
    "GOALSEEK_LEVERS", "_set_equity", "_GsCancelled", "_gs_validate",
    "run_sweep", "run_goalseek", "_dominates", "run_frontier",
    "run_sensitivity", "run_backtest",
]
