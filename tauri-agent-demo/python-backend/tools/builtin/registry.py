from typing import Iterable

from ..base import Tool
from .ast_tools import CodeAstTool
from .file_tools import ListFilesTool, ReadFileTool, WriteFileTool
from .handoff_tool import HandoffTool
from .patch_tools import ApplyPatchTool
from .search_tools import SearchTool
from .shell_tools import RgTool, RunShellTool
from .subagent_tool import SpawnSubagentTool


def iter_builtin_tools() -> Iterable[Tool]:
    yield RgTool()
    yield ApplyPatchTool()
    yield SearchTool()
    yield ReadFileTool()
    yield WriteFileTool()
    yield ListFilesTool()
    yield RunShellTool()
    yield CodeAstTool()
    yield HandoffTool()
    yield SpawnSubagentTool()
