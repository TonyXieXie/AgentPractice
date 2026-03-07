from typing import Iterable

from ..base import Tool
from .ast_tools import CodeAstTool
from .file_tools import ListFilesTool, ReadFileTool, WriteFileTool
from .patch_tools import ApplyPatchTool
from .search_tools import TavilySearchTool
from .shell_tools import RgTool, RunShellTool
from .subagent_tool import SpawnSubagentTool


def iter_builtin_tools() -> Iterable[Tool]:
    yield RgTool()
    yield ApplyPatchTool()
    yield TavilySearchTool()
    yield ReadFileTool()
    yield WriteFileTool()
    yield ListFilesTool()
    yield RunShellTool()
    yield CodeAstTool()
    yield SpawnSubagentTool()
