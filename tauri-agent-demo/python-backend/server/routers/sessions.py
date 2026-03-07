from typing import List

from fastapi import APIRouter

from models import ChatMessage, ChatSession
from server.services import base_service
from server.services import session_service


router = APIRouter(tags=["sessions"])
router.add_api_route("/sessions", session_service.get_sessions, methods=["GET"], response_model=List[ChatSession])
router.add_api_route("/sessions/{session_id}", session_service.get_session, methods=["GET"], response_model=ChatSession)
router.add_api_route("/sessions", session_service.create_session, methods=["POST"], response_model=ChatSession)
router.add_api_route("/sessions/{session_id}", session_service.update_session, methods=["PUT"], response_model=ChatSession)
router.add_api_route("/sessions/{session_id}/copy", session_service.copy_session, methods=["POST"], response_model=ChatSession)
router.add_api_route("/sessions/{session_id}", session_service.delete_session, methods=["DELETE"])
router.add_api_route("/sessions/{session_id}/messages", session_service.get_session_messages, methods=["GET"], response_model=List[ChatMessage])
router.add_api_route("/sessions/{session_id}/llm_calls", session_service.get_session_llm_calls, methods=["GET"])
router.add_api_route("/sessions/{session_id}/tool_stats", session_service.get_session_tool_stats, methods=["GET"])
router.add_api_route("/sessions/{session_id}/agent_steps", session_service.get_session_agent_steps, methods=["GET"])
router.add_api_route("/sessions/{session_id}/rollback", session_service.rollback_session, methods=["POST"])
router.add_api_route("/attachments/{attachment_id}", base_service.get_attachment, methods=["GET"])
