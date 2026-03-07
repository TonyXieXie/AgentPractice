from typing import Any, Dict, List, Optional

from database import db


def list_permission_requests(status: Optional[str] = None) -> List[Dict[str, Any]]:
    return db.get_permission_requests(status=status)


def create_permission_request(
    tool_name: str,
    action: str,
    path: str,
    reason: Optional[str] = None,
    session_id: Optional[str] = None,
) -> int:
    return db.create_permission_request(
        tool_name=tool_name,
        action=action,
        path=path,
        reason=reason,
        session_id=session_id,
    )


def get_permission_request(request_id: int) -> Optional[Dict[str, Any]]:
    return db.get_permission_request(request_id)


def update_permission_request(request_id: int, status: str) -> Optional[Dict[str, Any]]:
    return db.update_permission_request(request_id, status)
