from fastapi import APIRouter

from models import Team, TeamHandoffEvent
from server.services import team_service


router = APIRouter(tags=["teams"])
router.add_api_route("/teams/{team_id}", team_service.get_team, methods=["GET"], response_model=Team)
router.add_api_route("/teams/{team_id}/handoffs", team_service.get_team_handoffs, methods=["GET"], response_model=list[TeamHandoffEvent])
