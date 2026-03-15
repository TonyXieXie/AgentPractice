from fastapi import HTTPException

from repositories import team_repository


def get_team(team_id: str):
    team = team_repository.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team


def get_team_handoffs(team_id: str):
    team = team_repository.get_team(team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team_repository.list_handoff_events(team_id)
