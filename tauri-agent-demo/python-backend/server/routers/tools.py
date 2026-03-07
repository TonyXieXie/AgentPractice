from typing import List

from fastapi import APIRouter

from models import ToolPermissionRequest
from server.services import ast_service, tool_service


router = APIRouter(tags=["tools"])
router.add_api_route("/tools/ast", ast_service.run_ast, methods=["POST"])
router.add_api_route("/ast/notify", ast_service.notify_ast, methods=["POST"])
router.add_api_route("/ast/settings/all", ast_service.get_ast_settings_all_route, methods=["GET"])
router.add_api_route("/ast/settings", ast_service.get_ast_settings_route, methods=["GET"])
router.add_api_route("/ast/settings", ast_service.update_ast_settings_route, methods=["PUT"])
router.add_api_route("/ast/cache", ast_service.get_ast_cache, methods=["GET"])
router.add_api_route("/ast/code-map", ast_service.get_code_map, methods=["GET"])
router.add_api_route("/tools/permissions", tool_service.get_tool_permissions, methods=["GET"], response_model=List[ToolPermissionRequest])
router.add_api_route("/tools/permissions/{request_id}", tool_service.update_tool_permission, methods=["PUT"], response_model=ToolPermissionRequest)
