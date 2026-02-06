import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Dict, Tuple

from database import db


def _run_git(args: list, cwd: str, env: Optional[dict] = None) -> str:
    try:
        completed = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git is not available") from exc

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(message or "git command failed")

    return (completed.stdout or "").strip()


def _get_git_root(work_path: str) -> Optional[str]:
    try:
        output = _run_git(["-C", work_path, "rev-parse", "--show-toplevel"], cwd=work_path)
    except Exception:
        return None
    return output.strip() if output else None


def _snapshot_base_dir() -> Path:
    env_path = os.getenv("TAURI_AGENT_SNAPSHOT_DIR") or os.getenv("TAURI_AGENT_DATA_DIR")
    if env_path:
        return Path(env_path).expanduser().resolve() / "snapshots"
    return Path.home() / ".tauri-agent" / "snapshots"


def _hidden_repo_dir(work_path: str) -> Path:
    normalized = str(Path(work_path).expanduser().resolve())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return _snapshot_base_dir() / digest / "git"


def _build_hidden_env(work_path: str, git_dir: Path) -> Dict[str, str]:
    env = os.environ.copy()
    env["GIT_DIR"] = str(git_dir)
    env["GIT_WORK_TREE"] = str(Path(work_path).expanduser().resolve())
    return env


def _write_default_excludes(git_dir: Path) -> None:
    info_dir = git_dir / "info"
    try:
        info_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    exclude_path = info_dir / "exclude"
    if exclude_path.exists():
        return
    patterns = [
        ".git/",
        "node_modules/",
        "dist/",
        "build/",
        ".venv/",
        "venv/",
        "__pycache__/",
        "target/",
        ".npm-cache/",
        ".npm-cache-2/"
    ]
    try:
        exclude_path.write_text("\n".join(patterns) + "\n", encoding="utf-8")
    except Exception:
        return


def _ensure_hidden_repo(work_path: str) -> Dict[str, str]:
    git_dir = _hidden_repo_dir(work_path)
    env = _build_hidden_env(work_path, git_dir)
    if git_dir.exists() and git_dir.is_dir():
        return env

    git_dir.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "--quiet"], cwd=str(Path(work_path).expanduser().resolve()), env=env)
    _write_default_excludes(git_dir)
    return env


def _get_repo_context(work_path: str) -> Dict[str, Optional[str]]:
    root = str(Path(work_path).expanduser().resolve())
    git_root = _get_git_root(root)
    if git_root:
        return {"root": git_root, "env": None}
    hidden_env = _ensure_hidden_repo(root)
    return {"root": root, "env": hidden_env}


def _build_index_env(base_env: Optional[Dict[str, str]], index_file: str) -> Dict[str, str]:
    env = os.environ.copy()
    if base_env:
        env.update(base_env)
    env["GIT_INDEX_FILE"] = index_file
    return env


def _create_temp_index_path() -> Tuple[str, str]:
    temp_dir = tempfile.mkdtemp(prefix="tauri-agent-index-")
    return temp_dir, os.path.join(temp_dir, "index")


def create_snapshot_tree(work_path: str) -> str:
    ctx = _get_repo_context(work_path)
    root = ctx["root"]
    base_env = ctx.get("env")

    temp_dir, index_path = _create_temp_index_path()
    env = _build_index_env(base_env, index_path)

    try:
        _run_git(["add", "-A"], cwd=root, env=env)
        tree_hash = _run_git(["write-tree"], cwd=root, env=env)
        if not tree_hash:
            raise RuntimeError("Failed to create snapshot tree")
        return tree_hash
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def ensure_snapshot(session_id: str, message_id: int, work_path: str) -> Optional[str]:
    if not session_id or not message_id or not work_path:
        return None

    existing = db.get_file_snapshot(session_id, message_id)
    if existing:
        return existing.get("tree_hash")

    tree_hash = create_snapshot_tree(work_path)
    db.create_file_snapshot(session_id, message_id, tree_hash, work_path)
    return tree_hash


def restore_snapshot(tree_hash: str, work_path: str) -> None:
    if not tree_hash or not work_path:
        raise RuntimeError("Missing snapshot data")

    ctx = _get_repo_context(work_path)
    root = ctx["root"]
    base_env = ctx.get("env")

    temp_dir, index_path = _create_temp_index_path()
    env = _build_index_env(base_env, index_path)

    try:
        _run_git(["read-tree", tree_hash], cwd=root, env=env)
        _run_git(["checkout-index", "-a", "-f"], cwd=root, env=env)
        _run_git(["clean", "-fd"], cwd=root, env=env)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
