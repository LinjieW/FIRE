"""Everything this app knows about your plan, in one file you can paste elsewhere.

ROADMAP 4.0 Phase 4: bundle the DecisionPacket, the review memo, the
attribution and the triggered limitations into Markdown + JSON, with a
constraint preamble and a self-check list, so a user can hand the whole
picture to any external AI. The app itself keeps making zero network
requests; the user carries the file.

**It is NOT de-identified, and it says so first.** Ruled 2026-08-14, and the
ruling reversed the original plan. ROADMAP promised "de-identified by default,
reusing the 2.0 de-id pipeline"; the opening-conditions review found there is
no such pipeline -- what exists guards this app's own BUILT-IN DEFAULTS
against matching a real calibration baseline, which is a completely different
thing from scrubbing a user's own plan. Nothing in this repository can
anonymise your numbers.

The choice was to write that pipeline or to withdraw the promise. The promise
was withdrawn, because a de-identification claim is the kind that has to be
true in every corner -- an age, a state and a portfolio value are jointly
identifying even with a name removed -- and a half-built one is worse than
none: it is the sentence that makes someone paste a file they would otherwise
have read first.

So the pack states in its first lines what it contains and who can see it. The
warning is at the top rather than the bottom because a footer is what people
scroll past on their way to the copy button.

**Assembled, not computed.** Every section is something the app already
produced. This module invents no analysis, which is why it can be honest about
what is missing: a section that was never run says so rather than being
quietly absent.
"""
from __future__ import annotations

import json
from typing import Any, Optional

#: What the reader of this file -- human or machine -- is asked to respect.
#: First, because a preamble after the data is a preamble nobody read.
PREAMBLE_EN = """\
## Read this before using anything below

This file was exported from a local-first retirement modelling app. Three
things about it decide whether the advice you get back is worth anything.

**These numbers are one person's real financial position.** The export is NOT
anonymised. It contains actual balances, ages, salaries and locations as they
were entered. Once this file leaves the app, this app's privacy properties no
longer apply to it -- whoever or whatever you paste it into now has these
numbers, under their terms, not the app's.

**Every figure is conditional on assumptions that are listed here.** The
success rate is not a probability about the world; it is the fraction of
simulated paths that survived under the stated return, inflation and tax
assumptions. The limitations section names what is approximated and what is
not modelled at all. Advice that ignores that section is advice about a model
rather than about a plan.

**Sampling error is stated, and it is not the main uncertainty.** The interval
given for a success rate reflects the sampler only. Running more paths shrinks
it and makes no assumption truer.

## Self-check before you answer

1. Does your conclusion depend on a module that is switched OFF here? Say so
   rather than assuming it is negligible.
2. Does it depend on an inheritance, a sale, or another one-off inflow? Would
   it survive without it?
3. Are you comparing options that are all on this list, or recommending one
   that was never measured? An unmeasured option has no verdict here.
4. Does the difference you are pointing at exceed the sampling interval given?
5. Are you treating a `None` as a zero anywhere? In this app they are
   different: `None` means not measured, and zero means measured as none.
"""

PREAMBLE_ZH = """\
## 用下面的内容之前先读这段

这个文件从一个本地优先的退休测算 App 导出。有三件事决定了你拿回来的建议是否值钱。

**这些数字是一个人真实的财务状况。这份导出没有做任何脱敏**，里面是照原样填入的
实际余额、年龄、薪资与所在地。一旦这个文件离开 App，这个 App 的隐私性质就不再适用于它 ——
你把它粘贴给谁或粘贴给什么，对方就拿到了这些数字，按对方的条款，不是 App 的。

**每个数字都以这里列出的假设为条件。** 成功率不是关于现实世界的概率，
它是在给定收益率、通胀与税制假设下存活下来的模拟路径比例。局限那一节写明了
哪些是近似、哪些**根本没有建模**。无视那一节的建议，是关于一个模型的建议，不是关于一份计划的。

**抽样误差已经给出，而且它不是主要的不确定性。** 成功率旁边的区间只反映抽样本身。
跑更多路径会让它变窄，但不会让任何一条假设变得更真。

## 回答之前请自查

1. 你的结论是否依赖某个在这里**被关掉**的模块？请直说，而不是默认它可以忽略。
2. 是否依赖一笔遗产、一次卖出或其它一次性流入？没有它，结论还成立吗？
3. 你在比较的选项是否都在这份清单上？还是在推荐一个**从未被测量过**的选项？
   没有被测量的选项在这里没有结论。
4. 你所指出的差别，是否超过了这里给出的抽样区间？
5. 你有没有把某处的 `None` 当成 0？在这个 App 里它们不同：
   `None` 表示**没测**，0 表示**测了、结果是零**。
"""


def _section(title: str, body: Optional[str], absent_note: str) -> str:
    """A section that was never produced says so rather than being omitted.

    An absent heading reads as "there was nothing to say"; this project's
    whole discipline is that not-measured and measured-as-nothing are
    different, and a briefing file is exactly where that distinction gets
    flattened by whoever reads it next.
    """
    if body:
        return "## %s\n\n%s\n" % (title, body)
    return "## %s\n\n_%s_\n" % (title, absent_note)


def build(*, config: dict, packet: Optional[dict] = None,
          memo: Optional[dict] = None, attribution: Optional[dict] = None,
          limitations: Optional[dict] = None,
          sampling_error: Optional[dict] = None,
          language: str = "zh") -> dict:
    """Assemble the pack. Returns markdown, a JSON payload, and its own audit.

    `contains` is part of the return rather than something the caller works
    out: a user deciding whether to paste this somewhere should be able to
    read what is in it without parsing it.
    """
    if not isinstance(config, dict):
        raise TypeError("config must be a dict")
    zh = language == "zh"
    preamble = PREAMBLE_ZH if zh else PREAMBLE_EN

    absent = ("这一节没有内容，因为对应的分析这次没有运行 —— 这不等于「没有问题」。"
              if zh else
              "This section is empty because that analysis was not run. That "
              "is not the same as it having found nothing.")

    parts = [
        "# %s" % ("FIRE 计划简报包" if zh else "FIRE plan briefing pack"),
        "",
        preamble,
        _section("决策记录" if zh else "Decision packet",
                 _fenced(packet), absent),
        _section("复核备忘" if zh else "Review memo", _fenced(memo), absent),
        _section("归因" if zh else "Attribution", _fenced(attribution), absent),
        _section("这份配置触发的局限" if zh else "Limitations your config triggers",
                 _fenced(limitations), absent),
        _section("抽样误差" if zh else "Sampling error",
                 _fenced(sampling_error), absent),
        _section("完整配置" if zh else "Full configuration",
                 _fenced(config), absent),
    ]

    payload = {
        "format": "fire-briefing-pack-v1",
        "de_identified": False,
        "contains_real_figures": True,
        "config": config,
        "decision_packet": packet,
        "review_memo": memo,
        "attribution": attribution,
        "limitations": limitations,
        "sampling_error": sampling_error,
    }

    return {
        "markdown": "\n".join(parts),
        "json": payload,
        #: Said in the return value, not only in the file, so a caller cannot
        #: present this as anonymised without contradicting the thing it is
        #: presenting.
        "de_identified": False,
        "warning": (
            "这份导出未经脱敏，包含你的真实数字。离开本 App 的数据不受本 App 的"
            "隐私承诺保护。" if zh else
            "This export is NOT de-identified and contains your real figures. "
            "Data that leaves this app is not covered by this app's privacy "
            "properties."),
        "contains": {
            "decision_packet": packet is not None,
            "review_memo": memo is not None,
            "attribution": attribution is not None,
            "limitations": limitations is not None,
            "sampling_error": sampling_error is not None,
            "config": True,
        },
    }


def _fenced(value: Any) -> Optional[str]:
    if value is None:
        return None
    return "```json\n%s\n```" % json.dumps(value, ensure_ascii=False, indent=2,
                                           sort_keys=True)
