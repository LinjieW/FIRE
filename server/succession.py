"""What to hand someone if you are not here to explain it.

S10 ① (idea bank), the content half. The briefing pack already exports a plan,
and it was measured before this was written: it cannot be reused. Its reader
wants analysis and knows the vocabulary. This reader is somebody else, on the
worst day of their year, who may not know what FIRE means and needs to ACT.

**It opens with three things to do, not with a summary.** A document that
begins by explaining a withdrawal strategy to a grieving spouse has chosen the
author's priorities over the reader's.

**The account map refuses credentials at the field level, and that is the
load-bearing property.** A handoff document that CAN hold a password becomes
the place someone puts one -- and then the most sensitive file they own is the
one they were told to give away. So the map holds only what a person needs in
order to FIND an account: institution, kind, roughly where the paperwork is.
Anything that looks like a credential is refused, loudly, with the reason.

**It is not encrypted, and that is a ruling rather than an omission** (user,
2026-08-18, OPEN_ITEMS U17). Encryption would add a failure mode that lands on
exactly the day this document has to work: the successor is the person least
likely to know a passphrase, and a capsule nobody can open has failed
completely rather than partially. The file carries no credentials by
construction, so what it exposes is "how much, at which institution" -- the
kind of thing people already have somewhere to keep. It tells the reader where
to put it instead of inventing a second place to lose a key.

**It does not tell the successor what to do with the money.** It says what
exists, where it is, and what the plan assumed. Deciding is theirs, possibly
with a professional, and a document that arrives with instructions from
someone who is gone would carry more authority than it has earned.
"""
from __future__ import annotations

import re
from typing import Any, Optional

#: Field names a map entry may carry. Anything else is refused rather than
#: dropped: silently discarding a field the user filled in would leave them
#: believing the capsule holds something it does not.
ALLOWED_ENTRY_FIELDS = ("institution", "kind", "where_to_look", "note")

#: Patterns that mean "this is a credential, not a location". Deliberately
#: broad -- a false refusal costs one edit, and a false accept costs the user
#: the one file they were told to hand over.
CREDENTIAL_PATTERNS = (
    (re.compile(r"\b\d{3}-?\d{2}-?\d{4}\b"), "looks like a Social Security number"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "looks like a card or account number"),
    # Latin terms take word boundaries; CJK ones must NOT. `\b` is defined by
    # word characters on either side, and Chinese text has no spaces -- so
    # "密码是 hunter2" slipped through the first version entirely. A bilingual
    # app whose credential check only works in one language is a check that
    # fails for exactly the users writing in the other one.
    (re.compile(r"(?i)\b(password|passcode|passphrase|pin|otp|2fa|"
                r"seed phrase|secret|api[_ ]?key|token)\b"),
     "names a credential"),
    (re.compile(r"(密码|口令|密钥|助记词|账号|卡号|验证码)"),
     "names a credential"),
)


NL = chr(10)


class CredentialRefused(ValueError):
    """Raised instead of storing something that must not be handed over."""


def check_entry(entry: dict) -> dict:
    """One account-map row, or a refusal explaining itself."""
    if not isinstance(entry, dict):
        raise CredentialRefused("each account entry must be a set of fields")
    unknown = [k for k in entry if k not in ALLOWED_ENTRY_FIELDS]
    if unknown:
        raise CredentialRefused(
            "the account map holds only %s -- it refuses %s so that this file "
            "never becomes the place a password lives"
            % (", ".join(ALLOWED_ENTRY_FIELDS), ", ".join(sorted(unknown))))
    for field, value in entry.items():
        if not isinstance(value, str):
            continue
        for pattern, why in CREDENTIAL_PATTERNS:
            if pattern.search(value):
                raise CredentialRefused(
                    "the value in '%s' %s. This document is meant to be handed "
                    "to somebody else; put the credential where you already "
                    "keep credentials and leave a pointer here instead."
                    % (field, why))
    return dict(entry)


def build(*, config: dict, accounts: Optional[list] = None,
          zh: bool = True) -> dict:
    """The capsule's content. Returns markdown plus what it contains."""
    if not isinstance(config, dict):
        raise TypeError("config must be a dict")
    checked = [check_entry(e) for e in (accounts or [])]

    state = config.get("state") or {}
    spend = state.get("expenses_y0")
    first_three = _first_three(zh)
    lines = [_title(zh), "", first_three, "", _what_this_is(zh), ""]

    lines.append("## %s" % ("这份计划假设了什么" if zh else "What the plan assumed"))
    lines.append("")
    lines.append(
        ("每年支出约 %s（今日美元）。" % _money(spend)) if (zh and spend) else
        ("Annual spending of about %s in today's dollars." % _money(spend))
        if spend else
        ("这份计划没有记录年度支出。" if zh else
         "This plan records no annual spending figure."))
    lines.append("")
    lines.append("## %s" % ("账户在哪里" if zh else "Where the accounts are"))
    lines.append("")
    if checked:
        for entry in checked:
            lines.append("- **%s** · %s%s" % (
                entry.get("institution") or ("未填" if zh else "unnamed"),
                entry.get("kind") or ("未填" if zh else "unspecified"),
                (" — %s" % entry["where_to_look"]) if entry.get("where_to_look") else ""))
    else:
        lines.append("**这里是空的。**" if zh else "**This is empty.**")
        lines.append("")
        lines.append(
            "空不等于「没有账户」，只等于「没人填过这一节」。"
            "填它的人只能是你。" if zh else
            "Empty does not mean there are no accounts. It means nobody filled "
            "this in, and you are the only person who can.")
    lines.append("")
    lines.append(_no_credentials_note(zh))
    lines.append("")
    lines.append(_getting_the_data_out(zh))
    lines.append("")
    lines.append(_where_to_keep_this(zh))

    return {
        "format": "fire-succession-capsule-v1",
        "markdown": "\n".join(lines),
        "accounts_recorded": len(checked),
        #: Stated rather than left for the reader to infer, the same way the
        #: briefing pack states what it contains.
        "contains": {
            "credentials": False,
            "account_locations": bool(checked),
            "plan_assumptions": True,
            "recovery_recipe": True,
        },
        "encrypted": False,
        "encryption_note": (
            "刻意不加密（用户裁定 2026-08-18）：口令丢失会让交接在最需要成功的"
            "那天彻底失败。文件不含凭据，请放进你已有的保管处。" if zh else
            "Deliberately unencrypted (ruled 2026-08-18): a lost passphrase "
            "would make the handover fail completely on the day it must work. "
            "The file carries no credentials; keep it where you already keep "
            "such things."),
        "not_included": [
            "任何凭据 —— 按字段拒绝，不是靠提醒" if zh else
            "any credential -- refused per field, not discouraged by a warning",
            "该怎么处理这笔钱的建议" if zh else
            "advice on what to do with the money",
        ],
    }


def _title(zh: bool) -> str:
    return "# 如果我不在了" if zh else "# If I am not here"


def _first_three(zh: bool) -> str:
    if zh:
        return (
            "## 先做这三件事\n\n"
            "1. **什么都不用急着卖。** 这份计划是按几十年算的，"
            "几周之内不做任何决定，不会让它变坏。\n"
            "2. **把下面「账户在哪里」那一节读一遍**，确认你能找到每一个。"
            "找不到的先记下来，不用马上解决。\n"
            "3. **带着这份文件去找一个你信任的专业人士。**"
            "这份文件说的是有什么、在哪里、当初假设了什么 —— "
            "它不告诉你该怎么做，那需要一个了解你处境的人。")
    return (
        "## Do these three things first\n\n"
        "1. **Nothing needs to be sold quickly.** This plan was built over "
        "decades; making no decision for several weeks will not damage it.\n"
        "2. **Read the account list below** and check you can find each one. "
        "Write down the ones you cannot; they do not need solving today.\n"
        "3. **Take this document to a professional you trust.** It says what "
        "exists, where it is, and what was assumed. It does not tell you what "
        "to do -- that needs somebody who knows your situation.")


def _what_this_is(zh: bool) -> str:
    if zh:
        return ("## 这份文件是什么\n\n"
                "它由一个叫 FIRE Modeling 的退休规划工具生成，"
                "那个工具只在本人的电脑上运行、从不联网。"
                "**你不需要那个工具也能用这份文件** —— 它就是一份普通文本。")
    return ("## What this document is\n\n"
            "It was produced by a retirement planning tool called FIRE "
            "Modeling, which runs only on its owner's computer and never "
            "connects to a network. **You do not need that tool to use this "
            "document**; it is ordinary text.")


def _no_credentials_note(zh: bool) -> str:
    if zh:
        return ("> **这份文件里没有任何密码、账号或凭据，那是刻意的。**\n"
                "> 生成它的工具在字段层拒绝这些东西 —— "
                "一份装得下密码的交接文件，会变成有人把密码写进去的地方，"
                "而它本来就是要交给别人的。")
    return ("> **There are no passwords, account numbers or credentials in "
            "this document, and that is deliberate.**\n"
            "> The tool refuses them per field. A handoff document that CAN "
            "hold a password becomes the place someone puts one -- and it is "
            "the file that was always meant to be given away.")


def _getting_the_data_out(zh: bool) -> str:
    if zh:
        return ("## 如果需要原始数据\n\n"
                "计划本身存在一个普通的 SQLite 文件里，没有加密、没有专有格式。"
                "仓库里的 `tools/recover_without_app.py` 只用 Python 标准库"
                "就能把它还成 JSON，**不需要这个 App 还在**。")
    return ("## If the raw data is needed\n\n"
            "The plan lives in an ordinary SQLite file -- not encrypted, not a "
            "proprietary format. `tools/recover_without_app.py` turns it into "
            "JSON using only the Python standard library, **and does not need "
            "this app to still exist**.")


def _where_to_keep_this(zh: bool) -> str:
    """The instruction that replaces encryption.

    Ruled 2026-08-18 (OPEN_ITEMS U17): no passphrase. A capsule nobody can
    open has failed completely rather than partially, and the successor is
    the person least likely to have a passphrase -- the failure would land
    on precisely the day this has to work. So the safeguard is telling the
    owner where to put a plain file, beside the things they already protect.
    """
    if zh:
        return NL.join([
            "## 这份文件该放在哪", "",
            "**它没有加密，那是刻意的。** 加了密就多一个失效方式：口令丢了它就"
            "永远打不开 —— 而最可能不知道口令的人，正是最需要读它的那个人。", "",
            "它不含任何凭据，所以敏感的是「有多少钱、在哪家机构」。"
            "**把它放在你已经存放这类东西的地方**：保险箱、密码管理器的附件、"
            "或者律师那里。然后告诉一个人它在哪 —— "
            "**没人知道它存在的交接包，等于不存在。**",
        ])
    return NL.join([
        "## Where to keep this", "",
        "**It is not encrypted, and that is deliberate.** Encryption adds a "
        "way to fail: a lost passphrase means it never opens again, and the "
        "person least likely to have the passphrase is the one who most "
        "needs to read it.", "",
        "It carries no credentials, so what is sensitive here is how much "
        "and at which institution. **Keep it where you already keep that "
        "kind of thing** -- a safe, an attachment in your password manager, "
        "or with your lawyer. Then tell one person where it is: **a capsule "
        "nobody knows about is a capsule that does not exist.**",
    ])

def _money(value: Any) -> str:
    try:
        return "${:,.0f}".format(float(value))
    except (TypeError, ValueError):
        return "—"
