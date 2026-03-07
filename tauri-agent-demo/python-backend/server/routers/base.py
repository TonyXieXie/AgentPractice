from fastapi import APIRouter

from server.services import base_service


router = APIRouter(tags=["base"])
router.add_api_route("/local-file", base_service.read_local_file, methods=["GET"])
router.add_api_route("/local-file-exists", base_service.local_file_exists, methods=["GET"])
router.add_api_route("/", base_service.read_root, methods=["GET"])
router.add_api_route("/__debug/info", base_service.debug_info, methods=["GET"])
router.add_api_websocket_route("/ws", base_service.websocket_endpoint)
