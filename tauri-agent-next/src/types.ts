export type SeverityLevel = "debug" | "info" | "warning" | "error";
export type VisibilityLevel = "public" | "internal" | "debug";

export type JsonObject = Record<string, unknown>;

export interface SharedFact {
  fact_id: string;
  session_id: string;
  run_id?: string | null;
  fact_seq: number;
  message_id?: string | null;
  sender_id: string;
  target_agent_id?: string | null;
  target_profile_id?: string | null;
  topic: string;
  fact_type: string;
  payload_json: JsonObject;
  metadata_json: JsonObject;
  visibility: VisibilityLevel;
  level: SeverityLevel;
  created_at: string;
}

export interface PrivateExecutionEvent {
  private_event_id: number;
  session_id: string;
  owner_agent_id: string;
  run_id?: string | null;
  task_id?: string | null;
  message_id?: string | null;
  tool_call_id?: string | null;
  trigger_fact_id?: string | null;
  parent_private_event_id?: number | null;
  kind: string;
  payload_json: JsonObject;
  created_at: string;
}

export interface CreateRunResponse {
  ok: true;
  run_id: string;
  session_id: string;
  user_agent_id: string;
  assistant_agent_id: string;
  status: "accepted";
}

export interface StopRunResponse {
  ok: true;
  run_id: string;
  status: string;
}

export interface SessionSharedFactsResponse {
  ok: true;
  session_id: string;
  shared_facts: SharedFact[];
  next_after_seq: number;
}

export interface SessionPrivateFactsResponse {
  ok: true;
  session_id: string;
  agent_id: string;
  private_events: PrivateExecutionEvent[];
  next_after_id: number;
}

export interface PromptTraceSnapshot {
  id: number;
  session_id: string;
  run_id?: string | null;
  agent_id?: string | null;
  llm_model?: string | null;
  max_context_tokens: number;
  prompt_budget: number;
  estimated_prompt_tokens: number;
  rendered_message_count: number;
  request_messages: JsonObject[];
  actions: JsonObject;
  created_at: string;
}

export interface SessionPromptTraceResponse {
  ok: true;
  session_id: string;
  agent_id: string;
  prompt_trace: PromptTraceSnapshot | null;
}

export interface WsAckFrame {
  kind: "ack";
  ws_session_id?: string | null;
  message?: string | null;
  payload: JsonObject;
}

export interface WsErrorFrame {
  kind: "error";
  ws_session_id?: string | null;
  message: string;
  payload: JsonObject;
}

export interface WsHeartbeatFrame {
  kind: "heartbeat";
  ws_session_id?: string | null;
  message: string;
}

export interface WsBootstrapSharedFactsFrame {
  kind: "bootstrap.shared_facts";
  ws_session_id?: string | null;
  shared_facts: SharedFact[];
}

export interface WsBootstrapPrivateEventsFrame {
  kind: "bootstrap.private_events";
  ws_session_id?: string | null;
  private_events: PrivateExecutionEvent[];
}

export interface WsBootstrapCursorsFrame {
  kind: "bootstrap.cursors";
  ws_session_id?: string | null;
  shared_after_seq: number;
  private_after_id: number;
}

export interface WsAppendSharedFactFrame {
  kind: "append.shared_fact";
  ws_session_id?: string | null;
  event_id: string;
  shared_fact: SharedFact;
}

export interface WsAppendPrivateEventFrame {
  kind: "append.private_event";
  ws_session_id?: string | null;
  event_id: string;
  private_event: PrivateExecutionEvent;
}

export type WsFrame =
  | WsAckFrame
  | WsErrorFrame
  | WsHeartbeatFrame
  | WsBootstrapSharedFactsFrame
  | WsBootstrapPrivateEventsFrame
  | WsBootstrapCursorsFrame
  | WsAppendSharedFactFrame
  | WsAppendPrivateEventFrame;
