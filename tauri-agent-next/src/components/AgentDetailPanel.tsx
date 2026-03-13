import { Activity, Bot, ChevronLeft, TerminalSquare, type LucideIcon } from "lucide-react";
import { useEffect, useRef } from "react";
import type { AgentTaskGroupView, AgentStepView, RunActorView } from "../workbench";
import { formatTime, statusTone, stringifyJson } from "../workbench";
import { MetricTile } from "./WorkbenchPrimitives";

function iconForStepKind(kind: AgentStepView["kind"]): LucideIcon {
  if (kind === "message") {
    return Bot;
  }
  if (kind === "tool_call" || kind === "observation" || kind === "directive") {
    return TerminalSquare;
  }
  return Activity;
}

export function AgentDetailPanel({
  actor,
  groups,
  selectedStepId,
  selectedStep,
  onBack,
  onSelectStep,
  onSelectToolStep,
}: {
  actor: RunActorView;
  groups: readonly AgentTaskGroupView[];
  selectedStepId: string | null;
  selectedStep: AgentStepView | null;
  onBack: () => void;
  onSelectStep: (step: AgentStepView) => void;
  onSelectToolStep: (step: AgentStepView) => void;
}) {
  const stepRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  useEffect(() => {
    if (!selectedStepId) {
      return;
    }
    const node = stepRefs.current[selectedStepId];
    if (!node) {
      return;
    }
    window.requestAnimationFrame(() => {
      node.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  }, [selectedStepId]);

  return (
    <div className="detail-stack">
      <div className="detail-header">
        <div className="detail-agent">
          <button type="button" className="back-button" onClick={onBack}>
            <ChevronLeft size={14} />
            <span>总览</span>
          </button>
          <div className="detail-agent-copy">
            <p className="panel-kicker">agent detail</p>
            <h2>{actor.name}</h2>
            <p>{actor.subtitle || actor.status}</p>
          </div>
        </div>
        <div className={`detail-chip tone-${statusTone(actor.status)}`}>{actor.status}</div>
      </div>

      <section className="detail-section">
        <div className="metric-grid">
          <MetricTile label="agent_id" value={actor.id} />
          <MetricTile label="status" value={actor.status} />
          <MetricTile label="reason" value={actor.reason || "-"} />
          <MetricTile
            label="steps"
            value={String(groups.reduce((count, group) => count + group.steps.length, 0))}
          />
        </div>
      </section>

      <section className="detail-section detail-section-fill">
        <div className="section-head">
          <h3>Execution Trace</h3>
          <span>{groups.length} task groups</span>
        </div>
        <div className="task-group-list">
          {groups.length ? (
            groups.map((group) => (
              <section key={group.id} className="task-group">
                <div className="task-group-head">
                  <div>
                    <strong>{group.title}</strong>
                    <p>
                      {group.reason || group.status}
                      {group.startedAt ? ` · ${formatTime(group.startedAt)}` : ""}
                    </p>
                  </div>
                  <span className={`exchange-badge tone-${statusTone(group.status)}`}>{group.status || "unknown"}</span>
                </div>
                <div className="task-step-list">
                  {group.steps.map((step) => {
                    const StepIcon = iconForStepKind(step.kind);
                    const selected = selectedStepId === step.id;
                    return (
                      <button
                        key={step.id}
                        type="button"
                        className={`step-card tone-${step.tone}${selected ? " is-selected" : ""}`}
                        onClick={() => (step.toolCallId ? onSelectToolStep(step) : onSelectStep(step))}
                        ref={(node) => {
                          stepRefs.current[step.id] = node;
                        }}
                      >
                        <div className="step-head">
                          <div className="step-title">
                            <StepIcon size={13} />
                            <span>{step.title}</span>
                          </div>
                          <span className="step-meta">{step.subtitle}</span>
                        </div>
                        <div className="step-body">{step.body}</div>
                      </button>
                    );
                  })}
                </div>
              </section>
            ))
          ) : (
            <div className="empty-box">当前 Agent 暂无执行步骤。</div>
          )}
        </div>
      </section>

      <section className="detail-section">
        <div className="section-head">
          <h3>Raw JSON</h3>
          <span>{selectedStep ? selectedStep.eventType : "none"}</span>
        </div>
        <div className="json-shell">
          <pre>{stringifyJson(selectedStep?.raw ?? { message: "暂无选中的 step" })}</pre>
        </div>
      </section>
    </div>
  );
}
