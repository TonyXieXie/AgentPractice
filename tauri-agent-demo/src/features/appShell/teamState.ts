import type {
  ChatSession,
  GroupedHandoffItem,
  Message,
  SessionExecutionStatus,
  TeamHandoffEvent,
  TeamOverviewState,
} from '../../types';

const TERMINAL_EVENT_KINDS = new Set(['completed', 'failed']);

const parseTimestamp = (value?: string | null, fallback: number = 0) => {
  if (!value) return fallback;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const getSessionRoleKey = (session?: ChatSession | null) => session?.role_key || session?.agent_profile || null;

const resolveSessionIdForRole = (sessions: ChatSession[], roleKey?: string | null) => {
  const normalizedRole = (roleKey || '').trim();
  if (!normalizedRole) return null;
  const matched = sessions.find((session) => getSessionRoleKey(session) === normalizedRole);
  return matched?.id || null;
};

export const extractRelatedTeamSessionIds = (message?: Message | null) => {
  const metadata = ((message as any)?.metadata || {}) as Record<string, unknown>;
  const teamId = typeof metadata.team_id === 'string' ? metadata.team_id : null;
  const sessionIds = new Set<string>();

  if (message?.session_id && teamId) {
    sessionIds.add(message.session_id);
  }

  ['to_session_id', 'target_session_id', 'from_session_id', 'source_session_id'].forEach((key) => {
    const value = metadata[key];
    if (typeof value === 'string' && value.trim()) {
      sessionIds.add(value.trim());
    }
  });

  return {
    teamId,
    sessionIds: Array.from(sessionIds),
  };
};

export const groupTeamHandoffItems = (events: TeamHandoffEvent[]): GroupedHandoffItem[] => {
  const latestByHandoff = new Map<string, { event: TeamHandoffEvent; index: number }>();

  events.forEach((event, index) => {
    if (!event?.handoff_id) return;
    const existing = latestByHandoff.get(event.handoff_id);
    if (!existing) {
      latestByHandoff.set(event.handoff_id, { event, index });
      return;
    }
    const currentTime = parseTimestamp(event.created_at, index);
    const existingTime = parseTimestamp(existing.event.created_at, existing.index);
    if (currentTime > existingTime || (currentTime === existingTime && index > existing.index)) {
      latestByHandoff.set(event.handoff_id, { event, index });
    }
  });

  return Array.from(latestByHandoff.values())
    .map(({ event, index }) => ({
      handoff_id: event.handoff_id,
      team_id: event.team_id,
      parent_handoff_id: event.parent_handoff_id || null,
      latest_event_kind: event.event_kind,
      from_session_id: event.from_session_id || null,
      from_role_key: event.from_role_key || null,
      to_session_id: event.to_session_id || null,
      to_role_key: event.to_role_key || null,
      reason: event.reason || null,
      work_summary: event.work_summary || null,
      artifact_summary: event.artifact_summary || null,
      changed_files: event.changed_files || null,
      artifact_source: event.artifact_source || null,
      artifact_owner_session_id: event.artifact_owner_session_id || null,
      artifact_owner_role_key: event.artifact_owner_role_key || null,
      task_payload: event.task_payload || null,
      result_summary: event.result_summary || null,
      error: event.error || null,
      latest_created_at: event.created_at || null,
      has_terminal_state: TERMINAL_EVENT_KINDS.has(event.event_kind),
      __sort_index: index,
    }))
    .sort((a, b) => {
      const timeDelta = parseTimestamp(b.latest_created_at, (b as any).__sort_index) - parseTimestamp(a.latest_created_at, (a as any).__sort_index);
      if (timeDelta !== 0) return timeDelta;
      return ((b as any).__sort_index || 0) - ((a as any).__sort_index || 0);
    })
    .map(({ __sort_index: _ignored, ...item }) => item);
};

type DeriveCurrentTeamExecutorParams = {
  teamId: string | null;
  teamSessions: ChatSession[];
  groupedHandoffs: GroupedHandoffItem[];
  messagesBySession: Record<string, Message[]>;
};

export const deriveCurrentTeamExecutor = ({
  teamId,
  teamSessions,
  groupedHandoffs,
  messagesBySession,
}: DeriveCurrentTeamExecutorParams): SessionExecutionStatus | null => {
  if (!teamId || teamSessions.length === 0) return null;

  const streamingCandidates = teamSessions
    .map((session) => {
      const cached = messagesBySession[session.id] || [];
      const streamingMessage = [...cached].reverse().find((message) => message?.metadata?.agent_streaming === true);
      if (!streamingMessage) return null;
      return {
        session,
        timestamp: parseTimestamp(streamingMessage.timestamp, 0),
      };
    })
    .filter((item): item is { session: ChatSession; timestamp: number } => Boolean(item))
    .sort((a, b) => b.timestamp - a.timestamp);

  if (streamingCandidates.length > 0) {
    const current = streamingCandidates[0].session;
    return {
      session_id: current.id,
      state: 'executing',
      source: 'streaming',
      team_id: current.team_id || teamId,
      updated_at: current.updated_at || current.created_at,
    };
  }

  const activeHandoff = groupedHandoffs.find((item) => !item.has_terminal_state);
  if (!activeHandoff) return null;

  const targetSessionId =
    activeHandoff.to_session_id ||
    resolveSessionIdForRole(teamSessions, activeHandoff.to_role_key) ||
    null;
  if (!targetSessionId) return null;

  const matched = teamSessions.find((session) => session.id === targetSessionId) || null;
  return {
    session_id: targetSessionId,
    state: 'executing',
    source: 'handoff',
    team_id: matched?.team_id || teamId,
    updated_at: matched?.updated_at || matched?.created_at || activeHandoff.latest_created_at || null,
  };
};

type BuildTeamOverviewStateParams = {
  teamId: string | null;
  teamName: string;
  leaderRoleKey?: string | null;
  currentRoleKey?: string | null;
  teamSessions: ChatSession[];
  groupedHandoffs: GroupedHandoffItem[];
  currentExecutor: SessionExecutionStatus | null;
  unreadBySession: Record<string, boolean>;
  pendingPermissionBySession: Record<string, boolean>;
  inFlightBySession: Record<string, boolean>;
  roleLabelById: Map<string, string>;
};

export const buildTeamOverviewState = ({
  teamId,
  teamName,
  leaderRoleKey,
  currentRoleKey,
  teamSessions,
  groupedHandoffs,
  currentExecutor,
  unreadBySession,
  pendingPermissionBySession,
  inFlightBySession,
  roleLabelById,
}: BuildTeamOverviewStateParams): TeamOverviewState | null => {
  if (!teamId || teamSessions.length === 0) return null;

  const sortedMembers = [...teamSessions].sort((a, b) => {
    const aRole = getSessionRoleKey(a);
    const bRole = getSessionRoleKey(b);
    if (aRole === leaderRoleKey && bRole !== leaderRoleKey) return -1;
    if (bRole === leaderRoleKey && aRole !== leaderRoleKey) return 1;
    return parseTimestamp(b.updated_at || b.created_at, 0) - parseTimestamp(a.updated_at || a.created_at, 0);
  });

  const hasTeamRequestInFlight = teamSessions.some((session) => Boolean(inFlightBySession[session.id]));
  const latestHandoff = groupedHandoffs[0] || null;

  let overallState: TeamOverviewState['overall_state'] = 'idle';
  if (currentExecutor?.session_id) {
    overallState = 'running';
  } else if (latestHandoff?.latest_event_kind === 'failed') {
    overallState = 'failed';
  } else if (hasTeamRequestInFlight) {
    overallState = 'waiting';
  }

  const currentExecutorSession =
    (currentExecutor?.session_id
      ? teamSessions.find((session) => session.id === currentExecutor.session_id) || null
      : null);

  return {
    team_id: teamId,
    team_name: teamName,
    leader_role_key: leaderRoleKey || null,
    leader_role_name: leaderRoleKey ? roleLabelById.get(leaderRoleKey) || leaderRoleKey : null,
    current_role_key: currentRoleKey || null,
    current_role_name: currentRoleKey ? roleLabelById.get(currentRoleKey) || currentRoleKey : null,
    overall_state: overallState,
    current_executor_session_id: currentExecutor?.session_id || null,
    current_executor_role_key: getSessionRoleKey(currentExecutorSession) || null,
    current_executor_role_name: currentExecutorSession
      ? roleLabelById.get(getSessionRoleKey(currentExecutorSession) || '') || getSessionRoleKey(currentExecutorSession) || null
      : null,
    current_executor_title: currentExecutorSession?.title || null,
    members: sortedMembers.map((session) => {
      const roleKey = getSessionRoleKey(session);
      return {
        session_id: session.id,
        title: session.title,
        role_key: roleKey,
        role_name: roleKey ? roleLabelById.get(roleKey) || roleKey : null,
        is_leader: Boolean(leaderRoleKey && roleKey === leaderRoleKey),
        is_executing: currentExecutor?.session_id === session.id,
        has_unread: Boolean(unreadBySession[session.id]),
        has_permission: Boolean(pendingPermissionBySession[session.id]),
        updated_at: session.updated_at || session.created_at,
      };
    }),
    handoffs: groupedHandoffs,
  };
};
