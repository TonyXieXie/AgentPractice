import json

from ..base import Tool, ToolParameter
from .common import _apply_patch_text, _maybe_create_snapshot, _parse_json_input


class ApplyPatchTool(Tool):
    def __init__(self):
        super().__init__()
        self.name = "apply_patch"
        self.description = (
            "Apply a patch to files. Format:\n"
            "*** Begin Patch\n"
            "*** Update File: path\n"
            "@@\n"
            "- old line\n"
            "+ new line\n"
            "*** End Patch"
        )
        self.parameters = [
            ToolParameter(
                name="patch",
                type="string",
                description="Patch content in apply_patch format.",
                required=True,
            )
        ]

    async def execute(self, input_data: str) -> str:
        data = _parse_json_input(input_data)
        patch_text = data.get("patch") or input_data
        if not patch_text:
            raise ValueError("Missing patch content.")
        try:
            _maybe_create_snapshot()
            result = _apply_patch_text(patch_text)
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


__all__ = ["ApplyPatchTool"]

