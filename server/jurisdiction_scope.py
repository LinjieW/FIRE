"""Which jurisdictions this product claims to serve, and what it does not do in each.

WHY THIS EXISTS. `IDEA_BANK` A32 asked for three things: a wording lint, a
feature red-line map, and a per-jurisdiction disclaimer layer. Roadmap 12
Phase 13 shipped the wording lint (`tests/test_advice_boundary.py`); the other
two waited on a product-scope ruling, because no map can say "this feature is a
red line in X" until somebody decides which X the product claims. The user
ruled on 2026-09-08: **the United States, Canada and mainland China**.

WHAT WAS ALREADY TRUE, measured before writing any of this. Most of the red
lines already existed as prose, scattered:

  * `server/limitations.py`'s `country_accounts` rule already states Canada's
    limits exactly -- no federal or provincial brackets, no provincial
    variation, CPP and OAS reported as `unmeasured` rather than as zero.
  * the general list's `city_catalog` entry already says a destination supplies
    cost of living, FX, healthcare and ONE flat withdrawal rate, "not a tax
    system".
  * `CHINA_RULE_RESEARCH_2026-09-04.md` already records why no China pack
    exists, with official sources and the treaty text quoted.

Three things were missing, and they are what this module adds:

  1. **Nowhere said which jurisdictions are claimed.** The disclaimers were
     general. A general disclaimer cannot tell a reader whether their country
     is one the product tried to get right.
  2. **China's ruling never reached the product.** It lived in a repository
     note that no user of the app can read.
  3. **The map was prose, so it had no holes to find.** Prose cannot be
     checked for a missing cell; a table can, and `tests/test_jurisdiction_scope.py`
     refuses one.

WHY A TABLE AND NOT MORE PROSE. The same reason Roadmap 13's invariant 2 exists:
"not modelled" and "modelled and it came out neutral" must not look alike. A
feature with no entry for a jurisdiction is the disclosure version of a false
zero, so every feature declares a status for every claimed jurisdiction and the
suite fails on an empty cell rather than rendering a shorter list.

WHAT THIS MODULE IS NOT. It is not a claim that anything here was reviewed by a
lawyer, that the product is registered or exempt anywhere, or that any regulator
has seen it. It adds no configuration leaf -- Roadmap 13 invariant 1 reserves
that for a slice of its own -- so it is stated unconditionally, like
`not_advice`, rather than triggered by a setting.
"""
from __future__ import annotations

import os
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: A feature is computed from rules this product ships for that jurisdiction.
MODELLED = "modelled"
#: Computed, but from something narrower than that jurisdiction's actual law.
#: A status that requires a reason: `partial` without one is the hole this
#: table exists to make visible.
PARTIAL = "partial"
#: Computed only from a number the user typed in. As good as their input and
#: no better -- which is a different claim from "modelled".
USER_SUPPLIED = "user_supplied"
#: Not computed at all. Not zero.
NOT_MODELLED = "not_modelled"

STATUSES = (MODELLED, PARTIAL, USER_SUPPLIED, NOT_MODELLED)


class Jurisdiction:
    """One claimed jurisdiction and the rule data that backs it, if any.

    `pack` is the file this product ships for it, or None. None is the honest
    answer for mainland China and it is load-bearing: a jurisdiction can be in
    the claimed list AND have no rule pack, which is exactly the combination
    Roadmap 12 Phase 12 ruled for. `tests/test_jurisdiction_scope.py` checks
    each `pack` against the filesystem, so shipping a China pack one day makes
    this table red instead of leaving it quietly wrong.
    """

    def __init__(self, code: str, zh: str, en: str, pack: Optional[str],
                 basis_zh: str, basis_en: str):
        self.code = code
        self.zh = zh
        self.en = en
        self.pack = pack
        self.basis_zh = basis_zh
        self.basis_en = basis_en


CLAIMED = (
    Jurisdiction(
        "US", "美国", "United States", "engine/rule_pack_us_offline.json",
        "十四个组件的离线规则包：联邦税档、FICA/SECA、IRMAA、ACA、缴款上限、社保规则与信托基金。",
        "A fourteen-component offline rule pack: federal brackets, FICA/SECA, "
        "IRMAA, ACA, contribution limits, Social Security rules and the trust fund."),
    Jurisdiction(
        "CA", "加拿大", "Canada", "engine/rule_pack_ca_accounts.json",
        "只有账户语义的 beta 规则包：non-registered / RRSP / RRIF / TFSA，"
        "以及 71 岁处置选项与 RRIF 最低提取表。**不是**一套加拿大税制。",
        "An accounts-only beta rule pack: non-registered, RRSP, RRIF and TFSA, "
        "plus the age-71 disposition options and the RRIF minimum-withdrawal "
        "table. It is **not** a Canadian tax system."),
    Jurisdiction(
        "CN", "中国大陆", "Mainland China", None,
        "**没有规则包。** 2026-09-04 的调研结论是公开数据不足以建一个可维护的中国规则包，"
        "该结论未被推翻。列入清单说的是「这个产品知道你可能在这里」，"
        "不是「这里的规则被建模了」。",
        "**There is no rule pack.** A 2026-09-04 review concluded that public "
        "sources were not sufficient to build a maintainable one, and that "
        "conclusion still stands. Being on this list means the product knows "
        "you may be here -- not that the rules here are modelled."),
)


class Feature:
    """One capability, and what it actually does in each claimed jurisdiction.

    `status` must name every claimed code. The suite refuses a missing one
    rather than rendering a shorter row, because a reader cannot tell a row
    that was left out from a row that had nothing to say.
    """

    def __init__(self, feature_id: str, zh: str, en: str, status: dict,
                 note_zh: Optional[dict] = None, note_en: Optional[dict] = None):
        self.id = feature_id
        self.zh = zh
        self.en = en
        self.status = status
        self.note_zh = note_zh or {}
        self.note_en = note_en or {}


FEATURES = (
    Feature(
        "national_income_tax", "联邦 / 国家所得税", "Federal / national income tax",
        {"US": MODELLED, "CA": USER_SUPPLIED, "CN": NOT_MODELLED},
        {"CA": "用你填写的有效税率，不是加拿大联邦或省级累进税档。"},
        {"CA": "Uses the effective rates you entered, not Canadian federal or "
               "provincial brackets."}),
    Feature(
        "subnational_income_tax", "州 / 省级所得税", "State / provincial income tax",
        {"US": PARTIAL, "CA": NOT_MODELLED, "CN": NOT_MODELLED},
        {"US": "十种**原型**（无所得税 / 统一低税率 / 累进高税率 / 退休收入豁免 等），"
               "不是任何一个具体州的法条。"},
        {"US": "Ten **archetypes** (no income tax, flat low, progressive high, "
               "retirement income exempt, and so on) rather than any particular "
               "state's statute."}),
    Feature(
        "payroll_tax", "工薪税（FICA / SECA）", "Payroll tax (FICA / SECA)",
        {"US": MODELLED, "CA": NOT_MODELLED, "CN": NOT_MODELLED}),
    Feature(
        "public_pension", "公共养老金", "Public pension",
        {"US": MODELLED, "CA": NOT_MODELLED, "CN": NOT_MODELLED},
        {"US": "社保给付规则与信托基金枯竭都建模。",
         "CA": "CPP / OAS **未建模**，在输出里报为 `unmeasured` 而不是 0。",
         "CN": "中国基本养老金、社保与住房公积金一概未建模。"},
        {"US": "Social Security benefit rules and trust-fund depletion are both modelled.",
         "CA": "CPP and OAS are **not modelled**; they report as `unmeasured`, not as zero.",
         "CN": "China's basic pension, social insurance and housing fund are not modelled."}),
    Feature(
        "public_pension_abroad", "公共养老金的境外折减", "Public pension haircut abroad",
        {"US": MODELLED, "CA": MODELLED, "CN": PARTIAL},
        {"US": "美国境内目的地为 0 —— 你还在美国，没有非居民预扣。",
         "CA": "协定豁免国之一，因此为 0。",
         "CN": "0.20 是引擎里写明的**复合判断**（协定后的预扣残值 + 中国是否再征 + 行政摩擦，"
               "区间 15–25%），**不是法定预扣率**。"},
        {"US": "US destinations are zero: you are still in the US, so there is "
               "no nonresident withholding.",
         "CA": "One of the treaty-exempt countries, so zero.",
         "CN": "The 0.20 figure is a **composite judgement** the engine documents "
               "(residual withholding after the treaty, whether China also taxes "
               "it, and administrative friction; a 15-25% range). It is **not** a "
               "statutory withholding rate."}),
    Feature(
        "health_insurance", "医保保费", "Health insurance premiums",
        {"US": MODELLED, "CA": USER_SUPPLIED, "CN": USER_SUPPLIED},
        {"US": "ACA 市场补贴与 Medicare IRMAA 都按规则包算。",
         "CA": "用你填写的目的地医疗年额。",
         "CN": "用你填写的目的地医疗年额；不建模中国的医保或大病保险。"},
        {"US": "ACA marketplace subsidies and Medicare IRMAA are both computed "
               "from the rule pack.",
         "CA": "Uses the destination healthcare figures you entered.",
         "CN": "Uses the destination healthcare figures you entered; China's "
               "public medical insurance is not modelled."}),
    Feature(
        "tax_advantaged_accounts", "税优账户", "Tax-advantaged accounts",
        {"US": MODELLED, "CA": PARTIAL, "CN": NOT_MODELLED},
        {"US": "401(k) / IRA / Roth / HSA / 政府 457(b) / ESPP 的形态与上限。",
         "CA": "只有 non-registered / RRSP / RRIF / TFSA 四类，且是 beta。",
         "CN": "**没有规则包**，所以个人养老金等账户一概没有账户语义。"},
        {"US": "401(k), IRA, Roth, HSA, governmental 457(b) and ESPP shapes and limits.",
         "CA": "Only the four beta shapes: non-registered, RRSP, RRIF and TFSA.",
         "CN": "**No rule pack exists**, so there are no account semantics for "
               "Chinese private pension accounts."}),
    Feature(
        "forced_distributions", "强制提取", "Forced distributions",
        {"US": PARTIAL, "CA": MODELLED, "CN": NOT_MODELLED},
        {"US": "RMD 用 IRS Uniform Lifetime Table，但它只在**选开**的 true-tax 路径上生效；"
               "默认的平坦税路径不算 RMD。",
         "CA": "RRIF 最低提取表按年初公允市值算，成立当年为 0。"},
        {"US": "RMDs use the IRS Uniform Lifetime Table, but only on the "
               "**opt-in** true-tax path; the default flat-tax path does not "
               "compute them.",
         "CA": "The RRIF minimum table applies to the start-of-year fair market "
               "value, and is zero in the year the RRIF is established."}),
    Feature(
        "account_disposition", "账户到期处置", "Account disposition at an age limit",
        {"US": NOT_MODELLED, "CA": PARTIAL, "CN": NOT_MODELLED},
        {"CA": "71 岁 RRSP 的三个合法选项里只建模「转入 RRIF」；"
               "取现与买年金会被**点名拒绝**，而不是悄悄按 RRIF 算。"},
        {"CA": "Of the three lawful age-71 RRSP routes only the RRIF transfer is "
               "modelled; cash-out and annuity are **refused by name** rather "
               "than quietly treated as a RRIF."}),
    Feature(
        "estate_tax", "遗产税", "Estate tax",
        {"US": PARTIAL, "CA": NOT_MODELLED, "CN": NOT_MODELLED},
        {"US": "**任何地方都不计算遗产税。** 只报告有多少条路径的期末组合高于"
               "**你自己填的**免税额，因为那个数是立法定的、会变，而本 App 不联网。"},
        {"US": "**No estate tax is computed anywhere.** The product only reports "
               "how many paths end above the exemption **you entered**, because "
               "that figure is legislated, it moves, and this app makes no "
               "network requests."}),
    Feature(
        "cross_border_treaty", "跨境税收协定与抵免", "Cross-border treaties and credits",
        {"US": NOT_MODELLED, "CA": NOT_MODELLED, "CN": NOT_MODELLED},
        {"US": "税收协定、预扣税与外国税收抵免的相互作用**在任何法域都没有建模**。"
               "唯一的例外是社保境外折减那一列，它按已公布规则填，且只是那一件事。"},
        {"US": "Treaty relief, withholding and the interaction with foreign tax "
               "credits are **not modelled in any jurisdiction**. The single "
               "exception is the Social Security haircut column, which follows a "
               "published rule and covers only that one thing."}),
)

#: Everywhere that is not on the list. The destination catalogue carries 308
#: destinations, so most of them land here, and saying so is the point: the
#: catalogue's usefulness is real but bounded, and a reader comparing Lisbon to
#: Austin should know which half of that comparison is modelled.
NOT_CLAIMED_ZH = (
    "**清单之外的目的地**：目的地目录里的其他地方只提供生活成本比、汇率波动、当地通胀、"
    "医疗成本、社保境外折减，以及**一个平坦的提取税率** —— **不是一套税制**。"
    "当地税档、当地资本利得规则、税收协定、财富税与弃籍税都不建模，"
    "模型内部只区分「本国」与「已搬迁」两种税基。这些默认值是**示意值**，请按你的真实情况核对修改。")
NOT_CLAIMED_EN = (
    "**Destinations off this list**: everywhere else in the catalogue supplies "
    "only a cost-of-living ratio, FX volatility, local inflation, healthcare "
    "costs, the Social Security haircut abroad and **one flat withdrawal-tax "
    "rate** -- **not a tax system**. Local brackets, local capital-gains rules, "
    "tax treaties, wealth taxes and exit taxes are not modelled, and the model "
    "distinguishes only two tax bases, home and relocated. Those defaults are "
    "**illustrative**; check and edit them for your real situation.")

#: Said in the same breath as the list itself. Naming jurisdictions is the kind
#: of statement a reader can over-read, so the sentence that bounds it travels
#: with it rather than sitting in a different document.
CAVEAT_ZH = (
    "「服务这些法域」的意思是：这个产品**试图**把这几个地方的规则弄对，并明确写出哪些没弄。"
    "它不表示这里的内容经过律师复核，也不表示本产品在任何地方注册、豁免或获得监管认可。")
CAVEAT_EN = (
    "\"Serves these jurisdictions\" means the product **tries** to get these "
    "places' rules right and states plainly which parts it does not. It does "
    "not mean any of this was reviewed by a lawyer, and it does not mean the "
    "product is registered, exempt or recognised by any regulator anywhere.")

CLAIMED_CODES = tuple(j.code for j in CLAIMED)

#: Country packs carry their own `scope` sentence -- the one field A32 asked
#: for. Measured on 2026-09-10, before this module existed: the Canada pack had
#: carried an accurate one since it was written, the schema did not require it,
#: and NOTHING IN THE REPOSITORY READ IT. Requiring it without reading it would
#: have fixed the cheaper half and left the sentence exactly as unseen as
#: before, so the declaration quotes the pack rather than restating it -- one
#: source, and a test that fails if the quote goes empty.
#:
#: Quoted verbatim, in the pack's own English, in both languages. The product
#: already shows source names that way (IRS Publication 915, IC78-18R7): a
#: translated quotation of a rule document would be a second text nobody
#: re-checks against the first.
def pack_scope(code: str) -> Optional[str]:
    """The pack's own statement of what falls outside it, or None.

    None is a real answer, not a failure: the US rule pack is a different
    schema with no such field, and mainland China has no pack at all. The
    suite pins which codes are expected to have one, so a pack that quietly
    stopped carrying its sentence turns red rather than rendering nothing.
    """
    jurisdiction = next((j for j in CLAIMED if j.code == code), None)
    if jurisdiction is None or not jurisdiction.pack:
        return None
    if not jurisdiction.pack.endswith("_accounts.json"):
        return None
    try:
        import fire_country_pack as PACK
        payload = PACK.pack_for(code)
    except Exception:                                       # noqa: BLE001
        return None
    text = str(payload.get("scope") or "").strip()
    return text or None


def declaration(language: str = "zh") -> dict:
    """The scope statement, ready to render. Config-independent by design.

    It takes no config because it is not conditional: it holds whatever you
    configure, which is the same reason `not_advice` is in the general list
    rather than in the triggered one. Conditioning it would imply there is a
    setting that switches the boundary off.
    """
    zh = language == "zh"
    rows = []
    for feature in FEATURES:
        cells = []
        for code in CLAIMED_CODES:
            note = (feature.note_zh if zh else feature.note_en).get(code)
            cells.append({"jurisdiction": code,
                          "status": feature.status[code],
                          "note": note})
        rows.append({"id": feature.id,
                     "feature": feature.zh if zh else feature.en,
                     "cells": cells})
    return {
        "claimed": [{"code": j.code,
                     "name": j.zh if zh else j.en,
                     "pack": j.pack,
                     "pack_scope": pack_scope(j.code),
                     "basis": j.basis_zh if zh else j.basis_en}
                    for j in CLAIMED],
        "features": rows,
        "not_claimed": NOT_CLAIMED_ZH if zh else NOT_CLAIMED_EN,
        "caveat": CAVEAT_ZH if zh else CAVEAT_EN,
    }
