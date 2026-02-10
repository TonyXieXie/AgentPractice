import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.builtin.system_tools import (
    _AST_EXT_LANGUAGE,
    _AST_IGNORE_DIRS,
    _ast_for_file,
    _get_ast_config
)
from ast_file_filter import collect_ast_files, should_include_file
from ast_settings import get_ast_settings
from app_config import get_app_config


def _normalize_path(path: str) -> Path:
    return Path(path).expanduser().resolve()


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _path_key(path: Path) -> str:
    return str(path).lower()


def _collect_files(root: Path, max_files: Optional[int] = None) -> List[Path]:
    settings = get_ast_settings(str(root))
    max_limit = settings.get("max_files") if isinstance(settings, dict) else None
    cfg_limit = max_files if max_files is not None else None
    limit = max_limit if max_limit is not None else cfg_limit
    limit = int(limit) if limit is not None else None
    extensions = list(_AST_EXT_LANGUAGE.keys())
    return collect_ast_files(
        scan_root=root,
        settings_root=root,
        extensions=extensions,
        max_files=limit or 0,
        ignore_dir_names=set(_AST_IGNORE_DIRS),
        settings=settings
    )


def _ast_enabled() -> bool:
    app_cfg = get_app_config()
    agent_cfg = app_cfg.get("agent", {}) if isinstance(app_cfg, dict) else {}
    return bool(agent_cfg.get("ast_enabled", True))


@dataclass
class AstFileEntry:
    path: str
    payload: Dict[str, Any]
    file_mtime: float
    parsed_at: float


class AstRootIndex:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.RLock()
        self.files: Dict[str, AstFileEntry] = {}
        self.scan_in_progress = False
        self.last_scan: Optional[float] = None

    def _parse_file(self, path: Path) -> AstFileEntry:
        cfg = _get_ast_config()
        payload = _ast_for_file(
            file_path=path,
            mode="full",
            language=None,
            max_nodes=cfg["max_nodes"],
            max_depth=cfg["max_depth"],
            max_bytes=cfg["max_bytes"],
            include_positions=True,
            include_text=True,
            max_symbols=cfg["max_symbols"]
        )
        parsed_at = time.time()
        try:
            mtime = path.stat().st_mtime
        except Exception:
            mtime = 0.0
        payload["file_mtime"] = mtime
        payload["parsed_at"] = parsed_at
        payload["include_text"] = True
        return AstFileEntry(path=str(path), payload=payload, file_mtime=mtime, parsed_at=parsed_at)

    def ensure_file(self, path: Path) -> Dict[str, Any]:
        if not _ast_enabled():
            return {"ok": False, "path": str(path), "error": "AST disabled."}
        if not path.exists() or not path.is_file():
            key = _path_key(path)
            with self.lock:
                self.files.pop(key, None)
            return {"ok": False, "path": str(path), "error": "File not found."}

        try:
            mtime = path.stat().st_mtime
        except Exception:
            mtime = 0.0
        key = _path_key(path)
        with self.lock:
            entry = self.files.get(key)
            if entry and entry.parsed_at >= mtime and entry.payload.get("include_text") is True:
                return entry.payload

        entry = self._parse_file(path)
        with self.lock:
            self.files[key] = entry
        return entry.payload

    def scan_root(self) -> None:
        if not _ast_enabled():
            return
        cfg = _get_ast_config()
        files = _collect_files(self.root, cfg.get("max_files"))
        for path in files:
            if not _is_within_root(path, self.root):
                continue
            self.ensure_file(path)
        with self.lock:
            self.last_scan = time.time()

    def scan_root_async(self) -> bool:
        if not _ast_enabled():
            return False
        with self.lock:
            if self.scan_in_progress:
                return False
            self.scan_in_progress = True

        def run() -> None:
            try:
                self.scan_root()
            finally:
                with self.lock:
                    self.scan_in_progress = False

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return True

    def notify_paths(self, paths: List[str]) -> int:
        if not _ast_enabled():
            return 0
        if not paths:
            return 0
        settings = get_ast_settings(str(self.root))
        cfg = _get_ast_config()
        extensions = list(_AST_EXT_LANGUAGE.keys())
        limit = settings.get("max_files") if isinstance(settings, dict) else None
        if limit is None:
            limit = cfg.get("max_files")
        try:
            limit = int(limit or 0)
        except Exception:
            limit = cfg.get("max_files")
        updated = 0
        for raw in paths:
            if not raw:
                continue
            try:
                path = _normalize_path(raw)
            except Exception:
                continue
            if not _is_within_root(path, self.root):
                continue
            if path.is_dir():
                for file_path in collect_ast_files(
                    scan_root=path,
                    settings_root=self.root,
                    extensions=extensions,
                    max_files=limit or 0,
                    ignore_dir_names=set(_AST_IGNORE_DIRS),
                    settings=settings
                ):
                    if not _is_within_root(file_path, self.root):
                        continue
                    self.ensure_file(file_path)
                    updated += 1
                continue
            if not should_include_file(path, self.root, settings, set(_AST_IGNORE_DIRS)):
                continue
            self.ensure_file(path)
            updated += 1
        return updated

    def list_entries(self, include_payload: bool = False) -> List[Dict[str, Any]]:
        if not _ast_enabled():
            return []
        entries: List[Dict[str, Any]] = []
        with self.lock:
            items = list(self.files.values())
        for entry in items:
            try:
                current_mtime = Path(entry.path).stat().st_mtime
            except Exception:
                current_mtime = entry.file_mtime
            stale = entry.parsed_at < current_mtime
            item = {
                "path": entry.path,
                "file_mtime": current_mtime,
                "parsed_at": entry.parsed_at,
                "stale": stale,
                "include_text": bool(entry.payload.get("include_text"))
            }
            if include_payload:
                item["payload"] = entry.payload
            entries.append(item)
        entries.sort(key=lambda it: it.get("path") or "")
        return entries


class AstIndex:
    def __init__(self):
        self.lock = threading.RLock()
        self.roots: Dict[str, AstRootIndex] = {}

    def get_root(self, root: str) -> AstRootIndex:
        resolved = _normalize_path(root)
        key = _path_key(resolved)
        with self.lock:
            index = self.roots.get(key)
            if not index:
                index = AstRootIndex(resolved)
                self.roots[key] = index
            return index

    def ensure_root(self, root: str) -> bool:
        if not _ast_enabled():
            return False
        index = self.get_root(root)
        return index.scan_root_async()

    def notify_paths(self, root: str, paths: List[str]) -> int:
        if not _ast_enabled():
            return 0
        index = self.get_root(root)
        return index.notify_paths(paths)

    def get_file_payload(self, root: str, path: str) -> Dict[str, Any]:
        index = self.get_root(root)
        return index.ensure_file(_normalize_path(path))

    def get_root_entries(self, root: str, include_payload: bool = False) -> List[Dict[str, Any]]:
        index = self.get_root(root)
        return index.list_entries(include_payload)


_AST_INDEX = AstIndex()


def get_ast_index() -> AstIndex:
    return _AST_INDEX
