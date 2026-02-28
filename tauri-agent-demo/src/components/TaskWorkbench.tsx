import { useEffect, useMemo, useState } from 'react';
import {
  cancelTask,
  createTask,
  getTask,
  getTaskEvents,
  handoffTask,
  listTasks,
  type AgentTaskCreatePayload,
  type AgentTaskHandoffPayload,
} from '../api';
import type { AgentTask, AgentTaskEvent } from '../types';
import { wsClient } from '../wsClient';
import type { WsEvent } from '../wsTypes';
import './TaskWorkbench.css';

type Props = {
  sessionId: string | null;
  enabled: boolean;
};

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled']);

const isTaskEvent = (event: WsEvent): event is Extract<WsEvent, { type: 'task_started' | 'task_progress' | 'task_handoff' | 'task_completed' | 'task_failed' | 'task_cancelled' }> => {
  return (
    event.type === 'task_started' ||
    event.type === 'task_progress' ||
    event.type === 'task_handoff' ||
    event.type === 'task_completed' ||
    event.type === 'task_failed' ||
    event.type === 'task_cancelled'
  );
};

export default function TaskWorkbench({ sessionId, enabled }: Props) {
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [events, setEvents] = useState<AgentTaskEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createTitle, setCreateTitle] = useState('');
  const [createInput, setCreateInput] = useState('');
  const [targetProfileId, setTargetProfileId] = useState('');
  const [handoffInput, setHandoffInput] = useState('');
  const [handoffProfileId, setHandoffProfileId] = useState('');
  const [busy, setBusy] = useState(false);

  const selectedTask = useMemo(
    () => tasks.find((item) => item.id === selectedTaskId) || null,
    [tasks, selectedTaskId]
  );

  const loadTasks = async () => {
    if (!sessionId || !enabled) {
      setTasks([]);
      setSelectedTaskId(null);
      setEvents([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const rows = await listTasks({ sessionId, limit: 200 });
      setTasks(rows);
      if (!selectedTaskId && rows.length > 0) {
        setSelectedTaskId(rows[0].id);
      } else if (selectedTaskId && !rows.some((item) => item.id === selectedTaskId)) {
        setSelectedTaskId(rows[0]?.id || null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load tasks');
    } finally {
      setLoading(false);
    }
  };

  const loadEvents = async (taskId: string) => {
    try {
      const rows = await getTaskEvents(taskId, 0, 1000);
      setEvents(rows);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load task events');
    }
  };

  useEffect(() => {
    void loadTasks();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, enabled]);

  useEffect(() => {
    if (!selectedTaskId) {
      setEvents([]);
      return;
    }
    void loadEvents(selectedTaskId);
  }, [selectedTaskId]);

  useEffect(() => {
    if (!enabled || !sessionId) return;
    const timer = window.setInterval(() => {
      void loadTasks();
      if (selectedTaskId) {
        void loadEvents(selectedTaskId);
      }
    }, 3000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, sessionId, selectedTaskId]);

  useEffect(() => {
    const off = wsClient.onEvent((event) => {
      if (!isTaskEvent(event)) return;
      if (!sessionId || event.session_id !== sessionId) return;

      setTasks((prev) => {
        const idx = prev.findIndex((item) => item.id === event.task_id);
        if (idx < 0) return prev;
        const next = [...prev];
        const current = next[idx];
        next[idx] = {
          ...current,
          status: (event.status as AgentTask['status']) || current.status,
          error_code: event.error_code || current.error_code,
          error_message: event.error_message || current.error_message,
          result:
            event.type === 'task_completed'
              ? String(event.payload?.result || current.result || '')
              : current.result,
          updated_at: event.timestamp || current.updated_at,
        };
        return next;
      });

      if (selectedTaskId && selectedTaskId === event.task_id) {
        setEvents((prev) => {
          if (prev.some((item) => item.seq === event.seq)) return prev;
          const next: AgentTaskEvent = {
            task_id: event.task_id,
            seq: event.seq,
            event_type: event.type,
            status: event.status,
            message: event.message,
            payload: event.payload,
            error_code: event.error_code,
            error_message: event.error_message,
            created_at: event.timestamp,
          };
          return [...prev, next].sort((a, b) => a.seq - b.seq);
        });
      }
    });
    return off;
  }, [sessionId, selectedTaskId]);

  const handleCreate = async () => {
    if (!sessionId || !createInput.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const payload: AgentTaskCreatePayload = {
        session_id: sessionId,
        title: createTitle.trim() || undefined,
        input: createInput,
        target_profile_id: targetProfileId.trim() || undefined,
        metadata: {
          origin: 'task_workbench',
        },
      };
      const created = await createTask(payload);
      setCreateInput('');
      setCreateTitle('');
      setTasks((prev) => [created, ...prev.filter((item) => item.id !== created.id)]);
      setSelectedTaskId(created.id);
      await loadEvents(created.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create task');
    } finally {
      setBusy(false);
    }
  };

  const handleHandoff = async () => {
    if (!selectedTask || !handoffInput.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const payload: AgentTaskHandoffPayload = {
        input: handoffInput,
        target_profile_id: handoffProfileId.trim() || undefined,
        metadata: {
          origin: 'task_workbench_handoff',
        },
      };
      const created = await handoffTask(selectedTask.id, payload);
      setHandoffInput('');
      setTasks((prev) => [created, ...prev.filter((item) => item.id !== created.id)]);
      setSelectedTaskId(created.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to handoff task');
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async () => {
    if (!selectedTask) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await cancelTask(selectedTask.id, { reason: 'Cancelled from task workbench', propagate: true });
      setTasks((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      await loadEvents(updated.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to cancel task');
    } finally {
      setBusy(false);
    }
  };

  const refreshSelected = async () => {
    if (!selectedTaskId) return;
    try {
      const task = await getTask(selectedTaskId);
      setTasks((prev) => prev.map((item) => (item.id === task.id ? task : item)));
      await loadEvents(selectedTaskId);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to refresh task');
    }
  };

  if (!enabled) {
    return <div className="task-workbench disabled">Task Center is disabled in app config.</div>;
  }

  if (!sessionId) {
    return <div className="task-workbench disabled">No active session. Switch to Legacy mode and create/select a session first.</div>;
  }

  return (
    <div className="task-workbench">
      <div className="task-workbench-header">
        <h2>Task Workbench</h2>
        <div className="task-workbench-meta">Session: {sessionId}</div>
      </div>

      {error && <div className="task-error">{error}</div>}

      <div className="task-grid">
        <section className="task-pane task-create-pane">
          <h3>Create Task</h3>
          <input
            value={createTitle}
            onChange={(e) => setCreateTitle(e.target.value)}
            placeholder="Task title (optional)"
          />
          <input
            value={targetProfileId}
            onChange={(e) => setTargetProfileId(e.target.value)}
            placeholder="Target profile id (optional)"
          />
          <textarea
            value={createInput}
            onChange={(e) => setCreateInput(e.target.value)}
            placeholder="Describe task input"
            rows={4}
          />
          <button type="button" onClick={handleCreate} disabled={busy || !createInput.trim()}>
            Create Task
          </button>
        </section>

        <section className="task-pane task-list-pane">
          <div className="task-list-header">
            <h3>Tasks</h3>
            <button type="button" onClick={() => void loadTasks()} disabled={loading || busy}>
              Refresh
            </button>
          </div>
          <div className="task-list">
            {tasks.map((task) => (
              <button
                key={task.id}
                type="button"
                className={`task-row ${selectedTaskId === task.id ? 'active' : ''}`}
                onClick={() => setSelectedTaskId(task.id)}
              >
                <div className="task-row-title">{task.title || 'Task'}</div>
                <div className={`task-row-status status-${task.status}`}>{task.status}</div>
                <div className="task-row-id">{task.id}</div>
              </button>
            ))}
            {tasks.length === 0 && <div className="task-empty">No tasks yet.</div>}
          </div>
        </section>

        <section className="task-pane task-detail-pane">
          <div className="task-detail-header">
            <h3>Task Detail</h3>
            <button type="button" onClick={refreshSelected} disabled={!selectedTaskId || busy}>
              Reload
            </button>
          </div>

          {selectedTask ? (
            <>
              <div className="task-detail-summary">
                <div><strong>ID:</strong> {selectedTask.id}</div>
                <div><strong>Status:</strong> <span className={`status-${selectedTask.status}`}>{selectedTask.status}</span></div>
                <div><strong>Instance:</strong> {selectedTask.assigned_instance_id || '-'}</div>
                <div><strong>Profile:</strong> {selectedTask.target_profile_id || '-'}</div>
                <div><strong>Loop:</strong> {selectedTask.loop_group_id || '-'} / {selectedTask.loop_iteration}</div>
                {selectedTask.error_message && <div className="task-detail-error"><strong>Error:</strong> {selectedTask.error_message}</div>}
                {selectedTask.result && (
                  <div className="task-detail-result">
                    <strong>Result:</strong>
                    <pre>{selectedTask.result}</pre>
                  </div>
                )}
              </div>

              <div className="task-actions">
                <textarea
                  value={handoffInput}
                  onChange={(e) => setHandoffInput(e.target.value)}
                  placeholder="Handoff payload"
                  rows={3}
                />
                <input
                  value={handoffProfileId}
                  onChange={(e) => setHandoffProfileId(e.target.value)}
                  placeholder="Handoff target profile (optional)"
                />
                <div className="task-actions-row">
                  <button type="button" onClick={handleHandoff} disabled={busy || !handoffInput.trim()}>
                    Handoff Task
                  </button>
                  <button
                    type="button"
                    onClick={handleCancel}
                    disabled={busy || TERMINAL.has(selectedTask.status)}
                  >
                    Cancel Task
                  </button>
                </div>
              </div>

              <div className="task-events">
                <h4>Event Stream</h4>
                <div className="task-event-list">
                  {events.map((event) => (
                    <div key={event.seq} className="task-event-row">
                      <div className="task-event-head">
                        <span>#{event.seq}</span>
                        <span>{event.event_type}</span>
                        <span>{event.status || '-'}</span>
                      </div>
                      <div className="task-event-body">{event.message || '-'}</div>
                    </div>
                  ))}
                  {events.length === 0 && <div className="task-empty">No events.</div>}
                </div>
              </div>
            </>
          ) : (
            <div className="task-empty">Select a task to inspect details.</div>
          )}
        </section>
      </div>
    </div>
  );
}
