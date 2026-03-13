import { buildApiError } from "./base";
import type {
  CreateRunResponse,
  SessionPrivateFactsResponse,
  SessionPromptTraceResponse,
  SessionSharedFactsResponse,
  StopRunResponse,
} from "../types";

export interface CreateRunPayload {
  content: string;
  strategy?: string;
  work_path?: string;
  request_overrides: Record<string, unknown>;
}

async function fetchJson<T>(baseUrl: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${baseUrl}${path}`, init);
  if (!response.ok) {
    throw await buildApiError(response, `Request failed for ${path}`);
  }
  return (await response.json()) as T;
}

export function createRun(baseUrl: string, payload: CreateRunPayload): Promise<CreateRunResponse> {
  return fetchJson<CreateRunResponse>(baseUrl, "/runs", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

export function stopRun(baseUrl: string, runId: string): Promise<StopRunResponse> {
  return fetchJson<StopRunResponse>(baseUrl, `/runs/${encodeURIComponent(runId)}/stop`, {
    method: "POST",
  });
}

function buildQuery(params: Record<string, string | number | undefined | null>): string {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") {
      return;
    }
    query.set(key, String(value));
  });
  const text = query.toString();
  return text ? `?${text}` : "";
}

export function getSessionSharedFacts(
  baseUrl: string,
  sessionId: string,
  afterSeq: number,
  limit: number,
  runId?: string | null,
): Promise<SessionSharedFactsResponse> {
  return fetchJson<SessionSharedFactsResponse>(
    baseUrl,
    `/sessions/${encodeURIComponent(sessionId)}/facts/shared${buildQuery({
      after_seq: afterSeq,
      limit,
      run_id: runId,
    })}`,
  );
}

export function getSessionPrivateFacts(
  baseUrl: string,
  sessionId: string,
  agentId: string,
  afterId: number,
  limit: number,
  runId?: string | null,
): Promise<SessionPrivateFactsResponse> {
  return fetchJson<SessionPrivateFactsResponse>(
    baseUrl,
    `/sessions/${encodeURIComponent(sessionId)}/facts/private${buildQuery({
      agent_id: agentId,
      after_id: afterId,
      limit,
      run_id: runId,
    })}`,
  );
}

export function getLatestSessionPromptTrace(
  baseUrl: string,
  sessionId: string,
  agentId: string,
  runId?: string | null,
): Promise<SessionPromptTraceResponse> {
  return fetchJson<SessionPromptTraceResponse>(
    baseUrl,
    `/sessions/${encodeURIComponent(sessionId)}/prompt-trace/latest${buildQuery({
      agent_id: agentId,
      run_id: runId,
    })}`,
  );
}
