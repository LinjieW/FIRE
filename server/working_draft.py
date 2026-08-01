"""The working draft after cutover — a side-store, deliberately not the archive.

Why this exists
---------------
Before the cutover, the wizard's unsaved input lived in localStorage's
`fire_draft` and survived a restart. After it, `fire_draft` may not be written at
all — that is the whole point of the B1 fence — so the working draft became
session state in the browser and a restart lost it. The 2026-07-27 ruling calls
that a Phase 0 blocker: "正常保存、重启恢复" is one of the five categories that may
block, and unsaved work silently losing a durability class is exactly that.

Why it is a file beside the archive and not a table inside it
-------------------------------------------------------------
`recovery.logical_identity` hashes *every* table in `sqlite_master`, and the
archive-write seam's whole guarantee — prebind a measurement, swap, read it back
— is built on that hash. So a row that changes as the user types would advance
the control generation on every keystroke, and `StorageApi._write` copies the
entire archive to a staging image each time it runs. Writing the live file
without going through the seam is the S1 defect: the next startup reconciliation
correctly concludes an unowned writer has been at work and latches.

The control journal is the precedent this follows. `recovery-control.sqlite3`
already lives beside the archive in `support_root` and deliberately sits outside
its logical identity. A working draft belongs in that category: it is not
authoritative data, it is input the user has not yet decided to keep.

The three decisions this encodes (user, 2026-07-27)
--------------------------------------------------
1. **A draft is still written while the archive is latched or `source_changed`.**
   Refusing would be consistent with the project's fail-closed habit, but the
   thing being refused is the user's in-progress typing, and a latch is precisely
   when someone needs time to work the problem. The obligation that comes with
   this is a UI one: "plans are read-only, your draft is still kept" has to be
   two statements, or a successful draft save reads as "storage is fine".
2. **A draft newer than a restored archive is still offered, unannotated.** The
   side-store does not participate in the generation, so a restore does not roll
   it back. Tracking that would mean stamping a generation here and maintaining a
   comparison; a draft is editable input, not an account.
3. **One draft, not one per plan.** That is the current `_sessionDraft`
   behaviour and what the ruling asks for literally.

What it does not do
-------------------
It never touches the archive, never allocates a generation, never appends to the
control journal, and requires no authority receipt — which is why it is *not* in
Section 6's "exact" seam list and is documented as its own boundary. It also
never reads or writes `fire_draft`: the pre-cutover draft is already carried into
`recovered_drafts` as immutable evidence, and a second copy of one draft is worse
than none.

Corruption is not an error here. A draft is by definition disposable, so an
unreadable, oversized, or unparseable file reports "no draft" rather than raising
into startup. Failing closed on a scratch file would turn a lost paragraph into a
lost app.
"""
from __future__ import annotations

import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any, Optional

import recovery as RECOVERY

FORMAT = "fire-working-draft-v1"
FILENAME = "working-draft.json"

# A wizard config is a few kilobytes. The cap is three orders of magnitude above
# that so it can only ever be hit by something that is not a draft, and it is
# enforced on the way in *and* on the way out — a file that grew between those
# two moments did not grow by this module's hand.
MAX_BYTES = 1 << 20


class WorkingDraftError(Exception):
    """A write could not be made durable. Reads never raise."""


def draft_path(archive_path: str) -> Path:
    """`support_root/working-draft.json`, derived without opening anything.

    Deliberately not routed through `BackupRestoreManager`: constructing one
    opens the control journal, and decision 1 requires this store to work while
    that journal is latched. The path is the archive's parent, which is the same
    `support_root` the manager would have computed.
    """
    return Path(os.path.abspath(os.path.expanduser(archive_path))).parent / FILENAME


def read(archive_path: str) -> Optional[Any]:
    """The stored draft, or None — including for every damaged-file case."""
    path = draft_path(archive_path)
    fd = None
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > MAX_BYTES):
            return None
        raw = os.read(fd, MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return None
    except OSError:
        return None
    finally:
        if fd is not None:
            os.close(fd)
    try:
        stored = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(stored, dict) or stored.get("format") != FORMAT:
        return None
    draft = stored.get("draft")
    # The draft body is opaque on purpose. A draft is a half-filled wizard, so
    # validating it against the config schema would refuse exactly the states it
    # exists to preserve; the browser normalises on open, as it already does for
    # legacy drafts.
    return draft if isinstance(draft, dict) else None


def write(archive_path: str, draft: Any) -> None:
    """Replace the draft atomically: temp file, fsync, rename, fsync dir."""
    if not isinstance(draft, dict):
        raise WorkingDraftError("a working draft must be a JSON object")
    payload = json.dumps({"format": FORMAT, "draft": draft},
                         ensure_ascii=False, separators=(",", ":"),
                         sort_keys=True).encode("utf-8")
    if len(payload) > MAX_BYTES:
        raise WorkingDraftError("working draft is too large to store")

    path = draft_path(archive_path)
    directory = path.parent
    try:
        # Same private-directory discipline the rest of `support_root` gets:
        # 0700, and no path component followed through a symlink. Its failure
        # is reported in this module's own error type — a caller of `write`
        # should not have to catch `RecoveryError` to find out that a draft did
        # not save.
        RECOVERY._secure_dir(directory)
    except Exception as exc:  # noqa: BLE001
        raise WorkingDraftError(
            "working draft directory is not usable") from exc
    temp = directory / f".{FILENAME}.{secrets.token_hex(8)}.tmp"
    fd = None
    try:
        fd = os.open(str(temp),
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.fchmod(fd, 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(fd)
    except OSError as exc:
        if fd is not None:
            os.close(fd)
            fd = None
        _unlink_quietly(temp)
        raise WorkingDraftError("working draft could not be written") from exc
    finally:
        if fd is not None:
            os.close(fd)
    try:
        # Atomic: a reader sees the old bytes or the new ones, never a partial
        # file. The directory fsync is what makes the rename itself survive a
        # power loss, not just the bytes it points at.
        os.replace(str(temp), str(path))
        RECOVERY._fsync_dir(directory)
    except OSError as exc:
        _unlink_quietly(temp)
        raise WorkingDraftError("working draft could not be replaced") from exc


def clear(archive_path: str) -> None:
    """Forget the draft. Absent is already the desired state, so that is fine."""
    _unlink_quietly(draft_path(archive_path))


def _unlink_quietly(path: Path) -> None:
    try:
        os.unlink(str(path))
    except OSError:
        pass
