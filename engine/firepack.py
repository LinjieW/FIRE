"""4.0 Phase 1 · the `.firepack` container: declarative data, signed.

A pack is a JSON document with a manifest and a detached Ed25519 signature. The
container exists so that a data file's provenance survives leaving this
repository -- content hash, schema version, vintage window, the engine versions
it applies to, and a signature over all of it.

**Verification only, and deliberately no new dependency.** Neither PyNaCl nor
`cryptography` is available here, and adding a native crypto wheel to a frozen
universal2 bundle is a real cost -- this build already fights NumPy's universal2
wheels. Ed25519 *verification* is 60 lines of modular arithmetic (RFC 8032
§5.1.7), it runs once per pack, and a pure-Python implementation cannot break a
cross-architecture build. Signing is not implemented here at all: the private
key never belongs in the app.

**Checked against RFC 8032's own test vectors**, not against itself. A signature
verifier that is subtly wrong is worse than none, because it accepts forgeries
while looking like it works; `tests/test_firepack.py` runs the published
vectors, including ones that must be REJECTED.

Scope note: packs ship with the build in this version. Runtime import is
deferred along with multi-vintage coexistence -- see ROADMAP_4.0.md Phase 1 --
because an imported pack would leave two users on one build running different
tax tables under one release identity. This module is the part that is useful
either way.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Optional

CONTAINER_FORMAT = "firepack-v1"

# --- Ed25519 verification, RFC 8032 -----------------------------------------
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _recover_x(y: int, sign: int) -> Optional[int]:
    if y >= _P:
        return None
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _I) % _P
    if (x * x - xx) % _P != 0:
        return None
    if x % 2 != sign:
        x = _P - x
    return x


def _point_add(p, q):
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = ((y1 - x1) * (y2 - x2)) % _P
    b = ((y1 + x1) * (y2 + x2)) % _P
    c = (2 * t1 * t2 * _D) % _P
    d = (2 * z1 * z2) % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return ((e * f) % _P, (g * h) % _P, (f * g) % _P, (e * h) % _P)


def _scalar_mult(p, e: int):
    q = (0, 1, 1, 0)
    while e > 0:
        if e & 1:
            q = _point_add(q, p)
        p = _point_add(p, p)
        e >>= 1
    return q


_BY = (4 * pow(5, _P - 2, _P)) % _P
_BX = _recover_x(_BY, 0)
_B = (_BX % _P, _BY % _P, 1, (_BX * _BY) % _P)


def _decode_point(data: bytes):
    if len(data) != 32:
        return None
    value = int.from_bytes(data, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, (x * y) % _P)


def _equal(p, q) -> bool:
    x1, y1, z1, _ = p
    x2, y2, z2, _ = q
    return (x1 * z2 - x2 * z1) % _P == 0 and (y1 * z2 - y2 * z1) % _P == 0


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """RFC 8032 §5.1.7. Returns False rather than raising on any malformed
    input -- a caller must not have to distinguish "bad signature" from
    "bad encoding" to stay safe."""
    if len(signature) != 64 or len(public_key) != 32:
        return False
    a = _decode_point(public_key)
    if a is None:
        return False
    r = _decode_point(signature[:32])
    if r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:                       # non-canonical S: reject, do not reduce
        return False
    digest = hashlib.sha512(signature[:32] + public_key + message).digest()
    k = int.from_bytes(digest, "little") % _L
    return _equal(_scalar_mult(_B, s), _point_add(r, _scalar_mult(a, k)))


# --- the container ----------------------------------------------------------

class PackError(ValueError):
    """A pack this module refuses rather than loads."""


def canonical_bytes(value: Mapping[str, Any]) -> bytes:
    """The same canonicalisation `fire_rule_pack` hashes with, deliberately:
    two different canonical forms for one payload is two different identities
    for one set of tax tables."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def build_manifest(payload: Mapping[str, Any], *, pack_id: str,
                   schema_version: int, vintage_start: str, vintage_end: str,
                   engine_min: str, engine_max: str) -> dict:
    """Everything a verifier needs without trusting the payload's own claims."""
    return {
        "format": CONTAINER_FORMAT,
        "pack_id": pack_id,
        "schema_version": int(schema_version),
        "content_sha256": hashlib.sha256(canonical_bytes(payload)).hexdigest(),
        "vintage_start": vintage_start,
        "vintage_end": vintage_end,
        "engine_min": engine_min,
        "engine_max": engine_max,
    }


def verify(container: Mapping[str, Any], public_key: bytes) -> dict:
    """Fail closed. Every refusal names what failed, and none of them fall back.

    The order matters: shape, then signature, then content hash. Checking the
    hash first would let an attacker-supplied payload decide which error the
    user sees.
    """
    if not isinstance(container, Mapping):
        raise PackError("pack is not an object")
    for field in ("format", "manifest", "payload", "signature"):
        if field not in container:
            raise PackError("pack is missing %r" % field)
    if container["format"] != CONTAINER_FORMAT:
        raise PackError("unknown container format %r; this app reads %s"
                        % (container["format"], CONTAINER_FORMAT))
    manifest = container["manifest"]
    payload = container["payload"]
    if not isinstance(manifest, Mapping) or not isinstance(payload, Mapping):
        raise PackError("manifest and payload must both be objects")

    try:
        signature = bytes.fromhex(str(container["signature"]))
    except ValueError as exc:
        raise PackError("signature is not hex") from exc
    if not ed25519_verify(public_key, canonical_bytes(manifest), signature):
        raise PackError("signature does not verify against this app's public "
                        "key -- refusing, and nothing was written")

    declared = manifest.get("content_sha256")
    actual = hashlib.sha256(canonical_bytes(payload)).hexdigest()
    if declared != actual:
        # The signature covers the manifest, so a payload swapped after signing
        # lands here rather than at the signature check.
        raise PackError("payload does not match the signed manifest hash "
                        "(signed %s, got %s)" % (declared, actual))

    if not _executable_free(payload):
        raise PackError("payload contains something that is not plain data; a "
                        "pack carries declarative data and never code")
    return dict(payload)


def _executable_free(value: Any) -> bool:
    """A pack must be plain JSON scalars, lists and string-keyed objects.

    ROADMAP's acceptance line is "pack 内不存在任何可执行内容". Checked
    structurally rather than by scanning for keywords, because a keyword list
    is a denylist and this is the one place a denylist is the wrong shape.
    """
    if isinstance(value, Mapping):
        return all(isinstance(k, str) and _executable_free(v)
                   for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return all(_executable_free(item) for item in value)
    return isinstance(value, (str, int, float, bool, type(None)))


# --- pack QA: what a candidate must pass before it is signed ----------------

#: Every component must declare these. A pack whose provenance is partial is a
#: pack that will be trusted more than it deserves in about a year, when nobody
#: remembers which figures were checked.
#:
#: `status` is deliberately NOT here. The first draft required it and the real
#: pack failed every component -- because status is *computed* at run time by
#: `rule_pack_for_run` from the maintenance date and the config's
#: applicability, and storing it in the payload would freeze a judgement that
#: depends on today's date. Requiring a field the data should not carry is the
#: same assumed-shape mistake this file has now made twice.
REQUIRED_COMPONENT_FIELDS = ("id", "label", "source_vintage",
                             "maintenance_due_on", "provenance", "values")

#: `provenance_status` is NOT an enum. It holds a citation --
#: `official_irs_rev_proc_2025_32`, `official_cms_2026_irmaa` -- and this file
#: guessed a vocabulary for it before reading one, which is the second wrong
#: guess about this same payload in one sitting. It is checked as what it is: a
#: non-empty string naming where the figure came from. A closed vocabulary here
#: would reject every real citation and force curators to launder sources into
#: three approved words.


def qa_report(payload: Mapping[str, Any],
              previous: Optional[Mapping[str, Any]] = None) -> dict:
    """Everything wrong with a candidate pack, in one pass.

    Returns a report rather than raising on the first problem: a curator
    fixing an annual refresh wants the whole list, not one item at a time.

    The check that earns its keep is the last one. Comparing against the
    previous pack catches **a value that moved while its vintage did not** --
    the failure that looks like nothing happened, and the one a reader has no
    way to detect later. Everything else here is shape.
    """
    problems = []

    if not isinstance(payload, Mapping):
        return {"ok": False, "problems": ["payload is not an object"]}
    if not _executable_free(payload):
        problems.append("payload contains something that is not plain data")

    components = payload.get("components")
    if not isinstance(components, list) or not components:
        problems.append("payload has no components list")
        components = []

    seen = set()
    for index, component in enumerate(components):
        where = "components[%d]" % index
        if not isinstance(component, Mapping):
            problems.append("%s is not an object" % where)
            continue
        for field in REQUIRED_COMPONENT_FIELDS:
            if not component.get(field):
                problems.append("%s is missing %s" % (where, field))
        component_id = component.get("id")
        if component_id in seen:
            problems.append("%s duplicates id %r" % (where, component_id))
        seen.add(component_id)
        provenance = component.get("provenance_status")
        if provenance is not None and not (isinstance(provenance, str)
                                           and provenance.strip()):
            problems.append("%s provenance_status must name a source, got %r"
                            % (where, provenance))
        due = component.get("maintenance_due_on")
        if due is not None and not _is_iso_date(due):
            problems.append("%s maintenance_due_on is not YYYY-MM-DD: %r"
                            % (where, due))

    if previous is not None:
        problems.extend(_silent_value_changes(payload, previous))

    return {"ok": not problems, "problems": problems,
            "components": len(components),
            "content_sha256": hashlib.sha256(
                canonical_bytes(payload)).hexdigest() if not problems else None}


def _is_iso_date(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    parts = value.split("-")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return False
    year, month, day = (int(part) for part in parts)
    return 1 <= month <= 12 and 1 <= day <= 31 and year >= 1


def _silent_value_changes(payload: Mapping[str, Any],
                          previous: Mapping[str, Any]) -> list:
    """Components whose data moved while their declared vintage did not.

    This is the whole reason a pack carries a vintage per component instead of
    one date for the file. A refreshed table with a stale vintage reads as
    "these are last year's numbers, unchanged" when they are this year's --
    or worse, when someone edited one figure by hand.
    """
    def by_id(pack):
        return {c.get("id"): c for c in (pack.get("components") or [])
                if isinstance(c, Mapping)}

    before, after = by_id(previous), by_id(payload)
    problems = []
    for component_id, component in sorted(after.items()):
        old = before.get(component_id)
        if old is None:
            continue                      # a new component has nothing to drift from
        moved = canonical_bytes(_data_only(component)) != \
            canonical_bytes(_data_only(old))
        if moved and component.get("source_vintage") == old.get("source_vintage"):
            problems.append(
                "component %r changed its data but kept source_vintage %r -- "
                "a refreshed table wearing last year's label"
                % (component_id, component.get("source_vintage")))
    return problems


def _data_only(component: Mapping[str, Any]) -> dict:
    """The component minus its own provenance fields, so a vintage bump is not
    itself counted as a data change."""
    return {k: v for k, v in component.items()
            if k not in ("source_vintage", "maintenance_due_on",
                         "provenance", "provenance_status")}
