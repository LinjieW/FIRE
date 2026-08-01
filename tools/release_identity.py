#!/usr/bin/env python3
"""Build a deterministic, provisional release-evidence package.

This module is deliberately independent from the FIRE runtime.  It records
four different things without pretending that they are one release build:

* the current post-Phase-0A source tree;
* semantic data/rule vintages and the hashes of their source containers;
* the legacy frozen ``FIRE Modeling.app`` artifact;
* a content-derived identity which references the three objects above.

The command never imports the app, reaches the network, uses host timestamps,
or changes the bundle.  Generated evidence files are excluded from the source
allowlist so their own digests cannot become recursive.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import runpy
import stat
import tarfile
import tempfile
from typing import Any, Iterable, Mapping


MANIFEST_VERSION = 1
ARCHIVE_FORMAT = "ustar"
ARCHIVE_ROOT_PREFIX = "fire-modeling-source"

# These are source inputs, not generated evidence.  A file outside this
# inventory is an error unless it is one of the explicit debris/artifact
# exclusions below.  That makes a newly added build or runtime input fail
# closed instead of silently escaping the baseline.
# Every top-level entry must appear either here or in EXCLUDED_TOP_LEVEL, or
# package generation refuses. The distinction, following what was already here:
# current normative documents are evidence and are measured; historical,
# operational, and forward-looking material is named and left out.
#
# Sixteen entries had accumulated in the real tree without being classified
# either way, so `build_package()` would have refused on the repository it is
# meant to describe. Nothing caught it because the existing coverage builds from
# a reduced fixture, which by construction contains only files the scope already
# knows about. There is now a test over the real tree.
ROOT_FILES: dict[str, str] = {
    "AppIcon.icns": "bundle_asset",
    "ADVERSARIAL_AUDIT_2026-07-12.md": "documentation",
    "ATTRIBUTION_ROBUSTNESS_PROTOCOL.md": "documentation",
    "ATTRIBUTION_PROTOCOL_BLOCK_CONDITIONS_2026-07-21.md": "documentation",
    "DESIGN_APPLE_UIUX_2026-07.md": "documentation",
    "DESIGN_M4_BROWSER_CUTOVER_2026-07-25.md": "documentation",
    "DECISION_A3_RECOVERED_DRAFTS_2026-07-27.md": "documentation",
    "DECISION_ROW3_WORKING_DRAFT_2026-07-27.md": "documentation",
    "SCOPE_RULING_2026-07-27.md": "documentation",
    "BACKLOG_POST_PHASE0.md": "documentation",
    "PHASE_0_EXIT_CONTRACT.md": "documentation",
    "PHASE_0_PERSISTENCE_DESIGN.md": "documentation",
    "PHASE1_RULE_PACK_CONTRACT.md": "documentation",
    "PROGRESS_3.0.md": "documentation",
    "README.md": "documentation",
    "ROADMAP_3.0.md": "documentation",
    "ROADMAP_4.0.md": "documentation",
    "WORKSTREAMS.md": "documentation",
    ".gitignore": "repository_contract",
    "build-app.sh": "build_contract",
    "build-requirements.lock": "dependency_lock",
    "build-standalone.sh": "legacy_build_contract",
    "pyi_main.py": "runtime_entrypoint",
}

DIRECTORIES: dict[str, tuple[str, set[str]]] = {
    "engine": ("runtime_engine", {".py", ".json"}),
    "server": ("runtime_server", {".py"}),
    "web": ("runtime_web", {".js", ".css", ".html"}),
    # .json is here for the formal-migration golden vectors, which are test
    # data rather than code but are exactly as load-bearing: the cutover
    # digest comparison is checked against them.
    "tests": ("validation_tests", {".py", ".json"}),
    "tools": ("evidence_tool", {".py"}),
}

# The frozen candidate has a narrower input contract than the broad evidence
# package above.  Documentation and tests remain evidence, but they are not
# build inputs and therefore must not invalidate a candidate runtime identity.
RUNTIME_ROOT_FILES: dict[str, str] = {
    "AppIcon.icns": "effective_bundle_icon",
    "build-app.sh": "frozen_build_contract",
    "build-requirements.lock": "frozen_dependency_lock",
    "pyi_main.py": "frozen_runtime_entrypoint",
}
RUNTIME_DIRECTORIES: dict[str, tuple[str, set[str]]] = {
    "engine": ("runtime_engine", {".py", ".json"}),
    "server": ("runtime_server", {".py"}),
    "web": ("runtime_web", {".js", ".css", ".html"}),
}
RUNTIME_TOOL_FILES = {
    "tools/frozen_identity.py": "runtime_identity_generator",
    "tools/release_identity.py": "runtime_identity_dependency",
}
# Tools that run *around* a release rather than feeding the runtime identity.
# They still have to be named — the point of the check below is that nothing
# enters tools/ unclassified — but their bytes must stay out of the runtime
# manifest. Putting tools/promote.py in RUNTIME_TOOL_FILES would mean editing a
# comment in the promotion orchestrator invalidated a candidate whose runtime
# bytes had not changed, and §8 is explicit that only runtime, schema, server or
# bundled-JS changes do that.
RELEASE_ONLY_TOOL_FILES = {
    "tools/promote.py": "release_orchestrator",
}

# When each hand-run observation in `build_package`'s `test_evidence` block was
# taken.  Per entry, not one date for the block: the generator does not run these,
# so they age independently of each other *and* of the package they are recorded
# in.  A single `observed_on` covering the whole block was itself a misstatement —
# it said 2026-07-14 while carrying a count re-observed on 2026-07-25.
#
# Refreshing an entry means re-running its command by hand and updating both its
# count and its date here. The generator must never guess either.
TEST_EVIDENCE_OBSERVED_ON = {
    "persistence_contracts": "2026-07-14",
    "regression": "2026-07-14",
    "standard_official_replay": "2026-07-14",
    "release_evidence_contracts": "2026-07-26",
    "phase0_inventory": "2026-07-26",
    "javascript_syntax": "2026-07-14",
    "frozen_bundle_smoke": "2026-07-14",
}
RUNTIME_NUMPY_VERSION = "2.2.6"
RUNTIME_TOOLCHAIN_PATH = Path(
    "/Library/Frameworks/Python.framework/Versions/3.10/bin/python3.10")

# These paths are deliberately named in the manifest.  They are not source
# inputs: app output, local build state, caches, and operational instructions
# either belong in another component or must stay out of the archive.
EXCLUDED_TOP_LEVEL: dict[str, str] = {
    ".DS_Store": "macOS filesystem metadata",
    ".build": "local build outputs, virtualenvs, and merged wheels",
    ".agents": "agent workspace metadata",
    ".claude": "local operational metadata",
    ".codex": "local operational metadata",
    "AGENTS.md": "workspace instructions, not application source",
    "AUDIT_2026-07-09.md": "historical audit outside this evidence scope",
    # Named ahead of the UI merge, where it currently lives. Unclassified, it
    # raises `unclassified top-level path` the moment it lands — the same
    # failure `tests/formal_migration_vectors.json` already caused once, when
    # release identity stayed broken for days because nothing ran that gate.
    # Excluded rather than measured, matching its two siblings above: audit
    # packets are historical evidence about a branch, not application source.
    # It belongs here and not in ROOT_FILES for a second reason — that map is
    # also a required-inputs list, so naming an absent file there makes every
    # manifest build on this branch fail.
    "AUDIT_PACKET_UI_2026-07-19.md": "UI-branch audit packet outside this scope",
    "FIRE Modeling.app": "measured separately as the artifact component",
    ".git": "version-control metadata is not an authenticity anchor for this package",
    "PRODUCT_DESIGN_REVIEW_2026-07-10.md": "historical design input outside this scope",
    "ROADMAP_2.0.md": "historical roadmap outside this scope",
    "ROADMAP_3.0_AUDIT_2026-07-16.md": "historical audit outside this evidence scope",
    "CODEX_PROMPT_3.0_2026-07-16.md": "review prompt, not application source",
    "CODEX_PROMPT_ROUND5_2026-07-27.md": "review prompt, not application source",
    "CODEX_MILESTONE_PHASE1_INCOME_CASHFLOW_2026-07-28.md": "session milestone, not application source",
    "CODEX_MILESTONE_PHASE1_RULE_PACK_2026-07-29.md": "session milestone, not application source",
    "CLAUDE_HANDOFF_PROMPT_2026-07-28.md": "session handoff instructions, not application source",
    "HANDOFF_CLAUDE_PHASE0_REPAIR_2026-07-25.md": "session handoff instructions, not application source",
    "HANDOFF_GPT_2026-07-25.md": "session handoff instructions, not application source",
    "WORKSTREAM_LOG.md": "operational log, not application source",
    "IDEA_BANK.md": "forward idea material outside this release scope",
    "IDEA_BANK_PLAYBOOK.md": "forward idea material outside this release scope",
    "IDEA_ARCHIVE_2026-07": "forward idea material outside this release scope",
    ".worktrees": "sibling development worktrees, not part of this tree's source",
    "dist": "legacy build output directory",
    "__pycache__": "Python bytecode cache",
}

GENERATED_PREFIXES = (
    "RELEASE_BASELINE_",
    "RELEASE_DATA_MANIFEST_",
    "RELEASE_ARTIFACT_MANIFEST_",
    "RELEASE_SOURCE_ARCHIVE_",
    "RELEASE_SOURCE_MANIFEST_",
    "SOURCE_MANIFEST_PHASE_",
)

UNSAFE_SUFFIXES = (
    ".db",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".tmp",
    ".bak",
)

DATA_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "rules.offline_pack",
        "source_files": ["engine/fire_rule_pack.py"],
        "symbol": "canonical_pack_payload, RULE_PACK_ID, RULE_PACK_SHA256",
        "scope": "Content-addressed offline US rule pack and executable maintenance metadata.",
        "vintage": "Canonical embedded 2026 operating values assembled/verified 2026-08-01",
        "units": "Mixed rule-specific units declared by each component",
        "provenance": "Component-specific official primary sources plus explicitly labeled product/historical assumptions are recorded in engine/fire_rule_pack.py; 2026 values are an offline model input refresh, not tax advice",
        "field_source_ledger": "Machine-readable field/group-to-source ledger is canonical_pack_payload['field_source_ledger']; it distinguishes official primary sources from product and historical assumptions.",
        "transformations": "Strict sorted JSON with no NaN/Infinity; runtime-only open bounds are reconstructed from a null sentinel.",
        "applies_to": ["runtime result metadata", "tax", "contributions", "ACA", "SSA"],
        "status": "offline_embedded_official_vintage_with_explicit_assumptions",
        "limitation": "Maintenance status is an app review deadline, not legal validity or tax advice.",
    },
    {
        "id": "returns.historical_blocks",
        "source_files": ["engine/fire_returns_x.py"],
        "symbol": "HIST_START_YEAR, HIST_EQUITY, HIST_BOND, HIST_INFLATION",
        "scope": "Embedded annual S&P 500 total return, 10-year Treasury return, and CPI inflation block table.",
        "vintage": "1928-2024; source review/fetch noted as 2026-07",
        "units": "annual decimal returns; equity includes dividends",
        "provenance": "Damodaran/NYU-Stern annual return series and BLS CPI-U, as documented in the source module",
        "transformations": "Values rounded to 1bp; circular block bootstrap preserves within-block sequence and cross-asset ordering.",
        "applies_to": ["returns_x.blocks"],
        "status": "illustrative_empirical_assumption",
        "limitation": "A 97-year embedded table is not a forecast or a guarantee; the vintage must be re-reviewed before a real decision.",
    },
    {
        "id": "social_security.awi_cola",
        "source_files": [
            "engine/fire_rule_pack.py",
            "engine/fire_v9_2_model.py",
            "server/ssa_import.py",
        ],
        "symbol": "AWI, COLA",
        "scope": "SSA average wage index and cost-of-living adjustment series used by the optional Social Security import path.",
        "vintage": "AWI 1951-2024; COLA through 2025 (payable January 2026); module data-vintage note 2026-08",
        "units": "AWI nominal dollars/index values; COLA annual decimal rates",
        "provenance": "Official SSA AWI/COLA series as described in the module's DATA VINTAGE note; verified 2026-08-01",
        "transformations": "Future wage-index years are conservatively capped at the latest embedded AWI year; no network refresh occurs at runtime.",
        "applies_to": ["server.ssa_import"],
        "status": "embedded_official_series_with_fallback",
        "limitation": "The embedded series is finite and the cap-after-latest-year rule is a conservative modeling choice, not a current SSA forecast.",
    },
    {
        "id": "tax.true_rules",
        "source_files": [
            "engine/fire_rule_pack.py",
            "engine/fire_tax_true.py",
            "engine/fire_v6_model.py",
            "engine/fire_v9_4_model.py",
        ],
        "symbol": "US_FEDERAL_RULES, ORD_*, LTCG_*, RMD_TABLE, US_ORDINARY_BRACKETS_SINGLE, fire_v9_4.EARLY_WD_PENALTY_*",
        "scope": "Default progressive and opt-in true-tax brackets, capital-gain thresholds, RMD divisors, Social Security thresholds, IRMAA tiers, and the early-withdrawal penalty used by active flat/shock paths.",
        "vintage": "2026 IRS tables; nominal SS thresholds and unchanged statutory penalty/RMD rules",
        "units": "real-dollar thresholds and annual dollar surcharges, except explicitly nominal-law Social Security thresholds",
        "provenance": "IRS Rev. Proc. 2025-32 (published in IRB 2025-45) and 2026 IRS retirement-limit guidance, URLs recorded in the canonical pack; no live legal refresh at runtime",
        "transformations": "The engine inflates real-dollar tables by CPI where documented; the Social Security thresholds remain nominal by statute.",
        "applies_to": [
            "tax.progressive", "tax.true", "medicare.irmaa",
            "fire_v9_4.early_withdrawal_penalty",
        ],
        "status": "offline_official_vintage_editable_reference",
        "limitation": "Not a complete tax filing or state-by-state tax engine; users must verify applicable law and stale-table warnings.",
    },
    {
        "id": "limits.contributions_embedded",
        "source_files": [
            "engine/fire_rule_pack.py",
            "engine/fire_v8_model.py",
        ],
        "symbol": "CONTRIBUTION_LIMIT_RULES, V8ContributionParams",
        "scope": "Editable first-year 401(k), Roth IRA, and HSA contribution defaults plus modeled annual growth.",
        "vintage": "2026 IRS employee limits",
        "units": "today-dollar annual limits and decimal annual growth",
        "provenance": "IRS 2026 401(k)/profit-sharing, IRA, and Rev. Proc. 2025-19 HSA guidance, URLs recorded in the canonical pack",
        "transformations": "Plan values are compared with, never silently replaced by, the current embedded reference.",
        "applies_to": ["accumulation contributions", "browser quick estimate"],
        "status": "official_vintage_editable_reference_values",
        "limitation": "A mismatch may be an intentional user override or an old default; origin cannot be inferred from an old plan.",
    },
    {
        "id": "health.aca_marketplace_2026_embedded",
        "source_files": [
            "engine/fire_rule_pack.py",
            "engine/fire_v9_1_model.py",
        ],
        "symbol": "ACA_MARKETPLACE_RULES, ACAParams",
        "scope": "Editable FPL basis, subsidy cliff, and contribution-rate defaults for possible US pre-Medicare retirement years.",
        "vintage": "2026 coverage using 2025 FPL",
        "units": "annual dollars, FPL multiple, decimal income shares",
        "provenance": "HealthCare.gov 2025 FPL, CMS 2026 Marketplace guidance, and IRS PTC timing guidance; URLs recorded in the canonical pack",
        "transformations": "FPL is inflated by modeled CPI; applicability is conservative and config-based rather than path-instrumented.",
        "applies_to": ["retirement medical expense", "ACA MAGI solver"],
        "status": "editable_reference_values",
        "limitation": "The pack records the embedded model basis, not proof of current Marketplace eligibility or premiums.",
    },
    {
        "id": "destination.city_library",
        "source_files": ["web/destination_catalog.js"],
        "symbol": "DEST_VINTAGE, DEST",
        "scope": "Illustrative destination defaults for cost of living, FX volatility, inflation, healthcare, Social Security haircut, and withdrawal tax.",
        "vintage": "2026-07 (review marker in web source)",
        "units": "relative cost/volatility or decimal rates; healthcare values are today's dollars",
        "provenance": "Product-maintained illustrative defaults; not represented as an authoritative government or market dataset",
        "transformations": "Defaults are editable in the UI and are used as scenario inputs rather than silently replacing a user's values.",
        "applies_to": ["web.destination_library"],
        "status": "illustrative_product_assumption",
        "limitation": "Country/city tax, healthcare, FX, inflation, and residency outcomes require independent verification; city rows are not advice.",
    },
    {
        "id": "engine.v98_defaults_and_rules",
        "source_files": ["server/engine_v98.py"],
        "symbol": "ENGINE_VERSION, default_config, adapter defaults",
        "scope": "The v9.8 runtime's default configuration and adapter rules that connect the web payload to the engine.",
        "vintage": "Code-reviewed 2026-07; individual parameters may have older documented vintages",
        "units": "Mixed model units; see the config schema and per-field disclosures",
        "provenance": "Product code and its inline calibration notes",
        "transformations": "Server-side additive defaults are applied before the engine run; this manifest records the container hash, not a field-level reimplementation.",
        "applies_to": ["server.engine_v98", "server.persistence.replay"],
        "status": "runtime_rule_set",
        "limitation": "This is an inventory of embedded assumptions, not proof that every parameter is current, externally audited, or appropriate for a particular household.",
    },
    {
        "id": "baseline.v96_official_snapshot",
        "source_files": ["engine/v96_official_baseline.json"],
        "symbol": "precomputed scenario metrics",
        "scope": "Precomputed v9.6 official baseline result snapshot retained for comparison and audit context.",
        "vintage": "Portfolio date 2026-05-16; model version v9.6",
        "units": "Scenario-specific percentiles, probabilities, dollars, and years as encoded in the JSON",
        "provenance": "Local precomputed artifact; provenance is the file and its embedded metadata, not a live rerun",
        "transformations": "None applied by this generator; the JSON is hashed as a whole container.",
        "applies_to": ["regression/comparison context"],
        "status": "historical_comparison_artifact",
        "limitation": "Not the current v9.8 runtime output and not evidence that the old frozen bundle corresponds to the current source tree.",
    },
)


class EvidenceError(ValueError):
    """Raised when the evidence scope cannot be made safe and deterministic."""


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value))


def _validate_capture_date(value: str) -> str:
    try:
        parsed = _dt.date.fromisoformat(value)
    except ValueError as exc:
        raise EvidenceError("capture date must be YYYY-MM-DD") from exc
    return parsed.isoformat()


def _safe_relative_path(path: str) -> str:
    if not path or "\\" in path:
        raise EvidenceError(f"unsafe path: {path!r}")
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
        raise EvidenceError(f"unsafe path: {path!r}")
    return candidate.as_posix()


def _safe_symlink_target(relative_path: str, target: str) -> str:
    """Validate a link lexically without following it on the host filesystem."""
    if not target or "\\" in target or PurePosixPath(target).is_absolute():
        raise EvidenceError(f"unsafe symlink target for {relative_path}: {target!r}")
    parts: list[str] = list(PurePosixPath(relative_path).parent.parts)
    for part in PurePosixPath(target).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise EvidenceError(f"symlink escapes source root: {relative_path} -> {target}")
            parts.pop()
        else:
            parts.append(part)
    _safe_relative_path("/".join(parts) if parts else "placeholder")
    return target


def _kind_and_stat(path: Path) -> tuple[str, os.stat_result]:
    info = path.lstat()
    mode = info.st_mode
    if stat.S_ISREG(mode):
        return "file", info
    if stat.S_ISLNK(mode):
        return "symlink", info
    if stat.S_ISDIR(mode):
        return "directory", info
    raise EvidenceError(f"unsupported filesystem object: {path}")


def _is_debris(path: Path) -> bool:
    name = path.name
    return (name == ".DS_Store" or name.endswith(".pyc") or
            name.endswith(".pyo") or name == ".pytest_cache")


def _generated_output(name: str) -> bool:
    return any(name.startswith(prefix) for prefix in GENERATED_PREFIXES)


def _check_safe_data_path(relative_path: str) -> None:
    lowered = relative_path.lower()
    if any(lowered.endswith(suffix) for suffix in UNSAFE_SUFFIXES):
        raise EvidenceError(f"runtime/user-state file is not source evidence: {relative_path}")


def _source_root_path(root: str | Path) -> Path:
    requested_root = Path(root).expanduser()
    if requested_root.is_symlink():
        raise EvidenceError("source root must not be a symlink")
    root_path = requested_root.resolve()
    if not root_path.is_dir():
        raise EvidenceError(f"source root is not a directory: {root}")
    return root_path


def _source_entry(root: Path, path: Path, category: str) -> dict[str, Any]:
    relative = _safe_relative_path(path.relative_to(root).as_posix())
    _check_safe_data_path(relative)
    kind, info = _kind_and_stat(path)
    if kind == "directory":
        raise EvidenceError(f"source allowlist must contain files or symlinks, not directory: {relative}")
    entry: dict[str, Any] = {
        "path": relative,
        "category": category,
        "kind": kind,
        "mode": f"{stat.S_IMODE(info.st_mode):04o}",
        "size": info.st_size,
    }
    if kind == "file":
        entry["sha256"] = sha256_file(path)
    else:
        entry["target"] = _safe_symlink_target(relative, os.readlink(path))
    return entry


def build_source_manifest(root: str | Path) -> dict[str, Any]:
    root_path = _source_root_path(root)

    entries: list[dict[str, Any]] = []
    for relative, category in ROOT_FILES.items():
        path = root_path / relative
        if not path.exists() and not path.is_symlink():
            raise EvidenceError(f"required source input is missing: {relative}")
        entries.append(_source_entry(root_path, path, category))

    for directory, (category, allowed_extensions) in DIRECTORIES.items():
        base = root_path / directory
        if base.is_symlink():
            raise EvidenceError(f"source root directory must not be a symlink: {directory}")
        if not base.is_dir():
            raise EvidenceError(f"required source directory is missing: {directory}")
        for current, dirnames, filenames in os.walk(base, topdown=True, followlinks=False):
            current_path = Path(current)
            dirnames[:] = sorted(dirnames)
            for dirname in list(dirnames):
                if dirname in {"__pycache__", ".pytest_cache"}:
                    dirnames.remove(dirname)
            for filename in sorted(filenames):
                path = current_path / filename
                if _is_debris(path):
                    continue
                suffix = path.suffix.lower()
                if suffix not in allowed_extensions:
                    relative = path.relative_to(root_path).as_posix()
                    raise EvidenceError(
                        f"unclassified file under {directory}/: {relative}; "
                        f"add its extension/category explicitly or remove it")
                entries.append(_source_entry(root_path, path, category))
            # A symlinked directory is returned in dirnames by os.walk.  It is
            # not followed, and it cannot silently enter the archive.
            for dirname in list(dirnames):
                candidate = current_path / dirname
                if candidate.is_symlink():
                    relative = candidate.relative_to(root_path).as_posix()
                    raise EvidenceError(f"source symlinked directory is not allowed: {relative}")

    entries.sort(key=lambda item: item["path"])
    if len({item["path"] for item in entries}) != len(entries):
        raise EvidenceError("duplicate source manifest path")
    scope = {
        "root_files": ROOT_FILES,
        "directories": {
            name: {"category": category, "extensions": sorted(extensions)}
            for name, (category, extensions) in DIRECTORIES.items()
        },
        "excluded_top_level": EXCLUDED_TOP_LEVEL,
        "generated_output_prefixes": list(GENERATED_PREFIXES),
        "debris_rules": [".DS_Store", "*.pyc", "*.pyo", ".pytest_cache", "__pycache__"],
        "unsafe_runtime_suffixes": list(UNSAFE_SUFFIXES),
    }
    component = {"scope": scope, "entries": entries}
    return {
        "manifest_version": MANIFEST_VERSION,
        "manifest_type": "release_evidence_source_manifest",
        "source_kind": "current_worktree_post_phase_0A",
        "scope": scope,
        "entries": entries,
        "component_sha256": sha256_json(component),
    }


def _runtime_external_entry(path: Path, label: str) -> dict[str, Any]:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise EvidenceError(f"runtime build input is missing: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise EvidenceError(f"runtime build input is not a regular file: {path}")
    return {
        "path": str(path),
        "category": label,
        "kind": "file",
        "mode": f"{stat.S_IMODE(info.st_mode):04o}",
        "size": info.st_size,
        "sha256": sha256_file(path),
    }


def build_runtime_manifest(root: str | Path) -> dict[str, Any]:
    """Build the narrower manifest used to identify a frozen candidate.

    This intentionally excludes README/roadmap/design documents and tests.
    The wheel and the external build interpreter are explicit because neither
    is covered by the broad current-worktree evidence manifest.
    """
    root_path = _source_root_path(root)
    entries: list[dict[str, Any]] = []
    for relative, category in RUNTIME_ROOT_FILES.items():
        path = root_path / relative
        if not path.exists() or path.is_symlink():
            raise EvidenceError(f"required runtime build input is missing or symlinked: {relative}")
        entries.append(_source_entry(root_path, path, category))

    for directory, (category, allowed_extensions) in RUNTIME_DIRECTORIES.items():
        base = root_path / directory
        if base.is_symlink() or not base.is_dir():
            raise EvidenceError(f"required runtime directory is missing or symlinked: {directory}")
        for current, dirnames, filenames in os.walk(base, topdown=True, followlinks=False):
            current_path = Path(current)
            dirnames[:] = sorted(dirnames)
            for dirname in list(dirnames):
                if dirname in {"__pycache__", ".pytest_cache"}:
                    dirnames.remove(dirname)
                    continue
                candidate = current_path / dirname
                if candidate.is_symlink():
                    relative = candidate.relative_to(root_path).as_posix()
                    raise EvidenceError(f"runtime symlinked directory is not allowed: {relative}")
            for filename in sorted(filenames):
                path = current_path / filename
                if _is_debris(path):
                    continue
                if path.suffix.lower() not in allowed_extensions:
                    relative = path.relative_to(root_path).as_posix()
                    raise EvidenceError(f"unclassified runtime file: {relative}")
                entries.append(_source_entry(root_path, path, category))

    tools_dir = root_path / "tools"
    for path in sorted(tools_dir.glob("*.py")):
        relative = path.relative_to(root_path).as_posix()
        if relative in RELEASE_ONLY_TOOL_FILES:
            continue
        if relative not in RUNTIME_TOOL_FILES:
            raise EvidenceError(f"unclassified runtime identity tool: {relative}")
    for relative, category in RUNTIME_TOOL_FILES.items():
        path = root_path / relative
        if not path.exists() or path.is_symlink():
            raise EvidenceError(f"required runtime identity tool is missing or symlinked: {relative}")
        entries.append(_source_entry(root_path, path, category))

    for child in root_path.iterdir():
        if child.suffix in {".sh", ".py"} and child.name not in {
                *RUNTIME_ROOT_FILES, "build-standalone.sh"}:
            raise EvidenceError(f"unclassified top-level runtime/build input: {child.name}")

    wheel_dir = root_path / ".build" / "wheels" / "merged"
    wheel_candidates = sorted(
        path for path in wheel_dir.glob(
            f"numpy-{RUNTIME_NUMPY_VERSION}-*universal2.whl")
        if path.is_file() and not path.is_symlink())
    if len(wheel_candidates) != 1:
        raise EvidenceError(
            f"expected exactly one local universal2 NumPy {RUNTIME_NUMPY_VERSION} wheel")
    wheel = _runtime_external_entry(wheel_candidates[0], "local_universal2_numpy_wheel")
    wheel["path"] = wheel_candidates[0].relative_to(root_path).as_posix()

    toolchain = _runtime_external_entry(
        RUNTIME_TOOLCHAIN_PATH, "universal2_build_python")
    entries.sort(key=lambda item: item["path"])
    scope = {
        "root_files": RUNTIME_ROOT_FILES,
        "directories": {
            name: {"category": category, "extensions": sorted(extensions)}
            for name, (category, extensions) in RUNTIME_DIRECTORIES.items()
        },
        "identity_tools": RUNTIME_TOOL_FILES,
        "target_arch": "universal2",
        "python_major_minor": [3, 10],
        "numpy_version": RUNTIME_NUMPY_VERSION,
        "wheel_scope": ".build/wheels/merged/numpy-<version>-*universal2.whl",
        "external_toolchain": str(RUNTIME_TOOLCHAIN_PATH),
    }
    component = {"scope": scope, "entries": entries,
                 "numpy_wheel": wheel, "toolchain": toolchain}
    return {
        "manifest_version": MANIFEST_VERSION,
        "manifest_type": "runtime_build_manifest",
        "runtime_kind": "frozen_candidate_inputs",
        "scope": scope,
        "entries": entries,
        "numpy_wheel": wheel,
        "toolchain": toolchain,
        "component_sha256": sha256_json(component),
    }


def _validate_top_level(root: Path, output_names: Iterable[str]) -> None:
    allowed = set(ROOT_FILES) | set(EXCLUDED_TOP_LEVEL) | set(DIRECTORIES)
    generated = set(output_names)
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        name = child.name
        if name in allowed or name in generated or _generated_output(name):
            continue
        raise EvidenceError(
            f"unclassified top-level path: {name}; add it to the evidence scope or exclude it explicitly")


def _rule_pack_receipt(root: Path) -> dict[str, Any]:
    """Execute the pure dependency-leaf pack and independently verify its hash.

    ``runpy`` avoids importing the project package or writing bytecode into a
    fixture tree.  Any filesystem/network side effect in the pack would violate
    its own runtime contract and is separately guarded by tests.
    """
    relative = "engine/fire_rule_pack.py"
    path = root / relative
    if not path.is_file() or path.is_symlink():
        raise EvidenceError(f"rule pack must be a regular file: {relative}")
    try:
        namespace = runpy.run_path(str(path), run_name="_fire_release_rule_pack")
        payload = namespace["canonical_pack_payload"]()
        declared_hash = namespace["RULE_PACK_SHA256"]
        pack_id = namespace["RULE_PACK_ID"]
    except Exception as exc:
        raise EvidenceError("canonical rule pack could not be evaluated") from exc
    try:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EvidenceError("canonical rule pack is not strict JSON") from exc
    actual_hash = hashlib.sha256(encoded).hexdigest()
    if declared_hash != actual_hash:
        raise EvidenceError("canonical rule pack hash does not match its payload")
    if pack_id != f"us-offline-{actual_hash[:16]}":
        raise EvidenceError("canonical rule pack id does not match its payload")
    return {
        "schema_version": payload.get("schema_version"),
        "pack_id": pack_id,
        "content_sha256": actual_hash,
        "source_file": relative,
    }


def build_data_manifest(root: str | Path) -> dict[str, Any]:
    root_path = _source_root_path(root)
    rule_pack = _rule_pack_receipt(root_path)
    entries: list[dict[str, Any]] = []
    containers: dict[str, dict[str, Any]] = {}
    for definition in DATA_DEFINITIONS:
        item = dict(definition)
        source_files = sorted(item["source_files"])
        item["source_files"] = source_files
        for relative in source_files:
            safe = _safe_relative_path(relative)
            path = root_path / safe
            if not path.is_file() or path.is_symlink():
                raise EvidenceError(f"data container must be a regular file: {relative}")
            _check_safe_data_path(safe)
            containers[safe] = {
                "path": safe,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        entries.append(item)
    entries.sort(key=lambda item: item["id"])
    payload = {
        "manifest_version": MANIFEST_VERSION,
        "manifest_type": "semantic_data_rule_vintage_manifest",
        "applies_to": "current source tree only; not asserted for the legacy frozen artifact",
        "entries": entries,
        "containers": [containers[key] for key in sorted(containers)],
        "rule_pack": rule_pack,
    }
    payload["component_sha256"] = sha256_json(payload)
    return payload


def _artifact_entry(root: Path, path: Path, relative: str) -> dict[str, Any]:
    kind, info = _kind_and_stat(path)
    entry: dict[str, Any] = {
        "path": relative,
        "kind": kind,
        "mode": f"{stat.S_IMODE(info.st_mode):04o}",
        "size": info.st_size,
    }
    if kind == "file":
        entry["sha256"] = sha256_file(path)
    elif kind == "symlink":
        entry["target"] = _safe_symlink_target(relative, os.readlink(path))
    return entry


def build_artifact_manifest(root: str | Path, artifact: str | Path) -> dict[str, Any]:
    root_path = _source_root_path(root)
    artifact_path = Path(artifact)
    if not artifact_path.is_absolute():
        artifact_path = root_path / artifact_path
    artifact_path = artifact_path.resolve()
    try:
        artifact_relative = artifact_path.relative_to(root_path).as_posix()
    except ValueError as exc:
        raise EvidenceError("artifact must be inside the workspace root") from exc
    if not artifact_path.is_dir():
        raise EvidenceError(f"artifact is not a directory: {artifact_relative}")

    entries: list[dict[str, Any]] = []
    stack: list[tuple[Path, str]] = [(artifact_path, ".")]
    while stack:
        current, relative = stack.pop()
        entries.append(_artifact_entry(artifact_path, current, relative))
        kind, _ = _kind_and_stat(current)
        if kind != "directory":
            continue
        children = sorted(os.scandir(current), key=lambda item: item.name, reverse=True)
        for child in children:
            child_relative = child.name if relative == "." else f"{relative}/{child.name}"
            _safe_relative_path(child_relative)
            stack.append((Path(child.path), child_relative))
    entries.sort(key=lambda item: item["path"])

    info_path = artifact_path / "Contents" / "Info.plist"
    if not info_path.is_file():
        raise EvidenceError("artifact is missing Contents/Info.plist")
    try:
        plist = plistlib.loads(info_path.read_bytes())
    except Exception as exc:  # plistlib has several exception types by Python version
        raise EvidenceError("artifact Info.plist is not readable") from exc
    bundle_identity = {
        "identifier": plist.get("CFBundleIdentifier"),
        "short_version": plist.get("CFBundleShortVersionString"),
        "bundle_version": plist.get("CFBundleVersion"),
        "name": plist.get("CFBundleName"),
        "executable": plist.get("CFBundleExecutable"),
        "info_plist_path": f"{artifact_relative}/Contents/Info.plist",
    }
    # The legacy bundle has a short version but no CFBundleVersion.  Record
    # that absence rather than inventing a build number; the short version is
    # the only version field used for this provisional snapshot.
    required_identity = ("identifier", "short_version", "executable")
    if any(bundle_identity[key] in (None, "") for key in required_identity):
        raise EvidenceError("artifact Info.plist is missing a required bundle identity field")
    component = {"artifact_path": artifact_relative, "bundle_identity": bundle_identity, "entries": entries}
    return {
        "manifest_version": MANIFEST_VERSION,
        "manifest_type": "legacy_frozen_artifact_manifest",
        "artifact_kind": "legacy_frozen_2.0_artifact_snapshot",
        "artifact_path": artifact_relative,
        "bundle_identity": bundle_identity,
        "entries": entries,
        "component_sha256": sha256_json(component),
    }


def _archive_mode(source_mode: str) -> int:
    return 0o755 if int(source_mode, 8) & 0o111 else 0o644


def _read_regular_snapshot(path: Path, relative: str, expected: Mapping[str, Any]) -> bytes:
    """Read the exact regular-file bytes represented by a manifest entry.

    Re-check the path with lstat, open without following symlinks where the
    host supports it, then hash the bytes that will actually be archived.  A
    file replacement between manifest creation and archive creation therefore
    fails closed instead of producing a manifest/archive mismatch.
    """
    kind, info = _kind_and_stat(path)
    if kind != "file":
        raise EvidenceError(f"source entry changed type before archive: {relative}")
    current_mode = f"{stat.S_IMODE(info.st_mode):04o}"
    if current_mode != expected["mode"] or info.st_size != expected["size"]:
        raise EvidenceError(f"source entry metadata drift before archive: {relative}")
    flags = os.O_RDONLY
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    flags |= nofollow
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvidenceError(f"source entry could not be opened without following a symlink: {relative}") from exc
    try:
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise EvidenceError(f"source entry is not a regular file at archive time: {relative}")
            opened_mode = f"{stat.S_IMODE(opened.st_mode):04o}"
            if opened_mode != expected["mode"] or opened.st_size != expected["size"]:
                raise EvidenceError(f"source entry descriptor metadata drift: {relative}")
            data = handle.read()
    finally:
        if descriptor != -1:
            os.close(descriptor)
    if len(data) != expected["size"] or sha256_bytes(data) != expected["sha256"]:
        raise EvidenceError(f"source entry content drift before archive: {relative}")
    return data


def write_deterministic_archive(root: str | Path, source_manifest: Mapping[str, Any], destination: str | Path,
                                capture_date: str) -> str:
    root_path = _source_root_path(root)
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    capture_date = _validate_capture_date(capture_date)
    archive_root = f"{ARCHIVE_ROOT_PREFIX}-{capture_date}"
    _safe_relative_path(archive_root)
    entries = list(source_manifest["entries"])
    names = [entry["path"] for entry in entries]
    if names != sorted(names) or len(names) != len(set(names)):
        raise EvidenceError("source manifest entries must be unique and sorted before archiving")

    with tarfile.open(destination_path, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        root_info = tarfile.TarInfo(archive_root + "/")
        root_info.type = tarfile.DIRTYPE
        root_info.mode = 0o755
        root_info.uid = root_info.gid = 0
        root_info.uname = root_info.gname = ""
        root_info.mtime = 0
        root_info.size = 0
        root_info.pax_headers = {}
        archive.addfile(root_info)
        for entry in entries:
            relative = _safe_relative_path(str(entry["path"]))
            source_path = root_path / Path(*PurePosixPath(relative).parts)
            target_name = f"{archive_root}/{relative}"
            if entry["kind"] == "file":
                data = _read_regular_snapshot(source_path, relative, entry)
                info = tarfile.TarInfo(target_name)
                info.type = tarfile.REGTYPE
                info.mode = _archive_mode(str(entry["mode"]))
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                info.pax_headers = {}
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            elif entry["kind"] == "symlink":
                kind, current_info = _kind_and_stat(source_path)
                if kind != "symlink":
                    raise EvidenceError(f"source symlink changed type before archive: {relative}")
                current_mode = f"{stat.S_IMODE(current_info.st_mode):04o}"
                current_target = _safe_symlink_target(relative, os.readlink(source_path))
                if current_mode != entry["mode"] or current_target != entry["target"]:
                    raise EvidenceError(f"source symlink drift before archive: {relative}")
                target = _safe_symlink_target(relative, str(entry["target"]))
                info = tarfile.TarInfo(target_name)
                info.type = tarfile.SYMTYPE
                info.mode = 0o777
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                info.size = 0
                info.linkname = target
                info.pax_headers = {}
                archive.addfile(info)
            else:
                raise EvidenceError(f"unsupported source archive entry kind: {entry['kind']}")

    with tarfile.open(destination_path, mode="r:") as archive:
        members = archive.getmembers()
        # tarfile normalizes the root directory member name by removing its
        # trailing slash on read; the archive still contains one directory
        # root followed by the exact sorted source allowlist.
        expected = [archive_root] + [f"{archive_root}/{name}" for name in names]
        actual = [member.name for member in members]
        if actual != expected:
            raise EvidenceError(f"archive member set/order drift: expected {len(expected)}, got {len(actual)}")
        for member in members:
            if member.mtime != 0 or member.uid != 0 or member.gid != 0 or member.uname or member.gname:
                raise EvidenceError(f"archive metadata is not normalized: {member.name}")
            if member.name != archive_root:
                _safe_relative_path(member.name[len(archive_root) + 1:])
    return sha256_file(destination_path)


def _output_names(capture_date: str) -> dict[str, str]:
    return {
        "source_manifest": f"RELEASE_SOURCE_MANIFEST_PROVISIONAL_{capture_date}.json",
        "data_manifest": f"RELEASE_DATA_MANIFEST_PROVISIONAL_{capture_date}.json",
        "artifact_manifest": f"RELEASE_ARTIFACT_MANIFEST_LEGACY_2.0_{capture_date}.json",
        "source_archive": f"RELEASE_SOURCE_ARCHIVE_PROVISIONAL_{capture_date}.tar",
        "baseline": f"RELEASE_BASELINE_2.0_PROVISIONAL_{capture_date}.json",
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> str:
    path.write_bytes(canonical_json(payload))
    return sha256_file(path)


def build_package(root: str | Path, output_dir: str | Path, capture_date: str,
                  artifact: str | Path = "FIRE Modeling.app") -> dict[str, Any]:
    root_path = _source_root_path(root)
    output_path = Path(output_dir).resolve()
    capture_date = _validate_capture_date(capture_date)
    names = _output_names(capture_date)
    _validate_top_level(root_path, names.values())

    source_manifest = build_source_manifest(root_path)
    data_manifest = build_data_manifest(root_path)
    artifact_manifest = build_artifact_manifest(root_path, artifact)
    output_path.mkdir(parents=True, exist_ok=True)

    archive_path = output_path / names["source_archive"]
    archive_sha256 = write_deterministic_archive(root_path, source_manifest, archive_path, capture_date)

    source_path = output_path / names["source_manifest"]
    data_path = output_path / names["data_manifest"]
    artifact_path = output_path / names["artifact_manifest"]
    source_file_sha256 = _write_json(source_path, source_manifest)
    data_file_sha256 = _write_json(data_path, data_manifest)
    artifact_file_sha256 = _write_json(artifact_path, artifact_manifest)

    lock_path = root_path / "build-requirements.lock"
    identity_payload = {
        "manifest_version": MANIFEST_VERSION,
        "identity_type": "provisional_evidence_package",
        "capture_date": capture_date,
        "source_component_sha256": source_manifest["component_sha256"],
        "source_manifest_file_sha256": source_file_sha256,
        "source_archive_sha256": archive_sha256,
        "data_component_sha256": data_manifest["component_sha256"],
        "data_manifest_file_sha256": data_file_sha256,
        "artifact_component_sha256": artifact_manifest["component_sha256"],
        "artifact_manifest_file_sha256": artifact_file_sha256,
        "lock_file_sha256": sha256_file(lock_path),
        "source_relationship_to_artifact": "unverified/non-corresponding",
        "git_tag": None,
        "authenticity": "content-derived integrity identifier; no external authenticity anchor",
    }
    identity_sha256 = sha256_json(identity_payload)
    evidence_package_id = f"pep-{capture_date}-{identity_sha256[:16]}"
    baseline = {
        "manifest_version": MANIFEST_VERSION,
        "manifest_type": "provisional_evidence_package",
        "identity_type": "provisional_evidence_package",
        "evidence_package_id": evidence_package_id,
        "identity_sha256": identity_sha256,
        "identity_algorithm": "sha256(canonical-json-v1(identity_payload))",
        "capture_date": capture_date,
        "status": "provisional-evidence-captured",
        "scope": "Post-Phase-0A source/data evidence paired with a legacy frozen 2.0 artifact snapshot",
        "provenance": {
            "git_tag": None,
            "source_kind": "current_worktree_post_phase_0A",
            "artifact_kind": "legacy_frozen_2.0_artifact_snapshot",
            "identity_algorithm": "sha256(canonical-json-v1(identity_payload))",
            "authenticity": "content-derived integrity identifier only; manifests can be replaced together without an external anchor",
        },
        "source": {
            "manifest_file": names["source_manifest"],
            "manifest_file_sha256": source_file_sha256,
            "component_sha256": source_manifest["component_sha256"],
            "archive_file": names["source_archive"],
            "archive_sha256": archive_sha256,
            "archive_format": ARCHIVE_FORMAT,
            "archive_root": f"{ARCHIVE_ROOT_PREFIX}-{capture_date}",
            "relationship_to_artifact": "unverified/non-corresponding",
            "relationship_basis": "The source is a post-Phase-0A worktree; no git/build provenance proves it produced the old bundle.",
            "privacy_disclosure": "The archive excludes local build state, caches, logs, SQLite files, and user-storage debris. The source tree may still contain legacy or user-derived calibration and is not a personal-data scrub.",
        },
        "data_manifest": {
            "file": names["data_manifest"],
            "file_sha256": data_file_sha256,
            "component_sha256": data_manifest["component_sha256"],
            "applies_to": "current source tree only; not asserted for the legacy frozen artifact",
            "semantic_entries": len(data_manifest["entries"]),
        },
        "artifact": {
            "path": artifact_manifest["artifact_path"],
            "manifest_file": names["artifact_manifest"],
            "manifest_file_sha256": artifact_file_sha256,
            "component_sha256": artifact_manifest["component_sha256"],
            "relationship_to_source": "unverified/non-corresponding",
            "bundle_identity": artifact_manifest["bundle_identity"],
            "filesystem_metadata_note": "Directory size and mode are included as local snapshot metadata; cross-filesystem portability is not verified.",
        },
        "dependencies": {
            "lock_file": "build-requirements.lock",
            "lock_file_sha256": sha256_file(lock_path),
            "wheel_content_locked": False,
            "toolchain_capture": "not included; system Python, macOS SDK, and local merged universal2 wheels remain external build prerequisites",
        },
        # Every number below is a *historical* observation this generator did not
        # produce.  `generator_executes_tests` and `status_note` already said so,
        # but the counts still read as current validation, and one of them had
        # been stale for weeks: `release_evidence_contracts` said 10 tests when
        # tests/test_release_identity.py had grown to 12.  A stale count under a
        # "passed" status is worse than no count, because it looks like evidence.
        #
        # So each count is stamped with the date it was observed — distinct from
        # the package's own `captured_for` — and the field is
        # `tests_at_observation` rather than `tests`, so it cannot be mistaken
        # for a number this run measured.  Refreshing them means re-running the
        # commands and updating TEST_EVIDENCE_OBSERVED_ON along with them.  The
        # generator must never guess a count, and the Phase 0 inventory gate,
        # not this block, is the authority on the current one.
        "test_evidence": {
            "evidence_kind": "declared_capture_observation",
            "captured_for": capture_date,
            "generator_executes_tests": False,
            "status_note": "Each entry below was observed on its own `observed_on` date by running its listed command by hand. This generator records them; it does not execute them and does not re-measure them. A count here is history, not a current result, and the dates differ because the observations do.",
            "persistence_contracts": {"command": "PYTHONDONTWRITEBYTECODE=1 python3 tests/test_persistence.py", "tests_at_observation": 22, "status": "passed", "observed_on": TEST_EVIDENCE_OBSERVED_ON["persistence_contracts"]},
            "regression": {"command": "PYTHONDONTWRITEBYTECODE=1 python3 tests/test_regression.py", "tests_at_observation": 86, "status": "passed", "observed_on": TEST_EVIDENCE_OBSERVED_ON["regression"]},
            "standard_official_replay": {"command": "PYTHONDONTWRITEBYTECODE=1 python3 tests/persistence_full_smoke.py", "standard_paths": 10000, "official_paths": 100000, "official_worker_counts": [2, 3], "status": "passed", "observed_on": TEST_EVIDENCE_OBSERVED_ON["standard_official_replay"]},
            "release_evidence_contracts": {"command": "PYTHONDONTWRITEBYTECODE=1 python3 tests/test_release_identity.py", "tests_at_observation": 20, "status": "passed", "observed_on": TEST_EVIDENCE_OBSERVED_ON["release_evidence_contracts"]},
            "phase0_inventory": {"command": "PYTHONDONTWRITEBYTECODE=1 python3 tests/phase0_baseline.py", "status": "passed", "observed_on": TEST_EVIDENCE_OBSERVED_ON["phase0_inventory"], "note": "The Phase 0 inventory gate. It is the authority on the current test count; this block is not."},
            "javascript_syntax": {"command": "PYTHONDONTWRITEBYTECODE=1 python3 tests/js_syntax_check.py web", "status": "passed", "observed_on": TEST_EVIDENCE_OBSERVED_ON["javascript_syntax"]},
            "frozen_bundle_smoke": {"command": "PYTHONDONTWRITEBYTECODE=1 python3 tests/frozen_smoke.py 'FIRE Modeling.app'", "architectures": ["arm64", "x86_64"], "status": "passed", "observed_on": TEST_EVIDENCE_OBSERVED_ON["frozen_bundle_smoke"], "note": "Existing legacy bundle only; no frozen SQLite claim."},
            "frozen_sqlite_smoke": "not-run",
        },
        "limitations": [
            "This is a provisional evidence package, not a GA release, a 2.0 historical source freeze, or a 3.0 release.",
            "The source/artifact relationship is unverified/non-corresponding; the current source is not claimed to have built the old bundle.",
            "No .git directory, git tag, Developer ID, notarization, or other external authenticity anchor is present.",
            "The current frozen bundle remains version 0.0.0 and has not been proven to include Phase 0A SQLite support.",
            "The source archive is deterministic but not hermetic: local universal2 wheel contents and toolchain are outside its scope.",
            "The semantic data manifest describes current source containers and their limitations; it is not a live data refresh or legal/financial advice.",
            "Artifact directory size and mode are local snapshot metadata; portability of that component digest across filesystems has not been verified.",
        ],
    }
    _write_json(output_path / names["baseline"], baseline)
    return {
        "capture_date": capture_date,
        "identity_sha256": identity_sha256,
        "evidence_package_id": evidence_package_id,
        "names": names,
        "paths": {key: output_path / value for key, value in names.items()},
        "baseline": baseline,
    }


def check_package(root: str | Path, capture_date: str,
                  artifact: str | Path = "FIRE Modeling.app") -> dict[str, Any]:
    root_path = _source_root_path(root)
    with tempfile.TemporaryDirectory(prefix="fire-release-check-") as temporary:
        expected = build_package(root_path, Path(temporary), capture_date, artifact)
        names = expected["names"]
        for key, name in names.items():
            actual_path = root_path / name
            expected_path = expected["paths"][key]
            if not actual_path.is_file():
                raise EvidenceError(f"missing generated evidence file: {name}")
            if actual_path.read_bytes() != expected_path.read_bytes():
                raise EvidenceError(f"generated evidence drift: {name}")
    return expected


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="workspace root")
    parser.add_argument("--output-dir", default=None, help="where generated evidence is written (default: root)")
    parser.add_argument("--artifact", default="FIRE Modeling.app", help="artifact path relative to root")
    parser.add_argument("--capture-date", required=True, help="explicit YYYY-MM-DD capture date")
    parser.add_argument("--check", action="store_true", help="recompute in a temporary directory and compare; write nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = Path(args.root).expanduser()
    if args.check:
        result = check_package(root, args.capture_date, args.artifact)
        print(json.dumps({"ok": True, "evidence_package_id": result["evidence_package_id"]}, sort_keys=True))
        return 0
    output_dir = Path(args.output_dir).resolve() if args.output_dir else root
    result = build_package(root, output_dir, args.capture_date, args.artifact)
    print(json.dumps({"ok": True, "evidence_package_id": result["evidence_package_id"],
                      "identity_sha256": result["identity_sha256"],
                      "files": result["names"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
