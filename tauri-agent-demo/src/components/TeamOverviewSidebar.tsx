import type { GroupedHandoffItem, TeamOverviewMemberState, TeamOverviewState } from '../types';
import './TeamOverviewSidebar.css';

type TeamOverviewSidebarProps = {
  overview: TeamOverviewState;
  onSelectSession: (sessionId: string) => void;
  onToggle: () => void;
};

const STATE_LABELS: Record<TeamOverviewState['overall_state'], string> = {
  running: 'Running',
  waiting: 'Waiting',
  idle: 'Idle',
  failed: 'Failed',
};

const HANDOFF_STAGE_LABELS: Record<GroupedHandoffItem['latest_event_kind'], string> = {
  requested: 'Requested',
  started: 'Started',
  completed: 'Completed',
  failed: 'Failed',
};

const formatRelativeTime = (value?: string | null) => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 60_000) return 'just now';
  const diffMin = Math.floor(diffMs / 60_000);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour}h ago`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 7) return `${diffDay}d ago`;
  return date.toLocaleDateString();
};

const getChangedFileBadge = (status: string) => {
  if (status === 'added') return 'A';
  if (status === 'deleted') return 'D';
  return 'M';
};

const getArtifactOwnerLabel = (item: GroupedHandoffItem) => item.artifact_owner_role_key || item.to_role_key || item.from_role_key || 'unknown';

const getMemberStatusLabel = (member: TeamOverviewMemberState) => {
  if (member.has_permission) return 'Permission';
  if (member.is_executing) return 'Executing';
  if (member.has_unread) return 'Unread';
  return 'Idle';
};

function HandoffCard({ item }: { item: GroupedHandoffItem }) {
  const changedFiles = item.changed_files || [];
  const hasDetails = Boolean(item.reason || item.work_summary || item.error);
  const artifactOwnerLabel = getArtifactOwnerLabel(item);
  const changedFilesLabel =
    changedFiles.length > 0
      ? `${artifactOwnerLabel} changed ${changedFiles.length} file${changedFiles.length === 1 ? '' : 's'}`
      : item.artifact_summary || '';

  return (
    <article className={`team-handoff-card ${item.latest_event_kind}`}>
      <div className="team-handoff-top">
        <div className="team-handoff-route">
          <span className="team-handoff-role">{item.from_role_key || 'unknown'}</span>
          <span className="team-handoff-arrow">to</span>
          <span className="team-handoff-role">{item.to_role_key || 'unknown'}</span>
        </div>
        <span className={`team-handoff-stage ${item.latest_event_kind}`}>
          {HANDOFF_STAGE_LABELS[item.latest_event_kind]}
        </span>
      </div>
      <div className="team-handoff-meta">
        {changedFilesLabel && <span>{changedFilesLabel}</span>}
        {item.latest_created_at && <span>{formatRelativeTime(item.latest_created_at)}</span>}
      </div>
      {hasDetails && (
        <details className="team-handoff-details">
          <summary>{item.error ? 'Details / error' : 'Details'}</summary>
          <div className="team-handoff-details-body">
            {item.reason && <div className="team-handoff-copy">{item.reason}</div>}
            {item.work_summary && <div className="team-handoff-copy muted">{item.work_summary}</div>}
            {item.error && <div className="team-handoff-error">{item.error}</div>}
          </div>
        </details>
      )}
      {changedFiles.length > 0 && (
        <details className="team-handoff-files">
          <summary>{`Changed by ${artifactOwnerLabel}`}</summary>
          <div className="team-handoff-files-list">
            {changedFiles.map((file, index) => (
              <div key={`${file.path}-${file.status}-${index}`} className="team-handoff-file-row">
                <span className={`team-handoff-file-badge ${file.status}`}>{getChangedFileBadge(file.status)}</span>
                <code className="team-handoff-file-path">{file.path}</code>
              </div>
            ))}
          </div>
        </details>
      )}
    </article>
  );
}

export default function TeamOverviewSidebar({
  overview,
  onSelectSession,
  onToggle,
}: TeamOverviewSidebarProps) {
  return (
    <aside className="team-overview-sidebar">
      <div className="team-overview-header">
        <div>
          <div className="team-overview-eyebrow">Team overview</div>
          <h2>{overview.team_name}</h2>
        </div>
        <button type="button" className="team-overview-toggle" onClick={onToggle} aria-label="Collapse team overview">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M15 18l-6-6 6-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>
      </div>

      <div className="team-overview-summary">
        <div className="team-summary-card">
          <span className="team-summary-label">Status</span>
          <span className={`team-summary-value ${overview.overall_state}`}>{STATE_LABELS[overview.overall_state]}</span>
        </div>
        <div className="team-summary-card">
          <span className="team-summary-label">Leader</span>
          <span className="team-summary-value">{overview.leader_role_name || overview.leader_role_key || 'Unknown'}</span>
        </div>
        <div className="team-summary-card">
          <span className="team-summary-label">Selected role</span>
          <span className="team-summary-value">{overview.current_role_name || overview.current_role_key || 'Unknown'}</span>
        </div>
        <div className="team-summary-card">
          <span className="team-summary-label">Current executor</span>
          <span className="team-summary-value">
            {overview.current_executor_role_name || overview.current_executor_role_key || 'None'}
          </span>
          {overview.current_executor_title && (
            <span className="team-summary-subtle">{overview.current_executor_title}</span>
          )}
        </div>
      </div>

      <section className="team-overview-section">
        <div className="team-overview-section-title">Members</div>
        <div className="team-members-list">
          {overview.members.map((member) => (
            <button
              key={member.session_id}
              type="button"
              className={`team-member-card${member.is_executing ? ' executing' : ''}`}
              onClick={() => onSelectSession(member.session_id)}
            >
              <div className="team-member-top">
                <div className="team-member-role">
                  <span>{member.role_name || member.role_key || 'Unknown'}</span>
                  {member.is_leader && <span className="team-member-pill">leader</span>}
                </div>
                <span className={`team-member-status ${getMemberStatusLabel(member).toLowerCase()}`}>
                  {getMemberStatusLabel(member)}
                </span>
              </div>
              <div className="team-member-title">{member.title}</div>
              <div className="team-member-time">{formatRelativeTime(member.updated_at)}</div>
            </button>
          ))}
        </div>
      </section>

      <section className="team-overview-section grow">
        <div className="team-overview-section-title">Recent handoffs</div>
        {overview.handoffs.length > 0 ? (
          <div className="team-handoff-list">
            {overview.handoffs.map((item) => (
              <HandoffCard key={item.handoff_id} item={item} />
            ))}
          </div>
        ) : (
          <div className="team-overview-empty">No handoff activity yet.</div>
        )}
      </section>
    </aside>
  );
}
