from typing import List, Optional

from database import db
from models import Team, TeamHandoffEvent


def create_team(root_session_id: str) -> Team:
    return db.create_team(root_session_id)


def get_team(team_id: str) -> Optional[Team]:
    return db.get_team(team_id)


def touch_team(team_id: str) -> Optional[Team]:
    return db.touch_team(team_id)


def create_handoff_event(event: TeamHandoffEvent) -> TeamHandoffEvent:
    return db.create_team_handoff_event(event)


def list_handoff_events(team_id: str) -> List[TeamHandoffEvent]:
    return db.get_team_handoff_events(team_id)


def has_handoff_events_since(team_id: str, timestamp: str) -> bool:
    return db.has_team_handoff_events_since(team_id, timestamp)
