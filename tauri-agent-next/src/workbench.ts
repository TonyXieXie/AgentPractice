import type {
  JsonObject,
  PrivateExecutionEvent,
  SharedFact,
} from "./types";

export type WorkbenchTone = "neutral" | "accent" | "success" | "warning" | "danger";
export type AgentStepKind =
  | "message"
  | "state"
  | "directive"
  | "tool_call"
  | "observation"
  | "llm"
  | "final_result"
  | "event";
export type EdgeStatus = "done" | "active";

export interface WorkbenchSharedFactRecord {
  seq: number;
  factId: string;
  sessionId: string;
  runId: string;
  messageId: string;
  senderId: string;
  targetAgentId: string;
  targetProfileId: string;
  topic: string;
  factType: string;
  visibility: string;
  level: string;
  payload: JsonObject;
  metadata: JsonObject;
  createdAt: string;
  raw: SharedFact;
}

export interface WorkbenchPrivateEventRecord {
  privateEventId: number;
  sessionId: string;
  runId: string;
  ownerAgentId: string;
  taskId: string;
  messageId: string;
  toolCallId: string;
  triggerFactId: string;
  parentPrivateEventId: number | null;
  kind: string;
  payload: JsonObject;
  createdAt: string;
  raw: PrivateExecutionEvent;
}

export interface RunInfoView {
  sessionId: string;
  runId: string;
  status: string;
  latestSeq: number;
  strategy: string;
  reply: string;
  error: string;
  startedAt: string;
  finishedAt: string;
  controllerAgentId: string;
  userAgentId: string;
  assistantAgentId: string;
}

export interface RunActorView {
  id: string;
  name: string;
  subtitle: string;
  status: string;
  role: string;
  reason: string;
  lastTopic: string;
  isKnownSystem: boolean;
  order: number;
  metadata: JsonObject;
}

export interface MessageExchangeView {
  id: string;
  seq: number;
  messageId: string;
  topic: string;
  objectType: string;
  senderId: string;
  receiverId: string;
  senderName: string;
  receiverName: string;
  senderStatus: string;
  receiverStatus: string;
  summary: string;
  isGraphable: boolean;
  receivedSeq: number | null;
  createdAt: string;
  sent: WorkbenchSharedFactRecord;
  received: null;
  directivePayload: JsonObject;
}

export interface HandoffEdgeView {
  key: string;
  from: string;
  to: string;
  topic: string;
  firstSeq: number;
  lastSeq: number;
  count: number;
  status: EdgeStatus;
  latestExchangeId: string;
}

export interface AgentStepView {
  id: string;
  seq: number;
  taskId: string;
  kind: AgentStepKind;
  title: string;
  subtitle: string;
  body: string;
  tone: WorkbenchTone;
  eventType: string;
  toolCallId: string;
  messageId: string;
  createdAt: string;
  raw: unknown;
}

export interface AgentTaskGroupView {
  id: string;
  agentId: string;
  title: string;
  status: string;
  reason: string;
  steps: AgentStepView[];
  startedAt: string;
  lastAt: string;
}

type ControlIds = {
  controllerAgentId: string;
  userAgentId: string;
  assistantAgentId: string;
};

type TimelineEntry = {
  groupKey: string;
  groupReason: string;
  groupTitle: string;
  statusHint: string;
  createdAt: string;
  lane: number;
  order: number;
  step: AgentStepView;
};

const SUMMARY_FIELDS = [
  "reply",
  "content",
  "message",
  "planning_summary",
  "plan_summary",
  "code_summary",
  "summary_text",
  "result",
  "error",
  "status",
  "topic",
  "tool_name",
  "requested_strategy",
  "strategy",
] as const;

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function toJsonObject(value: unknown): JsonObject {
  return isObject(value) ? (value as JsonObject) : {};
}

function coerceText(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string") {
    return value.trim();
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.map((item) => coerceText(item)).filter(Boolean).join(" ").trim();
  }
  if (isObject(value)) {
    for (const key of SUMMARY_FIELDS) {
      const text = coerceText(value[key]);
      if (text) {
        return text;
      }
    }
    return safeJson(value);
  }
  return String(value).trim();
}

function payloadOfFact(fact: SharedFact): JsonObject {
  return toJsonObject(fact.payload_json);
}

function metadataOfFact(fact: SharedFact): JsonObject {
  return toJsonObject(fact.metadata_json);
}

function payloadOfPrivateEvent(event: PrivateExecutionEvent): JsonObject {
  return toJsonObject(event.payload_json);
}

export function cleanText(value: unknown): string {
  return coerceText(value);
}

export function trimText(value: string, maxLength: number): string {
  return value.length <= maxLength ? value : `${value.slice(0, maxLength - 3)}...`;
}

export function formatError(error: unknown): string {
  if (typeof error === "string") {
    return error;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return error ? String(error) : "unknown error";
}

export function formatTime(value: string): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) {
    return value;
  }
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function stringifyJson(data: unknown): string {
  return safeJson(data, true);
}

function safeJson(data: unknown, pretty = false): string {
  try {
    return JSON.stringify(data, null, pretty ? 2 : 0);
  } catch {
    return String(data);
  }
}

export function shortId(value: string): string {
  const text = cleanText(value);
  return text ? text.slice(0, 8) : "unknown";
}

function normalizeToken(value: string): string {
  return cleanText(value).replace(/[^a-zA-Z0-9]+/g, " ").trim();
}

function humanizeToken(value: string): string {
  const normalized = normalizeToken(value);
  if (!normalized) {
    return "";
  }
  return normalized
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => (part.length <= 3 ? part.toUpperCase() : `${part[0].toUpperCase()}${part.slice(1)}`))
    .join(" ");
}

function renderPayloadText(payload: JsonObject, fallback = ""): string {
  for (const field of SUMMARY_FIELDS) {
    const text = coerceText(payload[field]);
    if (text) {
      return text;
    }
  }
  const fallbackText = cleanText(fallback);
  return fallbackText || safeJson(payload);
}

export function mergeSharedFacts(
  existing: readonly SharedFact[],
  incoming: readonly SharedFact[],
): SharedFact[] {
  const bySeq = new Map<number, SharedFact>();
  [...existing, ...incoming].forEach((fact) => {
    bySeq.set(Number(fact.fact_seq || 0), fact);
  });
  return Array.from(bySeq.values()).sort((left, right) => Number(left.fact_seq) - Number(right.fact_seq));
}

export function mergePrivateEvents(
  existing: readonly PrivateExecutionEvent[],
  incoming: readonly PrivateExecutionEvent[],
): PrivateExecutionEvent[] {
  const byId = new Map<number, PrivateExecutionEvent>();
  [...existing, ...incoming].forEach((event) => {
    byId.set(Number(event.private_event_id || 0), event);
  });
  return Array.from(byId.values()).sort(
    (left, right) => Number(left.private_event_id) - Number(right.private_event_id),
  );
}

export function latestSharedSeq(facts: readonly SharedFact[]): number {
  return facts.reduce((max, fact) => Math.max(max, Number(fact.fact_seq || 0)), 0);
}

export function latestPrivateEventId(events: readonly PrivateExecutionEvent[]): number {
  return events.reduce((max, event) => Math.max(max, Number(event.private_event_id || 0)), 0);
}

export function normalizeSharedFactRecord(fact: SharedFact): WorkbenchSharedFactRecord {
  return {
    seq: Number(fact.fact_seq || 0),
    factId: cleanText(fact.fact_id),
    sessionId: cleanText(fact.session_id),
    runId: cleanText(fact.run_id),
    messageId: cleanText(fact.message_id),
    senderId: cleanText(fact.sender_id),
    targetAgentId: cleanText(fact.target_agent_id),
    targetProfileId: cleanText(fact.target_profile_id),
    topic: cleanText(fact.topic),
    factType: cleanText(fact.fact_type),
    visibility: cleanText(fact.visibility) || "public",
    level: cleanText(fact.level) || "info",
    payload: payloadOfFact(fact),
    metadata: metadataOfFact(fact),
    createdAt: cleanText(fact.created_at),
    raw: fact,
  };
}

export function normalizePrivateEventRecord(
  event: PrivateExecutionEvent,
): WorkbenchPrivateEventRecord {
  return {
    privateEventId: Number(event.private_event_id || 0),
    sessionId: cleanText(event.session_id),
    runId: cleanText(event.run_id),
    ownerAgentId: cleanText(event.owner_agent_id),
    taskId: cleanText(event.task_id),
    messageId: cleanText(event.message_id),
    toolCallId: cleanText(event.tool_call_id),
    triggerFactId: cleanText(event.trigger_fact_id),
    parentPrivateEventId:
      event.parent_private_event_id === null || event.parent_private_event_id === undefined
        ? null
        : Number(event.parent_private_event_id),
    kind: cleanText(event.kind),
    payload: payloadOfPrivateEvent(event),
    createdAt: cleanText(event.created_at),
    raw: event,
  };
}

function summarizePayload(payload: JsonObject, fallback = ""): string {
  const rendered = renderPayloadText(payload, fallback);
  return trimText(rendered.replace(/\s+/g, " "), 180);
}

function summarizeSharedFact(fact: SharedFact): string {
  const payload = payloadOfFact(fact);
  if (cleanText(fact.sender_id) === "external:http") {
    return summarizePayload(payload, fact.topic);
  }
  if (fact.topic === "run.finished") {
    const status = cleanText(payload.status);
    const reply = cleanText(payload.reply);
    const error = cleanText(payload.error);
    return trimText([status, error || reply].filter(Boolean).join(" · ") || fact.topic, 180);
  }
  return summarizePayload(payload, fact.topic);
}

function renderPrivateEventText(event: PrivateExecutionEvent): string {
  const payload = payloadOfPrivateEvent(event);
  if (event.kind === "tool_call") {
    const toolName = cleanText(payload.tool_name) || "tool";
    const argumentsText = coerceText(payload.arguments) || safeJson(payload.arguments);
    return argumentsText
      ? `我调用了工具 ${toolName}，参数是：${argumentsText}`
      : `我调用了工具 ${toolName}。`;
  }
  if (event.kind === "tool_result") {
    const toolName = cleanText(payload.tool_name) || "tool";
    if (payload.ok === false) {
      return `工具 ${toolName} 执行失败：${cleanText(payload.error) || "未知错误"}`;
    }
    const output = cleanText(payload.output);
    return output ? `工具 ${toolName} 返回：${output}` : `工具 ${toolName} 已执行完成。`;
  }
  if (event.kind === "execution_error") {
    return cleanText(payload.error) || cleanText(payload.content) || "执行失败";
  }
  return renderPayloadText(payload, event.kind);
}

function compareTimeline(
  leftAt: string,
  leftOrder: number,
  leftSeq: number,
  rightAt: string,
  rightOrder: number,
  rightSeq: number,
): number {
  if (leftAt !== rightAt) {
    return leftAt.localeCompare(rightAt);
  }
  if (leftOrder !== rightOrder) {
    return leftOrder - rightOrder;
  }
  return leftSeq - rightSeq;
}

function deriveControlIds(sharedFacts: readonly SharedFact[]): ControlIds {
  for (let index = sharedFacts.length - 1; index >= 0; index -= 1) {
    const payload = payloadOfFact(sharedFacts[index]);
    const controllerAgentId = cleanText(payload.controller_agent_id);
    const userAgentId = cleanText(payload.user_agent_id);
    const assistantAgentId = cleanText(payload.assistant_agent_id);
    if (controllerAgentId || userAgentId || assistantAgentId) {
      return { controllerAgentId, userAgentId, assistantAgentId };
    }
  }
  return {
    controllerAgentId: "",
    userAgentId: "",
    assistantAgentId: "",
  };
}

function knownAlias(agentId: string, controlIds: ControlIds): string {
  if (!agentId) {
    return "";
  }
  if (agentId === "external:http") {
    return "External HTTP";
  }
  if (controlIds.userAgentId && agentId === controlIds.userAgentId) {
    return "User Proxy";
  }
  if (
    controlIds.controllerAgentId &&
    controlIds.controllerAgentId !== controlIds.userAgentId &&
    agentId === controlIds.controllerAgentId
  ) {
    return "Run Controller";
  }
  if (controlIds.assistantAgentId && agentId === controlIds.assistantAgentId) {
    return "Primary Assistant";
  }
  return "";
}

function extractRunFacts(
  sharedFacts: readonly SharedFact[],
  selectedRunId?: string | null,
): SharedFact[] {
  const normalizedRunId = cleanText(selectedRunId);
  if (normalizedRunId) {
    const scoped = sharedFacts.filter((fact) => cleanText(fact.run_id) === normalizedRunId);
    if (scoped.length) {
      return scoped;
    }
  }
  return [...sharedFacts];
}

export function deriveRunInfo(
  sharedFacts: readonly SharedFact[],
  selectedRunId?: string | null,
): RunInfoView {
  const facts = extractRunFacts(sharedFacts, selectedRunId);
  const latestRunFact = [...facts].reverse().find((fact) => cleanText(fact.topic).startsWith("run."));
  const latestRunPayload = latestRunFact ? payloadOfFact(latestRunFact) : {};
  const firstRunStarted = facts.find((fact) => fact.topic === "run.started");
  const lastRunFinished = [...facts].reverse().find((fact) => fact.topic === "run.finished");
  const controlIds = deriveControlIds(facts);
  const latestRunId =
    cleanText(selectedRunId) ||
    cleanText(latestRunFact?.run_id) ||
    cleanText(facts[facts.length - 1]?.run_id);

  const inferredStatus =
    cleanText(latestRunPayload.status) ||
    (latestRunFact?.topic === "run.finished"
      ? "completed"
      : latestRunFact?.topic === "run.started"
        ? "running"
        : latestRunFact?.topic === "run.submit"
          ? "submitted"
          : "idle");

  return {
    sessionId: cleanText(facts[facts.length - 1]?.session_id),
    runId: latestRunId,
    status: inferredStatus,
    latestSeq: latestSharedSeq(facts),
    strategy:
      cleanText(latestRunPayload.strategy) ||
      cleanText(payloadOfFact(firstRunStarted || ({} as SharedFact)).strategy),
    reply: cleanText(latestRunPayload.reply),
    error: cleanText(latestRunPayload.error),
    startedAt: cleanText(firstRunStarted?.created_at),
    finishedAt: cleanText(lastRunFinished?.created_at),
    controllerAgentId: controlIds.controllerAgentId,
    userAgentId: controlIds.userAgentId,
    assistantAgentId: controlIds.assistantAgentId,
  };
}

function extractPrivateStatus(event: PrivateExecutionEvent): string {
  const payload = payloadOfPrivateEvent(event);
  if (event.kind === "execution_error" || payload.ok === false) {
    return "error";
  }
  if (event.kind === "tool_call") {
    return "working";
  }
  if (event.kind === "tool_result") {
    return "active";
  }
  return cleanText(payload.task_status) || cleanText(payload.status);
}

function extractPrivateReason(event: PrivateExecutionEvent): string {
  const payload = payloadOfPrivateEvent(event);
  return cleanText(payload.reason) || cleanText(payload.summary_text) || cleanText(payload.error);
}

function fallbackActorName(agentId: string, profileId: string, alias: string): string {
  if (alias) {
    return alias;
  }
  if (profileId) {
    return humanizeToken(profileId);
  }
  return `agent:${shortId(agentId)}`;
}

export function deriveRunActors(
  sharedFacts: readonly SharedFact[],
  privateEventsByAgent: Readonly<Record<string, readonly PrivateExecutionEvent[]>>,
  runInfo: RunInfoView,
): RunActorView[] {
  const facts = extractRunFacts(sharedFacts, runInfo.runId);
  const controlIds = deriveControlIds(facts);
  const actorIds = new Set<string>();
  const profileByAgent = new Map<string, string>();
  const firstSeqByAgent = new Map<string, number>();
  const latestTopicByAgent = new Map<string, string>();
  const latestPrivateByAgent = new Map<string, PrivateExecutionEvent>();

  facts.forEach((fact) => {
    const senderId = cleanText(fact.sender_id);
    const targetAgentId = cleanText(fact.target_agent_id);
    const targetProfileId = cleanText(fact.target_profile_id);
    if (senderId) {
      actorIds.add(senderId);
      if (!firstSeqByAgent.has(senderId)) {
        firstSeqByAgent.set(senderId, Number(fact.fact_seq || 0));
      }
      latestTopicByAgent.set(senderId, cleanText(fact.topic));
    }
    if (targetAgentId) {
      actorIds.add(targetAgentId);
      if (!firstSeqByAgent.has(targetAgentId)) {
        firstSeqByAgent.set(targetAgentId, Number(fact.fact_seq || 0));
      }
      latestTopicByAgent.set(targetAgentId, cleanText(fact.topic));
      if (targetProfileId && !profileByAgent.has(targetAgentId)) {
        profileByAgent.set(targetAgentId, targetProfileId);
      }
    }
  });

  [controlIds.controllerAgentId, controlIds.userAgentId, controlIds.assistantAgentId]
    .filter(Boolean)
    .forEach((agentId) => actorIds.add(agentId));

  Object.entries(privateEventsByAgent).forEach(([agentId, events]) => {
    const latest = [...events].pop();
    if (latest) {
      latestPrivateByAgent.set(agentId, latest);
      actorIds.add(agentId);
    }
  });

  const latestFactSenderId = cleanText(
    [...facts]
      .reverse()
      .find((fact) => !cleanText(fact.topic).startsWith("run."))?.sender_id,
  );
  const runSettled = ["completed", "done", "finished", "error", "stopped"].includes(
    cleanText(runInfo.status).toLowerCase(),
  );

  return Array.from(actorIds)
    .filter(Boolean)
    .map<RunActorView>((agentId) => {
      const alias = knownAlias(agentId, controlIds);
      const profileId = profileByAgent.get(agentId) || "";
      const latestPrivate = latestPrivateByAgent.get(agentId);
      const reason = latestPrivate ? extractPrivateReason(latestPrivate) : "";
      const privateStatus = latestPrivate ? extractPrivateStatus(latestPrivate) : "";
      const status = privateStatus || (runSettled ? "settled" : agentId === latestFactSenderId ? "active" : "observed");
      const lastTopic = latestTopicByAgent.get(agentId) || "";
      const name = fallbackActorName(agentId, profileId, alias);
      return {
        id: agentId,
        name,
        subtitle: [reason || lastTopic, status].filter(Boolean).join(" · "),
        status,
        role: profileId || alias,
        reason,
        lastTopic,
        isKnownSystem: Boolean(alias),
        order: firstSeqByAgent.get(agentId) ?? Number.MAX_SAFE_INTEGER,
        metadata: profileId ? { profile_id: profileId } : {},
      };
    })
    .sort((left, right) => {
      if (left.order !== right.order) {
        return left.order - right.order;
      }
      return left.name.localeCompare(right.name);
    });
}

export function deriveMessageExchanges(
  sharedFacts: readonly SharedFact[],
  actors: readonly RunActorView[],
  selectedRunId?: string | null,
): MessageExchangeView[] {
  const facts = extractRunFacts(sharedFacts, selectedRunId);
  const actorMap = new Map(actors.map((actor) => [actor.id, actor]));

  return facts.map<MessageExchangeView>((fact) => {
    const record = normalizeSharedFactRecord(fact);
    const sender = actorMap.get(record.senderId);
    const receiver = actorMap.get(record.targetAgentId);
    const objectType =
      cleanText(record.metadata.object_type) ||
      cleanText(record.metadata.message_type) ||
      record.factType ||
      "fact";
    return {
      id: record.factId || `fact-${record.seq}`,
      seq: record.seq,
      messageId: record.messageId,
      topic: record.topic || record.factType || "fact",
      objectType,
      senderId: record.senderId,
      receiverId: record.targetAgentId,
      senderName: sender?.name || fallbackActorName(record.senderId, "", ""),
      receiverName:
        receiver?.name ||
        (record.targetProfileId
          ? humanizeToken(record.targetProfileId)
          : record.targetAgentId
            ? `agent:${shortId(record.targetAgentId)}`
            : "Session"),
      senderStatus: sender?.status || "unknown",
      receiverStatus: receiver?.status || "unknown",
      summary: summarizeSharedFact(fact),
      isGraphable: Boolean(record.senderId && record.targetAgentId && record.senderId !== record.targetAgentId),
      receivedSeq: null,
      createdAt: record.createdAt,
      sent: record,
      received: null,
      directivePayload: record.payload,
    };
  });
}

export function deriveHandoffEdges(
  exchanges: readonly MessageExchangeView[],
): HandoffEdgeView[] {
  const edgeMap = new Map<string, HandoffEdgeView>();
  exchanges
    .filter((exchange) => exchange.isGraphable)
    .forEach((exchange) => {
      const key = `${exchange.senderId}|${exchange.receiverId}|${exchange.topic}`;
      const existing = edgeMap.get(key);
      if (!existing) {
        edgeMap.set(key, {
          key,
          from: exchange.senderId,
          to: exchange.receiverId,
          topic: exchange.topic,
          firstSeq: exchange.seq,
          lastSeq: exchange.seq,
          count: 1,
          status: ["completed", "settled", "done", "idle"].includes(
            exchange.receiverStatus.toLowerCase(),
          )
            ? "done"
            : "active",
          latestExchangeId: exchange.id,
        });
        return;
      }
      existing.lastSeq = Math.max(existing.lastSeq, exchange.seq);
      existing.count += 1;
      existing.latestExchangeId = exchange.id;
      if (!["completed", "settled", "done", "idle"].includes(exchange.receiverStatus.toLowerCase())) {
        existing.status = "active";
      }
    });
  return Array.from(edgeMap.values()).sort((left, right) => left.firstSeq - right.firstSeq);
}

function summarizeAgentPeer(fact: SharedFact, agentId: string): string {
  const senderId = cleanText(fact.sender_id);
  const targetAgentId = cleanText(fact.target_agent_id);
  const targetProfileId = cleanText(fact.target_profile_id);
  if (senderId === agentId) {
    return targetProfileId || targetAgentId || "session";
  }
  return senderId || "session";
}

function groupStatusPriority(status: string): number {
  const normalized = status.toLowerCase();
  if (["error", "failed", "danger"].includes(normalized)) {
    return 5;
  }
  if (["active", "working", "running"].includes(normalized)) {
    return 4;
  }
  if (["completed", "done", "settled", "success"].includes(normalized)) {
    return 3;
  }
  if (["observed", "idle"].includes(normalized)) {
    return 2;
  }
  if (normalized) {
    return 1;
  }
  return 0;
}

function nextGroupStatus(current: string, incoming: string): string {
  return groupStatusPriority(incoming) >= groupStatusPriority(current) ? incoming : current;
}

function toneForStatus(status: string): WorkbenchTone {
  const normalized = status.toLowerCase();
  if (["error", "failed", "stopped"].includes(normalized)) {
    return "danger";
  }
  if (["completed", "done", "finished", "settled", "success"].includes(normalized)) {
    return "success";
  }
  if (["active", "working", "running"].includes(normalized)) {
    return "accent";
  }
  if (["waiting", "paused", "pending"].includes(normalized)) {
    return "warning";
  }
  return "neutral";
}

function kindForSharedFact(fact: SharedFact, agentId: string, runInfo: RunInfoView): AgentStepKind {
  if (
    fact.topic === "run.finished" &&
    agentId &&
    (agentId === runInfo.userAgentId || agentId === runInfo.controllerAgentId)
  ) {
    return "final_result";
  }
  if (cleanText(fact.topic).startsWith("run.")) {
    return "state";
  }
  return "message";
}

function titleForSharedFact(fact: SharedFact): string {
  return humanizeToken(fact.topic) || "Shared Fact";
}

function bodyForSharedFact(fact: SharedFact, agentId: string): string {
  const direction =
    cleanText(fact.sender_id) === agentId
      ? `发送给 ${humanizeToken(summarizeAgentPeer(fact, agentId)) || summarizeAgentPeer(fact, agentId)}`
      : cleanText(fact.target_agent_id) === agentId
        ? `收到自 ${humanizeToken(summarizeAgentPeer(fact, agentId)) || summarizeAgentPeer(fact, agentId)}`
        : "共享事实";
  return trimText(`${direction} · ${summarizeSharedFact(fact)}`, 320);
}

function kindForPrivateEvent(event: PrivateExecutionEvent): AgentStepKind {
  if (event.kind === "tool_call") {
    return "tool_call";
  }
  if (event.kind === "tool_result") {
    return "observation";
  }
  if (event.kind === "reasoning_summary") {
    return "llm";
  }
  if (event.kind === "reasoning_note" && cleanText(payloadOfPrivateEvent(event).event_type) === "agent.state_changed") {
    return "state";
  }
  if (event.kind === "private_summary") {
    return "directive";
  }
  if (event.kind === "execution_error") {
    return "event";
  }
  return "event";
}

function titleForPrivateEvent(event: PrivateExecutionEvent): string {
  const payload = payloadOfPrivateEvent(event);
  if (event.kind === "tool_call" || event.kind === "tool_result") {
    return cleanText(payload.tool_name) || humanizeToken(event.kind);
  }
  if (event.kind === "reasoning_note" && cleanText(payload.event_type) === "agent.state_changed") {
    return "State Changed";
  }
  return humanizeToken(event.kind) || "Private Event";
}

function statusForPrivateEvent(event: PrivateExecutionEvent): string {
  return extractPrivateStatus(event) || (event.kind === "private_summary" ? "completed" : "observed");
}

function bodyForPrivateEvent(event: PrivateExecutionEvent): string {
  return trimText(renderPrivateEventText(event), 400);
}

function groupKeyForPrivateEvent(record: WorkbenchPrivateEventRecord): string {
  if (record.triggerFactId) {
    return `fact:${record.triggerFactId}`;
  }
  if (record.taskId) {
    return `task:${record.taskId}`;
  }
  if (record.toolCallId) {
    return `tool:${record.toolCallId}`;
  }
  return `private:${record.privateEventId}`;
}

function groupTitleForPrivateEvent(
  event: PrivateExecutionEvent,
  triggerFact: SharedFact | undefined,
): string {
  if (triggerFact) {
    return humanizeToken(triggerFact.topic) || "Triggered Work";
  }
  if (event.task_id) {
    return `Task ${shortId(cleanText(event.task_id))}`;
  }
  return humanizeToken(event.kind) || "Private Work";
}

export function deriveAgentTaskGroups(
  agentId: string,
  sharedFacts: readonly SharedFact[],
  privateEvents: readonly PrivateExecutionEvent[],
  runInfo: RunInfoView,
): AgentTaskGroupView[] {
  const facts = extractRunFacts(sharedFacts, runInfo.runId);
  const relevantShared = facts.filter(
    (fact) => cleanText(fact.sender_id) === agentId || cleanText(fact.target_agent_id) === agentId,
  );
  const relevantPrivate = privateEvents.filter(
    (event) => cleanText(event.owner_agent_id) === agentId,
  );
  const factMap = new Map(facts.map((fact) => [cleanText(fact.fact_id), fact]));
  const timeline: TimelineEntry[] = [];

  relevantShared.forEach((fact) => {
    const record = normalizeSharedFactRecord(fact);
    const statusHint =
      fact.topic === "run.finished" ? cleanText(payloadOfFact(fact).status) || "completed" : "observed";
    timeline.push({
      groupKey: `fact:${record.factId || record.seq}`,
      groupReason: summarizeSharedFact(fact),
      groupTitle: titleForSharedFact(fact),
      statusHint,
      createdAt: record.createdAt,
      lane: 0,
      order: record.seq,
      step: {
        id: `shared-${record.seq}-${record.factId || record.messageId || "fact"}`,
        seq: record.seq,
        taskId: `fact:${record.factId || record.seq}`,
        kind: kindForSharedFact(fact, agentId, runInfo),
        title: titleForSharedFact(fact),
        subtitle: [record.topic, `seq ${record.seq}`, formatTime(record.createdAt)].filter(Boolean).join(" · "),
        body: bodyForSharedFact(fact, agentId),
        tone: toneForStatus(statusHint || record.level),
        eventType: record.topic,
        toolCallId: "",
        messageId: record.messageId,
        createdAt: record.createdAt,
        raw: record.raw,
      },
    });
  });

  relevantPrivate.forEach((event) => {
    const record = normalizePrivateEventRecord(event);
    const triggerFact = record.triggerFactId ? factMap.get(record.triggerFactId) : undefined;
    const statusHint = statusForPrivateEvent(event);
    timeline.push({
      groupKey: groupKeyForPrivateEvent(record),
      groupReason: triggerFact ? summarizeSharedFact(triggerFact) : bodyForPrivateEvent(event),
      groupTitle: groupTitleForPrivateEvent(event, triggerFact),
      statusHint,
      createdAt: record.createdAt,
      lane: 1,
      order: record.privateEventId,
      step: {
        id: `private-${record.privateEventId}`,
        seq: record.privateEventId,
        taskId: groupKeyForPrivateEvent(record),
        kind: kindForPrivateEvent(event),
        title: titleForPrivateEvent(event),
        subtitle: [record.kind, `event ${record.privateEventId}`, formatTime(record.createdAt)]
          .filter(Boolean)
          .join(" · "),
        body: bodyForPrivateEvent(event),
        tone: toneForStatus(statusHint),
        eventType: record.kind,
        toolCallId: record.toolCallId,
        messageId: record.messageId,
        createdAt: record.createdAt,
        raw: record.raw,
      },
    });
  });

  timeline.sort((left, right) =>
    compareTimeline(
      left.createdAt,
      left.lane,
      left.order,
      right.createdAt,
      right.lane,
      right.order,
    ),
  );

  const groups = new Map<string, AgentTaskGroupView>();
  timeline.forEach((entry) => {
    const existing = groups.get(entry.groupKey);
    if (existing) {
      existing.steps.push(entry.step);
      existing.lastAt = entry.createdAt || existing.lastAt;
      existing.status = nextGroupStatus(existing.status, entry.statusHint);
      if (!existing.reason && entry.groupReason) {
        existing.reason = entry.groupReason;
      }
      return;
    }
    groups.set(entry.groupKey, {
      id: entry.groupKey,
      agentId,
      title: entry.groupTitle,
      status: entry.statusHint || "observed",
      reason: entry.groupReason,
      steps: [entry.step],
      startedAt: entry.createdAt,
      lastAt: entry.createdAt,
    });
  });

  return Array.from(groups.values())
    .map((group) => ({
      ...group,
      steps: [...group.steps].sort((left, right) =>
        compareTimeline(left.createdAt, 0, left.seq, right.createdAt, 0, right.seq),
      ),
    }))
    .sort((left, right) =>
      compareTimeline(
        left.startedAt,
        0,
        left.steps[0]?.seq ?? Number.MAX_SAFE_INTEGER,
        right.startedAt,
        0,
        right.steps[0]?.seq ?? Number.MAX_SAFE_INTEGER,
      ),
    );
}

export function statusTone(status: string): WorkbenchTone {
  return toneForStatus(status);
}
