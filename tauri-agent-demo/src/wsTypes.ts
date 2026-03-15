import type { Message } from './types';

export type WsEvent = SubagentDoneEvent | SubagentStartedEvent | SessionMessageEvent;

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

export interface SessionMessageEvent {
  type: 'session_message';
  session_id: string;
  message: Message;
  active_agent_profile?: string | null;
}

export type WsStatusListener = (connected: boolean) => void;
