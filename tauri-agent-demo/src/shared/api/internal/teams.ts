import type { Team, TeamHandoffEvent } from '../../../types';
import { API_BASE_URL } from './base';

export async function getTeam(teamId: string): Promise<Team> {
    const response = await fetch(`${API_BASE_URL}/teams/${teamId}`);
    if (!response.ok) throw new Error('Failed to fetch team');
    return response.json();
}

export async function getTeamHandoffs(teamId: string): Promise<TeamHandoffEvent[]> {
    const response = await fetch(`${API_BASE_URL}/teams/${teamId}/handoffs`);
    if (!response.ok) throw new Error('Failed to fetch team handoffs');
    return response.json();
}
