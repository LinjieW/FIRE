"""FIRE Modeling — engine & adapter regression suite (stdlib unittest, no deps).

Freezes every guarantee this app makes into executable form:
  * de-identification (served defaults never equal the real calibration baseline)
  * OFF-state golden values (all opt-in modules off => exact reproducibility)
  * per-module directionality (children/fees/layoff/pension/household/sale)
  * delivered-cash accounting, including the bounded legacy <=$1 tolerance
  * chunked-parallel protocol determinism (worker count must not change results)
  * result-schema compatibility with the standalone report builder
  * every served preset actually runs

Run:      python3 tests/test_regression.py            (~1–2 min)
Builds:   build-app.sh runs this first and aborts on failure (SKIP_TESTS=1 to override).

GOLDEN values are intentional pins. If you change engine math ON PURPOSE,
re-derive them (they are printed by tests on failure) and update the constants —
never delete the pins.
"""
import copy
import math
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, "server"))

import engine_adapter as ENG                      # noqa: E402
from fire_v95_actual_baseline import INITIAL_STACK_ACTUAL  # noqa: E402

SEED = 96_000
# Re-derived 2026-08-01 after the approved 2026 rule-pack refresh changed the
# 401(k) first-year default from $23,500 to $24,500.  The engine/RNG call graph
# is unchanged; the terminal deltas are model-vintage effects. Both values are
# cross-arch pins.
# Re-recorded once by `FIRE4-P3-DIVIDEND-DRAG/r1` (2026-08-14), deliberately
# and with the user's authorisation: the taxable drag is now derived from a
# yield and a rate (1.7% x 14.7% = 0.002499) instead of the hardcoded 0.0025,
# so every run that touches a taxable bucket moves by about +0.002%. Previous
# values kept so the change stays legible rather than merely overwritten:
#   GOLDEN_TERMINAL        2_607_287.5967222806   (delta +50.73)
#   GOLDEN_LEGACY_TERMINAL 2_557_596.4512437508   (delta +26.35)
GOLDEN_TERMINAL = 2_607_338.325309041          # 2026 pack · 800 paths · seed 96000
GOLDEN_FIRE_P50 = 38.0
GOLDEN_LEGACY_TERMINAL = 2_557_622.7997192703  # 2026 pack · annual_spending_now=40440


def cfg0():
    return ENG.default_config()


def summ(c, n=600):
    return ENG.summary(c, n, SEED, False)


class TestI18nLint(unittest.TestCase):
    """Every CJK string that reaches the UI must travel through an i18n
    channel: tt(zh,en), a data-i18n tag, an explicit L === "zh" branch, or
    a bilingual ["zh","en"] pair. Added after the goal-seek dropdowns
    shipped Chinese-only into the EN build (2026-07-12) — this catches
    hardcoded strings; the setLang re-render contract (app.js setLang
    comment) covers built-content staleness."""

    CJK = __import__("re").compile(r"[一-鿿]")
    ALLOW = [__import__("re").compile(p) for p in (
        r"\btt\(",                                   # tt(zh, en)
        r"data-i18n",                                # tag-refresh channel
        r'L\s*===?\s*"zh"',                          # explicit branch
        r'"[^"]*[一-鿿][^"]*"\s*,\s*"',      # ["zh","en"] same line
        r'zh:\s*"',                                  # {zh:..., en:...} defs
        r'^\s*"[^"]*",?\s*$',                        # continuation of a pair
        r'^\s*"[a-z0-9_.]+":\s*\[',                  # dict key opening a pair
        r'^\s*\["[^"]*[一-鿿]',              # array pair opening line
        r'data-lang="zh"',                           # the language button itself
    )]

    def _linted_files(self):
        """Every web file that can carry user-visible copy, found by scanning.

        This used to be a hardcoded three-file tuple, which meant a *new* web
        file with user-visible strings escaped the lint silently — and the i18n
        channel is one of the frozen interfaces in `WORKSTREAMS.md` §5, so a hole
        here is a hole in a mandatory merge gate. `js_syntax_check.py` already
        globs; this now matches it.

        Stylesheets are deliberately out of scope. User-visible copy in CSS would
        have to come through a `content:` property, which is a different lint,
        and pulling `.css` in would flag ordinary Chinese design comments.
        """
        from pathlib import Path
        web = Path(ROOT) / "web"
        files = sorted(str(p.relative_to(ROOT)) for p in web.rglob("*.js"))
        index = web / "index.html"
        if index.exists():
            files.append(str(index.relative_to(ROOT)))
        return files

    def test_no_hardcoded_cjk_outside_i18n_channels(self):
        import re
        offenders = []
        linted = self._linted_files()
        # A scan that found nothing to scan would pass vacuously.
        self.assertIn("web/app.js", linted)
        for fn in linted:
            with open(os.path.join(ROOT, fn), encoding="utf-8") as fh:
                lines = fh.readlines()
            in_block = False
            for i, line in enumerate(lines):
                s = line.strip()
                if in_block:
                    if "*/" in s:
                        in_block = False
                    continue
                if s.startswith("/*") and "*/" not in s:
                    in_block = True
                    continue
                if not self.CJK.search(line):
                    continue
                # `/* … */` on one line is a comment too. Without this it fell
                # through to the offender list, so a single Chinese design note
                # anywhere in a scanned file would turn a mandatory merge gate
                # red for a reason that has nothing to do with i18n.
                if s.startswith("/*") and s.endswith("*/"):
                    continue
                if s.startswith(("//", "*", "<!--")):
                    continue
                # window: the marker may sit up to 3 lines above (multi-line
                # tt(...) calls, template-literal arguments)
                ctx = "".join(lines[max(0, i - 3):i + 1])
                if any(p.search(l) for p in self.ALLOW
                       for l in ctx.splitlines()):
                    continue
                offenders.append(f"{fn}:{i + 1}: {s[:80]}")
        self.assertFalse(offenders,
                         "hardcoded CJK outside i18n channels:\n"
                         + "\n".join(offenders))


class TestDeidentification(unittest.TestCase):
    def test_served_defaults_are_fuzzed(self):
        c = cfg0()
        for k in ("pretax_401k", "roth_ira", "hsa", "taxable"):
            self.assertNotEqual(c["initial"][k], getattr(INITIAL_STACK_ACTUAL, k),
                                f"served {k} equals the real baseline")

    def test_no_identity_strings_in_sources(self):
        bad = ("Linjie", "Fidelity", "Edgeworth", "Econic")
        for sub in ("engine", "server", "web"):
            d = os.path.join(ROOT, sub)
            for fn in os.listdir(d):
                if not fn.endswith((".py", ".js", ".html", ".css")):
                    continue
                text = pathlib.Path(d, fn).read_text(
                    encoding="utf-8", errors="ignore")
                for w in bad:
                    self.assertNotIn(w, text, f"{w} found in {sub}/{fn}")


class TestGoldenValues(unittest.TestCase):
    def test_default_config_reproduces_golden(self):
        s = ENG.summary(cfg0(), 800, SEED, False)
        self.assertAlmostEqual(s["terminal_real_p50"], GOLDEN_TERMINAL, places=3,
                               msg=f"got {s['terminal_real_p50']:.6f}")
        self.assertEqual(s["fire_age_p50"], GOLDEN_FIRE_P50)

    def test_legacy_spending_parity(self):
        c = cfg0()
        c["contributions"]["annual_spending_now"] = 40_440
        s = ENG.summary(c, 800, SEED, False)
        self.assertAlmostEqual(s["terminal_real_p50"], GOLDEN_LEGACY_TERMINAL,
                               places=3, msg=f"got {s['terminal_real_p50']:.6f}")

    def test_off_modules_do_not_consume_rng(self):
        """Toggling any opt-in module dict WITHOUT enabling it must not move
        a single float (the default-off = bit-identical contract)."""
        base = summ(cfg0())
        c = cfg0()
        c["layoff"]["p_annual"] = 0.99          # enabled stays False
        c["income_streams"]["pension_annual_real"] = 99_999
        c["household"]["spouse_base_salary_pre"] = 999_999
        c["relocation"]["fx_sigma"] = 0.99
        c["tax_true"]["rmd_age"] = 73
        c["housing"]["price"] = 9_999_999
        c["returns"].update({"persistence": 0.01, "block_years": 1,
                             "inflation_ar1": 0.9})
        c["children"] = []
        c["life_events"] = []
        self.assertEqual(summ(c), base)

    def test_disabled_relocation_fx_is_full_summary_identical(self):
        base = ENG.summary(cfg0(), 800, SEED, False)
        c = cfg0()
        c["relocation"].update({"enabled": False, "fx_sigma": 3.0,
                                "fx_drift": 0.8, "ppp_kappa": 0.5})
        self.assertEqual(ENG.summary(c, 800, SEED, False), base)


class TestDirectionality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = summ(cfg0())

    def test_child_costs_hurt(self):
        c = cfg0()
        c["children"] = [{"parent_age_at_birth": 33, "annual_cost_real": 15000,
                          "support_years": 22, "college_total_real": 200000}]
        s = summ(c)
        self.assertLess(s["terminal_real_p50"], self.base["terminal_real_p50"])
        self.assertGreaterEqual(s["fire_age_p50"], self.base["fire_age_p50"])

    def test_fees_drag(self):
        c = cfg0()
        c["returns"]["expense_ratio"] = 0.0110
        self.assertLess(summ(c)["terminal_real_p50"],
                        self.base["terminal_real_p50"])

    def test_home_sale_inflow_helps(self):
        c = cfg0()
        c["other_assets"].update({"home_equity": 400000, "sell_home_enabled": True,
                                  "sell_home_age": 62, "sell_home_net_real": 350000})
        self.assertGreater(summ(c)["terminal_real_p50"],
                           self.base["terminal_real_p50"])

    def test_pension_helps(self):
        c = cfg0()
        c["income_streams"].update({"pension_enabled": True,
                                    "pension_annual_real": 24000,
                                    "pension_start_age": 62})
        self.assertGreater(summ(c)["terminal_real_p50"],
                           self.base["terminal_real_p50"])

    def test_harsh_layoff_delays_fire(self):
        # NOTE: one shared rng stream across sequential paths => cross-config
        # deltas are statistical; assert on the strong signal (median FIRE age)
        # under a deliberately harsh setting.
        c = cfg0()
        c["layoff"].update({"enabled": True, "p_annual": 0.25,
                            "gap_months": 11, "p_cap": 0.9})
        s = summ(c, 1200)
        b = summ(cfg0(), 1200)
        self.assertGreater(s["fire_age_p50"], b["fire_age_p50"])

    def test_spouse_earner_accelerates_fire(self):
        c = cfg0()
        c["household"].update({"enabled": True, "spouse_age_offset": 0,
                               "spouse_base_salary_pre": 120000,
                               "spouse_pretax_401k_limit_y1": 23500,
                               "spouse_roth_ira_limit_y1": 7500})
        self.assertLessEqual(summ(c)["fire_age_p50"], self.base["fire_age_p50"])

    def test_mortality_sex_changes_table(self):
        m = ENG.build_kwargs(cfg0(), False)["mortality"]
        c = cfg0(); c["mortality"]["sex"] = "female"
        f = ENG.build_kwargs(c, False)["mortality"]
        self.assertNotEqual((m.alpha, m.beta), (f.alpha, f.beta))


class TestMortalityAndMandatoryEvents(unittest.TestCase):
    def _never_fi(self):
        c = cfg0()
        c["state"].update({"start_age": 30, "accum_years": 8,
                           "expenses_y0": 1_000_000_000})
        c["initial"] = {k: 0 for k in c["initial"]}
        c["other_assets"].update({"cash": 0, "other_liquid": 0})
        c["mortality"].update({"enabled": True, "cap_age": 31})
        return c

    def test_no_fi_path_samples_death_and_censors_milestones(self):
        c = self._never_fi()
        c["milestones"] = [50_000]
        raw = ENG._run(c, 1, SEED, False)[0]
        self.assertTrue(raw["died_during_accum"])
        self.assertEqual(raw["age_at_death"], 31)
        self.assertGreater(raw["accum_path"][2]["total"], 50_000,
                           "fixture must cross only after death")
        s = ENG.run_scenarios(c, 1, SEED)["home"]
        self.assertEqual(s["died_during_accum_rate"], 1.0)
        self.assertEqual(s["milestones"]["50000"]["reach_probability"], 0.0)

    def test_household_censors_only_at_last_survivor(self):
        c = self._never_fi()
        c["household"].update({"enabled": True, "spouse_age_offset": -10})
        household = ENG._run(c, 1, SEED, False)[0]
        self.assertFalse(household["died_during_accum"])
        self.assertTrue(household["censored_no_fire"])
        c["household"]["enabled"] = False
        self.assertTrue(ENG._run(c, 1, SEED, False)[0]["died_during_accum"])

    def test_household_death_stops_deceased_earner_future_contributions(self):
        import numpy as np
        import fire_v9_8_model as V98

        def run_case(start_age, spouse_age_offset, mortality_enabled=True):
            c = self._never_fi()
            c["state"].update({"start_age": start_age, "accum_years": 3,
                               "retire_horizon": 1, "inflation": 0.0})
            c["contributions"].update({
                "pretax_401k_limit_y1": 100.0,
                "roth_ira_limit_y1": 0.0,
                "hsa_limit_y1": 0.0,
                "irs_limit_growth": 0.0,
                "match_rate": 0.0,
                "annual_spending_now": 1_000_000_000.0,
            })
            c["promotion"]["enabled"] = False
            c["tax_us"]["drag_taxable"] = 0.0
            c["obbba"].update({"mode": "permanent",
                               "annual_savings_y0": 500.0})
            c["mortality"].update({
                "enabled": mortality_enabled,
                "cap_age": 110,
            })
            c["household"].update({
                "enabled": True,
                "spouse_age_offset": spouse_age_offset,
                "spouse_pretax_401k_limit_y1": 1_000.0,
                "spouse_roth_ira_limit_y1": 0.0,
                "spouse_hsa_limit_y1": 0.0,
                "spouse_match_rate": 0.0,
            })

            fixed_lifetime = (
                types.SimpleNamespace(name="fixed"),
                np.zeros(4),
                np.zeros(4),
            )
            with mock.patch.object(
                    V98, "sample_lifetime_v7",
                    side_effect=lambda *args, **kwargs: fixed_lifetime), \
                    mock.patch.object(
                        V98, "sample_bond_returns",
                        side_effect=lambda equity, *args: np.zeros(len(equity))), \
                    mock.patch.object(
                        V98, "annual_mortality_rate",
                        side_effect=lambda age, params: 1.0 if age >= 35 else 0.0):
                return ENG._run(c, 1, SEED, False)[0]

        cases = (
            # Household-on with mortality disabled stays on the old path.
            ("mortality_off", 30, 0, False,
             [0.0, 1_100.0, 2_198.9, 3_296.7011],
             [0.0, 500.0, 1_000.0, 1_500.0]),
            # Nobody dies: the new mortality/contribution seam must be inert.
            ("none", 30, 0, True,
             [0.0, 1_100.0, 2_198.9, 3_296.7011],
             [0.0, 500.0, 1_000.0, 1_500.0]),
            # Spouse dies at age 41 after the first contribution year.
            ("spouse", 30, 10, True,
             [0.0, 1_100.0, 1_198.9, 1_297.7011],
             [0.0, 500.0, 1_000.0, 1_500.0]),
            # Primary dies at age 35; spouse remains the only earner.
            ("primary", 34, -10, True,
             [0.0, 1_100.0, 2_098.9, 3_096.8011],
             [0.0, 500.0, 500.0, 500.0]),
        )
        for (label, start_age, offset, mortality_enabled,
             expected_pretax, expected_obbba) in cases:
            with self.subTest(deceased=label):
                result = run_case(
                    start_age, offset,
                    mortality_enabled=mortality_enabled,
                )
                self.assertFalse(result["died_during_accum"])
                self.assertTrue(result["censored_no_fire"])
                self.assertEqual(
                    [step["accounts"].pretax_401k
                     for step in result["accum_path"]],
                    expected_pretax,
                )
                self.assertEqual(
                    [step.get("obbba_boost_nominal", 0.0)
                     for step in result["accum_path"]],
                    expected_obbba,
                )

    def test_household_alive_states_charge_expenses_once(self):
        import fire_v8_model as V8

        c = cfg0()
        c["contributions"].update({
            "base_salary_pre": 100.0,
            "bonus_pre": 0.0,
            "ot_income_pre": 0.0,
            "salary_growth_pre": 0.0,
            "pretax_401k_limit_y1": 0.0,
            "roth_ira_limit_y1": 0.0,
            "hsa_limit_y1": 0.0,
            "match_rate": 0.0,
            "marginal_tax_pre": 0.0,
            "annual_spending_now": 40.0,
        })
        c["promotion"]["enabled"] = False
        c["household"].update({
            "enabled": True,
            "spouse_base_salary_pre": 80.0,
            "spouse_bonus_pre": 0.0,
            "spouse_salary_growth_pre": 0.0,
            "spouse_pretax_401k_limit_y1": 0.0,
            "spouse_roth_ira_limit_y1": 0.0,
            "spouse_hsa_limit_y1": 0.0,
            "spouse_match_rate": 0.0,
            "spouse_marginal_tax_pre": 0.0,
        })
        kw = ENG.build_kwargs(c, False)
        expected_taxable = {
            (True, True): 140.0,   # 100 + 80 - one $40 expense charge
            (True, False): 60.0,
            (False, True): 40.0,
            (False, False): 0.0,
        }
        with ENG._household_ctx(c):
            for alive, expected in expected_taxable.items():
                with self.subTest(alive=alive):
                    contributions = V8.compute_contributions_for_year(
                        1, None, 0.0,
                        kw["promo_params"].base_salary_post,
                        kw["contrib_params"], kw["promo_params"],
                        primary_alive=alive[0], spouse_alive=alive[1],
                        pool_household_expenses=True,
                    )
                    self.assertEqual(contributions.taxable, expected)
                    self.assertEqual(
                        contributions.total, expected,
                        "only taxable savings are enabled in this fixture",
                    )

            # Expenses must be charged against the pooled household residual.
            # The old primary-first floor returned $80 here: it exhausted only
            # the primary's $20, then let the spouse keep all $80.
            c["contributions"]["base_salary_pre"] = 20.0
            kw = ENG.build_kwargs(c, False)
            pooled = V8.compute_contributions_for_year(
                1, None, 0.0,
                kw["promo_params"].base_salary_post,
                kw["contrib_params"], kw["promo_params"],
                primary_alive=True, spouse_alive=True,
                pool_household_expenses=True,
            )
            self.assertEqual(pooled.taxable, 60.0)
            legacy = V8.compute_contributions_for_year(
                1, None, 0.0,
                kw["promo_params"].base_salary_post,
                kw["contrib_params"], kw["promo_params"],
                primary_alive=True, spouse_alive=True,
                pool_household_expenses=False,
            )
            self.assertEqual(
                legacy.taxable, 80.0,
                "mortality-off household callers retain their frozen behavior",
            )

        with self.assertRaisesRegex(ValueError, "accumulation horizon"):
            V8.project_stratified_v8(
                [0.0, 0.0], [0.0, 0.0], None, [0.0, 0.0],
                alive_by_year=((True, True),),
            )

    def test_household_mortality_preview_preserves_shared_rng_prefix(self):
        import numpy as np
        import fire_v8_model as V8
        import fire_v9_8_model as V98

        mortality = V98.MortalityParams(enabled=True, cap_age=110)
        cases = (
            # No death: two draws in each of three accumulation years.
            ("no_death", 30, 0, 3, 6, (True, True), None),
            # Both die at the end of year one; later years consume no draws.
            ("last_death", 34, 0, 1, 2, (False, False), 35),
            # Spouse dies in year one; a delayed stop adds one survivor draw/year.
            ("delayed_stop", 30, 10, 3, 4, (True, False), None),
        )
        with mock.patch.object(
                V98, "annual_mortality_rate",
                side_effect=lambda age, params: 1.0 if age >= 35 else 0.0):
            for (label, start_age, offset, stop_year, expected_draws,
                 expected_alive, expected_death) in cases:
                with self.subTest(path=label):
                    household = V8.HouseholdParams(
                        enabled=True, spouse_age_offset=offset,
                    )
                    rng = np.random.default_rng(SEED)
                    untouched = np.random.default_rng(SEED)
                    schedule = V98._sample_household_accum_mortality_schedule(
                        rng, 3, start_age, mortality, household,
                    )
                    # Previewing must not consume the official shared stream.
                    self.assertEqual(rng.random(), untouched.random())

                    rng = np.random.default_rng(SEED)
                    alive = V98._resume_household_accum_mortality(
                        rng, schedule, stop_year,
                    )
                    reference = np.random.default_rng(SEED)
                    reference.random(expected_draws)
                    np.testing.assert_array_equal(
                        rng.random(8), reference.random(8),
                        err_msg="the next shared-stream path must start identically",
                    )
                    self.assertEqual(alive, (
                        expected_alive[0], expected_alive[1], expected_death,
                    ))
                    self.assertEqual(
                        schedule.draw_counts_after_year[stop_year],
                        expected_draws,
                    )

        class CountingRng:
            def __init__(self):
                self.calls = 0

            def random(self):
                self.calls += 1
                return 1.0

        layoff_rng = CountingRng()
        old_layoff = V8._LAYOFF
        V8._LAYOFF = V8.LayoffParams(
            enabled=True, p_annual=0.5, rng=layoff_rng,
        )
        try:
            V8.project_stratified_v8(
                [0.0] * 3, [0.0] * 3, None, [0.0] * 3,
                alive_by_year=((True, True), (True, False), (True, False)),
            )
        finally:
            V8._LAYOFF = old_layoff
        self.assertEqual(layoff_rng.calls, 3)

    def test_last_survivor_death_wins_same_year_fire_crossing(self):
        import numpy as np
        import fire_v9_8_model as V98

        c = self._never_fi()
        c["state"].update({
            "start_age": 34,
            "accum_years": 2,
            "retire_horizon": 1,
            "expenses_y0": 1_000.0,
            "swr_pref": 0.05,
            "inflation": 0.0,
        })
        c["contributions"].update({
            "pretax_401k_limit_y1": 20_000.0,
            "roth_ira_limit_y1": 0.0,
            "hsa_limit_y1": 0.0,
            "irs_limit_growth": 0.0,
            "match_rate": 0.0,
            "annual_spending_now": 1_000.0,
        })
        c["promotion"]["enabled"] = False
        c["household"].update({
            "enabled": True,
            "spouse_age_offset": 0,
            "spouse_pretax_401k_limit_y1": 20_000.0,
            "spouse_roth_ira_limit_y1": 0.0,
            "spouse_hsa_limit_y1": 0.0,
            "spouse_match_rate": 0.0,
        })
        fixed_lifetime = (
            types.SimpleNamespace(name="fixed"),
            np.zeros(3),
            np.zeros(3),
        )
        with mock.patch.object(
                V98, "sample_lifetime_v7",
                side_effect=lambda *args, **kwargs: fixed_lifetime), \
                mock.patch.object(
                    V98, "sample_bond_returns",
                    side_effect=lambda equity, *args: np.zeros(len(equity))), \
                mock.patch.object(
                    V98, "annual_mortality_rate",
                    side_effect=lambda age, params: 1.0 if age >= 35 else 0.0):
            result = ENG._run(c, 1, SEED, False)[0]

        self.assertTrue(result["died_during_accum"])
        self.assertEqual(result["age_at_death"], 35)
        self.assertFalse(result["reached_fire"])
        self.assertIsNone(result["fire_age"])
        self.assertGreater(
            result["accum_path"][1]["total"],
            c["state"]["expenses_y0"] / c["state"]["swr_pref"],
            "fixture must cross FIRE in the same year both members die",
        )

    def test_survivor_keeps_deceased_higher_ss_record(self):
        import numpy as np
        import fire_v9_8_model as V98
        from fire_v6_model import AccountStack

        c = cfg0()
        c["household"].update({"enabled": True, "spouse_age_offset": 0,
                               "spouse_pia_monthly_y0": 1_000,
                               "spouse_claim_age": 67})
        c["social_security"].update({"enabled": True,
                                     "pia_monthly_y0": 3_000,
                                     "claim_age": 67})
        c["mortality"]["enabled"] = False
        kw = ENG.build_kwargs(c, False)
        with ENG._household_ctx(c):
            wd = V98.simulate_retirement_v98(
                starting_accounts=AccountStack(taxable=2_000_000),
                starting_age=66, fire_year_cpi_cumulative=1.0,
                equity_returns=[0.0], bond_returns=[0.0], inflations=[0.0],
                rule=kw["rule"], glide_path=kw["glide_path"],
                relocation=kw["relocation"], sh_property=kw["sh_property"],
                medical=kw["medical"], aca=kw["aca"], mortality=kw["mortality"],
                roth_ladder=kw["roth_ladder"], ss=kw["ss"], ftc=kw["ftc"],
                eldercare_events=[], inheritance_event=None, state=kw["state"],
                tax_us=kw["tax_us"], tax_cn=kw["tax_cn"],
                rng=np.random.default_rng(1), china_healthcare=kw["china_healthcare"],
                ss_nra=kw["ss_nra"], tax_true=kw["tax_true"],
                primary_alive_at_start=False, spouse_alive_at_start=True)
        self.assertEqual(wd["ss_total_received_real"], 36_000.0)

    def test_unaffordable_events_are_explicit_failures(self):
        c = cfg0()
        c["mortality"]["enabled"] = False
        c["life_events"] = [{"age": 31, "amount_real": 10_000_000,
                             "label": "mandatory"}]
        r = ENG._run(c, 1, SEED, False)[0]
        self.assertFalse(r["lifetime_success"])
        self.assertEqual(r["event_shortfalls"][0]["phase"], "accumulation")
        self.assertGreater(r["event_shortfalls"][0]["shortfall_real"], 0.0)
        s = ENG.run_scenarios(c, 1, SEED)["home"]
        self.assertEqual(s["reached_fi_rate"], 0.0)
        self.assertEqual(s["true_accumulation_failure_rate"], 1.0)
        self.assertEqual(s["event_shortfall_rate"], 1.0)

        # A post-FIRE event uses the full withdrawal order, but must still fail
        # if the entire stack cannot fund it.
        c = cfg0()
        c["mortality"]["enabled"] = False
        c["initial"] = {k: (10_000_000 if k == "taxable" else 0)
                        for k in c["initial"]}
        c["life_events"] = [{"age": 32, "amount_real": 1_000_000_000,
                             "label": "mandatory"}]
        r = ENG._run(c, 1, SEED, False)[0]
        self.assertEqual(r["fire_age"], 30)
        self.assertFalse(r["lifetime_success"])
        self.assertEqual(r["event_shortfalls"][0]["phase"], "retirement")
        self.assertEqual(r["withdrawal"]["shortfall_age"], 32)


class TestCompilers(unittest.TestCase):
    def test_assets_fold_into_taxable(self):
        c = cfg0()
        c["other_assets"]["cash"] = 30000
        c["other_assets"]["other_liquid"] = 20000
        init = ENG.build_kwargs(c, False)["initial"]
        self.assertEqual(init.taxable, cfg0()["initial"]["taxable"] + 50000)

    def test_home_equity_excluded_without_sale(self):
        c = cfg0()
        c["other_assets"]["home_equity"] = 500000
        kw = ENG.build_kwargs(c, False)
        self.assertEqual(kw["initial"].taxable, cfg0()["initial"]["taxable"])
        self.assertIsNone(kw["life_events"])

    def test_children_compile_to_events(self):
        c = cfg0()
        c["children"] = [{"parent_age_at_birth": 33, "annual_cost_real": 10000,
                          "support_years": 3, "college_total_real": 40000}]
        ev = ENG.build_kwargs(c, False)["life_events"]
        self.assertEqual(len(ev), 3 + 4)                       # support + 4 college yrs
        self.assertIn((33, 10000.0), ev)
        self.assertIn((51, 10000.0), ev)   # college year 1: age 33+18, 40000/4

    def test_pension_cola_vs_noncola(self):
        c = cfg0()
        c["income_streams"].update({"pension_enabled": True,
                                    "pension_annual_real": 10000,
                                    "pension_start_age": 65, "pension_cola": False})
        kw = ENG.build_kwargs(c, False)
        self.assertIsNone(kw["life_events"])
        stream = kw["income_streams"][0]
        self.assertEqual(stream.kind, "pension")
        self.assertEqual(stream.start_age, 65)
        self.assertFalse(stream.cola)
        self.assertIsNone(stream.nominal_anchor_cpi)
        c["income_streams"]["pension_cola"] = True
        stream2 = ENG.build_kwargs(c, False)["income_streams"][0]
        self.assertTrue(stream2.cola)
        self.assertEqual(stream2.annual_real, 10000.0)


class TestMemberIncomeStreams(unittest.TestCase):
    """Phase 1: owned income is annual cash, not an anonymous windfall."""

    @staticmethod
    def _fixed_lifetime(years):
        import numpy as np
        return (
            types.SimpleNamespace(name="fixed"),
            np.zeros(years),
            np.zeros(years),
        )

    def test_equity_compiles_to_exactly_n_future_payments(self):
        c = cfg0()
        c["income_streams"].update({
            "equity_enabled": True,
            "equity_annual_real": 12_345.0,
            "equity_years": 1,
            "equity_owner": "primary",
        })
        kw = ENG.build_kwargs(c, False)
        self.assertIsNone(
            kw["life_events"],
            "structured income must not be flattened into anonymous life events",
        )
        self.assertEqual(len(kw["income_streams"]), 1)
        stream = kw["income_streams"][0]
        self.assertEqual(stream.kind, "equity")
        self.assertEqual(stream.owner, "primary")
        self.assertEqual(stream.start_age, c["state"]["start_age"] + 1)
        self.assertEqual(stream.duration_years, 1)

    def test_parttime_never_accelerates_fire_before_retirement(self):
        import numpy as np
        import fire_v9_8_model as V98

        c = cfg0()
        c["state"].update({
            "start_age": 30,
            "accum_years": 3,
            "retire_horizon": 1,
            "expenses_y0": 1_000_000_000.0,
            "inflation": 0.0,
        })
        c["initial"] = {k: 0.0 for k in c["initial"]}
        c["contributions"].update({
            "base_salary_pre": 0.0,
            "bonus_pre": 0.0,
            "ot_income_pre": 0.0,
            "pretax_401k_limit_y1": 0.0,
            "roth_ira_limit_y1": 0.0,
            "hsa_limit_y1": 0.0,
            "match_rate": 0.0,
            "annual_spending_now": 1_000_000_000.0,
        })
        c["promotion"]["enabled"] = False
        c["mortality"]["enabled"] = False
        c["returns"].update({
            "friction_accum": 0.0,
            "expense_ratio": 0.0,
            "rebalance_cost": 0.0,
        })
        c["tax_us"]["drag_taxable"] = 0.0
        c["income_streams"].update({
            "parttime_enabled": True,
            "parttime_annual_real": 100.0,
            "parttime_start_age": 31,
            "parttime_years": 3,
            "parttime_owner": "primary",
        })
        fixed = self._fixed_lifetime(4)
        with mock.patch.object(
                V98, "sample_lifetime_v7",
                side_effect=lambda *args, **kwargs: fixed), \
                mock.patch.object(
                    V98, "sample_bond_returns",
                    side_effect=lambda equity, *args: np.zeros(len(equity))):
            result = ENG._run(c, 1, SEED, False)[0]
        self.assertEqual(
            [step["accounts"].taxable for step in result["accum_path"]],
            [0.0, 0.0, 0.0, 0.0],
        )

    def test_primary_income_stops_after_pre_fire_death(self):
        import numpy as np
        import fire_v9_8_model as V98

        def run(owner):
            c = cfg0()
            c["state"].update({
                "start_age": 30,
                "accum_years": 3,
                "retire_horizon": 1,
                "expenses_y0": 1_000_000_000.0,
                "inflation": 0.0,
            })
            c["initial"] = {k: 0.0 for k in c["initial"]}
            c["contributions"].update({
                "base_salary_pre": 0.0,
                "bonus_pre": 0.0,
                "ot_income_pre": 0.0,
                "pretax_401k_limit_y1": 0.0,
                "roth_ira_limit_y1": 0.0,
                "hsa_limit_y1": 0.0,
                "match_rate": 0.0,
                "annual_spending_now": 1_000_000_000.0,
            })
            c["promotion"]["enabled"] = False
            c["returns"].update({
                "friction_accum": 0.0,
                "expense_ratio": 0.0,
                "rebalance_cost": 0.0,
            })
            c["tax_us"]["drag_taxable"] = 0.0
            c["mortality"].update({"enabled": True, "cap_age": 110})
            c["household"].update({
                "enabled": True,
                "spouse_age_offset": -10,
                "spouse_base_salary_pre": 0.0,
                "spouse_bonus_pre": 0.0,
                "spouse_pretax_401k_limit_y1": 0.0,
                "spouse_roth_ira_limit_y1": 0.0,
                "spouse_hsa_limit_y1": 0.0,
                "spouse_match_rate": 0.0,
            })
            c["income_streams"].update({
                "rental_enabled": True,
                "rental_annual_net_real": 100.0,
                "rental_start_age": 31,
                "rental_end_age": 33,
                "rental_owner": owner,
            })
            fixed = self._fixed_lifetime(4)
            with mock.patch.object(
                    V98, "sample_lifetime_v7",
                    side_effect=lambda *args, **kwargs: fixed), \
                    mock.patch.object(
                        V98, "sample_bond_returns",
                        side_effect=lambda equity, *args: np.zeros(len(equity))), \
                    mock.patch.object(
                        V98, "annual_mortality_rate",
                        side_effect=lambda age, params: 1.0 if age >= 31 else 0.0):
                return ENG._run(c, 1, SEED, False)[0]

        primary = run("primary")
        self.assertEqual(
            [step["accounts"].taxable for step in primary["accum_path"]],
            [0.0, 100.0, 100.0, 100.0],
            "death-year income remains; later primary-owned income stops",
        )
        self.assertEqual(primary["accum_income_meta"]["received_nominal"], 100.0)
        self.assertEqual(
            primary["accum_income_meta"]["received_nominal_by_kind"],
            {"rental": 100.0},
        )
        self.assertEqual(
            primary["accum_income_meta"]["received_nominal_by_age"],
            {31: 100.0},
        )
        for owner in ("household", "unspecified", "spouse"):
            with self.subTest(owner=owner):
                shared = run(owner)
                self.assertEqual(
                    [step["accounts"].taxable
                     for step in shared["accum_path"]],
                    [0.0, 100.0, 200.0, 300.0],
                )
                self.assertEqual(
                    shared["accum_income_meta"]["received_nominal"], 300.0)
                self.assertEqual(
                    shared["accum_income_meta"]["received_nominal_by_age"],
                    {31: 100.0, 32: 100.0, 33: 100.0},
                )

        # A spouse-owned stream still uses the primary user's age axis. In
        # single-person mode every valid owner collapses to the primary.
        self.assertTrue(
            V98._income_owner_is_alive(
                "spouse", False, primary_alive=True, spouse_alive=False)
        )

    def test_retirement_income_offsets_need_and_parttime_uses_actual_start(self):
        import numpy as np
        import fire_v9_8_model as V98
        from fire_v6_model import AccountStack

        c = cfg0()
        c["state"].update({"start_age": 30, "expenses_y0": 100.0})
        c["medical"].update({
            "non_medical_y0": 100.0,
            "routine_y0": 0.0,
            "premium_working": 0.0,
            "premium_aca": 0.0,
            "premium_medicare": 0.0,
            "oop_y0": 0.0,
            "cpi_delta_routine": 0.0,
            "cpi_delta_premium": 0.0,
            "cpi_delta_oop": 0.0,
        })
        c["social_security"]["enabled"] = False
        c["mortality"]["enabled"] = False
        c["tax_us"].update({
            "drag_taxable": 0.0,
            "withdrawal_tax_taxable": 0.0,
            "withdrawal_tax_traditional": 0.0,
        })
        kw = ENG.build_kwargs(c, False)
        stream = V98.IncomeStreamSpec(
            kind="parttime",
            owner="primary",
            annual_real=40.0,
            start_age=31,
            duration_years=2,
            after_fire_only=True,
        )
        with mock.patch.object(
                V98, "estimate_magi_proxy",
                wraps=V98.estimate_magi_proxy) as magi_proxy:
            wd = V98.simulate_retirement_v98(
                starting_accounts=AccountStack(taxable=1_000.0),
                starting_age=35,
                fire_year_cpi_cumulative=1.0,
                equity_returns=[0.0, 0.0],
                bond_returns=[0.0, 0.0],
                inflations=[0.0, 0.0],
                rule=V98.FixedRealRule(),
                glide_path=kw["glide_path"],
                relocation=kw["relocation"],
                sh_property=kw["sh_property"],
                medical=kw["medical"],
                aca=kw["aca"],
                mortality=kw["mortality"],
                roth_ladder=kw["roth_ladder"],
                ss=kw["ss"],
                ftc=kw["ftc"],
                eldercare_events=[],
                inheritance_event=None,
                state=kw["state"],
                tax_us=kw["tax_us"],
                tax_cn=kw["tax_cn"],
                friction=0.0,
                rng=np.random.default_rng(1),
                china_healthcare=kw["china_healthcare"],
                ss_nra=kw["ss_nra"],
                tax_true=kw["tax_true"],
                income_streams=(stream,),
            )
        self.assertEqual(wd["total_income_received_nominal"], 80.0)
        self.assertEqual(wd["total_income_applied_nominal"], 80.0)
        self.assertEqual(wd["total_wd_received_nominal"], 120.0)
        self.assertEqual(sum(wd["nominal_consumption_path"]), 200.0)
        # The after-tax cash lowers the portfolio-withdrawal proxy, but never
        # masquerades as MAGI itself: 100 target - 40 income => 60 proxy need.
        first_proxy = magi_proxy.call_args_list[0].kwargs
        self.assertEqual(first_proxy["taxable_wd_nominal"], 30.0)
        self.assertEqual(first_proxy["pretax_401k_wd_nominal"], 18.0)

        # The flat-tax withdrawal helper also has a historical $1 solvency
        # tolerance. In a year with real structured cash, consumption must use
        # the $59.50 actually delivered plus the $40 income, not invent the
        # missing fifty cents to display the $100 target.
        flat_boundary = V98.simulate_retirement_v98(
            starting_accounts=AccountStack(taxable=59.50),
            starting_age=35,
            fire_year_cpi_cumulative=1.0,
            equity_returns=[0.0],
            bond_returns=[0.0],
            inflations=[0.0],
            rule=V98.FixedRealRule(),
            glide_path=kw["glide_path"],
            relocation=kw["relocation"],
            sh_property=kw["sh_property"],
            medical=kw["medical"],
            aca=kw["aca"],
            mortality=kw["mortality"],
            roth_ladder=kw["roth_ladder"],
            ss=kw["ss"],
            ftc=kw["ftc"],
            eldercare_events=[],
            inheritance_event=None,
            state=kw["state"],
            tax_us=kw["tax_us"],
            tax_cn=kw["tax_cn"],
            friction=0.0,
            rng=np.random.default_rng(1),
            china_healthcare=kw["china_healthcare"],
            ss_nra=kw["ss_nra"],
            tax_true=kw["tax_true"],
            income_streams=(stream,),
        )
        self.assertTrue(flat_boundary["survived_financially"])
        self.assertEqual(flat_boundary["nominal_consumption_path"], [99.50])
        self.assertAlmostEqual(
            sum(flat_boundary["nominal_consumption_path"]),
            flat_boundary["total_wd_received_nominal"]
            + flat_boundary["total_ss_applied_nominal"]
            + flat_boundary["total_income_applied_nominal"],
        )

    def test_non_cola_uses_realized_first_payment_cpi_as_nominal_anchor(self):
        import numpy as np
        import fire_v9_8_model as V98
        from fire_v6_model import AccountStack

        c = cfg0()
        c["state"].update({"start_age": 30, "expenses_y0": 0.0})
        c["medical"].update({
            "non_medical_y0": 0.0,
            "routine_y0": 0.0,
            "premium_working": 0.0,
            "premium_aca": 0.0,
            "premium_medicare": 0.0,
            "oop_y0": 0.0,
        })
        c["social_security"]["enabled"] = False
        c["mortality"]["enabled"] = False
        c["tax_us"]["drag_taxable"] = 0.0
        kw = ENG.build_kwargs(c, False)

        def received(cola):
            stream = V98.IncomeStreamSpec(
                kind="pension",
                owner="primary",
                annual_real=100.0,
                start_age=31,
                cola=cola,
                nominal_anchor_cpi=(None if cola else 1.1),
            )
            wd = V98.simulate_retirement_v98(
                starting_accounts=AccountStack(taxable=1_000.0),
                starting_age=30,
                fire_year_cpi_cumulative=1.0,
                equity_returns=[0.0, 0.0],
                bond_returns=[0.0, 0.0],
                inflations=[0.1, 0.2],
                rule=V98.FixedRealRule(),
                glide_path=kw["glide_path"],
                relocation=kw["relocation"],
                sh_property=kw["sh_property"],
                medical=kw["medical"],
                aca=kw["aca"],
                mortality=kw["mortality"],
                roth_ladder=kw["roth_ladder"],
                ss=kw["ss"],
                ftc=kw["ftc"],
                eldercare_events=[],
                inheritance_event=None,
                state=kw["state"],
                tax_us=kw["tax_us"],
                tax_cn=kw["tax_cn"],
                friction=0.0,
                rng=np.random.default_rng(1),
                china_healthcare=kw["china_healthcare"],
                ss_nra=kw["ss_nra"],
                tax_true=kw["tax_true"],
                income_streams=(stream,),
            )
            return wd["total_income_received_nominal"]

        self.assertAlmostEqual(received(False), 220.0)
        self.assertAlmostEqual(received(True), 242.0)

    def test_owner_compatibility_and_validation(self):
        import fire_v9_8_model as V98
        import persistence as P

        old = {
            "config_version": 2,
            "income_streams": {
                "pension_enabled": True,
                "pension_annual_real": 10_000.0,
            },
        }
        normalized = P.normalize_config(old, cfg0)
        self.assertEqual(
            normalized["income_streams"]["pension_owner"],
            "unspecified",
        )
        old["income_streams"]["pension_owner"] = None
        normalized_null = P.normalize_config(old, cfg0)
        self.assertEqual(
            normalized_null["income_streams"]["pension_owner"],
            "unspecified",
        )

        bad = cfg0()
        bad["income_streams"].update({
            "pension_enabled": True,
            "pension_annual_real": 10_000.0,
            "pension_owner": "not-a-member",
        })
        with self.assertRaisesRegex(ValueError, "pension_owner"):
            ENG.build_kwargs(bad, False)

        bad["income_streams"]["pension_enabled"] = False
        self.assertIsNone(ENG.build_kwargs(bad, False)["income_streams"])

        # "Disabled invalid data is inert" covers every dormant leaf, not only
        # owner. Old drafts can retain malformed hidden values; switching the
        # feature off must not parse them or move the OFF path.
        dormant = cfg0()
        dormant["income_streams"].update({
            "pension_enabled": False,
            "pension_annual_real": "not-a-number",
            "pension_owner": "not-a-member",
            "rental_enabled": False,
            "rental_annual_net_real": "not-a-number",
            "rental_owner": "not-a-member",
            "parttime_enabled": False,
            "parttime_annual_real": "not-a-number",
            "parttime_owner": "not-a-member",
            "equity_enabled": False,
            "equity_annual_real": "not-a-number",
            "equity_owner": "not-a-member",
        })
        self.assertIsNone(ENG.build_kwargs(dormant, False)["income_streams"])

        for nonfinite in ("nan", "inf", "-inf"):
            with self.subTest(nonfinite=nonfinite):
                invalid_number = cfg0()
                invalid_number["income_streams"].update({
                    "pension_enabled": True,
                    "pension_annual_real": nonfinite,
                    "pension_owner": "primary",
                })
                with self.assertRaisesRegex(
                        ValueError, "pension_annual_real.*finite"):
                    ENG.build_kwargs(invalid_number, False)
        with self.assertRaisesRegex(ValueError, "annual_real.*finite"):
            V98.IncomeStreamSpec(
                kind="pension",
                annual_real=float("inf"),
                owner="primary",
                start_age=65,
            )

    def test_all_disabled_preserves_full_result_and_rng_state(self):
        from dataclasses import replace
        import numpy as np
        import fire_v9_8_model as V98
        from fire_v6_model import AccountStack

        kw = ENG.build_kwargs(cfg0(), False)
        config = kw.pop("config")
        self.assertIsNone(kw["income_streams"])
        historical_kwargs = dict(kw)
        historical_kwargs.pop("income_streams")
        rng_historical = np.random.default_rng(SEED)
        rng_explicit_none = np.random.default_rng(SEED)
        historical = V98.simulate_lifecycle_v98(
            config=config, rng=rng_historical, **historical_kwargs)
        explicit_none = V98.simulate_lifecycle_v98(
            config=config, rng=rng_explicit_none, **kw)
        self.assertNotIn("accum_income_meta", historical)
        self.assertNotIn("accum_income_meta", explicit_none)
        self.assertEqual(historical, explicit_none)
        np.testing.assert_array_equal(
            rng_historical.random(16),
            rng_explicit_none.random(16),
            err_msg="disabled income streams moved the shared RNG state",
        )

        # Cross-version OFF-path golden: the true-tax solver deliberately
        # accepts a sub-dollar fixed-point tolerance, but the historical
        # no-income result records the configured spending target. Comparing
        # only omitted-vs-None on the new implementation cannot catch drift
        # from that parent behavior.
        c = cfg0()
        c["state"].update({"start_age": 30, "expenses_y0": 50_000.0})
        c["medical"].update({
            "non_medical_y0": 50_000.0,
            "routine_y0": 0.0,
            "premium_working": 0.0,
            "premium_aca": 0.0,
            "premium_medicare": 0.0,
            "oop_y0": 0.0,
            "cpi_delta_routine": 0.0,
            "cpi_delta_premium": 0.0,
            "cpi_delta_oop": 0.0,
        })
        c["social_security"]["enabled"] = False
        c["mortality"]["enabled"] = False
        c["tax_true"]["enabled"] = True
        true_tax_kw = ENG.build_kwargs(c, False)
        retirement_kwargs = dict(
            starting_accounts=AccountStack(pretax_401k=1_000_000.0),
            starting_age=30,
            fire_year_cpi_cumulative=1.0,
            equity_returns=[0.0],
            bond_returns=[0.0],
            inflations=[0.0],
            rule=V98.FixedRealRule(),
            glide_path=true_tax_kw["glide_path"],
            relocation=true_tax_kw["relocation"],
            sh_property=true_tax_kw["sh_property"],
            medical=true_tax_kw["medical"],
            aca=true_tax_kw["aca"],
            mortality=true_tax_kw["mortality"],
            roth_ladder=true_tax_kw["roth_ladder"],
            ss=true_tax_kw["ss"],
            ftc=true_tax_kw["ftc"],
            eldercare_events=[],
            inheritance_event=None,
            state=true_tax_kw["state"],
            tax_us=true_tax_kw["tax_us"],
            tax_cn=true_tax_kw["tax_cn"],
            friction=0.0,
            china_healthcare=true_tax_kw["china_healthcare"],
            ss_nra=true_tax_kw["ss_nra"],
            tax_true=true_tax_kw["tax_true"],
        )
        rng_off = np.random.default_rng(1)
        off_path = V98.simulate_retirement_v98(
            rng=rng_off,
            income_streams=None,
            **retirement_kwargs,
        )
        self.assertEqual(
            off_path["nominal_consumption_path"],
            [50_000.0],
            "disabled structured income changed the historical true-tax result",
        )
        self.assertTrue(off_path["survived_financially"])
        off_path_residual = (
            sum(off_path["nominal_consumption_path"])
            - off_path["total_wd_received_nominal"]
            - off_path["total_ss_applied_nominal"]
        )
        self.assertGreater(off_path_residual, 0.0)
        self.assertLess(
            off_path_residual,
            1.0,
            "a successful no-receipt true-tax year exceeded its $1 bound",
        )

        # The same historical target-recording tolerance also exists on the
        # flat-tax withdrawal path. Disclosure and the aggregate diagnostic
        # must not pretend the bounded residual is TRUE-tax-only.
        flat_kwargs = dict(retirement_kwargs)
        flat_kwargs.update({
            "starting_accounts": AccountStack(taxable=49_999.50),
            "tax_true": None,
            "tax_us": replace(
                true_tax_kw["tax_us"],
                drag_taxable=0.0,
                withdrawal_tax_taxable=0.0,
            ),
        })
        flat_no_receipt = V98.simulate_retirement_v98(
            rng=np.random.default_rng(1),
            income_streams=None,
            **flat_kwargs,
        )
        self.assertTrue(flat_no_receipt["survived_financially"])
        self.assertEqual(
            flat_no_receipt["nominal_consumption_path"], [50_000.0])
        flat_residual = (
            sum(flat_no_receipt["nominal_consumption_path"])
            - flat_no_receipt["total_wd_received_nominal"]
            - flat_no_receipt["total_ss_applied_nominal"]
        )
        self.assertEqual(flat_residual, 0.50)
        future_stream = V98.IncomeStreamSpec(
            kind="pension",
            annual_real=1.0,
            owner="primary",
            start_age=100,
        )
        rng_future = np.random.default_rng(1)
        future = V98.simulate_retirement_v98(
            rng=rng_future,
            income_streams=(future_stream,),
            **retirement_kwargs,
        )
        conditional_income_keys = {
            "total_income_received_nominal",
            "total_income_applied_nominal",
            "income_surplus_credited_nominal",
            "income_received_by_kind_nominal",
            "income_applied_by_kind_nominal",
            "income_surplus_by_kind_nominal",
        }
        future_without_conditional_ledgers = {
            key: value for key, value in future.items()
            if key not in conditional_income_keys
        }
        self.assertEqual(
            future_without_conditional_ledgers,
            off_path,
            "a future-only income schedule changed pre-payment results",
        )
        np.testing.assert_array_equal(
            rng_future.random(16),
            rng_off.random(16),
            err_msg="a future-only income schedule moved the RNG state",
        )

        # A stream that really will pay inside the horizon still cannot rewrite
        # the years before its first receipt. The aggregate compatibility
        # residual is bounded in nominal dollars by the true-tax solver's
        # historical < $1 tolerance for each no-receipt year.
        two_year_kwargs = dict(
            retirement_kwargs,
            equity_returns=[0.0, 0.0],
            bond_returns=[0.0, 0.0],
            inflations=[0.0, 0.0],
        )
        rng_two_none = np.random.default_rng(1)
        two_year_none = V98.simulate_retirement_v98(
            rng=rng_two_none,
            income_streams=None,
            **two_year_kwargs,
        )
        scheduled_stream = V98.IncomeStreamSpec(
            kind="pension",
            annual_real=1.0,
            owner="primary",
            start_age=32,
        )
        rng_two_scheduled = np.random.default_rng(1)
        two_year_scheduled = V98.simulate_retirement_v98(
            rng=rng_two_scheduled,
            income_streams=(scheduled_stream,),
            **two_year_kwargs,
        )
        self.assertEqual(
            two_year_scheduled["nominal_consumption_path"][0],
            two_year_none["nominal_consumption_path"][0],
        )
        self.assertEqual(
            two_year_scheduled["portfolio_path"][:2],
            two_year_none["portfolio_path"][:2],
        )
        self.assertEqual(
            two_year_scheduled["shortfall_age"],
            two_year_none["shortfall_age"],
        )
        scheduled_lhs = sum(
            two_year_scheduled["nominal_consumption_path"])
        scheduled_rhs = (
            two_year_scheduled["total_wd_received_nominal"]
            + two_year_scheduled["total_ss_applied_nominal"]
            + two_year_scheduled["total_income_applied_nominal"]
        )
        scheduled_residual = scheduled_lhs - scheduled_rhs
        self.assertGreater(scheduled_residual, 0.0)
        self.assertLess(
            scheduled_residual,
            1.0,
            "one no-receipt true-tax year exceeded its $1 compatibility bound",
        )
        np.testing.assert_array_equal(
            rng_two_scheduled.random(16),
            rng_two_none.random(16),
            err_msg="a scheduled future receipt moved the RNG state",
        )

    def test_retirement_death_filters_individual_but_not_survivor_streams(self):
        import numpy as np
        import fire_v9_8_model as V98
        from fire_v6_model import AccountStack

        c = cfg0()
        c["state"].update({"start_age": 30, "expenses_y0": 100.0})
        c["medical"].update({
            "non_medical_y0": 100.0,
            "routine_y0": 0.0,
            "premium_working": 0.0,
            "premium_aca": 0.0,
            "premium_medicare": 0.0,
            "oop_y0": 0.0,
        })
        c["social_security"]["enabled"] = False
        c["mortality"].update({"enabled": True, "cap_age": 110})
        c["tax_us"].update({
            "drag_taxable": 0.0,
            "withdrawal_tax_taxable": 0.0,
            "withdrawal_tax_traditional": 0.0,
        })
        c["household"].update({
            "enabled": True,
            "spouse_age_offset": -10,
            "spouse_pia_monthly_y0": 0.0,
        })
        kw = ENG.build_kwargs(c, False)

        def run(owner, household_config=c):
            streams = None
            if owner is not None:
                streams = (V98.IncomeStreamSpec(
                    kind="pension",
                    owner=owner,
                    annual_real=10.0,
                    start_age=31,
                ),)
            rng = np.random.default_rng(1)
            with ENG._household_ctx(household_config), mock.patch.object(
                    V98, "annual_mortality_rate",
                    side_effect=lambda age, params: 1.0 if age >= 31 else 0.0):
                result = V98.simulate_retirement_v98(
                    starting_accounts=AccountStack(taxable=1_000.0),
                    starting_age=30,
                    fire_year_cpi_cumulative=1.0,
                    equity_returns=[0.0],
                    bond_returns=[0.0],
                    inflations=[0.0],
                    rule=V98.FixedRealRule(),
                    glide_path=kw["glide_path"],
                    relocation=kw["relocation"],
                    sh_property=kw["sh_property"],
                    medical=kw["medical"],
                    aca=kw["aca"],
                    mortality=kw["mortality"],
                    roth_ladder=kw["roth_ladder"],
                    ss=kw["ss"],
                    ftc=kw["ftc"],
                    eldercare_events=[],
                    inheritance_event=None,
                    state=kw["state"],
                    tax_us=kw["tax_us"],
                    tax_cn=kw["tax_cn"],
                    friction=0.0,
                    rng=rng,
                    china_healthcare=kw["china_healthcare"],
                    ss_nra=kw["ss_nra"],
                    tax_true=kw["tax_true"],
                    income_streams=streams,
                )
            return result, rng.random(16)

        suppressed, suppressed_tail = run("primary")
        self.assertEqual(
            suppressed["total_income_received_nominal"], 0.0,
            "retirement mortality precedes that year's individual cash flow",
        )
        no_stream, no_stream_tail = run(None)
        conditional_income_keys = {
            "total_income_received_nominal",
            "total_income_applied_nominal",
            "income_surplus_credited_nominal",
            "income_received_by_kind_nominal",
            "income_applied_by_kind_nominal",
            "income_surplus_by_kind_nominal",
        }
        self.assertEqual(
            {
                key: value for key, value in suppressed.items()
                if key not in conditional_income_keys
            },
            no_stream,
            "death before the first receipt changed the historical result",
        )
        np.testing.assert_array_equal(
            suppressed_tail,
            no_stream_tail,
            err_msg="a death-suppressed stream moved the RNG state",
        )
        for owner in ("spouse", "household", "unspecified"):
            with self.subTest(owner=owner):
                self.assertEqual(
                    run(owner)[0]["total_income_received_nominal"], 10.0)

        single = copy.deepcopy(c)
        single["household"]["enabled"] = False
        single["mortality"]["enabled"] = False
        single_kw = ENG.build_kwargs(single, False)
        kw.update(single_kw)
        self.assertEqual(
            run("spouse", household_config=single)[0][
                "total_income_received_nominal"],
            10.0,
        )

    def test_income_ledgers_reconcile_by_kind_with_surplus_and_ss(self):
        import numpy as np
        import fire_v9_8_model as V98
        from fire_v6_model import AccountStack

        c = cfg0()
        c["state"].update({"start_age": 61, "expenses_y0": 100.0})
        c["medical"].update({
            "non_medical_y0": 100.0,
            "routine_y0": 0.0,
            "premium_working": 0.0,
            "premium_aca": 0.0,
            "premium_medicare": 0.0,
            "oop_y0": 0.0,
        })
        c["mortality"]["enabled"] = False
        c["social_security"].update({
            "enabled": True,
            "pia_monthly_y0": 2.5,
            "fra_age": 62,
            "claim_age": 62,
            "cpi_indexed": True,
        })
        c["tax_us"].update({
            "drag_taxable": 0.0,
            "withdrawal_tax_taxable": 0.0,
            "withdrawal_tax_traditional": 0.0,
        })
        kw = ENG.build_kwargs(c, False)
        streams = (
            V98.IncomeStreamSpec(
                kind="pension", annual_real=60.0,
                owner="primary", start_age=62),
            V98.IncomeStreamSpec(
                kind="rental", annual_real=80.0,
                owner="household", start_age=62, end_age=62),
        )
        wd = V98.simulate_retirement_v98(
            starting_accounts=AccountStack(taxable=1_000.0),
            starting_age=61,
            fire_year_cpi_cumulative=1.0,
            equity_returns=[0.0],
            bond_returns=[0.0],
            inflations=[0.0],
            rule=V98.FixedRealRule(),
            glide_path=kw["glide_path"],
            relocation=kw["relocation"],
            sh_property=kw["sh_property"],
            medical=kw["medical"],
            aca=kw["aca"],
            mortality=kw["mortality"],
            roth_ladder=kw["roth_ladder"],
            ss=kw["ss"],
            ftc=kw["ftc"],
            eldercare_events=[],
            inheritance_event=None,
            state=kw["state"],
            tax_us=kw["tax_us"],
            tax_cn=kw["tax_cn"],
            friction=0.0,
            rng=np.random.default_rng(1),
            china_healthcare=kw["china_healthcare"],
            ss_nra=kw["ss_nra"],
            tax_true=kw["tax_true"],
            income_streams=streams,
        )
        self.assertEqual(wd["total_income_received_nominal"], 140.0)
        self.assertEqual(wd["total_income_applied_nominal"], 100.0)
        self.assertEqual(wd["income_surplus_credited_nominal"], 40.0)
        self.assertEqual(wd["total_wd_received_nominal"], 0.0)
        self.assertEqual(wd["total_ss_applied_nominal"], 0.0)
        self.assertEqual(wd["ss_surplus_credited_nominal"], 30.0)
        self.assertEqual(sum(wd["nominal_consumption_path"]), 100.0)
        self.assertAlmostEqual(wd["final_accounts"].taxable, 1_070.0)
        for prefix in ("income_received", "income_applied",
                       "income_surplus"):
            by_kind = wd[f"{prefix}_by_kind_nominal"]
            total_key = {
                "income_received": "total_income_received_nominal",
                "income_applied": "total_income_applied_nominal",
                "income_surplus": "income_surplus_credited_nominal",
            }[prefix]
            self.assertAlmostEqual(sum(by_kind.values()), wd[total_key])
        self.assertAlmostEqual(
            wd["total_income_received_nominal"],
            wd["total_income_applied_nominal"]
            + wd["income_surplus_credited_nominal"],
        )

    def test_true_tax_shortfall_with_income_and_ss_fails_and_reconciles(self):
        import numpy as np
        import fire_v9_8_model as V98
        from fire_v6_model import AccountStack

        c = cfg0()
        c["state"].update({"start_age": 61, "expenses_y0": 150_000.0})
        c["medical"].update({
            "non_medical_y0": 150_000.0,
            "routine_y0": 0.0,
            "premium_working": 0.0,
            "premium_aca": 0.0,
            "premium_medicare": 0.0,
            "oop_y0": 0.0,
            "cpi_delta_routine": 0.0,
            "cpi_delta_premium": 0.0,
            "cpi_delta_oop": 0.0,
        })
        c["mortality"]["enabled"] = False
        c["social_security"].update({"enabled": True, "claim_age": 62})
        c["tax_true"]["enabled"] = True
        kw = ENG.build_kwargs(c, False)
        stream = V98.IncomeStreamSpec(
            kind="pension",
            annual_real=50_000.0,
            owner="primary",
            start_age=62,
        )

        def run(run_kw, income_streams, rng=None):
            rng = rng or np.random.default_rng(1)
            return V98.simulate_retirement_v98(
                starting_accounts=AccountStack(),
                starting_age=61,
                fire_year_cpi_cumulative=1.0,
                equity_returns=[0.0],
                bond_returns=[0.0],
                inflations=[0.0],
                rule=V98.FixedRealRule(),
                glide_path=run_kw["glide_path"],
                relocation=run_kw["relocation"],
                sh_property=run_kw["sh_property"],
                medical=run_kw["medical"],
                aca=run_kw["aca"],
                mortality=run_kw["mortality"],
                roth_ladder=run_kw["roth_ladder"],
                ss=run_kw["ss"],
                ftc=run_kw["ftc"],
                eldercare_events=[],
                inheritance_event=None,
                state=run_kw["state"],
                tax_us=run_kw["tax_us"],
                tax_cn=run_kw["tax_cn"],
                friction=0.0,
                rng=rng,
                china_healthcare=run_kw["china_healthcare"],
                ss_nra=run_kw["ss_nra"],
                tax_true=run_kw["tax_true"],
                income_streams=income_streams,
            )

        with mock.patch.object(
                V98, "compute_ss_annual_income", return_value=100_000.0):
            wd = run(kw, (stream,))

        self.assertFalse(wd["survived_financially"])
        self.assertFalse(wd["lifetime_success"])
        self.assertEqual(wd["shortfall_age"], 62)
        self.assertEqual(wd["total_wd_received_nominal"], 0.0)
        self.assertEqual(wd["total_income_applied_nominal"], 50_000.0)
        self.assertEqual(wd["total_ss_applied_nominal"], 99_800.0)
        self.assertEqual(sum(wd["nominal_consumption_path"]), 149_800.0)
        self.assertAlmostEqual(
            sum(wd["nominal_consumption_path"]),
            wd["total_wd_received_nominal"]
            + wd["total_ss_applied_nominal"]
            + wd["total_income_applied_nominal"],
        )

        # Adjacent pre-existing true-tax defect exposed by the same allocation:
        # gross SS can equal the nominal need while SS taxation leaves a
        # material (> $1) shortfall. This is a deliberate model-truth repair,
        # not an income feature effect; None and a future-only stream must both
        # fail identically.
        no_income_c = copy.deepcopy(c)
        no_income_c["state"]["expenses_y0"] = 100_000.0
        no_income_c["medical"]["non_medical_y0"] = 100_000.0
        no_income_kw = ENG.build_kwargs(no_income_c, False)
        future_stream = V98.IncomeStreamSpec(
            kind="pension",
            annual_real=1.0,
            owner="primary",
            start_age=100,
        )
        with mock.patch.object(
                V98, "compute_ss_annual_income", return_value=100_000.0):
            no_income = run(no_income_kw, None)
            future_only = run(no_income_kw, (future_stream,))
        self.assertFalse(no_income["survived_financially"])
        self.assertFalse(no_income["lifetime_success"])
        self.assertEqual(no_income["shortfall_age"], 62)
        self.assertEqual(no_income["total_wd_received_nominal"], 0.0)
        self.assertEqual(no_income["total_ss_applied_nominal"], 99_800.0)
        self.assertEqual(sum(no_income["nominal_consumption_path"]), 99_800.0)
        self.assertAlmostEqual(
            sum(no_income["nominal_consumption_path"]),
            no_income["total_wd_received_nominal"]
            + no_income["total_ss_applied_nominal"],
        )
        conditional_income_keys = {
            "total_income_received_nominal",
            "total_income_applied_nominal",
            "income_surplus_credited_nominal",
            "income_received_by_kind_nominal",
            "income_applied_by_kind_nominal",
            "income_surplus_by_kind_nominal",
        }
        self.assertEqual(
            {
                key: value for key, value in future_only.items()
                if key not in conditional_income_keys
            },
            no_income,
        )

        # The repair follows the solver's authoritative shortfall, not a
        # Social-Security special case. With no accounts and no SS the same
        # material-shortfall rule must fail and record zero delivered cash.
        with mock.patch.object(
                V98, "compute_ss_annual_income", return_value=0.0):
            non_ss_shortfall = run(no_income_kw, None)
        self.assertFalse(non_ss_shortfall["survived_financially"])
        self.assertEqual(non_ss_shortfall["shortfall_age"], 62)
        self.assertEqual(non_ss_shortfall["total_wd_received_nominal"], 0.0)
        self.assertEqual(non_ss_shortfall["total_ss_applied_nominal"], 0.0)
        self.assertEqual(sum(
            non_ss_shortfall["nominal_consumption_path"]), 0.0)

        # A scheduled primary-owned payment suppressed by actual household
        # mortality is still a no-receipt year. It must match None in result
        # and RNG while preserving the same truthful material-shortfall
        # failure.
        death_c = copy.deepcopy(no_income_c)
        death_c["household"].update({
            "enabled": True,
            "spouse_age_offset": -10,
            "spouse_pia_monthly_y0": 0.0,
            "survivor_spending_frac": 1.0,
        })
        death_c["mortality"].update({"enabled": True, "cap_age": 110})
        death_kw = ENG.build_kwargs(death_c, False)
        due_primary_stream = V98.IncomeStreamSpec(
            kind="pension",
            annual_real=1.0,
            owner="primary",
            start_age=62,
        )
        rng_death_none = np.random.default_rng(1)
        rng_death_stream = np.random.default_rng(1)
        mortality = lambda age, params: 1.0 if age >= 62 else 0.0
        with ENG._household_ctx(death_c), mock.patch.object(
                V98, "annual_mortality_rate", side_effect=mortality), \
                mock.patch.object(
                    V98, "compute_ss_annual_income", return_value=100_000.0):
            death_none = run(death_kw, None, rng_death_none)
        with ENG._household_ctx(death_c), mock.patch.object(
                V98, "annual_mortality_rate", side_effect=mortality), \
                mock.patch.object(
                    V98, "compute_ss_annual_income", return_value=100_000.0):
            death_suppressed = run(
                death_kw, (due_primary_stream,), rng_death_stream)
        self.assertEqual(
            {
                key: value for key, value in death_suppressed.items()
                if key not in conditional_income_keys
            },
            death_none,
        )
        self.assertFalse(death_none["survived_financially"])
        np.testing.assert_array_equal(
            rng_death_stream.random(16),
            rng_death_none.random(16),
            err_msg="a death-suppressed payment moved the RNG state",
        )

        # ACA is evaluated twice around the TRUE-tax solve. When the final need
        # moves by exactly $1 the solver is intentionally not rerun, so the
        # material-shortfall decision must compare delivered cash with final
        # need2 rather than reuse the preliminary solve's stale shortfall.
        aca_c = cfg0()
        aca_c["state"].update({
            "start_age": 30,
            "expenses_y0": 1_000.0,
        })
        aca_c["medical"].update({
            "non_medical_y0": 900.0,
            "routine_y0": 0.0,
            "premium_working": 0.0,
            "premium_aca": 100.0,
            "premium_medicare": 0.0,
            "oop_y0": 0.0,
            "cpi_delta_routine": 0.0,
            "cpi_delta_premium": 0.0,
            "cpi_delta_oop": 0.0,
            "aca_start_age": 0,
            "medicare_age": 65,
        })
        aca_c["social_security"]["enabled"] = False
        aca_c["mortality"]["enabled"] = False
        aca_c["roth_ladder"]["enabled"] = False
        aca_c["tax_us"].update({
            "drag_taxable": 0.0,
            "withdrawal_tax_taxable": 0.0,
            "withdrawal_tax_traditional": 0.0,
        })
        aca_c["tax_true"].update({
            "enabled": True,
            "rmd_enabled": False,
            "irmaa_enabled": False,
            "taxable_gain_fraction": 0.0,
            "state_rate": 0.0,
        })
        aca_kw = ENG.build_kwargs(aca_c, False)
        aca_stream = V98.IncomeStreamSpec(
            kind="pension",
            owner="primary",
            annual_real=200.0,
            start_age=31,
        )
        with mock.patch.object(
                V98, "compute_aca_premium_paid",
                side_effect=[100.0, 99.0]):
            aca_boundary = V98.simulate_retirement_v98(
                starting_accounts=AccountStack(taxable=798.50),
                starting_age=30,
                fire_year_cpi_cumulative=1.0,
                equity_returns=[0.0],
                bond_returns=[0.0],
                inflations=[0.0],
                rule=V98.FixedRealRule(),
                glide_path=aca_kw["glide_path"],
                relocation=aca_kw["relocation"],
                sh_property=aca_kw["sh_property"],
                medical=aca_kw["medical"],
                aca=aca_kw["aca"],
                mortality=aca_kw["mortality"],
                roth_ladder=aca_kw["roth_ladder"],
                ss=aca_kw["ss"],
                ftc=aca_kw["ftc"],
                eldercare_events=[],
                inheritance_event=None,
                state=aca_kw["state"],
                tax_us=aca_kw["tax_us"],
                tax_cn=aca_kw["tax_cn"],
                friction=0.0,
                rng=np.random.default_rng(1),
                china_healthcare=aca_kw["china_healthcare"],
                ss_nra=aca_kw["ss_nra"],
                tax_true=aca_kw["tax_true"],
                income_streams=(aca_stream,),
            )
        self.assertTrue(aca_boundary["survived_financially"])
        self.assertIsNone(aca_boundary["shortfall_age"])
        self.assertEqual(
            aca_boundary["nominal_consumption_path"], [998.50])
        self.assertEqual(
            aca_boundary["total_wd_received_nominal"], 798.50)
        self.assertEqual(
            aca_boundary["total_income_applied_nominal"], 200.0)
        self.assertEqual(aca_boundary["total_ss_applied_nominal"], 0.0)

    def test_income_does_not_retroactively_cure_mandatory_event_shortfall(self):
        import numpy as np
        import fire_v9_8_model as V98
        from fire_v6_model import AccountStack

        c = cfg0()
        c["state"].update({"start_age": 30, "expenses_y0": 10.0})
        c["medical"].update({
            "non_medical_y0": 10.0,
            "routine_y0": 0.0,
            "premium_working": 0.0,
            "premium_aca": 0.0,
            "premium_medicare": 0.0,
            "oop_y0": 0.0,
        })
        c["social_security"]["enabled"] = False
        c["mortality"]["enabled"] = False
        c["tax_us"].update({
            "drag_taxable": 0.0,
            "withdrawal_tax_taxable": 0.0,
            "withdrawal_tax_traditional": 0.0,
        })
        kw = ENG.build_kwargs(c, False)
        stream = V98.IncomeStreamSpec(
            kind="rental", annual_real=1_000.0,
            owner="primary", start_age=31, end_age=31)
        wd = V98.simulate_retirement_v98(
            starting_accounts=AccountStack(),
            starting_age=30,
            fire_year_cpi_cumulative=1.0,
            equity_returns=[0.0],
            bond_returns=[0.0],
            inflations=[0.0],
            rule=V98.FixedRealRule(),
            glide_path=kw["glide_path"],
            relocation=kw["relocation"],
            sh_property=kw["sh_property"],
            medical=kw["medical"],
            aca=kw["aca"],
            mortality=kw["mortality"],
            roth_ladder=kw["roth_ladder"],
            ss=kw["ss"],
            ftc=kw["ftc"],
            eldercare_events=[],
            inheritance_event=None,
            state=kw["state"],
            tax_us=kw["tax_us"],
            tax_cn=kw["tax_cn"],
            friction=0.0,
            rng=np.random.default_rng(1),
            china_healthcare=kw["china_healthcare"],
            ss_nra=kw["ss_nra"],
            life_events=[(31, 1_000.0)],
            tax_true=kw["tax_true"],
            income_streams=(stream,),
        )
        self.assertTrue(wd["life_event_shortfalls"])
        self.assertFalse(wd["lifetime_success"])

        # The ordering is year-local, not a permanent wall between channels:
        # income received in an earlier year is ordinary taxable cash and must
        # be available to a later mandatory event.
        c = cfg0()
        c["state"].update({
            "start_age": 30,
            "accum_years": 2,
            "retire_horizon": 1,
            "expenses_y0": 1_000_000_000.0,
            "inflation": 0.0,
        })
        c["initial"] = {k: 0.0 for k in c["initial"]}
        c["contributions"].update({
            "base_salary_pre": 0.0,
            "bonus_pre": 0.0,
            "ot_income_pre": 0.0,
            "pretax_401k_limit_y1": 0.0,
            "roth_ira_limit_y1": 0.0,
            "hsa_limit_y1": 0.0,
            "match_rate": 0.0,
            "annual_spending_now": 1_000_000_000.0,
        })
        c["promotion"]["enabled"] = False
        c["mortality"]["enabled"] = False
        c["returns"].update({
            "friction_accum": 0.0,
            "expense_ratio": 0.0,
            "rebalance_cost": 0.0,
        })
        c["tax_us"].update({
            "drag_taxable": 0.0,
            "withdrawal_tax_taxable": 0.0,
        })
        c["life_events"] = [{
            "age": 32,
            "amount_real": 50.0,
            "label": "later mandatory outflow",
        }]
        c["income_streams"].update({
            "rental_enabled": True,
            "rental_annual_net_real": 100.0,
            "rental_start_age": 31,
            "rental_end_age": 32,
            "rental_owner": "primary",
        })
        fixed = self._fixed_lifetime(3)
        with mock.patch.object(
                V98, "sample_lifetime_v7",
                side_effect=lambda *args, **kwargs: fixed), \
                mock.patch.object(
                    V98, "sample_bond_returns",
                    side_effect=lambda equity, *args: np.zeros(len(equity))):
            lifecycle = ENG._run(c, 1, SEED, False)[0]
        self.assertEqual(
            [step["accounts"].taxable for step in lifecycle["accum_path"]],
            [0.0, 100.0, 150.0],
        )
        self.assertEqual(
            lifecycle["accum_life_event_meta"]["underfunded_years"], 0)
        self.assertEqual(
            lifecycle["accum_life_event_meta"][
                "funding_shortfall_nominal_by_age"],
            {},
        )


class TestInvariants(unittest.TestCase):
    def test_cash_conservation_in_every_mode(self):
        variants = {"default": cfg0()}
        v = cfg0(); v["children"] = [{"parent_age_at_birth": 33,
                                      "annual_cost_real": 15000,
                                      "support_years": 22,
                                      "college_total_real": 100000}]
        variants["children"] = v
        v = cfg0(); v["household"].update({"enabled": True,
                                           "spouse_base_salary_pre": 90000})
        variants["household"] = v
        v = cfg0(); v["layoff"]["enabled"] = True
        variants["layoff"] = v
        v = cfg0(); v["income_streams"].update({"pension_enabled": True,
                                                "pension_annual_real": 20000})
        variants["pension"] = v
        v = cfg0(); v["income_streams"].update({
            "pension_enabled": True,
            "pension_annual_real": 20000,
            "pension_owner": "primary",
        })
        v["tax_true"]["enabled"] = True
        # Receipt from the first modeled year makes this the strict active-cash
        # invariant. Separate tests above bind the historical < $1/year
        # compatibility residual in true-tax years before any receipt.
        v["income_streams"]["pension_start_age"] = (
            v["state"]["start_age"] + 1)
        v["mortality"]["enabled"] = False
        variants["pension_true_tax"] = v
        for name, c in variants.items():
            r = ENG.run_full(c, 500, SEED, 300)
            self.assertLess(r["home"]["invariant_max_rel_error"], 1e-9,
                            f"invariant violated in mode '{name}'")


class TestChunkedProtocol(unittest.TestCase):
    def test_worker_count_never_changes_results(self):
        old_thr, old_chunk = ENG.MP_THRESHOLD, ENG.MP_CHUNK
        ENG.MP_THRESHOLD, ENG.MP_CHUNK = 1000, 500     # force 4 chunks at n=2000
        try:
            configs = {"disabled": cfg0()}
            active = cfg0()
            active["income_streams"].update({
                "pension_enabled": True,
                "pension_annual_real": 20_000.0,
                "pension_owner": "primary",
            })
            configs["active_pension"] = active
            outs = {name: [] for name in configs}
            for w in ("2", "3"):
                os.environ["FIRE_MP_WORKERS"] = w
                for name, config in configs.items():
                    r = ENG.run_full(config, 2000, SEED, 300)
                    outs[name].append(r["home"]["terminal_real"]["p50"])
                    self.assertTrue(
                        str(r.get("mode", "")).startswith("chunked-4x"))
            for name, values in outs.items():
                self.assertEqual(
                    values[0], values[1],
                    f"worker count changed chunked {name} results",
                )
            self.assertNotEqual(
                outs["disabled"][0], outs["active_pension"][0],
                "workers agreed only because both silently dropped income",
            )
        finally:
            ENG.MP_THRESHOLD, ENG.MP_CHUNK = old_thr, old_chunk
            os.environ.pop("FIRE_MP_WORKERS", None)


class TestDataVintage(unittest.TestCase):
    """Runtime-owned offline rule-pack and annual-review contracts."""

    def test_city_library_vintage_fresh(self):
        import datetime, re
        js = pathlib.Path(ROOT, "web", "destination_catalog.js").read_text(encoding="utf-8")
        m = re.search(r'DEST_VINTAGE = "(\d{4})-(\d{2})"', js)
        self.assertIsNotNone(m, "DEST_VINTAGE stamp missing from web/destination_catalog.js")
        y, mo = int(m.group(1)), int(m.group(2))
        age_months = (datetime.date.today().year - y) * 12 + (datetime.date.today().month - mo)
        self.assertLessEqual(age_months, 18,
                             f"city library is {age_months} months old — review the "
                             f"illustrative defaults and bump DEST_VINTAGE")

    def test_rule_pack_is_content_addressed_and_offline(self):
        import hashlib
        import json
        import fire_rule_pack as RP

        payload = RP.canonical_pack_payload()
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode("utf-8")
        expected = hashlib.sha256(encoded).hexdigest()
        self.assertEqual(RP.RULE_PACK_SHA256, expected)
        self.assertEqual(RP.RULE_PACK_ID, f"us-offline-{expected[:16]}")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["delivery"], "offline_embedded")
        self.assertFalse(payload["runtime_network_refresh"])
        probe = (
            "import sys;"
            f"sys.path.insert(0,{str(pathlib.Path(ROOT, 'engine'))!r});"
            "import fire_rule_pack as p;"
            "print(p.RULE_PACK_ID,p.RULE_PACK_SHA256)"
        )
        first = subprocess.check_output(
            [sys.executable, "-c", probe], text=True).strip()
        second = subprocess.check_output(
            [sys.executable, "-c", probe], text=True).strip()
        self.assertEqual(first, second)
        source = pathlib.Path(
            ROOT, "engine", "fire_rule_pack.py").read_text(encoding="utf-8")
        # No network and no clock, absolutely. The pack must stay a
        # deterministic, content-addressed leaf: a hidden fetch or a hidden
        # `today()` would make one run's tax tables differ from another's with
        # nothing in the result saying so.
        for forbidden in ("date.today(", "urlopen(", "requests.", "socket.",
                          "read_text(", "write_text("):
            self.assertNotIn(forbidden, source)
        # `open(` was on that list too, and 4.0's rule that a pack carries
        # declarative data rather than a Python literal cannot be satisfied
        # without reading a file. The two conflict and one had to give; what
        # the ban actually protects — determinism and content addressing — is
        # preserved by narrowing it rather than dropping it:
        #   * exactly one read, inside `_load_payload`;
        #   * of a file that ships inside the bundle and is part of the release
        #     identity manifest, so editing it moves the app's identity;
        #   * whose content hash is pinned in tests/test_rule_pack_payload.py.
        self.assertEqual(source.count("open("), 1)
        loader = source[source.index("def _load_payload"):]
        loader = loader[:loader.index("_PACK_PAYLOAD:")]
        self.assertIn("open(", loader)
        # The path is derived, never taken from a caller or the environment:
        # a pack read from somewhere a user chose is a different guarantee.
        path_fn = source[source.index("def _payload_path"):
                         source.index("def _load_payload")]
        self.assertIn("_PAYLOAD_FILENAME", path_fn)
        for external in ("environ", "argv", "input("):
            self.assertNotIn(external, path_fn)

    def test_rule_pack_maintenance_deadline_and_applicability(self):
        import fire_rule_pack as RP

        cfg = cfg0()
        on_deadline = RP.rule_pack_for_run(cfg, as_of="2026-12-31")
        after_deadline = RP.rule_pack_for_run(cfg, as_of="2027-01-01")
        today = RP.rule_pack_for_run(cfg, as_of="2026-07-29")
        self.assertEqual(on_deadline["status"], "current")
        self.assertEqual(after_deadline["status"], "stale")
        self.assertEqual(today["status"], "current")
        self.assertEqual(today["conclusion_status"], "current")
        self.assertEqual(today["evaluated_on"], "2026-07-29")

        components = {row["id"]: row for row in today["components"]}
        self.assertEqual(components["contribution_limits"]["status"], "current")
        self.assertEqual(components["aca_marketplace"]["status"], "current")
        self.assertEqual(components["us_federal_tax"]["status"], "current")
        self.assertEqual(components["medicare_irmaa"]["status"],
                         "not_used_at_run")
        self.assertEqual(components["ssa_benefit_rules"]["status"], "current")
        self.assertEqual(components["ssa_statement_import"]["status"],
                         "not_used_at_run")

        true_tax = copy.deepcopy(cfg)
        true_tax["tax_true"]["enabled"] = True
        active = RP.rule_pack_for_run(true_tax, as_of="2026-07-29")
        active_components = {row["id"]: row for row in active["components"]}
        self.assertEqual(active_components["us_federal_tax"]["status"], "current")
        self.assertEqual(active_components["medicare_irmaa"]["status"], "current")

    def test_field_source_ledger_covers_every_canonical_value_group(self):
        import fire_rule_pack as RP

        payload = RP.canonical_pack_payload()
        components = {row["id"]: row for row in payload["components"]}
        ledger = payload["field_source_ledger"]
        self.assertTrue(ledger)
        covered = {}
        for row in ledger:
            component_id = row["component_id"]
            self.assertIn(component_id, components)
            self.assertTrue(row["field_group"])
            fields = row["fields"]
            self.assertTrue(fields)
            source_class = row["source_class"]
            self.assertIn(source_class, {
                "official_primary", "product_assumption",
                "historical_counterfactual",
            })
            if source_class == "official_primary":
                self.assertTrue(row["sources"])
            else:
                self.assertIn("not", row["note"].lower())
            for field in fields:
                self.assertIn(field, components[component_id]["values"])
                self.assertNotIn((component_id, field), covered)
                covered[(component_id, field)] = row["field_group"]
        expected = {
            (component_id, field)
            for component_id, component in components.items()
            for field in component["values"]
        }
        self.assertEqual(set(covered), expected)

        default_row = next(row for row in ledger
                           if row["field_group"] == "default_scenario")
        self.assertEqual(default_row["source_class"], "product_assumption")
        self.assertEqual(default_row["fields"], ["default_scenario"])
        self.assertEqual(default_row["sources"], [])
        bend_row = next(row for row in ledger
                        if row["field_group"] == "ssa_statement_series")
        self.assertIn("bend1_1979", bend_row["fields"])
        self.assertIn("bend2_1979", bend_row["fields"])
        self.assertIn("https://www.ssa.gov/oact/COLA/bendpoints.html",
                      bend_row["sources"])

        aca = components["aca_marketplace"]["provenance"]["conversion"]
        self.assertIn("understates PTC below 300% FPL", aca)
        self.assertIn("overstate it below 100% FPL", aca)
        self.assertIn("2021-2025 historical counterfactual", aca)
        limits = components["contribution_limits"]["provenance"]["conversion"]
        self.assertIn("self-only", limits)
        self.assertIn("family cap is modeled", limits)
        ssa = components["ssa_benefit_rules"]["provenance"]["conversion"]
        self.assertIn("1960-and-later birth cohort", ssa)

    def test_plan_values_are_compared_without_claiming_authorship(self):
        import fire_rule_pack as RP

        cfg = cfg0()
        cfg["contributions"]["pretax_401k_limit_y1"] += 1
        descriptor = RP.rule_pack_for_run(cfg, as_of="2026-12-31")
        component = {
            row["id"]: row for row in descriptor["components"]
        }["contribution_limits"]
        self.assertEqual(component["effective_source"],
                         "user_or_legacy_override")
        self.assertEqual(component["status"], "review_required")
        self.assertEqual(descriptor["status"], "review_required")
        self.assertEqual(descriptor["conclusion_status"], "review_required")

    def test_inactive_components_do_not_contaminate_the_run_status(self):
        import fire_rule_pack as RP

        cfg = cfg0()
        cfg["state"]["start_age"] = 65
        cfg["contributions"]["pretax_401k_limit_y1"] = 0
        cfg["contributions"]["roth_ira_limit_y1"] = 0
        cfg["contributions"]["hsa_limit_y1"] = 0
        cfg["social_security"]["enabled"] = False
        descriptor = RP.rule_pack_for_run(cfg, as_of="2026-07-29")
        rows = {row["id"]: row for row in descriptor["components"]}
        self.assertEqual(descriptor["status"], "current")
        for component_id in (
                "us_federal_tax", "medicare_irmaa", "contribution_limits",
                "aca_marketplace", "ssa_benefit_rules",
                "ssa_statement_import"):
            self.assertEqual(rows[component_id]["status"], "not_used_at_run")

        progressive = cfg0()
        progressive["tax_us"]["progressive"] = True
        p = RP.rule_pack_for_run(progressive, as_of="2026-07-29")
        p_rows = {row["id"]: row for row in p["components"]}
        self.assertEqual(p_rows["us_federal_tax"]["status"], "current")
        self.assertEqual(p_rows["medicare_irmaa"]["status"],
                         "not_used_at_run")

        true_no_irmaa = cfg0()
        true_no_irmaa["tax_true"].update({
            "enabled": True, "irmaa_enabled": False})
        t = RP.rule_pack_for_run(true_no_irmaa, as_of="2026-07-29")
        t_rows = {row["id"]: row for row in t["components"]}
        self.assertEqual(t_rows["us_federal_tax"]["status"], "current")
        self.assertEqual(t_rows["medicare_irmaa"]["status"],
                         "not_used_at_run")

    def test_runtime_defaults_are_single_sourced_without_numeric_drift(self):
        import fire_rule_pack as RP
        import fire_tax_true as TRUE
        import fire_v6_model as V6
        import fire_v8_model as V8
        import fire_v9_1_model as V91
        import fire_v9_2_model as V92
        import ssa_import as SSA

        federal = RP.US_FEDERAL_RULES
        limits = RP.CONTRIBUTION_LIMIT_RULES
        aca = RP.ACA_MARKETPLACE_RULES
        ssa = RP.SSA_RULES
        self.assertEqual(V6.US_ORDINARY_BRACKETS_SINGLE,
                         list(federal["ordinary_single"]))
        self.assertEqual(V6.TaxParams().std_deduction,
                         federal["std_deduction_single"])
        self.assertEqual(TRUE.ORD_SINGLE, list(federal["ordinary_single"]))
        self.assertEqual(TRUE.ORD_MFJ, list(federal["ordinary_mfj"]))
        self.assertEqual(TRUE.STD_DED_SINGLE,
                         federal["std_deduction_single"])
        self.assertEqual(TRUE.STD_DED_MFJ, federal["std_deduction_mfj"])
        self.assertEqual(V8.V8ContributionParams().pretax_401k_limit_y1,
                         limits["pretax_401k_limit_y1"])
        self.assertEqual(V8.V8ContributionParams().roth_ira_limit_y1,
                         limits["roth_ira_limit_y1"])
        self.assertEqual(V8.V8ContributionParams().hsa_limit_y1,
                         limits["hsa_limit_y1"])
        self.assertEqual(V91.ACAParams().fpl_single_y0,
                         aca["fpl_single_y0"])
        self.assertEqual(V91.ACAParams().cap_pct_pre_ira,
                         aca["cap_pct_pre_ira"])
        self.assertEqual(V92.SocialSecurityParams().fra_age,
                         ssa["fra_age"])
        self.assertEqual(SSA.AWI_MAX_YEAR, ssa["awi_through_year"])
        self.assertEqual(max(SSA.COLA), ssa["cola_through_year"])

        js = pathlib.Path(ROOT, "web", "app.js").read_text(encoding="utf-8")
        self.assertNotIn("Math.min(23500", js)
        self.assertNotIn("spouse_pretax_401k_limit_y1: 23500", js)

    def test_2026_official_pack_values_and_approximation_boundaries(self):
        import fire_rule_pack as RP
        from fire_v9_1_model import ACAParams, ACAScenario, compute_aca_premium_paid

        self.assertEqual(
            RP.US_FEDERAL_RULES["ordinary_single"],
            ((0.0, 0.10), (12_400.0, 0.12), (50_400.0, 0.22),
             (105_700.0, 0.24), (201_775.0, 0.32), (256_225.0, 0.35),
             (640_600.0, 0.37)))
        self.assertEqual(
            RP.US_FEDERAL_RULES["ordinary_mfj"],
            ((0.0, 0.10), (24_800.0, 0.12), (100_800.0, 0.22),
             (211_400.0, 0.24), (403_550.0, 0.32), (512_450.0, 0.35),
             (768_700.0, 0.37)))
        self.assertEqual(RP.US_FEDERAL_RULES["std_deduction_single"], 16_100.0)
        self.assertEqual(RP.US_FEDERAL_RULES["std_deduction_mfj"], 32_200.0)
        self.assertEqual(
            RP.US_FEDERAL_RULES["ltcg_single"],
            ((0.0, 0.0), (49_450.0, 0.15), (545_500.0, 0.20)))
        self.assertEqual(
            RP.US_FEDERAL_RULES["ltcg_mfj"],
            ((0.0, 0.0), (98_900.0, 0.15), (613_700.0, 0.20)))
        self.assertEqual(RP.CONTRIBUTION_LIMIT_RULES["pretax_401k_limit_y1"],
                         24_500.0)
        self.assertEqual(RP.CONTRIBUTION_LIMIT_RULES["roth_ira_limit_y1"],
                         7_500.0)
        self.assertEqual(RP.CONTRIBUTION_LIMIT_RULES["hsa_limit_y1"], 4_400.0)
        self.assertEqual(RP.CONTRIBUTION_LIMIT_RULES["irs_limit_growth"], 0.03)
        self.assertIn("not an IRS value", next(
            row for row in RP.canonical_pack_payload()["components"]
            if row["id"] == "contribution_limits")["provenance"]["conversion"])

        aca = ACAParams(scenario=ACAScenario.PRE_IRA_CLIFF)
        fpl = aca.fpl_single_y0
        below_100 = compute_aca_premium_paid(
            10_000.0, 0.5 * fpl, 1.0, aca)
        self.assertAlmostEqual(below_100, 0.0996 * 0.5 * fpl, places=8)
        at_cliff = compute_aca_premium_paid(
            10_000.0, 4.0 * fpl, 1.0, aca)
        self.assertAlmostEqual(at_cliff, 0.0996 * 4.0 * fpl, places=8)
        above_cliff = compute_aca_premium_paid(
            10_000.0, 4.0 * fpl + 0.01, 1.0, aca)
        self.assertEqual(above_cliff, 10_000.0)
        aca_row = next(row for row in RP.canonical_pack_payload()["components"]
                       if row["id"] == "aca_marketplace")
        self.assertIn("not the full IRS piecewise", aca_row["provenance"]["conversion"])

        ssa_row = next(row for row in RP.canonical_pack_payload()["components"]
                       if row["id"] == "ssa_benefit_rules")
        self.assertIn("birth cohort", ssa_row["provenance"]["conversion"])

    def test_v94_early_penalty_is_pack_bound_and_threshold_exact(self):
        import fire_rule_pack as RP
        import fire_v9_4_model as V94
        from fire_v6_model import AccountStack, TaxParams

        self.assertEqual(V94.EARLY_WD_PENALTY_AGE,
                         RP.US_FEDERAL_RULES["early_withdrawal_age"])
        self.assertEqual(V94.EARLY_WD_PENALTY_RATE,
                         RP.US_FEDERAL_RULES["early_withdrawal_rate"])
        source = pathlib.Path(ROOT, "engine", "fire_v9_4_model.py").read_text(
            encoding="utf-8")
        self.assertNotIn("EARLY_WD_PENALTY_AGE = 59.5", source)
        self.assertNotIn("EARLY_WD_PENALTY_RATE = 0.10", source)

        tax = TaxParams(withdrawal_tax_traditional=0.0,
                        withdrawal_tax_taxable=0.0,
                        withdrawal_tax_hsa=0.0)
        _, actual_below, penalty_below = V94.withdraw_with_seasoning_v94(
            AccountStack(pretax_401k=1_000.0), 100.0, tax, 0.0, 59.4)
        _, actual_at, penalty_at = V94.withdraw_with_seasoning_v94(
            AccountStack(pretax_401k=1_000.0), 100.0, tax, 0.0, 59.5)
        self.assertAlmostEqual(actual_below, 100.0)
        self.assertAlmostEqual(actual_at, 100.0)
        self.assertAlmostEqual(
            penalty_below,
            (100.0 / (1.0 - RP.US_FEDERAL_RULES["early_withdrawal_rate"]))
            * RP.US_FEDERAL_RULES["early_withdrawal_rate"])
        self.assertEqual(penalty_at, 0.0)

    def test_contribution_pack_evidence_covers_active_spouse_stream(self):
        import fire_rule_pack as RP

        limits = RP.CONTRIBUTION_LIMIT_RULES

        def row(config, as_of="2026-12-31"):
            descriptor = RP.rule_pack_for_run(config, as_of=as_of)
            return descriptor, next(item for item in descriptor["components"]
                                    if item["id"] == "contribution_limits")

        spouse_only = cfg0()
        spouse_only["state"]["start_age"] = 60
        spouse_only["contributions"].update({
            "base_salary_pre": 0.0, "bonus_pre": 0.0, "ot_income_pre": 0.0,
            "pretax_401k_limit_y1": 0.0, "roth_ira_limit_y1": 0.0,
            "hsa_limit_y1": 0.0,
        })
        spouse_only["household"].update({
            "enabled": True, "spouse_base_salary_pre": 120_000.0,
            "spouse_bonus_pre": 0.0,
            "spouse_pretax_401k_limit_y1": limits["pretax_401k_limit_y1"],
            "spouse_roth_ira_limit_y1": limits["roth_ira_limit_y1"],
            "spouse_hsa_limit_y1": limits["hsa_limit_y1"],
        })
        descriptor, component = row(spouse_only)
        self.assertEqual(component["status"], "current")
        self.assertEqual(component["effective_source"], "matches_pack_value")
        self.assertEqual(component["mismatched_fields"], [])
        self.assertIn("household.spouse_pretax_401k_limit_y1",
                      component["configured_values"])
        self.assertNotIn("contributions.pretax_401k_limit_y1",
                         component["configured_values"])
        self.assertEqual(descriptor["status"], "current")

        spouse_mismatch = copy.deepcopy(spouse_only)
        spouse_mismatch["household"]["spouse_pretax_401k_limit_y1"] += 1.0
        _, component = row(spouse_mismatch)
        self.assertEqual(component["status"], "review_required")
        self.assertEqual(component["effective_source"],
                         "user_or_legacy_override")
        self.assertEqual(component["mismatched_fields"],
                         ["household.spouse_pretax_401k_limit_y1"])

        both = cfg0()
        both["household"].update({
            "enabled": True, "spouse_base_salary_pre": 120_000.0,
            "spouse_pretax_401k_limit_y1": limits["pretax_401k_limit_y1"],
            "spouse_roth_ira_limit_y1": limits["roth_ira_limit_y1"],
            "spouse_hsa_limit_y1": limits["hsa_limit_y1"],
        })
        both["household"]["spouse_pretax_401k_limit_y1"] += 1.0
        _, component = row(both)
        self.assertEqual(component["status"], "review_required")
        self.assertEqual(component["mismatched_fields"],
                         ["household.spouse_pretax_401k_limit_y1"])

        disabled_spouse = cfg0()
        disabled_spouse["household"].update({
            "enabled": False, "spouse_base_salary_pre": 120_000.0,
            "spouse_pretax_401k_limit_y1": limits["pretax_401k_limit_y1"] + 99,
        })
        _, component = row(disabled_spouse)
        self.assertEqual(component["status"], "current")
        self.assertEqual(component["effective_source"], "matches_pack_value")
        self.assertEqual(component["mismatched_fields"], [])

        dormant = cfg0()
        dormant["contributions"].update({
            "base_salary_pre": 0.0, "bonus_pre": 0.0, "ot_income_pre": 0.0,
            "pretax_401k_limit_y1": 0.0, "roth_ira_limit_y1": 0.0,
            "hsa_limit_y1": 0.0,
        })
        dormant["household"]["enabled"] = False
        _, component = row(dormant)
        self.assertEqual(component["status"], "not_used_at_run")
        self.assertEqual(component["effective_source"], "matches_pack_value")
        self.assertEqual(component["mismatched_fields"], [])

        stale = copy.deepcopy(spouse_mismatch)
        _, component = row(stale, as_of="2027-01-01")
        self.assertEqual(component["status"], "stale")
        self.assertEqual(component["review_status"], "stale")

    def test_flat_early_penalty_marks_federal_pack_applicable_conservatively(self):
        import fire_rule_pack as RP

        flat = cfg0()
        deadline = RP.rule_pack_for_run(flat, as_of="2026-12-31")
        deadline_rows = {row["id"]: row for row in deadline["components"]}
        self.assertEqual(deadline_rows["us_federal_tax"]["status"], "current")
        after = RP.rule_pack_for_run(flat, as_of="2027-01-01")
        after_rows = {row["id"]: row for row in after["components"]}
        self.assertEqual(after_rows["us_federal_tax"]["status"], "stale")
        self.assertEqual(after_rows["us_federal_tax"]["applicability"],
                         "applicable")

        late = cfg0()
        late["state"].update({"start_age": 60, "accum_years": 10})
        late_rows = {
            row["id"]: row for row in RP.rule_pack_for_run(
                late, as_of="2027-01-01")["components"]
        }
        self.assertEqual(late_rows["us_federal_tax"]["status"],
                         "not_used_at_run")

        initial_fi = cfg0()
        initial_fi["state"].update({"accum_years": 0, "start_age": 40})
        initial_rows = {
            row["id"]: row for row in RP.rule_pack_for_run(
                initial_fi, as_of="2027-01-01")["components"]
        }
        self.assertEqual(initial_rows["us_federal_tax"]["applicability"],
                         "applicable")

    def test_ssa_full_series_is_owned_by_the_canonical_pack(self):
        import fire_rule_pack as RP
        import ssa_import as SSA

        source = next(row for row in RP.canonical_pack_payload()["components"]
                      if row["id"] == "ssa_statement_import")["values"]
        awi = {int(year): float(value) for year, value in source["awi_series"]}
        cola = {int(year): float(value) for year, value in source["cola_series"]}
        self.assertEqual(SSA.AWI, awi)
        self.assertEqual(SSA.COLA, cola)
        self.assertEqual(len(SSA.AWI), 74)
        self.assertEqual(len(SSA.COLA), 51)
        self.assertEqual(SSA.AWI[1951], 2799.16)
        self.assertEqual(SSA.COLA[2025], 0.028)
        # The consumer must derive the table, not quietly retain a second
        # hand-maintained copy that could drift from the content hash.
        ssa_source = pathlib.Path(ROOT, "server", "ssa_import.py").read_text(
            encoding="utf-8")
        self.assertNotIn("2799.16", ssa_source)
        self.assertNotIn("0.028", ssa_source)

    def test_rule_pack_receipt_malformed_or_unknown_fails_closed(self):
        import build_report
        import fire_rule_pack as RP

        result = ENG.run_full(cfg0(), 20, SEED, 10)
        valid = copy.deepcopy(result["meta"]["rule_pack"])
        valid_html = build_report.build(result, {"lang": "en"})
        self.assertNotIn("rule vintage unrecorded", valid_html)
        self.assertIn("within the app review window", valid_html)
        mutations = []
        missing_hash = copy.deepcopy(valid)
        missing_hash.pop("content_sha256")
        mutations.append(missing_hash)
        unknown_status = copy.deepcopy(valid)
        unknown_status["status"] = "future_status"
        mutations.append(unknown_status)
        partial = copy.deepcopy(valid)
        partial["components"].pop()
        mutations.append(partial)
        malformed_row = copy.deepcopy(valid)
        malformed_row["components"][0].pop("effective_source")
        mutations.append(malformed_row)
        inconsistent = copy.deepcopy(valid)
        inconsistent["conclusion_status"] = "stale"
        mutations.append(inconsistent)

        # Internal consistency is part of the frozen evidence contract; a
        # format-valid receipt must not become a current claim by changing only
        # one field.  Use a deadline-day receipt as the positive base so each
        # mutation is a distinct contradiction rather than merely another
        # already-stale result.
        # A late-start positive control keeps the federal-tax component
        # genuinely not-used; the new conservative early-penalty rule makes
        # the default age-30 fixture applicable even when progressive/true
        # tax is off.
        current_cfg = cfg0()
        current_cfg["state"]["start_age"] = 60
        current = RP.rule_pack_for_run(current_cfg, as_of="2026-12-31")
        current_candidate = copy.deepcopy(result)
        current_candidate["meta"]["rule_pack"] = copy.deepcopy(current)
        self.assertTrue(build_report._valid_rule_pack_receipt(current))
        numeric_schema = copy.deepcopy(current)
        numeric_schema["schema_version"] = 1.0
        self.assertTrue(build_report._valid_rule_pack_receipt(numeric_schema))
        moved_past_due = copy.deepcopy(current)
        moved_past_due["evaluated_on"] = "2027-01-01"
        mutations.append(moved_past_due)
        year_zero = copy.deepcopy(current)
        year_zero["evaluated_on"] = "0000-12-31"
        mutations.append(year_zero)
        boolean_schema = copy.deepcopy(current)
        boolean_schema["schema_version"] = True
        mutations.append(boolean_schema)
        wrong_prefix = copy.deepcopy(current)
        wrong_prefix["pack_id"] = "us-offline-" + (
            "0" * 16 if current["content_sha256"][:16] != "0" * 16 else "f" * 16)
        mutations.append(wrong_prefix)
        applicable = next(row for row in current["components"]
                          if row["applicability"] == "applicable")
        row_state_mismatch = copy.deepcopy(current)
        next(row for row in row_state_mismatch["components"]
             if row["id"] == applicable["id"])["review_status"] = "stale"
        mutations.append(row_state_mismatch)
        not_used = next(row for row in current["components"]
                        if row["applicability"] == "not_used_at_run")
        not_used_mismatch = copy.deepcopy(current)
        next(row for row in not_used_mismatch["components"]
             if row["id"] == not_used["id"])["status"] = "stale"
        mutations.append(not_used_mismatch)
        duplicate_ids = copy.deepcopy(current)
        self.assertTrue(duplicate_ids["applicable_component_ids"])
        duplicate_ids["applicable_component_ids"].append(
            duplicate_ids["applicable_component_ids"][0])
        mutations.append(duplicate_ids)
        nonstring_id = copy.deepcopy(current)
        nonstring_id["components"][0]["id"] = {"unexpected": True}
        mutations.append(nonstring_id)
        nonstring_field = copy.deepcopy(current)
        nonstring_field["components"][0]["mismatched_fields"] = [{"unexpected": True}]
        mutations.append(nonstring_field)
        nonstring_array_id = copy.deepcopy(current)
        nonstring_array_id["applicable_component_ids"] = [{"unexpected": True}]
        mutations.append(nonstring_array_id)
        for receipt in mutations:
            candidate = copy.deepcopy(result)
            candidate["meta"]["rule_pack"] = receipt
            self.assertFalse(build_report._valid_rule_pack_receipt(receipt))
            html = build_report.build(candidate, {"lang": "en"})
            self.assertIn("rule vintage unrecorded", html)
            self.assertNotIn("within the app review window", html)
        current_html = build_report.build(current_candidate, {"lang": "en"})
        self.assertIn("within the app review window", current_html)

    def test_run_result_binds_pack_without_entering_numeric_replay_hash(self):
        import fire_rule_pack as RP
        from persistence import deterministic_result_sha256

        result = ENG.run_full(cfg0(), 20, SEED, 10)
        descriptor = result["meta"]["rule_pack"]
        self.assertEqual(descriptor["pack_id"], RP.RULE_PACK_ID)
        self.assertEqual(descriptor["content_sha256"], RP.RULE_PACK_SHA256)
        without_display_metadata = copy.deepcopy(result)
        without_display_metadata["meta"].pop("rule_pack")
        self.assertEqual(deterministic_result_sha256(result),
                         deterministic_result_sha256(without_display_metadata))
        dated = copy.deepcopy(result)
        dated["meta"]["rule_pack"] = RP.rule_pack_for_run(
            cfg0(), as_of="2026-12-31")
        self.assertNotEqual(result["meta"]["rule_pack"]["evaluated_on"],
                            dated["meta"]["rule_pack"]["evaluated_on"])
        self.assertEqual(deterministic_result_sha256(result),
                         deterministic_result_sha256(dated))


class TestTrueTaxEngine(unittest.TestCase):
    """T3 golden cases for the E1 true tax engine (2026 tables), plus the
    integration contracts: OFF = bit-identical, ON conserves cash flow."""

    def test_unit_goldens(self):
        from fire_tax_true import (ordinary_tax_real, ltcg_tax_real,
                                   ss_taxable_amount, rmd_required,
                                   irmaa_annual_surcharge_real, TrueTaxParams)
        self.assertAlmostEqual(ordinary_tax_real(50_000, False), 5_752.00, places=2)
        self.assertAlmostEqual(ordinary_tax_real(100_000, True), 11_504.00, places=2)
        self.assertAlmostEqual(ss_taxable_amount(24_000, 30_000, False), 11_300.0, places=2)
        self.assertEqual(ss_taxable_amount(24_000, 5_000, False), 0.0)
        self.assertAlmostEqual(ltcg_tax_real(30_000, 30_000, False), 1_582.50, places=2)
        self.assertAlmostEqual(rmd_required(500_000, 75, TrueTaxParams(rmd_enabled=True)),
                               500_000 / 24.6, places=2)
        self.assertAlmostEqual(rmd_required(500_000, 101, TrueTaxParams(rmd_enabled=True)),
                               500_000 / 6.0, places=2)
        self.assertEqual(irmaa_annual_surcharge_real(150_000, False, 1), 2_884.8)
        self.assertEqual(irmaa_annual_surcharge_real(90_000, False, 1), 0.0)
        for mfj, thresholds, exact, below_tier, next_tier in (
                (False, (109_000, 137_000, 171_000, 205_000, 500_000),
                 (0.0, 1_148.4, 2_884.8, 4_620.0, 6_936.0),
                 (0.0, 1_148.4, 2_884.8, 4_620.0, 6_355.2),
                 (1_148.4, 2_884.8, 4_620.0, 6_355.2, 6_936.0)),
                (True, (218_000, 274_000, 342_000, 410_000, 750_000),
                 (0.0, 1_148.4, 2_884.8, 4_620.0, 6_936.0),
                 (0.0, 1_148.4, 2_884.8, 4_620.0, 6_355.2),
                 (1_148.4, 2_884.8, 4_620.0, 6_355.2, 6_936.0)),
        ):
            for index, (threshold, exact_surcharge, below_surcharge) in enumerate(
                    zip(thresholds, exact, below_tier)):
                below = math.nextafter(threshold, -math.inf)
                above = math.nextafter(threshold, math.inf)
                self.assertEqual(
                    irmaa_annual_surcharge_real(below, mfj, 1), below_surcharge)
                self.assertEqual(
                    irmaa_annual_surcharge_real(threshold, mfj, 1),
                    exact_surcharge)
                self.assertEqual(
                    irmaa_annual_surcharge_real(above, mfj, 1),
                    next_tier[index])

    def test_standard_deduction_shelters_ltcg(self):
        from fire_tax_true import TrueTaxParams, solve_retirement_year
        from fire_v6_model import AccountStack
        r = solve_retirement_year(
            AccountStack(taxable=50_000), need_after_tax_nominal=50_000,
            ss_gross_nominal=0, conversions_nominal=0, roth_locked=0,
            age=67, cpi=1.0,
            p=TrueTaxParams(enabled=True, taxable_gain_fraction=1.0,
                            rmd_enabled=False, irmaa_enabled=False))
        self.assertEqual(r["tax_total"], 0.0)
        self.assertEqual(r["delivered"], 50_000.0)

    def test_rmd_uses_prior_december_balance(self):
        from fire_tax_true import TrueTaxParams, solve_retirement_year
        from fire_v6_model import AccountStack
        r = solve_retirement_year(
            AccountStack(pretax_401k=1_000_000), need_after_tax_nominal=0,
            ss_gross_nominal=0, conversions_nominal=0, roth_locked=0,
            age=75, cpi=1.0, p=TrueTaxParams(enabled=True),
            rmd_balance_prior_year_end=500_000)
        self.assertAlmostEqual(r["gross_wd"], 500_000 / 24.6, places=6)

    def test_off_is_bit_identical(self):
        c = cfg0()
        c["tax_true"]["rmd_age"] = 73          # knobs without enabled: no-op
        s = ENG.summary(c, 800, SEED, False)
        self.assertAlmostEqual(s["terminal_real_p50"], GOLDEN_TERMINAL, places=3)

    def test_on_conserves_cash_flow(self):
        import numpy as np
        import fire_v9_8_model as V98
        from fire_v95_actual_baseline import match_excludes_bonus
        c = cfg0(); c["tax_true"]["enabled"] = True
        kw = ENG.build_kwargs(c, False); cfgobj = kw.pop("config")
        errs = []
        with match_excludes_bonus():
            rng = np.random.default_rng(SEED)
            for _ in range(60):
                r = V98.simulate_lifecycle_v98(config=cfgobj, rng=rng, **kw)
                wd = r.get("withdrawal") or {}
                if r.get("reached_fire") and wd:
                    errs.append(wd.get("true_tax_flow_err", 0.0))
        self.assertTrue(errs)
        self.assertLess(max(errs), 1e-6)

    def test_rmd_age_moves_tax_and_after_tax_results(self):
        c = cfg0(); c["tax_true"]["enabled"] = True
        a = ENG.summary(c, 600, SEED, False)
        c2 = cfg0(); c2["tax_true"]["enabled"] = True; c2["tax_true"]["rmd_age"] = 72
        b = ENG.summary(c2, 600, SEED, False)
        # The conditional pre-tax terminal P50 excludes failed/nonpositive
        # paths and may coincide when the successful population changes. The
        # RMD mechanism is instead pinned to tax and after-tax wealth effects.
        self.assertNotEqual(a["true_tax_p50"], b["true_tax_p50"])
        self.assertNotEqual(a["terminal_after_tax_real_p50"],
                            b["terminal_after_tax_real_p50"])

        from fire_tax_true import TrueTaxParams, solve_retirement_year
        from fire_v6_model import AccountStack
        common = dict(
            accounts=AccountStack(pretax_401k=500_000),
            need_after_tax_nominal=0.0, ss_gross_nominal=0.0,
            conversions_nominal=0.0, roth_locked=0.0, age=73, cpi=1.0,
        )
        forced = solve_retirement_year(
            p=TrueTaxParams(enabled=True, rmd_enabled=True, rmd_age=72,
                            irmaa_enabled=False), **common)
        not_yet = solve_retirement_year(
            p=TrueTaxParams(enabled=True, rmd_enabled=True, rmd_age=75,
                            irmaa_enabled=False), **common)
        self.assertGreater(forced["gross_wd"], 0.0)
        self.assertGreater(forced["tax_total"], 0.0)
        self.assertLess(forced["accounts"].pretax_401k, 500_000.0)
        self.assertEqual(not_yet["gross_wd"], 0.0)
        self.assertEqual(not_yet["tax_total"], 0.0)

    def test_real_tax_summary_uses_year_specific_deflation(self):
        import numpy as np
        c = cfg0()
        c["tax_true"]["enabled"] = True
        results = ENG._run(c, 80, SEED, False)
        values = [r["withdrawal"]["true_tax_total_real"] for r in results
                  if r.get("reached_fire") and r.get("withdrawal")]
        self.assertTrue(values)
        expected = float(np.percentile(values, 50))
        self.assertAlmostEqual(ENG.summary(c, 80, SEED, False)["true_tax_p50"],
                               expected, places=9)

    def test_ss_zero_paths_are_not_dropped(self):
        c = cfg0()
        c["social_security"]["enabled"] = False
        self.assertEqual(ENG.summary(c, 80, SEED, False)["ss_p50"], 0.0)

    def test_true_tax_does_not_refund_conversion_tax_after_relocation(self):
        c = cfg0()
        c["mortality"]["enabled"] = False
        c["initial"]["taxable"] = 5_000_000
        c["relocation"].update({"enabled": True, "relocation_age": 30})
        off = ENG.summary(c, 20, SEED, True)
        c["tax_true"]["enabled"] = True
        self.assertEqual(ENG.summary(c, 20, SEED, True), off)

    @staticmethod
    def _irmaa_trace(start_age=60, inflations=None, magi_by_age=None,
                     solver_sequences=None, household=False,
                     kill_spouse_age=None, irmaa_enabled=True,
                     irmaa_return=0.0):
        """Run a deterministic retirement seam and record IRMAA inputs.

        The solver stub keeps account state/cash flow intact while supplying
        age-labelled MAGI records. This makes the causal source year and
        filing-status contract observable without adding a public result field.
        """
        import numpy as np
        import fire_v9_8_model as V98

        inflations = list(inflations or [0.0] * 7)
        magi_by_age = dict(magi_by_age or {})
        solver_sequences = {
            int(age): list(values)
            for age, values in (solver_sequences or {}).items()
        }
        c = cfg0()
        c["state"].update({"start_age": int(start_age),
                            "retire_horizon": len(inflations)})
        c["medical"].update({"medicare_age": 65, "premium_aca": 0.0,
                              "premium_medicare": 0.0})
        c["social_security"]["enabled"] = False
        c["tax_true"].update({"enabled": True,
                              "irmaa_enabled": bool(irmaa_enabled)})
        c["mortality"]["enabled"] = kill_spouse_age is not None
        if household:
            c["household"].update({"enabled": True,
                                    "spouse_age_offset": 1})

        kw = ENG.build_kwargs(c, False)
        direct = {name: kw[name] for name in (
            "state", "rule", "glide_path", "relocation", "sh_property",
            "medical", "aca", "mortality", "roth_ladder", "ss", "ftc",
            "tax_us", "tax_cn", "china_healthcare", "ss_nra",
            "income_streams", "tax_true")}
        solve_calls = []
        irmaa_calls = []
        solve_counts = {}

        def fake_solver(accounts, need_after_tax_nominal, ss_gross_nominal,
                        conversions_nominal, roth_locked, age, cpi, params,
                        rmd_balance_prior_year_end=None, gain_fraction=None,
                        **_forward_compatible):
            # `gain_fraction` is named rather than swallowed because it is the
            # one Phase 3 added and these traces care what the solver is told.
            # `**_forward_compatible` is here for a duller reason: this stub
            # stands in for a real function, and when the real signature grew
            # the stub raised TypeError instead of failing an assertion --
            # which nothing noticed, because this file is not in the gate's
            # SUITES. It is now.
            age = int(age)
            index = solve_counts.get(age, 0)
            solve_counts[age] = index + 1
            values = solver_sequences.get(age)
            if values is None:
                values = [float(magi_by_age.get(age, 0.0))]
            magi = float(values[min(index, len(values) - 1)])
            solve_calls.append((age, index, magi))
            return {
                "accounts": accounts.copy(),
                "tax_total": 0.0,
                "penalty": 0.0,
                "ss_taxable": 0.0,
                "magi_agi_nominal": magi,
                "magi_aca_nominal": magi,
                "delivered": float(max(0.0, need_after_tax_nominal)),
                "deposit_back": 0.0,
                "gross_wd": float(max(0.0, need_after_tax_nominal)),
                "flow_err": 0.0,
                "shortfall": 0.0,
                # Phase 3 keys. This trace does not exercise cost basis or the
                # dividend drag, but the production loop reads these every
                # year, and a stub that omits them fails with KeyError -- an
                # error, not an assertion, which says nothing about the
                # behaviour under test.
                "taxable_wd": 0.0,
                "gain_fraction_used": 0.0,
                "ordinary_taxable_real": 0.0,
            }

        def fake_irmaa(magi_real, mfj, persons):
            irmaa_calls.append((float(magi_real), bool(mfj), int(persons)))
            return float(irmaa_return)

        def fake_mortality_rate(age, _params):
            if kill_spouse_age is None or int(age) != int(kill_spouse_age):
                return 0.0
            # In the household fixture, only the spouse reaches the target
            # age first; the primary remains alive for the premium year.
            if household:
                return 1.0 if getattr(_params, "sex_label", None) == "female" else 0.0
            return 1.0

        rng = np.random.default_rng(18_003)
        with (ENG._household_ctx(c),
              mock.patch.object(V98, "solve_retirement_year",
                                side_effect=fake_solver),
              mock.patch.object(V98, "irmaa_annual_surcharge_real",
                                side_effect=fake_irmaa),
              mock.patch.object(V98, "annual_mortality_rate",
                                side_effect=fake_mortality_rate)):
            result = V98.simulate_retirement_v98(
                starting_accounts=kw["initial"].copy(),
                starting_age=int(start_age),
                fire_year_cpi_cumulative=1.0,
                equity_returns=[0.0] * len(inflations),
                bond_returns=[0.0] * len(inflations),
                inflations=inflations,
                eldercare_events=[], inheritance_event=None,
                rng=rng, **direct)
        return {
            "result": result,
            "irmaa_calls": irmaa_calls,
            "solve_calls": solve_calls,
            "rng_tail": rng.random(8),
        }

    def test_irmaa_lookback_uses_high_t_minus_2_magi_not_low_current_magi(self):
        trace = self._irmaa_trace(
            inflations=[0.0, 0.0, 0.10, 0.10, 0.0, 0.0, 0.0],
            magi_by_age={63: 300_000.0, 65: 0.0})
        self.assertTrue(trace["irmaa_calls"])
        # At premium age 65, the source is tax year 63 and the denominator is
        # the premium-year CPI (1.21), not source-year CPI (1.10).
        self.assertAlmostEqual(trace["irmaa_calls"][0][0],
                               300_000.0 / 1.21, places=8)

    def test_irmaa_lookback_reverses_when_current_magi_is_high(self):
        source_high = self._irmaa_trace(
            magi_by_age={63: 300_000.0, 65: 0.0})["irmaa_calls"][0][0]
        current_high = self._irmaa_trace(
            magi_by_age={63: 0.0, 65: 300_000.0})["irmaa_calls"][0][0]
        self.assertGreater(source_high, current_high)
        self.assertEqual(current_high, 0.0)

    def test_irmaa_history_records_final_magi_after_second_solve(self):
        trace = self._irmaa_trace(
            solver_sequences={65: [111_000.0, 222_000.0]},
            magi_by_age={63: 0.0}, irmaa_return=50.0)
        age_67_calls = [magi for age, _idx, magi in trace["solve_calls"]
                        if age == 67]
        self.assertGreaterEqual(len(age_67_calls), 1)
        # The age-67 IRMAA call is the age-65 final solve, not its preliminary
        # value. With CPI=1 this is directly observable in the trace.
        self.assertEqual(trace["irmaa_calls"][2][0], 222_000.0)

    def test_irmaa_history_carries_tax_year_filing_status(self):
        trace = self._irmaa_trace(
            household=True, kill_spouse_age=65,
            magi_by_age={63: 300_000.0, 65: 0.0})
        self.assertTrue(trace["irmaa_calls"])
        # Spouse dies at primary age 64 (spouse age 65); the premium-year
        # filing is single, but the age-63 source record was joint.
        self.assertTrue(trace["irmaa_calls"][0][1])
        self.assertEqual(trace["irmaa_calls"][0][2], 1)

    def test_irmaa_missing_history_falls_back_and_disabled_is_path_local(self):
        import numpy as np
        fallback = self._irmaa_trace(
            start_age=65, inflations=[0.10, 0.0],
            magi_by_age={66: 300_000.0})
        self.assertAlmostEqual(fallback["irmaa_calls"][0][0],
                               300_000.0 / 1.10, places=8)
        disabled = self._irmaa_trace(
            magi_by_age={63: 300_000.0}, irmaa_enabled=False)
        self.assertEqual(disabled["irmaa_calls"], [])
        repeated = self._irmaa_trace(
            magi_by_age={63: 300_000.0})
        np.testing.assert_array_equal(
            repeated["rng_tail"],
            self._irmaa_trace(magi_by_age={63: 300_000.0})["rng_tail"])


class TestACA2026(unittest.TestCase):
    def test_default_restores_cliff_and_uses_household_fpl(self):
        from fire_v9_1_model import (ACAParams, ACAScenario,
                                     compute_aca_premium_paid)
        p = ACAParams()
        self.assertEqual(p.scenario, ACAScenario.PRE_IRA_CLIFF)
        # Single-person 400% FPL = $62,600: one dollar above pays full premium.
        self.assertEqual(compute_aca_premium_paid(20_000, 62_601, 1.0, p),
                         20_000.0)
        # The same income remains below the two-person cliff ($84,600).
        self.assertLess(compute_aca_premium_paid(
            20_000, 62_601, 1.0, p, household_size=2), 20_000.0)


class TestStrategyLibrary(unittest.TestCase):
    """E3 withdrawal-strategy library: unit goldens, engine-compat (relocation
    re-seed) tolerance, and sharpened directional contracts.

    NOTE on the VPW contract: under the DEFAULT config VPW's min consumption
    is NOT below GK's (VPW's floating budget starts ~20% above GK's SWR-bound
    budget and the defaults are too safe to separate them), so the min-vs-GK
    comparison is pinned under an aggressive swr_pref where the gap is wide
    (~25%). Solvency == 1.0 is the mathematical contract (rate-capped
    percent-of-portfolio withdrawals cannot deplete even after tax gross-up).
    """

    @classmethod
    def setUpClass(cls):
        cls.gk = summ(cfg0())

    @staticmethod
    def _typed(rt, **rule_kw):
        c = cfg0()
        c["rule"]["type"] = rt
        c["rule"].update(rule_kw)
        return c

    def test_unit_goldens(self):
        from fire_rules_x import VPWRule, FloorUpsideRule, ABWRule
        import fire_v9_8_model as V98
        r = VPWRule(expected_real_return=0.04, depletion_age=100)
        st = V98._init_rule(r, 1_000_000, 40_000, 0.04, 1.0)
        t, _ = r.compute_target_withdrawal(5, 65, 1_000_000, 0.03, 1.30, st)
        self.assertAlmostEqual(t, 53_577.322368, places=5)   # 4%/35y annuity
        f = FloorUpsideRule(floor_ratio=0.85)
        st = V98._init_rule(f, 1_000_000, 40_000, 0.04, 1.0)
        tc, _ = f.compute_target_withdrawal(3, 55, 400_000, 0.05, 1.05, st)
        self.assertAlmostEqual(tc, 35_700.0, places=6)       # floor binds
        tb, _ = f.compute_target_withdrawal(3, 55, 2_000_000, 0.0, 1.0, st)
        self.assertAlmostEqual(tb, 80_000.0, places=6)       # upside binds
        a = ABWRule(expected_real_return=0.04, horizon_age=100, bequest_frac=0.5)
        st = V98._init_rule(a, 1_000_000, 40_000, 0.04, 1.0)
        ta, _ = a.compute_target_withdrawal(10, 60, 1_200_000, 0.02, 1.2, st)
        self.assertAlmostEqual(ta, 54_314.093595, places=5)  # bequest carved out

    def test_reseed_tolerance(self):
        """The relocation branch re-initializes any rule mid-flight and
        restores guardrail_triggers; first post-re-seed call arrives at
        year >= 1. Every library rule must survive that exact sequence."""
        from fire_v9_1_model import FixedRealRule
        from fire_rules_x import VPWRule, FloorUpsideRule, ABWRule
        import fire_v9_8_model as V98
        for rule in (FixedRealRule(), VPWRule(), FloorUpsideRule(), ABWRule()):
            st = V98._init_rule(rule, 1_000_000, 40_000, 0.04, 1.0)
            prev = st.get("guardrail_triggers", 0)
            st = V98._init_rule(rule, 500_000, 30_000, 0.06, 1.4)
            st["guardrail_triggers"] = prev
            t, st2 = rule.compute_target_withdrawal(3, 60, 480_000, 0.03, 1.5, st)
            self.assertGreater(t, 0.0, rule.name)
            self.assertEqual(st2.get("guardrail_triggers", 0), prev, rule.name)

    def test_rule_type_gk_is_bit_identical(self):
        c = cfg0()
        c["rule"]["type"] = "gk"
        self.assertEqual(summ(c)["terminal_real_p50"],
                         self.gk["terminal_real_p50"])
        with self.assertRaises(ValueError):
            ENG.build_kwargs(self._typed("nope"), False)

    def test_vpw_cannot_deplete_but_cuts_deeper(self):
        vpw = summ(self._typed("vpw"))
        self.assertEqual(vpw["post_fire_solvency"], 1.0)
        # aggressive-FIRE separation: thin portfolio => VPW tracks crashes
        # 1:1 while GK cuts at most 10%/yr behind guardrails
        ch = cfg0(); ch["state"]["swr_pref"] = 0.055
        cv = cfg0(); cv["state"]["swr_pref"] = 0.055; cv["rule"]["type"] = "vpw"
        gk_h, vpw_h = summ(ch), summ(cv)
        self.assertEqual(vpw_h["post_fire_solvency"], 1.0)
        self.assertLess(vpw_h["min_cons_p50"], gk_h["min_cons_p50"])

    def test_floor_upside_floor_holds(self):
        """With the ACA premium leg zeroed (consumed == rule target), the
        floor rule's worst real consumption must stay >= floor_ratio × the
        fixed-real budget — path-for-path the same initial budget."""
        cf = cfg0(); cf["medical"]["premium_aca"] = 0.0
        cf["rule"]["type"] = "fixed_real"
        cu = cfg0(); cu["medical"]["premium_aca"] = 0.0
        cu["rule"]["type"] = "floor_upside"; cu["rule"]["floor_ratio"] = 0.85
        fr, fu = summ(cf), summ(cu)
        self.assertGreaterEqual(fu["min_cons_p50"],
                                0.85 * fr["min_cons_p50"] - 1.0)

    def test_abw_spends_down(self):
        abw = summ(self._typed("abw"))
        self.assertLess(abw["terminal_real_p50"], self.gk["terminal_real_p50"])
        self.assertGreater(abw["cons_p50"], self.gk["cons_p50"])


class TestReturns2(unittest.TestCase):
    """E4 returns 2.0: default-off bit-identity, historical-table pins,
    exact transition contracts, AR(1) directionality, and integration."""

    def test_off_is_bit_identical(self):
        c = cfg0()                          # knobs without model: no-op
        c["returns"]["persistence"] = 0.01
        c["returns"]["block_years"] = 1
        c["returns"]["inflation_ar1"] = 0.9
        s = ENG.summary(c, 800, SEED, False)
        self.assertAlmostEqual(s["terminal_real_p50"], GOLDEN_TERMINAL, places=3)

    def test_hist_table_pins(self):
        from fire_returns_x import HIST_EQUITY, HIST_BOND, HIST_CPI
        for t in (HIST_EQUITY, HIST_BOND, HIST_CPI):
            self.assertEqual(len(t), 97)     # 1928..2024 inclusive
        self.assertAlmostEqual(HIST_EQUITY[0], 0.4381)          # 1928
        self.assertAlmostEqual(HIST_EQUITY[1931 - 1928], -0.4384)
        self.assertAlmostEqual(HIST_EQUITY[2008 - 1928], -0.3655)
        self.assertAlmostEqual(HIST_EQUITY[-1], 0.2488)         # 2024
        self.assertAlmostEqual(HIST_BOND[1982 - 1928], 0.3281)
        self.assertAlmostEqual(HIST_BOND[2022 - 1928], -0.1783)
        self.assertAlmostEqual(HIST_CPI[1980 - 1928], 0.135)
        self.assertAlmostEqual(HIST_CPI[2022 - 1928], 0.080)
        import numpy as np
        self.assertTrue(0.025 < np.mean(HIST_CPI) < 0.035)

    def test_blocks_only_resamples_history(self):
        import numpy as np
        from fire_returns_x import (_sample_blocks, ReturnsXParams,
                                    HIST_EQUITY, HIST_BOND, HIST_CPI)
        triples = set(zip(HIST_EQUITY, HIST_BOND, HIST_CPI))
        xp = ReturnsXParams(enabled=True, model="blocks", block_years=7)
        a = _sample_blocks(65, np.random.default_rng(11), xp)
        b = _sample_blocks(65, np.random.default_rng(11), xp)
        self.assertEqual(a, b)               # deterministic per seed
        for t in zip(*a):
            self.assertIn(t, triples)        # pure resampling, no synthesis

    def test_markov_transition_contracts(self):
        import numpy as np
        from fire_returns_x import _sample_markov, ReturnsXParams
        from fire_v6_model import REGIMES
        from fire_v7_model import V7Config
        cfg = V7Config()
        # persistence=1: the spell never ends (== lifetime-fixed semantics)
        _, _, names = _sample_markov(
            60, np.random.default_rng(3), cfg,
            ReturnsXParams(enabled=True, persistence=1.0))
        self.assertEqual(len(set(names)), 1)
        # persistence=0: redraws from the configured mixture every year;
        # same-state redraws are legal and restart the spell.
        _, _, names = _sample_markov(
            50_000, np.random.default_rng(3), cfg,
            ReturnsXParams(enabled=True, persistence=0.0))
        self.assertTrue(any(a == b for a, b in zip(names, names[1:])))
        observed = {name: names.count(name) / len(names) for name in set(names)}
        expected = {r.name: r.prob for r in REGIMES}
        self.assertEqual(set(observed), set(expected))
        for name, weight in expected.items():
            self.assertAlmostEqual(observed[name], weight, delta=0.015)

    def test_ar1_inflation_autocorrelates(self):
        import numpy as np
        from fire_returns_x import _sample_markov, ReturnsXParams
        from fire_v7_model import V7Config
        cfg = V7Config()
        def ac1(phi, seed=17, n=1500):
            _, inf, _ = _sample_markov(
                n, np.random.default_rng(seed), cfg,
                ReturnsXParams(enabled=True, inflation_ar1=phi))
            x = np.array(inf)
            return float(np.corrcoef(x[:-1], x[1:])[0, 1])
        self.assertGreater(ac1(0.9), 0.6)
        self.assertLess(abs(ac1(0.0)), 0.15)

    def test_models_move_results_and_run(self):
        base = summ(cfg0())
        for m in ("markov", "blocks"):
            c = cfg0(); c["returns"]["model"] = m
            s = ENG.summary(c, 300, SEED, False)
            self.assertNotEqual(s["terminal_real_p50"], base["terminal_real_p50"], m)
            self.assertGreater(s["lifetime_success"], 0.5, m)
        with self.assertRaises(ValueError):
            c = cfg0(); c["returns"]["model"] = "garch"
            ENG.build_kwargs(c, False)


class TestHousing(unittest.TestCase):
    """E5: amortization math, event compilation, directional rent-vs-buy
    contracts (30y window — over very long horizons appreciation-linked
    carrying costs legitimately erode the equity edge), OFF bit-identical."""

    def test_off_knobs_are_bit_identical(self):
        base = summ(cfg0())["terminal_real_p50"]
        c = cfg0()
        c["housing"]["price"] = 9_999_999          # enabled stays False
        c["housing"]["monthly_rent"] = 99_999
        self.assertEqual(summ(c)["terminal_real_p50"], base)

    def test_amortization_contracts(self):
        import housing as H
        s = H.mortgage_schedule(400_000, 0.065, 30)
        self.assertEqual(len(s), 30)
        self.assertGreater(s[0]["interest"] / s[0]["payment"],
                           s[-1]["interest"] / s[-1]["payment"])
        self.assertAlmostEqual(s[-1]["balance_end"], 0.0, places=2)
        total_principal = sum(r["principal_paid"] for r in s)
        self.assertAlmostEqual(total_principal, 400_000, delta=1.0)
        sr = H.mortgage_schedule(400_000, 0.065, 30, refi_year=10, refi_rate=0.04)
        self.assertLess(sr[10]["payment"], s[10]["payment"])   # refi cuts payment

    def test_rent_vs_buy_directional(self):
        import housing as H
        base = {"state": {"start_age": 30, "accum_years": 25, "retire_horizon": 40},
                "returns": {"inflation_mu": 0.03},
                "housing": {"enabled": True, "mode": "buy", "purchase_age": 35}}
        idx = (35 - 30) + 30 - 1                    # 30 years after purchase

        def gap(mut):
            c = copy.deepcopy(base)
            c["housing"].update(mut)
            return H.rent_vs_buy_deterministic(c)["buy_minus_rent"][idx]
        self.assertGreater(gap({"appreciation_real": 0.03}),
                           gap({"appreciation_real": 0.0}))
        self.assertLess(gap({"rate": 0.08}), gap({"rate": 0.04}))

    def test_mortgage_real_payment_uses_realized_cpi_anchor(self):
        import housing as H
        import fire_v9_8_model as V98

        c = {
            "state": {"start_age": 30},
            "returns": {"inflation_mu": 0.03},
            "housing": {"enabled": True, "mode": "buy",
                        "purchase_age": 35, "term_years": 30},
        }
        payload = H.compile_housing_mortgage(c)
        spec = V98.HousingMortgageSpec(
            purchase_age=payload["purchase_age"], payments=payload["payments"])
        pre_purchase = [0.03] * 5
        low_path = pre_purchase + [0.01] * 4
        high_path = pre_purchase + [0.10] * 4
        low = dict(V98.resolve_housing_mortgage_events(
            spec, 30, low_path))
        high = dict(V98.resolve_housing_mortgage_events(
            spec, 30, high_path))
        self.assertIn(36, low)
        self.assertLess(high[36], low[36])
        purchase_cpi = math.prod(1.0 + x for x in pre_purchase)
        self.assertAlmostEqual(
            low[36], spec.payments[0] * purchase_cpi
            / (purchase_cpi * 1.01), places=10)

    def test_mortgage_real_payment_formula_and_refi_rows(self):
        import housing as H
        import fire_v9_8_model as V98

        c = {
            "state": {"start_age": 30},
            "returns": {"inflation_mu": 0.03},
            "housing": {"enabled": True, "mode": "buy",
                        "purchase_age": 35, "term_years": 30,
                        "refi_enabled": True, "refi_age": 45,
                        "refi_rate": 0.04},
        }
        payload = H.compile_housing_mortgage(c)
        spec = V98.HousingMortgageSpec(
            purchase_age=payload["purchase_age"], payments=payload["payments"])
        inflations = [0.03] * 5 + [0.02, 0.08, 0.01, 0.04, 0.03] * 3
        events = dict(V98.resolve_housing_mortgage_events(
            spec, 30, inflations))
        purchase_cpi = math.prod(1.0 + x for x in inflations[:5])
        cpi = 1.0
        for years, inf in enumerate(inflations, start=1):
            cpi *= 1.0 + inf
            age = 30 + years
            if age in events:
                schedule_year = age - spec.purchase_age
                self.assertAlmostEqual(
                    events[age] * cpi,
                    spec.payments[schedule_year - 1] * purchase_cpi,
                    places=8)
        self.assertLess(spec.payments[10], spec.payments[0])

    def test_explicit_zero_mean_inflation_is_not_defaulted(self):
        import housing as H

        c = {
            "state": {"start_age": 30},
            "returns": {"inflation_mu": 0.0},
            "housing": {"enabled": True, "mode": "buy",
                        "purchase_age": 35, "term_years": 30},
        }
        with_mortgage = H.compile_housing_events(c)
        without_mortgage = H.compile_housing_events(c, include_mortgage=False)
        by_age = lambda rows, age: sum(amount for row_age, amount in rows
                                       if row_age == age)
        mortgage = by_age(with_mortgage, 36) - by_age(without_mortgage, 36)
        payload = H.compile_housing_mortgage(c)
        self.assertAlmostEqual(mortgage, payload["payments"][0], places=8)

    def test_mortgage_spec_boundaries_and_negative_inflation(self):
        import housing as H
        import fire_v9_8_model as V98

        rent = cfg0()
        rent["housing"].update({"enabled": True, "mode": "rent"})
        self.assertIsNone(ENG.build_kwargs(rent, False)["housing_mortgage"])

        no_loan = copy.deepcopy(rent)
        no_loan["housing"].update({"mode": "buy", "down_pct": 1.0})
        kw = ENG.build_kwargs(no_loan, False)
        self.assertIsNone(kw["housing_mortgage"])
        self.assertTrue(H.compile_housing_events(
            no_loan, include_mortgage=False))

        c = cfg0()
        c["housing"].update({"enabled": True, "mode": "buy",
                              "purchase_age": c["state"]["start_age"] - 5})
        payload = H.compile_housing_mortgage(c)
        spec = V98.HousingMortgageSpec(**payload)
        start_age = c["state"]["start_age"]
        self.assertEqual(spec.purchase_age, start_age + 1)
        negative = dict(V98.resolve_housing_mortgage_events(
            spec, start_age, [0.0] * 2 + [-0.10] * 2))
        flat = dict(V98.resolve_housing_mortgage_events(
            V98.HousingMortgageSpec(
                purchase_age=spec.purchase_age, payments=spec.payments),
            start_age, [0.0] * 4))
        self.assertGreater(negative[start_age + 3], flat[start_age + 3])

    def test_housing_disabled_preserves_full_result_and_rng_state(self):
        import numpy as np
        import fire_v9_8_model as V98

        base = cfg0()
        mutated = copy.deepcopy(base)
        mutated["housing"].update({
            "enabled": False, "mode": "buy", "price": 9_999_999,
            "monthly_rent": 99_999, "rate": 0.12,
        })

        def run(c):
            kw = ENG.build_kwargs(c, False)
            config = kw.pop("config")
            rng = np.random.default_rng(SEED)
            result = V98.simulate_lifecycle_v98(
                config=config, rng=rng, **kw)
            return result, rng.random(16)

        base_result, base_tail = run(base)
        mutated_result, mutated_tail = run(mutated)
        self.assertEqual(base_result, mutated_result)
        np.testing.assert_array_equal(base_tail, mutated_tail)

    def test_housing_carry_and_mortgage_merge_before_retirement_shortfall(self):
        import numpy as np
        import fire_v9_8_model as V98

        c = cfg0()
        c["state"].update({"start_age": 30, "accum_years": 1,
                            "retire_horizon": 2, "expenses_y0": 1.0,
                            "swr_pref": 0.2})
        c["initial"] = {key: (5_000.0 if key == "taxable" else 0.0)
                         for key in c["initial"]}
        c["contributions"].update({
            "base_salary_pre": 0.0, "bonus_pre": 0.0, "ot_income_pre": 0.0,
            "pretax_401k_limit_y1": 0.0, "roth_ira_limit_y1": 0.0,
            "hsa_limit_y1": 0.0, "match_rate": 0.0,
            "annual_spending_now": 1.0,
        })
        c["promotion"]["enabled"] = False
        c["mortality"]["enabled"] = False
        c["social_security"]["enabled"] = False
        c["housing"].update({
            "enabled": True, "mode": "buy", "purchase_age": 30,
            "price": 100_000, "down_pct": 0.0, "closing_pct": 0.0,
            "rate": 0.0, "term_years": 30, "tax_pct": 0.10,
            "maint_pct": 0.0, "insurance_annual": 0.0,
            "appreciation_real": 0.0,
        })
        c["returns"].update({"friction_accum": 0.0,
                              "friction_retire": 0.0,
                              "expense_ratio": 0.0, "rebalance_cost": 0.0})
        c["tax_us"].update({"drag_taxable": 0.0,
                             "withdrawal_tax_taxable": 0.0,
                             "withdrawal_tax_traditional": 0.0})
        c["medical"].update({"non_medical_y0": 0.0, "routine_y0": 0.0,
                              "premium_working": 0.0, "premium_aca": 0.0,
                              "premium_medicare": 0.0, "oop_y0": 0.0})
        fixed = (types.SimpleNamespace(name="fixed"), np.zeros(3), np.zeros(3))
        with mock.patch.object(
                V98, "sample_lifetime_v7",
                side_effect=lambda *args, **kwargs: fixed), \
                mock.patch.object(
                    V98, "sample_bond_returns",
                    side_effect=lambda equity, *args: np.zeros(len(equity))):
            result = ENG._run(c, 1, SEED, False)[0]

        self.assertEqual(result["fire_age"], 30)
        shortfalls = [s for s in result["event_shortfalls"]
                      if s["phase"] == "retirement"]
        self.assertEqual(len(shortfalls), 1)
        self.assertEqual(shortfalls[0]["age"], 32)
        # age 32 housing event = 10,000 carrying + 3,333.333... mortgage;
        # the two positive components must not be reported as separate events.
        self.assertAlmostEqual(
            shortfalls[0]["mandatory_outflow_real"], 13_333.333333333334,
            places=8)
        self.assertAlmostEqual(shortfalls[0]["shortfall_real"],
                               8_333.333333333334, places=8)

    def test_event_compilation(self):
        import housing as H
        c = cfg0()
        c["housing"].update({"enabled": True, "mode": "buy",
                             "purchase_age": 35, "replace_annual": 18_000})
        ev = H.compile_housing_events(c)
        self.assertTrue(ev)
        buy_evs = [a for (age, a) in ev if age == 35 and a > 0]
        self.assertEqual(len(buy_evs), 1)
        h = c["housing"]
        exp = (h["price"] * (1 + h["appreciation_real"]) ** 5
               * (h["down_pct"] + h["closing_pct"]))
        self.assertAlmostEqual(buy_evs[0], exp, places=6)
        refunds = [a for (_ag, a) in ev if a < 0]
        self.assertTrue(all(abs(a + 18_000) < 1e-9 for a in refunds))

    def test_purchase_at_or_before_start_charges_first_modeled_year(self):
        import housing as H
        c = cfg0()
        c["housing"].update({"enabled": True, "mode": "buy",
                             "purchase_age": c["state"]["start_age"] - 5})
        ev = H.compile_housing_events(c)
        first_age = c["state"]["start_age"] + 1
        first_outflows = [amount for age, amount in ev
                          if age == first_age and amount > 0]
        self.assertEqual(len(first_outflows), 1)
        self.assertGreater(first_outflows[0], 0.0)

    def test_modes_run_and_differ(self):
        outs = {}
        for m in ("rent", "buy"):
            c = cfg0()
            c["housing"].update({"enabled": True, "mode": m,
                                 "replace_annual": 18_000, "monthly_rent": 1_500})
            outs[m] = summ(c, 400)
        self.assertNotEqual(outs["rent"]["terminal_real_p50"],
                            outs["buy"]["terminal_real_p50"])


class TestFxPPP(unittest.TestCase):
    """E6: PPP mean-reversion on FX. kappa=0 must equal the field-absent
    config exactly (same z draw by construction), kappa>0 must shrink the
    terminal-FX log-variance and move relocation results."""

    @staticmethod
    def _reloc_cfg(kappa=None):
        c = cfg0()
        c["relocation"].update({"enabled": True, "relocation_age": 50})
        if kappa is None:
            c["relocation"].pop("ppp_kappa", None)
            c["relocation"].pop("fx_ppp", None)
        else:
            c["relocation"]["ppp_kappa"] = kappa
        return c

    def test_kappa_zero_is_identical(self):
        a = ENG.summary(self._reloc_cfg(0.0), 300, SEED, True)
        b = ENG.summary(self._reloc_cfg(None), 300, SEED, True)
        self.assertEqual(a, b)

    def test_ppp_shrinks_fx_dispersion_and_moves_results(self):
        import numpy as np

        def fx_logvar(kappa):
            res = ENG._run(self._reloc_cfg(kappa), 250, SEED, True)
            fx = [r["withdrawal"]["final_fx"] for r in res
                  if r.get("withdrawal") and r["withdrawal"].get("final_fx")]
            self.assertGreater(len(fx), 100)
            return float(np.var(np.log(fx)))
        self.assertLess(fx_logvar(0.20), 0.5 * fx_logvar(0.0))
        self.assertNotEqual(ENG.summary(self._reloc_cfg(0.2), 300, SEED, True),
                            ENG.summary(self._reloc_cfg(0.0), 300, SEED, True))


class TestCsvImport(unittest.TestCase):
    """D1: synthetic de-identified broker samples -> bucket suggestions;
    unknown formats fail cleanly; unclassifiable accounts land in
    `unassigned` (never silently guessed)."""

    FID = ('Account Number,Account Name,Symbol,Description,Quantity,'
           'Last Price,Current Value\n'
           'X111,ROTH IRA,VTI,VANGUARD TOTAL STOCK,100,$250.00,"$25,000.00"\n'
           'X111,ROTH IRA,SPAXX,FIDELITY GOV MMKT,500,$1.00,$500.00\n'
           'X222,401(K) PLAN,FXAIX,FIDELITY 500 INDEX,80,"$1,000.00","$80,000.00"\n'
           'X333,Individual,AAPL,APPLE INC,50,$200.00,"$10,000.00"\n\n'
           'Disclaimer: data as of today.')
    SCH = ('"Positions for account Roth IRA ...789 as of 07/12/2026"\n'
           'Symbol,Description,Qty (Quantity),Price,Mkt Val (Market Value)\n'
           'SWTSX,SCHWAB TOTAL MARKET,200,$110.00,"$22,000.00"\n\n'
           '"Positions for account Individual ...456 as of 07/12/2026"\n'
           'Symbol,Description,Qty (Quantity),Price,Mkt Val (Market Value)\n'
           'SCHB,SCHWAB US BROAD,150,$60.00,"$9,000.00"\n'
           'Cash & Cash Investments,--,--,--,"$1,000.00"')
    VAN = ('Account Number,Investment Name,Symbol,Shares,Share Price,Total Value\n'
           '88888888,Vanguard Total Stock Mkt Idx,VTSAX,300.5,120.00,36060.00\n'
           '88888888,Vanguard Federal MMkt,VMFXX,1000,1.00,1000.00')

    def test_fidelity_style(self):
        from csv_import import parse_broker_csv
        s = parse_broker_csv(self.FID)["suggestion"]
        self.assertEqual(s["roth_ira"], 25_500.0)
        self.assertEqual(s["pretax_401k"], 80_000.0)
        self.assertEqual(s["taxable"], 10_000.0)
        self.assertEqual(s["unassigned"], 0.0)

    def test_schwab_sections(self):
        from csv_import import parse_broker_csv
        r = parse_broker_csv(self.SCH)
        s = r["suggestion"]
        self.assertEqual(s["roth_ira"], 22_000.0)
        self.assertEqual(s["taxable"], 10_000.0)   # incl. the cash row
        self.assertEqual(len(r["accounts"]), 2)

    def test_vanguard_unassigned_not_guessed(self):
        from csv_import import parse_broker_csv
        r = parse_broker_csv(self.VAN)
        self.assertEqual(r["suggestion"]["unassigned"], 37_060.0)
        self.assertIsNone(r["accounts"][0]["bucket"])
        self.assertTrue(r["warnings"])

    def test_unknown_format_fails_cleanly(self):
        from csv_import import parse_broker_csv
        self.assertIn("error", parse_broker_csv("hello world\nno csv here"))
        self.assertIn("error", parse_broker_csv(""))

    def test_money_cleaning(self):
        from csv_import import _money
        self.assertEqual(_money("$1,234.56"), 1234.56)
        self.assertEqual(_money("(123.45)"), -123.45)
        self.assertEqual(_money("--"), 0.0)
        self.assertEqual(_money("NaN"), 0.0)
        self.assertEqual(_money("Infinity"), 0.0)

    def test_multiline_fields_and_labels_are_safe(self):
        from csv_import import parse_broker_csv
        text = ('Account Name,Description,Current Value\n'
                '"Roth <img src=x onerror=1> 1234-5678",'
                '"two-line\ndescription","$1,250.00"\n')
        r = parse_broker_csv(text)
        self.assertNotIn("error", r)
        self.assertEqual(r["accounts"][0]["total"], 1_250.0)
        label = r["accounts"][0]["label"]
        self.assertNotIn("<", label)
        self.assertNotIn("1234", label)


class TestSsaImport(unittest.TestCase):
    """D2: bend points must reproduce SSA's OFFICIAL published values; two
    hand-verified AIME/PIA goldens; XML parsing incl. failure modes."""

    @staticmethod
    def _xml(years, dob="1996-05-01"):
        rows = "".join(
            f'<osss:Earnings startYear="{y}" endYear="{y}">'
            f'<osss:FicaEarnings>{amt}</osss:FicaEarnings>'
            f'<osss:MedicareEarnings>{amt}</osss:MedicareEarnings>'
            f'</osss:Earnings>' for y, amt in years.items())
        return (f'<osss:OnlineSocialSecurityStatementData '
                f'xmlns:osss="http://ssa.gov/osss">'
                f'<osss:UserInformation><osss:DateOfBirth>{dob}'
                f'</osss:DateOfBirth></osss:UserInformation>'
                f'<osss:EarningsRecord>{rows}</osss:EarningsRecord>'
                f'</osss:OnlineSocialSecurityStatementData>')

    def test_bend_points_match_official(self):
        from ssa_import import bend_points
        self.assertEqual(bend_points(2024), (1174, 7078))
        self.assertEqual(bend_points(2025), (1226, 7391))

    def test_fire_earner_golden(self):
        from ssa_import import import_statement
        xml = self._xml({y: 120_000 for y in range(2018, 2030)})
        r = import_statement(xml)
        self.assertEqual(r["birth_year"], 1996)
        self.assertEqual(r["aime_monthly"], 3_766)
        self.assertEqual(r["pia_monthly"], 1_951.0)
        self.assertEqual(r["zeros_in_top35"], 23)
        self.assertEqual(
            r["rule_pack"]["component"]["id"], "ssa_statement_import")
        self.assertEqual(r["rule_pack"]["component"]["status"], "current")

    def test_long_career_golden_and_projection(self):
        from ssa_import import estimate_pia
        b = estimate_pia({y: 50_000 for y in range(1985, 2024)}, 1962)
        self.assertEqual(b["aime_monthly"], 8_856)
        self.assertEqual(b["pia_monthly_at_eligibility"], 3_212.5)
        self.assertEqual(b["pia_monthly"], 3_384.9)
        self.assertEqual(b["cola_through_year"], 2025)
        p = estimate_pia({y: 120_000 for y in range(2018, 2030)}, 1996,
                         project=True)
        self.assertEqual(p["projected_years"], 28)
        self.assertEqual(p["pia_monthly"], 3_613.7)

    def test_failure_modes(self):
        from ssa_import import import_statement, parse_ssa_xml
        self.assertIn("error", parse_ssa_xml("not xml at all"))
        self.assertIn("error", parse_ssa_xml("<root><nothing/></root>"))
        xml_no_dob = self._xml({2020: 90_000}).replace(
            "<osss:UserInformation><osss:DateOfBirth>1996-05-01"
            "</osss:DateOfBirth></osss:UserInformation>", "")
        self.assertIn("error", import_statement(xml_no_dob))       # no birth year
        r = import_statement(xml_no_dob, birth_year_fallback=1990)
        self.assertNotIn("error", r)                               # fallback works


class TestConfigSchema(unittest.TestCase):
    def test_version_stamp_present(self):
        self.assertGreaterEqual(cfg0().get("config_version", 0), 2)

    def test_checkins_are_plan_data_only(self):
        """I4: checkins live in the config schema but the engine must NEVER
        read them — stuffing them must not move a single float."""
        self.assertEqual(cfg0().get("checkins"), [])
        base = summ(cfg0())["terminal_real_p50"]
        c = cfg0()
        c["checkins"] = [{"date": "2026-07-12", "age": 31,
                          "actual_total_nominal": 999_999}]
        self.assertEqual(summ(c)["terminal_real_p50"], base)

    def test_v1_config_migrates_additively(self):
        """A 1.0-era config (no version, missing 2.0 groups) must run after the
        loader-style deep-merge and gain every current key."""
        v1 = {"name": "old plan", "state": {"start_age": 33, "expenses_y0": 50000},
              "initial": {"pretax_401k": 10000}}
        cur = cfg0()
        def deep_merge(a, b):
            if not isinstance(a, dict) or not isinstance(b, dict):
                return b if b is not None else a
            out = dict(a)
            for k, v in b.items():
                out[k] = deep_merge(out.get(k), v)
            return out
        merged = deep_merge(cur, v1)
        self.assertEqual(set(merged.keys()), set(cur.keys()) | {"config_version"} - {"config_version"} | set(cur.keys()))
        s = ENG.summary(merged, 300, SEED, False)
        self.assertIn("terminal_real_p50", s)
        self.assertEqual(merged["state"]["expenses_y0"], 50000)


class TestServerRobustness(unittest.TestCase):
    def test_request_cap_happens_before_body_read(self):
        import app as APP

        class MustNotRead:
            def read(self, _n):
                raise AssertionError("oversized body was read")

        h = object.__new__(APP.Handler)
        h.headers = {"Content-Length": str(APP.MAX_REQUEST_BYTES + 1)}
        h.rfile = MustNotRead()
        h.close_connection = False
        with self.assertRaises(APP.RequestTooLarge):
            h._read_body()
        self.assertTrue(h.close_connection)

    def test_request_body_must_be_object_and_json_rejects_nonfinite(self):
        import io
        import app as APP
        h = object.__new__(APP.Handler)
        raw = b"[1,2,3]"
        h.headers = {"Content-Length": str(len(raw))}
        h.rfile = io.BytesIO(raw)
        with self.assertRaisesRegex(ValueError, "JSON object"):
            h._read_body()
        raw = b'{"bad":NaN}'
        h.headers = {"Content-Length": str(len(raw))}
        h.rfile = io.BytesIO(raw)
        with self.assertRaisesRegex(ValueError, "non-finite"):
            h._read_body()
        h._send = lambda *_args: None
        with self.assertRaises(ValueError):
            h._json({"bad": float("inf")})

    def test_running_jobs_are_never_evicted(self):
        import app as APP
        old_jobs, old_seq = APP._JOBS, APP._JOB_SEQ
        try:
            APP._JOBS = {
                str(i): {"done": i != 1, "pct": 0, "stage": "x",
                         "result": None, "error": None, "cancelled": False}
                for i in range(1, 10)
            }
            APP._JOB_SEQ = [9]
            APP._new_job()
            self.assertIn("1", APP._JOBS)
            self.assertFalse(APP._JOBS["1"]["done"])
            self.assertLessEqual(len(APP._JOBS), 8)
        finally:
            APP._JOBS, APP._JOB_SEQ = old_jobs, old_seq

    def test_export_names_do_not_overwrite_and_errors_are_scrubbed(self):
        import app as APP
        with tempfile.TemporaryDirectory() as td:
            f1, p1 = APP._open_export(td, "result", "json")
            f1.close()
            f2, p2 = APP._open_export(td, "result", "json")
            f2.close()
            self.assertNotEqual(p1, p2)
        msg = APP._public_error(RuntimeError(ROOT + "/engine/private.py failed"))
        self.assertNotIn(ROOT, msg)

    def test_roth_selector_is_success_first_and_after_tax(self):
        import app as APP
        pts = [
            {"lifetime_success": 0.158,
             "terminal_after_tax_real_p50": 9_000_000},
            {"lifetime_success": 0.606,
             "terminal_after_tax_real_p50": 1_000_000},
        ]
        self.assertIs(APP._select_roth_best(pts), pts[1])


class TestFrontendAndBuildContracts(unittest.TestCase):
    def test_dynamic_labels_and_untrusted_names_use_safe_channels(self):
        js = pathlib.Path(ROOT, "web", "app.js").read_text(encoding="utf-8")
        self.assertIn("odLabel(TORN_LABELS", js)
        self.assertIn("odLabel(BT_LABELS", js)
        self.assertIn('${esc(pl.name || "")}', js)
        self.assertIn('esc(A.config.name || "")', js)
        self.assertIn('k === "__proto__"', js)

    @unittest.skipUnless(shutil.which("node"), "Node is a mandatory release dependency")
    def test_js_syntax_gate_rejects_invalid_source(self):
        gate = os.path.join(ROOT, "tests", "js_syntax_check.py")
        with tempfile.TemporaryDirectory() as td:
            pathlib.Path(td, "ok.js").write_text("const x = 1;\n", encoding="utf-8")
            ok = subprocess.run([sys.executable, gate, td], capture_output=True)
            self.assertEqual(ok.returncode, 0, ok.stderr.decode())
            pathlib.Path(td, "bad.js").write_text("const = ;\n", encoding="utf-8")
            bad = subprocess.run([sys.executable, gate, td], capture_output=True)
            self.assertNotEqual(bad.returncode, 0)

    def test_release_script_keeps_frozen_and_dual_arch_gates(self):
        sh = pathlib.Path(ROOT, "build-app.sh").read_text(encoding="utf-8")
        for token in ("run_regression_for_arch", "verify_universal_machos",
                      "tests/frozen_smoke.py", "tests/js_syntax_check.py",
                      "CANDIDATE_ONLY"):
            self.assertIn(token, sh)


class TestServedSurface(unittest.TestCase):
    def test_all_presets_run(self):
        import presets as P
        for key, p in P.PRESETS.items():
            s = ENG.summary(copy.deepcopy(p["config"]), 300, SEED, False)
            self.assertIn("terminal_real_p50", s, f"preset {key} failed")

    def test_report_builder_accepts_run_full_output(self):
        import build_report
        r = ENG.run_full(cfg0(), 400, SEED, 300)
        r["meta"].update({"current_age": 30, "annual_retirement_spending": 42000,
                          "safe_withdrawal_rate": 0.0333,
                          "relocation_enabled": False,
                          "protocol": {"paths": 400, "seed": SEED,
                                       "engine": ENG.ENGINE_VERSION,
                                       "mode": "sequential", "elapsed_s": 1}})
        for k in ("home", "relocation"):
            if k in r:
                r[k]["seed"] = SEED
        html = build_report.build(r)
        self.assertGreater(len(html), 5000)
        self.assertIn("蒙特卡洛", html)
        self.assertIn("离线规则仍在应用维护窗口内", html)
        self.assertIn(r["meta"]["rule_pack"]["pack_id"], html)
        extra = {"lang": "en",
                 "verdict_html": "<div class='v-main'>VERDICT-SENTINEL</div>",
                 "conclusions": [{"tone": "good", "title": "T1", "body": "B1"}],
                 "limitations": ["LIM-SENTINEL"],
                 "ab": {"a": {"label": "A", "s": r["home"]},
                        "b": {"label": "B", "s": r["home"]}}}
        html2 = build_report.build(r, extra)
        # `sampling SE` was this label until 2026-08-16 and was replaced on
        # purpose. It printed ONE standard error -- about 68% -- under a name
        # every reader takes for a confidence interval, so the honest 95%
        # width was roughly double what this report showed. Asserting the old
        # label would pin the misleading version of the very thing that was
        # fixed; asserting the property keeps the check and drops the wrong
        # word.
        for token in ("VERDICT-SENTINEL", "T1", "LIM-SENTINEL", "Scenario A/B",
                      "95% interval", "Monte Carlo"):
            self.assertIn(token, html2)
        legacy = copy.deepcopy(r)
        legacy["meta"].pop("rule_pack")
        self.assertIn(
            "rule vintage unrecorded",
            build_report.build(legacy, {"lang": "en"}))
        import fire_rule_pack as RP
        current = copy.deepcopy(r)
        current_cfg = cfg0()
        current_cfg["state"]["start_age"] = 60
        current["meta"]["rule_pack"] = RP.rule_pack_for_run(
            current_cfg, as_of="2026-12-31")
        self.assertIn(
            "within the app review window",
            build_report.build(current, {"lang": "en"}))
        overridden_cfg = copy.deepcopy(current_cfg)
        overridden_cfg["contributions"]["pretax_401k_limit_y1"] += 1
        review = copy.deepcopy(r)
        review["meta"]["rule_pack"] = RP.rule_pack_for_run(
            overridden_cfg, as_of="2026-12-31")
        self.assertIn(
            "review required",
            build_report.build(review, {"lang": "en"}))
        hostile = copy.deepcopy(r)
        hostile["meta"]["name"] = '<img src=x onerror="boom">'
        hostile["meta"]["rule_pack"]["components"][0]["label"] = (
            '<img src=x onerror="pack-boom">')
        hostile_extra = copy.deepcopy(extra)
        hostile_extra["verdict_html"] = '<script>alert(1)</script><b>safe</b>'
        hostile_extra["ab"]["a"]["label"] = '<svg onload="boom">'
        hostile_extra["conclusions"] = [{"tone": "bad\" onclick=\"boom",
                                          "title": "<img src=x>",
                                          "body": "<b>kept</b><script>bad</script>"}]
        hostile_extra["limitations"] = ['<b>kept</b><img src=x onerror="boom">']
        safe = build_report.build(hostile, hostile_extra)
        import re
        self.assertIsNone(re.search(r"<(?:script|img|svg)\b", safe, re.I))
        self.assertIn('<div class="card neutral">', safe)
        self.assertIn("<b>kept</b>", safe)

    def test_sweep_and_sensitivity_smoke(self):
        import app as APP
        sw = APP.run_sweep(cfg0(), "state.swr_pref", [0.03, 0.04], 500, SEED)
        self.assertEqual(len(sw["points"]), 2)
        self.assertNotEqual(sw["points"][0]["terminal_real_p50"],
                            sw["points"][1]["terminal_real_p50"])
        sens = APP.run_sensitivity(cfg0(), 500, SEED)
        self.assertEqual(len(sens["mu_band"]), 5)
        self.assertTrue(all("key" in row for row in sens["rows"]))

    def test_goalseek_contracts(self):
        """S1: structure, eval budget, expense-column monotonicity, and the
        nearest-feasible-point minimality (recomputed from returned data)."""
        import app as APP
        r = APP.run_goalseek(
            cfg0(), {"metric": "lifetime_success", "value": 0.98},
            [{"key": "expenses", "min": 30_000, "max": 90_000},
             {"key": "swr", "min": 0.028, "max": 0.055}],
            paths=400, seed=SEED, grid=4)
        g = 4
        self.assertEqual(len(r["z"]), g)
        self.assertTrue(all(len(row) == g for row in r["z"]))
        self.assertLessEqual(r["evals"], g * g + 2 * g + 1)
        # more spending can never make the goal EASIER (per-column count)
        cols = [sum(row[i] for row in r["feasible"]) for i in range(g)]
        self.assertEqual(cols, sorted(cols, reverse=True))
        # nearest: feasible, and no other returned point is strictly closer
        cur, near = r["current"], r["nearest"]
        self.assertTrue(near["ok"])
        xs, ys = r["levers"][0]["values"], r["levers"][1]["values"]
        sx, sy = xs[-1] - xs[0], ys[-1] - ys[0]
        pts = ([{"x": xs[i], "y": ys[j], "ok": r["feasible"][j][i]}
                for j in range(g) for i in range(g)] + r["refined"])
        d = lambda p: ((p["x"] - cur["x"]) / sx) ** 2 + ((p["y"] - cur["y"]) / sy) ** 2
        self.assertLessEqual(d(near), min(d(p) for p in pts if p["ok"]) + 1e-12)
        # bad input -> ValueError before any thread is involved
        with self.assertRaises(ValueError):
            APP.start_goalseek_job(cfg0(), {"metric": "nope", "value": 1},
                                   [], 400, SEED, 4)

    def test_frontier_pareto_contracts(self):
        """S2: the frontier is exactly the nondominated set (both halves
        recomputed from returned data), and the nearest frontier point is
        itself on the frontier."""
        import app as APP
        r = APP.run_frontier(cfg0(), paths=400, seed=SEED, grid=4)
        pts = r["points"]
        self.assertEqual(len(pts), 16)
        self.assertLessEqual(r["evals"], 17)
        front = [p for p in pts if p["frontier"]]
        self.assertTrue(front)
        for a in front:                     # mutually nondominated
            for b in front:
                if a is not b:
                    self.assertFalse(APP._dominates(a, b))
        for p in pts:                       # non-frontier => dominated
            if not p["frontier"]:
                self.assertTrue(any(APP._dominates(q, p) for q in pts))
        nf = r["nearest_frontier"]
        if nf is not None:
            self.assertTrue(nf["frontier"])

    def test_drill_contracts(self):
        """I3: age-slice counts bounded by batch size; bucket stats are
        internally consistent (share recomputable, bucket ⊆ all)."""
        a = ENG.drill(cfg0(), "age_slice", SEED, 120, age=50)
        self.assertLessEqual(a["alive"], a["n"])
        self.assertEqual(sum(a["regimes"].values()), a["alive"])
        if a["hist"]:
            self.assertLessEqual(sum(a["hist"]["counts"]), a["alive"])
        t = ENG.drill(cfg0(), "term_bucket", SEED, 120,
                      lo=1_000_000, hi=3_000_000)
        if t["bucket"]:
            self.assertAlmostEqual(t["share"],
                                   t["bucket"]["count"] / t["n_fired"], places=9)
            self.assertLessEqual(t["bucket"]["count"], t["all"]["count"])
        with self.assertRaises(ValueError):
            ENG.drill(cfg0(), "nope", SEED, 120)

    def test_story_smoke_and_ordering(self):
        """I2: three lives picked from ONE batch — the ordering contract
        (lucky terminal >= typical >= unlucky) is exact, not statistical."""
        r = ENG.story(cfg0(), 120, SEED)
        ss = r["stories"]
        self.assertTrue(ss)
        legacy = {}
        for k in ("typical", "lucky", "unlucky"):
            s = ss[k]
            ages = [p[0] for p in s["curve"]]
            self.assertEqual(ages, sorted(ages), f"{k}: curve ages not monotone")
            self.assertTrue(s["events"], f"{k}: no events extracted")
            self.assertIn(s["ending"]["kind"], ("ruin", "died", "horizon"))
            if s["fire_age"] is not None:
                self.assertIn(("fire",), [(e["kind"],) for e in s["events"]])
            legacy[k] = s["ending"].get("legacy_real")
        if all(v is not None for v in legacy.values()):
            self.assertGreaterEqual(legacy["lucky"], legacy["typical"])

    def test_backtest_smoke(self):
        bt = ENG.backtest(cfg0(), 40, SEED)
        self.assertGreaterEqual(len(bt["scenarios"]), 3)
        for sc in bt["scenarios"].values():
            self.assertTrue(sc["real_cons"])

    def test_backtest_runs_housing_mortgage_channel(self):
        for purchase_age in (35, 45):
            c = cfg0()
            c["mortality"]["enabled"] = False
            c["housing"].update({"enabled": True, "mode": "buy",
                                  "purchase_age": purchase_age})
            kw = ENG.build_kwargs(c, False)
            self.assertIsNotNone(kw["housing_mortgage"])
            bt = ENG.backtest(c, 40, SEED)
            self.assertEqual(set(bt["scenarios"]),
                             {"crash", "lost_decade", "stagflation"})
            for sc in bt["scenarios"].values():
                self.assertTrue(sc["real_cons"])
                self.assertIsInstance(sc["event_shortfalls"], list)

    def test_backtest_runs_active_non_cola_income_channel(self):
        base = ENG.backtest(cfg0(), 40, SEED)
        c = cfg0()
        c["mortality"]["enabled"] = False
        c["income_streams"].update({
            "pension_enabled": True,
            "pension_annual_real": 20_000.0,
            "pension_start_age": 41,
            "pension_cola": False,
            "pension_owner": "primary",
        })
        active = ENG.backtest(c, 40, SEED)
        for key in base["scenarios"]:
            self.assertTrue(active["scenarios"][key]["real_cons"])
            self.assertNotEqual(
                active["scenarios"][key]["terminal_real"],
                base["scenarios"][key]["terminal_real"],
                f"{key}: direct retirement caller silently dropped pension",
            )

    def test_backtest_honors_events_and_true_tax(self):
        c = cfg0()
        c["mortality"]["enabled"] = False
        c["tax_true"]["enabled"] = True
        c["life_events"] = [{"age": 41, "amount_real": 1_000_000_000,
                             "label": "mandatory"}]
        bt = ENG.backtest(c, 40, SEED)
        for sc in bt["scenarios"].values():
            self.assertFalse(sc["survived"])
            self.assertTrue(sc["event_shortfalls"])
            self.assertGreaterEqual(sc["true_tax_real"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
