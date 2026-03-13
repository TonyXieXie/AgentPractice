import type { ReactNode } from "react";
import { statusTone, type RunActorView, type WorkbenchTone } from "../workbench";

export function StatusPill({
  icon,
  label,
  tone = "neutral",
}: {
  icon: ReactNode;
  label: string;
  tone?: WorkbenchTone;
}) {
  return (
    <div className={`status-pill tone-${tone}`}>
      <span className="status-pill-icon">{icon}</span>
      <span>{label}</span>
    </div>
  );
}

export function MetricTile({ label, value }: { label: string; value: string }) {
  return (
    <article className="metric-tile">
      <span className="metric-label">{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

export function ActorRow({
  actor,
  selected,
  onClick,
}: {
  actor: RunActorView;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`actor-row${selected ? " is-selected" : ""}`}
      onClick={onClick}
    >
      <span className={`actor-dot tone-${statusTone(actor.status)}`} />
      <span className="actor-copy">
        <span className="actor-name">{actor.name}</span>
        <span className="actor-subtitle">{actor.subtitle || actor.status}</span>
      </span>
      <span className="actor-meta">{actor.status}</span>
    </button>
  );
}
