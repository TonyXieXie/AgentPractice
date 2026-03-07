from typing import List

from fastapi import APIRouter

from models import LLMConfig
from server.services import config_service


router = APIRouter(tags=["config"])
router.add_api_route("/configs", config_service.get_configs, methods=["GET"], response_model=List[LLMConfig])
router.add_api_route("/configs/default", config_service.get_default_config, methods=["GET"], response_model=LLMConfig)
router.add_api_route("/configs/{config_id}", config_service.get_config, methods=["GET"], response_model=LLMConfig)
router.add_api_route("/configs", config_service.create_config, methods=["POST"], response_model=LLMConfig)
router.add_api_route("/configs/{config_id}", config_service.update_config, methods=["PUT"], response_model=LLMConfig)
router.add_api_route("/configs/{config_id}", config_service.delete_config, methods=["DELETE"])
router.add_api_route("/app/config", config_service.get_app_config_route, methods=["GET"])
router.add_api_route("/app/config", config_service.set_app_config, methods=["PUT"])
router.add_api_route("/agent/prompt", config_service.get_agent_prompt_route, methods=["GET"])
router.add_api_route("/mcp/refresh", config_service.refresh_mcp_tools_route, methods=["POST"])
router.add_api_route("/skills", config_service.get_skills_route, methods=["GET"])
router.add_api_route("/tools", config_service.get_tools_route, methods=["GET"])
router.add_api_route("/tools/config", config_service.get_tools_config_route, methods=["GET"])
router.add_api_route("/tools/config", config_service.set_tools_config_route, methods=["PUT"])
