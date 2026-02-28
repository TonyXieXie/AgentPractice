import type { Message, TaskStatus, TaskErrorCode } from './types';

export interface TaskWsEventBase {
  session_id: string;
  task_id: string;
  seq: number;
  status?: TaskStatus;
  message?: string;
  payload?: Record<string, any>;
  error_code?: TaskErrorCode;
  error_message?: string;
  timestamp?: string;
  instance_id?: string;
}

export interface TaskStartedEvent extends TaskWsEventBase {
  type: 'task_started';
}

export interface TaskProgressEvent extends TaskWsEventBase {
  type: 'task_progress';
}

export interface TaskHandoffEvent extends TaskWsEventBase {
  type: 'task_handoff';
}

export interface TaskCompletedEvent extends TaskWsEventBase {
  type: 'task_completed';
}

export interface TaskFailedEvent extends TaskWsEventBase {
  type: 'task_failed';
}

export interface TaskCancelledEvent extends TaskWsEventBase {
  type: 'task_cancelled';
}

export interface SubagentDoneEvent {
  type: 'subagent_done';
  session_id: string;
  message: Message;
  child_session_id?: string;
  status?: 'ok' | 'error';
}

export interface SubagentStartedEvent {
  type: 'subagent_started';
  session_id: string;
  child_session_id?: string;
  child_title?: string;
}

export interface PtyOutputEvent {
  type: 'pty_output';
  session_id: string;
  pty_id: string;
  chunk: string;
  cursor: number;
  status?: 'running' | 'exited';
  exit_code?: number | null;
}

export type WsEvent =
  | TaskStartedEvent
  | TaskProgressEvent
  | TaskHandoffEvent
  | TaskCompletedEvent
  | TaskFailedEvent
  | TaskCancelledEvent
  | SubagentDoneEvent
  | SubagentStartedEvent
  | PtyOutputEvent;

export type WsStatusListener = (connected: boolean) => void;
