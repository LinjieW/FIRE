"""E5 housing module — mortgage math + event compilation (adapter layer only).

The engine core is untouched: housing cash flows compile into the existing
life-events channel ((age, amount_real) tuples, + = outflow, − = inflow,
today's dollars, CPI-indexed by the engine — same precedent as children /
income streams). `replace_annual` refunds the housing budget already inside
expenses_y0 as a yearly inflow so housing costs are not double-counted;
the FIRE threshold and GK budget stay on the user's full expenses_y0
(disclosed in the limitations panel).

MORTGAGE REALITY CHECK: a fixed-rate mortgage payment is NOMINALLY constant,
so its real burden falls with the realized CPI path after purchase. The
interactive adapter therefore compiles the mortgage schedule separately and
lets the v9.8 lifecycle resolve each path's real payment from the purchase-CPI
anchor. The deterministic rent-vs-buy chart intentionally keeps its configured
mean-inflation convention and labels that result as deterministic.

All functions are deterministic and unit-tested; no RNG, no engine imports.
"""

HOUSING_DEFAULTS = {
    "enabled": False,
    "mode": "buy",                # "rent" | "buy"
    "replace_annual": 0,          # housing budget already in expenses_y0 ($/yr today)
    # rent leg (also pre-purchase years in buy mode)
    "monthly_rent": 2_000,
    "rent_growth_real": 0.005,    # real (above-CPI) rent growth
    # buy leg
    "purchase_age": 35,
    "price": 500_000,             # today's $; grows at appreciation_real
    "down_pct": 0.20,
    "closing_pct": 0.02,
    "rate": 0.065,                # nominal fixed mortgage rate
    "term_years": 30,
    "tax_pct": 0.011,             # property tax, % of current real home value
    "maint_pct": 0.010,
    "insurance_annual": 1_500,    # today's $, CPI-indexed (constant real)
    "appreciation_real": 0.010,   # real home-price growth
    "refi_enabled": False,
    "refi_age": 45,
    "refi_rate": 0.050,
}


def _annual_payment(principal: float, rate: float, years: int) -> float:
    """Yearly total of the standard monthly-amortized payment."""
    n = max(int(years), 1) * 12
    rm = rate / 12.0
    if rm <= 1e-9:
        return principal / n * 12.0
    m = principal * rm / (1.0 - (1.0 + rm) ** (-n))
    return m * 12.0


def mortgage_schedule(principal: float, rate: float, term_years: int,
                      refi_year: int = None, refi_rate: float = None) -> list:
    """Year-by-year amortization rows:
    {year (1-based), payment, interest, principal_paid, balance_end}.
    An optional refinance at the END of refi_year re-amortizes the balance
    at refi_rate over the REMAINING term (rate change only)."""
    rows = []
    bal = float(principal)
    r = float(rate)
    pay = _annual_payment(bal, r, term_years)
    year = 0
    remaining = int(term_years)
    while remaining > 0 and bal > 0.005:
        year += 1
        # month-level pass for exactness, aggregated to the year
        rm, mpay = r / 12.0, pay / 12.0
        interest = principal_paid = 0.0
        for _ in range(12):
            i = bal * rm
            p = min(mpay - i, bal)
            interest += i
            principal_paid += p
            bal -= p
            if bal <= 0.005:
                break
        rows.append({"year": year, "payment": interest + principal_paid,
                     "interest": interest, "principal_paid": principal_paid,
                     "balance_end": max(bal, 0.0)})
        remaining -= 1
        if (refi_year is not None and year == int(refi_year)
                and refi_rate is not None and bal > 0.005 and remaining > 0):
            r = float(refi_rate)
            pay = _annual_payment(bal, r, remaining)
    return rows


def _h(cfg: dict) -> dict:
    out = dict(HOUSING_DEFAULTS)
    out.update({k: v for k, v in (cfg.get("housing") or {}).items()
                if v is not None})
    return out


def _purchase_context(cfg: dict):
    """Return the shared purchase inputs used by static and MC compilers."""
    h = _h(cfg)
    if not h.get("enabled"):
        return h, None, None, ()
    st = cfg.get("state") or {}
    start_age = int(st.get("start_age", 30))
    requested_p_age = int(h["purchase_age"])
    p_age = max(requested_p_age, start_age + 1)
    t_buy = p_age - start_age
    price_at_buy = float(h["price"]) * (1 + float(h["appreciation_real"])) ** max(t_buy, 0)
    loan = price_at_buy * (1 - float(h["down_pct"]))
    refi_yr = (int(h["refi_age"]) - p_age) if h.get("refi_enabled") else None
    sched = (mortgage_schedule(
        loan, float(h["rate"]), int(h["term_years"]),
        refi_year=refi_yr, refi_rate=float(h["refi_rate"])
    ) if str(h["mode"]) == "buy" and loan > 0.0 else [])
    return h, p_age, price_at_buy, tuple(sched)


def _carrying_cost_real(h: dict, age: int, start_age: int,
                        downsize: dict = None) -> float:
    """Real tax/maintenance/insurance cost for one post-purchase year.

    Roadmap 6.0 Phase 4 added `downsize`. Before that this function read one
    price out of one housing block and could not express two houses, which was
    measured before the phase was written rather than discovered during it: a
    plan that moves to a smaller place keeps paying the big house's tax and
    maintenance forever, which is a silent overstatement of cost for exactly
    the users most likely to downsize.

    `downsize` is None for every plan that has not asked, and then this
    computes what it always computed.
    """
    years_from_start = int(age) - int(start_age)
    hv = float(h["price"]) * (1 + float(h["appreciation_real"])) ** years_from_start
    if downsize and int(age) >= int(downsize["age"]):
        # After the move the carrying base is the NEW home, appreciating from
        # the move rather than from the plan's start: it was bought then.
        years_since_move = int(age) - int(downsize["age"])
        hv = (float(downsize["price"])
              * (1 + float(h["appreciation_real"])) ** years_since_move)
    return (hv * (float(h["tax_pct"]) + float(h["maint_pct"]))
            + float(h["insurance_annual"]))


def downsize_spec(cfg: dict):
    """The move, or None. Read from `other_assets`, never a second input box.

    Phase 3 established the rule the hard way: a home value typed in two
    places can disagree, and this repository has paid for one-fact-in-two-lists
    three times in a day. So the age comes from the sale the user already
    configured, and the new home's price is the one new number the move needs.
    """
    oa = cfg.get("other_assets") or {}
    if not isinstance(oa, dict) or not oa.get("downsize_enabled"):
        return None
    price = float(oa.get("downsize_new_price_real") or 0.0)
    if price <= 0:
        return None
    return {"age": int(oa.get("sell_home_age", 65)), "price": price}


def compile_housing_mortgage(cfg: dict):
    """Compile only the internal mortgage schedule for realized-CPI MC.

    The returned mapping is an adapter payload; ``server.engine_adapter`` turns it
    into a frozen/picklable engine spec. It is deliberately not part of the
    user's JSON/config or any public endpoint response. The carrying rows travel
    with the mortgage so the engine can merge the two housing-owned positive
    costs before generic event funding/shortfall handling.
    """
    h, p_age, _price_at_buy, sched = _purchase_context(cfg)
    if not h.get("enabled") or str(h["mode"]) != "buy" or not sched:
        return None
    st = cfg.get("state") or {}
    start_age = int(st.get("start_age", 30))
    horizon = int(st.get("accum_years", 25)) + int(st.get("retire_horizon", 40))
    _down = downsize_spec(cfg)
    carrying = tuple(
        (age, _carrying_cost_real(h, age, start_age, _down))
        for age in range(max(start_age + 1, int(p_age) + 1),
                         start_age + horizon + 1)
    )
    return {
        "purchase_age": int(p_age),
        "payments": tuple(float(row["payment"]) for row in sched),
        "carrying_by_age": carrying,
    }


def compile_housing_events(cfg: dict, *, include_mortgage: bool = True,
                           include_carry: bool = True) -> list:
    """[(age, amount_real)] in today's $; + = outflow, − = inflow.
    Purchase year carries down payment + closing only; carrying costs
    (tax/maintenance/insurance/mortgage) start the following year. The engine
    adapter may suppress both positive post-purchase components when an
    internal mortgage plan will merge them before funding; refunds, rent,
    down-payment and user events remain separate."""
    h, p_age, price_at_buy, sched = _purchase_context(cfg)
    if not h.get("enabled"):
        return []
    st = cfg.get("state") or {}
    start_age = int(st.get("start_age", 30))
    horizon = int(st.get("accum_years", 25)) + int(st.get("retire_horizon", 40))
    raw_pi = (cfg.get("returns") or {}).get("inflation_mu", 0.03)
    pi = 0.03 if raw_pi is None else float(raw_pi)

    buy = str(h["mode"]) == "buy"

    ev = []
    repl = float(h.get("replace_annual") or 0)
    rent0 = float(h["monthly_rent"]) * 12.0
    g = float(h["rent_growth_real"])
    for t in range(1, horizon + 1):
        age = start_age + t
        if repl > 0:
            ev.append((age, -repl))                      # refund expenses leg
        if (not buy) or age < p_age:
            ev.append((age, rent0 * (1 + g) ** t))       # rent (real-growing)
        elif age == p_age:
            ev.append((age, price_at_buy * (float(h["down_pct"])
                                            + float(h["closing_pct"]))))
        else:
            carry = _carrying_cost_real(h, age, start_age,
                                        downsize_spec(cfg))
            k = age - p_age                              # years since purchase
            mortgage = 0.0
            if include_mortgage and k <= len(sched):
                # Deterministic chart convention: nominal-fixed payment
                # deflated by the configured mean inflation. The MC adapter
                # keeps this schedule separate and uses realized CPI.
                mortgage = sched[k - 1]["payment"] / (1 + pi) ** k
            if include_carry:
                ev.append((age, carry + mortgage))
            elif include_mortgage and mortgage:
                ev.append((age, mortgage))
    return ev


def rent_vs_buy_deterministic(cfg: dict, r_real: float = 0.045) -> dict:
    """Classic deterministic rent-vs-buy: both live in the same home; the
    yearly cash-flow difference is invested at r_real. Returns per-year
    buy_net (home equity − invested-difference disadvantage) vs rent_net,
    and their gap. This is the DIRECTIONAL comparison; the MC comparison
    (/api/rentbuy) is the probabilistic one."""
    h = _h(cfg)
    st = cfg.get("state") or {}
    start_age = int(st.get("start_age", 30))
    horizon = int(st.get("accum_years", 25)) + int(st.get("retire_horizon", 40))
    raw_pi = (cfg.get("returns") or {}).get("inflation_mu", 0.03)
    pi = 0.03 if raw_pi is None else float(raw_pi)

    requested_p_age = int(h["purchase_age"])
    p_age = max(requested_p_age, start_age + 1)
    t_buy = max(p_age - start_age, 0)
    appr = float(h["appreciation_real"])
    price_at_buy = float(h["price"]) * (1 + appr) ** t_buy
    loan = price_at_buy * (1 - float(h["down_pct"]))
    refi_yr = (int(h["refi_age"]) - p_age) if h.get("refi_enabled") else None
    sched = mortgage_schedule(loan, float(h["rate"]), int(h["term_years"]),
                              refi_year=refi_yr, refi_rate=float(h["refi_rate"]))
    rent0 = float(h["monthly_rent"]) * 12.0
    g = float(h["rent_growth_real"])

    ages, gap, buy_net_s, rent_net_s = [], [], [], []
    fund = 0.0            # renter's invested difference (real)
    for t in range(1, horizon + 1):
        age = start_age + t
        rent_cost = rent0 * (1 + g) ** t
        if age < p_age:
            buy_cost = rent_cost                          # both rent pre-buy
        elif age == p_age:
            buy_cost = price_at_buy * (float(h["down_pct"]) + float(h["closing_pct"]))
        else:
            hv = float(h["price"]) * (1 + appr) ** t
            buy_cost = hv * (float(h["tax_pct"]) + float(h["maint_pct"])) \
                + float(h["insurance_annual"])
            k = age - p_age
            if k <= len(sched):
                buy_cost += sched[k - 1]["payment"] / (1 + pi) ** k
        fund = fund * (1 + r_real) + (buy_cost - rent_cost)
        k = age - p_age
        bal_real = (sched[k - 1]["balance_end"] / (1 + pi) ** k
                    if 1 <= k <= len(sched) else (loan if k < 1 else 0.0))
        equity = (float(h["price"]) * (1 + appr) ** t - bal_real) if age >= p_age else 0.0
        ages.append(age)
        buy_net_s.append(equity)
        rent_net_s.append(fund)
        gap.append(equity - fund)
    return {"ages": ages, "buy_equity": buy_net_s, "rent_fund": rent_net_s,
            "buy_minus_rent": gap, "schedule": sched,
            "assumed_r_real": r_real, "inflation_mu": pi}
