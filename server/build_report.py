"""
build_report.py — standalone HTML report aligned with the app's current design
language (verdict-first, sampling-error quantification, honesty panel,
conclusions & limitations passed straight from the UI so document == app).

build(results, extra=None) -> html string
  results : the run_full payload (meta + home [+ relocation]), dist stripped.
  extra   : optional dict from the frontend —
            { lang: "zh"|"en",
              verdict_html: str,            # UI verdict (links stripped here)
              conclusions: [{tone,title,body}],
              limitations: [str],
              ab: { a: {label, s: <home summary>},
                    b: {...} } or None }
Backward compatible: extra=None renders the zh document from results alone.
"""
from __future__ import annotations

import math
import re
from datetime import date
from html import escape
from html.parser import HTMLParser
from string import Template


def _pct(x, d=2):
    return f"{x * 100:.{d}f}%" if x is not None else "—"


def _dollar(x):
    return f"${x:,.0f}" if x is not None else "—"


def _age(x):
    return f"{x:.0f}" if x is not None else "—"


class _FragmentSanitizer(HTMLParser):
    """Tiny allow-list sanitizer for the few formatting tags emitted by the UI."""
    ALLOWED = {"b", "strong", "em", "i", "br", "code", "span", "div"}
    CLASSES = {"v-main", "v-sub"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.ALLOWED:
            return
        attr_text = ""
        if tag in {"div", "span"}:
            classes = next((v for k, v in attrs if k == "class"), "") or ""
            safe = " ".join(c for c in classes.split() if c in self.CLASSES)
            if safe:
                attr_text = f' class="{escape(safe, quote=True)}"'
        self.out.append(f"<{tag}{attr_text}>")

    def handle_endtag(self, tag):
        if tag in self.ALLOWED and tag != "br":
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(escape(data))


def _safe_fragment(value: str) -> str:
    parser = _FragmentSanitizer()
    parser.feed(str(value or ""))
    parser.close()
    return "".join(parser.out)


_RULE_PACK_COMPONENT_IDS = frozenset({
    "us_federal_tax", "medicare_irmaa", "contribution_limits",
    "aca_marketplace", "ssa_benefit_rules", "ssa_statement_import",
})
_RULE_PACK_STATUSES = frozenset({"current", "stale", "review_required"})
_RULE_PACK_COMPONENT_STATUSES = _RULE_PACK_STATUSES | {"not_used_at_run"}
_RULE_PACK_REVIEW_STATUSES = frozenset({
    "within_recorded_window", "stale", "review_required",
})
_RULE_PACK_SOURCES = frozenset({
    "pack", "matches_pack_value", "user_or_legacy_override",
})
_RULE_PACK_SOURCE_BY_COMPONENT = {
    "medicare_irmaa": frozenset({"pack"}),
    "ssa_statement_import": frozenset({"pack"}),
    "contribution_limits": frozenset({
        "matches_pack_value", "user_or_legacy_override",
    }),
    "aca_marketplace": frozenset({
        "matches_pack_value", "user_or_legacy_override",
    }),
    "ssa_benefit_rules": frozenset({
        "matches_pack_value", "user_or_legacy_override",
    }),
    "us_federal_tax": _RULE_PACK_SOURCES,
}
_RULE_PACK_EVALUATION_BASIS = "config_applicability_not_path_instrumentation"


def _valid_iso_date(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _valid_rule_pack_receipt(receipt) -> bool:
    """Accept only a complete v1 result receipt; reject unknown/partial data.

    Reports may open historical results, so this validates the receipt shape
    rather than comparing it to today's pack.  A malformed receipt is legacy
    evidence, never a current-year claim.
    """
    if not isinstance(receipt, dict):
        return False
    schema_version = receipt.get("schema_version")
    if (isinstance(schema_version, bool)
            or not isinstance(schema_version, (int, float))
            or schema_version != 1):
        return False
    if receipt.get("delivery") != "offline_embedded":
        return False
    if receipt.get("runtime_network_refresh") is not False:
        return False
    pack_id = receipt.get("pack_id")
    content_sha256 = receipt.get("content_sha256")
    if not isinstance(pack_id, str) or not re.fullmatch(
            r"us-offline-[0-9a-f]{16}", pack_id):
        return False
    if not isinstance(content_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", content_sha256):
        return False
    if pack_id != "us-offline-" + content_sha256[:16]:
        return False
    if not _valid_iso_date(receipt.get("evaluated_on")):
        return False
    status = receipt.get("status")
    if status not in _RULE_PACK_STATUSES or receipt.get("conclusion_status") != status:
        return False
    if receipt.get("evaluation_basis") != _RULE_PACK_EVALUATION_BASIS:
        return False
    rows = receipt.get("components")
    if not isinstance(rows, list) or len(rows) != len(_RULE_PACK_COMPONENT_IDS):
        return False
    if any(not isinstance(row, dict) for row in rows):
        return False
    ids = [row.get("id") for row in rows]
    if (any(not isinstance(item, str) for item in ids)
            or set(ids) != _RULE_PACK_COMPONENT_IDS
            or len(set(ids)) != len(ids)):
        return False
    evaluated_on = receipt["evaluated_on"]
    for row in rows:
        component_id = row.get("id")
        if not isinstance(row.get("label"), str) or not row["label"]:
            return False
        if not isinstance(row.get("source_vintage"), str) or not row["source_vintage"]:
            return False
        maintenance_due = row.get("maintenance_due_on")
        if not _valid_iso_date(maintenance_due):
            return False
        applicability = row.get("applicability")
        row_status = row.get("status")
        if applicability not in {"applicable", "not_used_at_run"}:
            return False
        if row_status not in _RULE_PACK_COMPONENT_STATUSES:
            return False
        if applicability == "applicable" and row_status == "not_used_at_run":
            return False
        if applicability == "not_used_at_run" and row_status != "not_used_at_run":
            return False
        review_status = row.get("review_status")
        if review_status not in _RULE_PACK_REVIEW_STATUSES:
            return False
        effective_source = row.get("effective_source")
        if effective_source not in _RULE_PACK_SOURCE_BY_COMPONENT[component_id]:
            return False
        mismatched_fields = row.get("mismatched_fields")
        if (not isinstance(mismatched_fields, list)
                or any(not isinstance(field, str) or not field
                       for field in mismatched_fields)
                or len(set(mismatched_fields)) != len(mismatched_fields)):
            return False
        override = effective_source == "user_or_legacy_override"
        if override != bool(mismatched_fields):
            return False
        past_due = evaluated_on > maintenance_due
        if applicability == "applicable":
            expected_review = (
                "stale" if past_due
                else ("review_required" if override else "within_recorded_window")
            )
            expected_status = "stale" if past_due else (
                "review_required" if override else "current")
        else:
            expected_review = (
                "stale" if past_due
                else ("review_required" if override else "within_recorded_window")
            )
            expected_status = "not_used_at_run"
        if review_status != expected_review or row_status != expected_status:
            return False
    applicable_ids = receipt.get("applicable_component_ids")
    stale_ids = receipt.get("stale_component_ids")
    review_ids = receipt.get("review_required_component_ids")
    def _unique_known_ids(value):
        return (isinstance(value, list)
                and all(isinstance(item, str) for item in value)
                and len(value) == len(set(value))
                and all(item in _RULE_PACK_COMPONENT_IDS for item in value))
    if not all(_unique_known_ids(value) for value in
               (applicable_ids, stale_ids, review_ids)):
        return False
    by_id = {row["id"]: row for row in rows}
    if set(applicable_ids) != {
            row["id"] for row in rows if row["applicability"] == "applicable"}:
        return False
    if set(stale_ids) != {
            row["id"] for row in rows
            if row["applicability"] == "applicable" and row["status"] == "stale"}:
        return False
    if set(review_ids) != {
            row["id"] for row in rows
            if row["applicability"] == "applicable" and row["status"] == "review_required"}:
        return False
    expected_overall = (
        "stale" if stale_ids else ("review_required" if review_ids else "current"))
    if status != expected_overall:
        return False
    # Keep this local lookup explicit: it makes malformed duplicate/unknown
    # component rows fail closed even if the list checks above are changed.
    return len(by_id) == len(_RULE_PACK_COMPONENT_IDS)


TXT = {
    "zh": dict(
        doc="FIRE 蒙特卡洛分析", core="核心结果", scen_home="情景 · 本土",
        scen_reloc="情景 · 搬迁", succ="终身偿付率", succ_note="三分支口径",
        fire="FIRE 年龄 P50", cons="P50 年消费 · real", term="P50 终值 · real",
        ss="P50 社保终生 · real", reached="到达 FI 率", solv="FIRE 后偿付率",
        ms="里程碑", ms_note="名义余额首次跨越 · 持续工作反事实", target="目标",
        prob="到达概率", medage="中位年龄", rng="P10–P90",
        pcts="期末组合分位数", basis="口径", real="实际 (real)", nom="名义",
        ab="方案 A/B 对比", metric="指标", honesty="方法与诚实度",
        inv="现金流对账", inv_note="到账的结构化收入年份按实际现金精确对账；无到账的成功年份保留每年不超过 $1 的历史提取容差（每次运行抽样 ≤400 条路径）",
        proto="运行协议", se="成功率抽样标准误",
        se_note="仅反映蒙特卡洛抽样，不含输入假设本身的不确定性",
        concl="结论（由本次运行的数字生成）", lim="模型未捕捉 / 近似处理（局限声明）",
        footer="本报告为个人财务建模输出，属教育性质的情景分析，不构成投资、税务或法律建议。数字随输入而变；请以自身真实数据核对假设。",
        gen="生成于", engine="引擎", paths_w="路径", scen_w="情景",
        event_fail="强制事件支付失败", event_note="必需支出未付足",
    ),
    "en": dict(
        doc="FIRE Monte Carlo Analysis", core="Headline results",
        scen_home="Scenario · Home", scen_reloc="Scenario · Relocation",
        succ="Lifetime success", succ_note="three-branch basis",
        fire="FIRE age P50", cons="P50 spend · real", term="P50 terminal · real",
        ss="P50 lifetime SS · real", reached="Reached-FI rate", solv="Post-FIRE solvency",
        ms="Milestones", ms_note="first nominal crossing · keep-working counterfactual",
        target="Target", prob="Reach prob.", medage="Median age", rng="P10–P90",
        pcts="Terminal percentiles", basis="Basis", real="Real", nom="Nominal",
        ab="Scenario A/B", metric="Metric", honesty="Method & honesty",
        inv="Cash-accounting check",
        inv_note="Actual structured-income receipt years reconcile to delivered cash; successful no-receipt years retain at most $1/year of historical withdrawal tolerance (≤400 sampled paths/run)",
        proto="Protocol", se="Success-rate sampling SE",
        se_note="Monte Carlo sampling only — not uncertainty in the assumptions themselves",
        concl="Conclusions (generated from this run)", lim="What the model does not capture (limitations)",
        footer="Personal financial modeling output — educational scenario analysis, not investment, tax, or legal advice. Numbers follow inputs; verify assumptions against your own data.",
        gen="Generated", engine="engine", paths_w="paths", scen_w="scenario(s)",
        event_fail="Mandatory-event failure", event_note="unpaid required outflow",
    ),
}

CSS = """
:root{--cream:#F7F4ED;--ivory:#FBF9F4;--card:#fff;--tint:#F1ECE0;
--ink:#1A1815;--ink2:#3D3833;--ink3:#6B645B;--mut:#8B8478;
--forest:#2A4A3A;--ox:#722F37;--gold:#8A6420;--warn:#C9701C;--bad:#9A2A2A;
--rule:rgba(26,24,21,.12);--rule2:rgba(26,24,21,.24);
--serif:'Songti SC','Noto Serif SC',Georgia,serif;
--sans:'PingFang SC','Noto Sans SC',system-ui,sans-serif;
--mono:ui-monospace,'SF Mono',Menlo,monospace}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--cream);color:var(--ink);font-family:var(--serif);
font-size:15px;line-height:1.7;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.wrap{max-width:920px;margin:0 auto;padding:44px 30px 70px}
h1{font-size:34px;font-weight:600;line-height:1.15;margin-bottom:4px}
.sub{font-family:var(--sans);font-size:13px;color:var(--ink3);
border-bottom:1px solid var(--rule);padding-bottom:16px;margin-bottom:26px}
h2{font-size:21px;font-weight:600;border-bottom:2px solid var(--ox);
padding-bottom:5px;margin:36px 0 16px}
.verdict{border:1px solid var(--rule);border-left:4px solid var(--forest);
background:var(--card);padding:20px 24px;margin:8px 0 6px}
.verdict.warn{border-left-color:var(--warn)}.verdict.bad{border-left-color:var(--bad)}
.verdict .v-main{font-size:18px;line-height:1.65}
.verdict .v-main b{font-family:var(--mono);color:var(--forest)}
.verdict.warn .v-main b{color:var(--warn)}
.verdict .v-sub{font-family:var(--sans);font-size:11.5px;color:var(--ink3);margin-top:10px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;
background:var(--rule);border:1px solid var(--rule);margin:14px 0}
.cell{background:var(--card);padding:16px 15px}
.cell .lab{font-family:var(--sans);font-size:10px;font-weight:600;letter-spacing:.06em;
text-transform:uppercase;color:var(--mut);margin-bottom:8px}
.cell .val{font-family:var(--mono);font-size:22px;font-weight:600;line-height:1}
.cell .val.g{color:var(--forest)}.cell .val.o{color:var(--ox)}
.cell .note{font-family:var(--sans);font-size:10.5px;color:var(--ink3);margin-top:6px}
table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:12px;margin:10px 0}
th,td{padding:7px 10px;text-align:right;border-bottom:1px solid var(--rule)}
th:first-child,td:first-child{text-align:left;font-family:var(--sans);font-weight:600;color:var(--ink2)}
th{font-family:var(--sans);font-size:10.5px;font-weight:600;letter-spacing:.04em;
text-transform:uppercase;color:var(--mut);border-bottom:1px solid var(--rule2)}
td.g{color:var(--forest);font-weight:600}td.m{color:var(--mut)}
.card{background:var(--card);border:1px solid var(--rule);border-left:3px solid #8B7355;
padding:13px 17px;margin-bottom:10px;break-inside:avoid}
.card.good{border-left-color:var(--forest)}.card.warn{border-left-color:var(--warn)}
.card.neutral{border-left-color:#C9A055}
.card .t{font-family:var(--sans);font-size:12.5px;font-weight:700;margin-bottom:4px}
.card .b{font-family:var(--sans);font-size:12px;color:var(--ink2);line-height:1.7}
.card .b b{font-family:var(--mono);color:var(--ox)}
.rulepack{background:var(--ivory);border:1px solid var(--rule);border-left:4px solid var(--forest);
padding:14px 18px;margin:8px 0 18px;break-inside:avoid}
.rulepack.warn{border-left-color:var(--warn)}.rulepack .t{font-family:var(--sans);
font-size:12.5px;font-weight:700;margin-bottom:5px}.rulepack .b{font-family:var(--sans);
font-size:11.5px;color:var(--ink2);line-height:1.65}.rulepack code{font-family:var(--mono);
font-size:10.5px;color:var(--gold)}
ul.lim{list-style:none;margin-top:6px}
ul.lim li{font-family:var(--sans);font-size:11.5px;color:var(--ink2);line-height:1.6;
padding:7px 0 7px 16px;border-bottom:1px solid var(--rule);position:relative;break-inside:avoid}
ul.lim li::before{content:'—';position:absolute;left:0;color:var(--gold)}
ul.lim li b{font-family:var(--mono);color:var(--ox)}
.hon{display:grid;grid-template-columns:1fr 1fr 1fr;gap:1px;background:var(--rule);
border:1px solid var(--rule);margin:12px 0}
.hon>div{background:var(--ivory);padding:13px 15px}
.hon .k{font-family:var(--sans);font-size:10px;font-weight:600;letter-spacing:.06em;
text-transform:uppercase;color:var(--mut);margin-bottom:6px}
.hon .v{font-family:var(--mono);font-size:16px;font-weight:600;color:var(--gold)}
.hon .s{font-family:var(--sans);font-size:10px;color:var(--ink3);margin-top:5px;line-height:1.5}
.footer{margin-top:44px;padding-top:14px;border-top:1px solid var(--rule);
font-family:var(--sans);font-size:10.5px;color:var(--mut);line-height:1.7}
@media print{@page{margin:12mm} body{background:#fff}
h2{break-after:avoid}.grid,.verdict,table{break-inside:avoid}}
@media(max-width:700px){.grid{grid-template-columns:1fr 1fr}.hon{grid-template-columns:1fr}}
"""


def _scenario_cells(s, T):
    fa, mc, tr = s["fire_age"], s["mean_real_consumption"], s["terminal_real"]
    event_cell = (f'<div class="cell"><div class="lab">{T["event_fail"]}</div>'
                  f'<div class="val o">{_pct(s["event_shortfall_rate"])}</div>'
                  f'<div class="note">{T["event_note"]}</div></div>'
                  if s.get("event_shortfall_rate") else "")
    return f"""
<div class="grid">
 <div class="cell"><div class="lab">{T['succ']}</div><div class="val g">{_pct(s['lifetime_success'])}</div><div class="note">{T['succ_note']}</div></div>
 <div class="cell"><div class="lab">{T['fire']}</div><div class="val">{_age(fa['p50'])}</div><div class="note">{T['rng']}: {_age(fa['p10'])}–{_age(fa['p90'])}</div></div>
 <div class="cell"><div class="lab">{T['cons']}</div><div class="val g">{_dollar(mc['p50'])}</div><div class="note">P10 {_dollar(mc['p10'])} · P90 {_dollar(mc['p90'])}</div></div>
 <div class="cell"><div class="lab">{T['term']}</div><div class="val g">{_dollar(tr['p50'])}</div><div class="note">P10 {_dollar(tr['p10'])} · P90 {_dollar(tr['p90'])}</div></div>
 <div class="cell"><div class="lab">{T['reached']}</div><div class="val">{_pct(s['reached_fi_rate'])}</div><div class="note">&nbsp;</div></div>
 <div class="cell"><div class="lab">{T['solv']}</div><div class="val">{_pct(s['post_fire_solvency'])}</div><div class="note">&nbsp;</div></div>
 <div class="cell"><div class="lab">{T['ss']}</div><div class="val">{_dollar(s['ss_total_real']['p50'])}</div><div class="note">&nbsp;</div></div>
 <div class="cell"><div class="lab">{T['inv']}</div><div class="val" style="font-size:15px">{s.get('invariant_max_rel_error', 0):.1e}</div><div class="note">{T['inv_note']}</div></div>
 {event_cell}
</div>"""


def _milestone_rows(s):
    out = ""
    for k in sorted((s.get("milestones") or {}).keys(), key=lambda x: int(x)):
        m = s["milestones"][k]
        label = f"${int(k) // 1_000_000}M" if int(k) >= 1_000_000 else f"${int(k) // 1000}K"
        out += (f"<tr><td>{label}</td><td>{_pct(m['reach_probability'])}</td>"
                f"<td>{_age(m['median_age'])}</td>"
                f"<td class='m'>{_age(m['p10_age'])} – {_age(m['p90_age'])}</td></tr>")
    return out


def _terminal_rows(s, T):
    tr, tn = s["terminal_real"], s["terminal_nominal"]
    return (f"<tr><td>{T['real']}</td><td class='g'>{_dollar(tr['p10'])}</td>"
            f"<td class='g'>{_dollar(tr['p50'])}</td><td class='g'>{_dollar(tr['p90'])}</td></tr>"
            f"<tr><td class='m'>{T['nom']}</td><td class='m'>{_dollar(tn['p10'])}</td>"
            f"<td class='m'>{_dollar(tn['p50'])}</td><td class='m'>{_dollar(tn['p90'])}</td></tr>")


def _rule_pack_block(meta: dict, lang: str) -> str:
    """Render the result-bound receipt; never trust client ``extra`` for it."""
    rp = meta.get("rule_pack") if isinstance(meta, dict) else None
    zh = lang == "zh"
    if not _valid_rule_pack_receipt(rp):
        title = ("历史结果 · 规则年份未记录" if zh
                 else "Legacy result · rule vintage unrecorded")
        body = (
            "这份结果早于离线 rule-pack 合同；报告不会按今天的日期猜测其年份。"
            "重要决定前请用当前版本重跑。"
            if zh else
            "This result predates the offline rule-pack contract. The report "
            "will not guess its vintage using today's date; re-run in a current "
            "build before an important decision."
        )
        return (f'<div class="rulepack warn"><div class="t">{title}</div>'
                f'<div class="b">{body}</div></div>')

    components = rp["components"]
    relevant = [
        row for row in components
        if isinstance(row, dict) and row.get("applicability") == "applicable"
    ]
    component_text = " · ".join(
        f'{escape(str(row.get("label") or row.get("id") or "—"))} '
        f'<code>{escape(str(row.get("source_vintage") or "—"))}</code>'
        for row in relevant
    ) or "—"
    receipt = (
        f'<code>{escape(str(rp.get("pack_id")))}</code> · '
        f'{"评估于" if zh else "evaluated"} '
        f'<code>{escape(str(rp.get("evaluated_on") or "—"))}</code>'
    )
    status = rp.get("status")
    if status == "stale":
        title = (
            "离线规则已过应用维护期 · 正式结论标为 stale" if zh else
            "Offline rules are past the app review date · conclusion marked stale"
        )
        body = (
            f"本次运行可能受这些年度规则影响：{component_text}。重要决定前请核对当年"
            f"官方数字。{receipt}"
            if zh else
            f"This run may be affected by: {component_text}. Verify current "
            f"official figures before an important decision. {receipt}"
        )
        tone = " warn"
    elif status == "review_required":
        title = (
            "计划值与当前 rule pack 不同 · 需要复核" if zh else
            "Plan values differ from this rule pack · review required"
        )
        body = (
            f"差异可能是用户 override，也可能是旧版本默认值；报告不会猜来源。"
            f"{component_text}。{receipt}"
            if zh else
            f"A difference may be a user override or a legacy default; the "
            f"report will not guess which. {component_text}. {receipt}"
        )
        tone = " warn"
    else:
        title = (
            "离线规则仍在应用维护窗口内" if zh else
            "Offline rules are within the app review window"
        )
        body = (
            f"{component_text}。这只表示未超过应用复核期限，不代表税务核验。{receipt}"
            if zh else
            f"{component_text}. This means only that the app review date has "
            f"not passed; it is not tax verification. {receipt}"
        )
        tone = ""
    return (f'<div class="rulepack{tone}"><div class="t">{title}</div>'
            f'<div class="b">{body}</div></div>')


def build(results: dict, extra: dict = None) -> str:
    extra = extra or {}
    lang = extra.get("lang", "zh") if extra.get("lang") in ("zh", "en") else "zh"
    T = TXT.get(lang, TXT["zh"])
    meta = results["meta"]
    home = results["home"]
    reloc = results.get("relocation")
    pr = meta.get("protocol", {})
    rule_pack = _rule_pack_block(meta, lang)
    n = int(pr.get("paths") or home.get("n_paths") or 0) or 1
    ls = home["lifetime_success"]
    se_pp = math.sqrt(max(ls * (1 - ls), 1e-12) / n) * 100

    verdict = ""
    if extra.get("verdict_html"):
        tone = "" if ls >= 0.9 else " warn" if ls >= 0.75 else " bad"
        verdict = f'<div class="verdict{tone}">{_safe_fragment(extra["verdict_html"])}</div>'

    scen = f"<h2>{T['scen_home'] if reloc else T['core']}</h2>" + _scenario_cells(home, T)
    if reloc:
        scen += f"<h2>{T['scen_reloc']}</h2>" + _scenario_cells(reloc, T)

    ms = (f"<h2>{T['ms']} <span style='font-family:var(--sans);font-size:11px;color:var(--mut)'>{T['ms_note']}</span></h2>"
          f"<table><thead><tr><th>{T['target']}</th><th>{T['prob']}</th><th>{T['medage']}</th>"
          f"<th>{T['rng']}</th></tr></thead><tbody>{_milestone_rows(home)}</tbody></table>")

    pcts = (f"<h2>{T['pcts']}</h2><table><thead><tr><th>{T['basis']}</th><th>P10</th><th>P50</th>"
            f"<th>P90</th></tr></thead><tbody>{_terminal_rows(home, T)}</tbody></table>")

    ab = ""
    abd = extra.get("ab")
    if abd and abd.get("a") and abd.get("b"):
        A, B = abd["a"], abd["b"]
        sa, sb = A["s"], B["s"]
        dls = (sb["lifetime_success"] - sa["lifetime_success"]) * 100
        dfa = (sb["fire_age"]["p50"] or 0) - (sa["fire_age"]["p50"] or 0)
        dtr = sb["terminal_real"]["p50"] - sa["terminal_real"]["p50"]
        ab = (f"<h2>{T['ab']}</h2><table><thead><tr><th>{T['metric']}</th>"
              f"<th>A · {escape(str(A.get('label', '')))}</th>"
              f"<th>B · {escape(str(B.get('label', '')))}</th>"
              f"<th>Δ (B−A)</th></tr></thead><tbody>"
              f"<tr><td>{T['succ']}</td><td>{_pct(sa['lifetime_success'])}</td>"
              f"<td>{_pct(sb['lifetime_success'])}</td><td class='g'>{dls:+.2f}pp</td></tr>"
              f"<tr><td>{T['fire']}</td><td>{_age(sa['fire_age']['p50'])}</td>"
              f"<td>{_age(sb['fire_age']['p50'])}</td><td class='g'>{dfa:+.0f}</td></tr>"
              f"<tr><td>{T['term']}</td><td>{_dollar(sa['terminal_real']['p50'])}</td>"
              f"<td>{_dollar(sb['terminal_real']['p50'])}</td><td class='g'>{dtr:+,.0f}</td></tr>"
              f"</tbody></table>")

    concl = ""
    if extra.get("conclusions"):
        cards = "".join(
            f'<div class="card {c.get("tone") if c.get("tone") in ("good", "warn", "neutral") else "neutral"}">'
            f'<div class="t">{_safe_fragment(c.get("title", ""))}</div>'
            f'<div class="b">{_safe_fragment(c.get("body", ""))}</div></div>'
            for c in extra["conclusions"])
        concl = f"<h2>{T['concl']}</h2>{cards}"

    lim = ""
    if extra.get("limitations"):
        lis = "".join(f"<li>{_safe_fragment(x)}</li>"
                      for x in extra["limitations"])
        lim = f"<h2>{T['lim']}</h2><ul class='lim'>{lis}</ul>"

    honesty = f"""
<h2>{T['honesty']}</h2>
<div class="hon">
 <div><div class="k">{T['inv']}</div><div class="v">{home.get('invariant_max_rel_error', 0):.2e}</div><div class="s">{T['inv_note']}</div></div>
 <div><div class="k">{T['proto']}</div><div class="v" style="font-size:12px">{n:,} {T['paths_w']} · seed {pr.get('seed', '—')} · {pr.get('mode', 'sequential')}</div><div class="s">{T['engine']} {pr.get('engine', 'v9.8-rc')} · {pr.get('elapsed_s', '—')}s</div></div>
 <div><div class="k">{T['se']}</div><div class="v">±{se_pp:.2f}pp</div><div class="s">{T['se_note']}</div></div>
</div>"""

    return Template("""<!DOCTYPE html>
<html lang="$lang"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$name · $doc</title><style>$css</style></head><body><div class="wrap">
<h1>$name</h1>
<div class="sub">$doc · $gen $today · $npaths $paths_w × $nscen $scen_w</div>
$rule_pack
$verdict
$scen
$ms
$pcts
$ab
$honesty
$concl
$lim
<div class="footer">$footer</div>
</div></body></html>""").substitute(
        lang=lang, css=CSS, name=escape(str(meta.get("name", "FIRE"))), doc=T["doc"],
        gen=T["gen"], today=date.today().isoformat(),
        npaths=f"{n:,}", paths_w=T["paths_w"],
        nscen=2 if reloc else 1, scen_w=T["scen_w"],
        rule_pack=rule_pack, verdict=verdict, scen=scen, ms=ms, pcts=pcts, ab=ab,
        honesty=honesty, concl=concl, lim=lim, footer=T["footer"],
    )
