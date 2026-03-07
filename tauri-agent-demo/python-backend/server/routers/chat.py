from fastapi import APIRouter

from models import ChatResponse
from server.services import ast_service, chat_service


router = APIRouter(tags=["chat"])
router.add_api_route("/chat", chat_service.chat, methods=["POST"], response_model=ChatResponse)
router.add_api_route("/chat/stream", chat_service.chat_stream, methods=["POST"])
router.add_api_route("/export", chat_service.export_chat_history, methods=["POST"])
router.add_api_route("/chat/agent/stream", chat_service.chat_agent_stream, methods=["POST"])
router.add_api_route("/patch/revert", chat_service.revert_patch, methods=["POST"])
router.add_api_route("/chat/stop", ast_service.stop_chat, methods=["POST"])
