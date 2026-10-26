"""Source identity for native experiments; dirty staging is preview-only."""
import hashlib
import subprocess
from pathlib import Path

PROTOCOL_VERSION = "2.0"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()


def freeze_source(repo, allow_dirty_preview=False):
    repo = Path(repo).resolve()
    head = git(repo, "rev-parse", "HEAD")
    changes = git(repo, "diff", "--name-only", "HEAD").splitlines()
    untracked = git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    dirty = bool(changes or untracked)
    if dirty and not allow_dirty_preview:
        names = sorted(set(changes + untracked))
        raise RuntimeError("source must be committed and clean; changed/untracked: " +
                           ", ".join(names[:10]) + ". Use --allow-dirty-preview only for static development.")
    return {"protocol_version": PROTOCOL_VERSION, "base_commit": head,
            "source_commit": None if dirty else head,
            "source_dirty": dirty, "dirty_preview_only": bool(allow_dirty_preview),
            "allow_execute": not dirty and not allow_dirty_preview,
            "tracked_changes": changes, "untracked_files": untracked,
            "repo": str(repo)}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()
