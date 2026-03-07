from fastapi import APIRouter

from server.services import pty_service


router = APIRouter(tags=["pty"])
router.add_api_route("/pty/stream", pty_service.stream_pty, methods=["POST"])
router.add_api_route("/pty/list", pty_service.list_ptys, methods=["GET"])
router.add_api_route("/pty/read", pty_service.read_pty, methods=["POST"])
router.add_api_route("/pty/send", pty_service.send_pty, methods=["POST"])
router.add_api_route("/pty/close", pty_service.close_pty, methods=["POST"])
