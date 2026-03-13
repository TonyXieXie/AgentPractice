import {
  Activity,
  Cable,
  Copy,
  GitBranch,
  Play,
  Square,
  Wifi,
  WifiOff,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { resolveApiBaseUrl } from "./api/base";
import {
  createRun,
  getSessionPrivateFacts,
  getSessionSharedFacts,
  stopRun,
  type CreateRunPayload,
} from "./api/client";
import { buildWebSocketUrl } from "./api/ws";
import { AgentDetailPanel } from "./components/AgentDetailPanel";
import { HandoffConversationPanel } from "./components/HandoffConversationPanel";
import { HandoffGraph } from "./components/HandoffGraph";
import { MetricTile, StatusPill } from "./components/WorkbenchPrimitives";
import type {
  CreateRunResponse,
  PrivateExecutionEvent,
  SessionPrivateFactsResponse,
  SessionSharedFactsResponse,
  SharedFact,
  WsFrame,
} from "./types";
import {
  cleanText,
  deriveAgentTaskGroups,
  deriveHandoffEdges,
  deriveMessageExchanges,
  deriveRunActors,
  deriveRunInfo,
  formatError,
  latestPrivateEventId,
  latestSharedSeq,
  mergePrivateEvents,
  mergeSharedFacts,
  statusTone,
  type AgentStepView,
} from "./workbench";

const FACT_PAGE_SIZE = 200;
const HEARTBEAT_MS = 25_000;
const RECONNECT_MAX_MS = 8_000;

type ScopeState = {
  sessionId: string | null;
  runId: string | null;
  agentId: string | null;
  toolCallId: string | null;
};

function readScopeFromUrl(): ScopeState {
  if (typeof window === "undefined") {
    return { sessionId: null, runId: null, agentId: null, toolCallId: null };
  }
  const params = new URLSearchParams(window.location.search);
  return {
    sessionId: cleanText(params.get("session_id")) || null,
    runId: cleanText(params.get("run_id")) || null,
    agentId: cleanText(params.get("agent_id")) || null,
    toolCallId: cleanText(params.get("tool_call_id")) || null,
  };
}

function factMatchesScope(fact: SharedFact, scope: ScopeState): boolean {
  if (!scope.sessionId || cleanText(fact.session_id) !== scope.sessionId) {
    return false;
  }
  return !scope.runId || cleanText(fact.run_id) === scope.runId;
}

function privateEventMatchesScope(event: PrivateExecutionEvent, scope: ScopeState): boolean {
  if (!scope.sessionId || cleanText(event.session_id) !== scope.sessionId) {
    return false;
  }
  if (scope.runId && cleanText(event.run_id) !== scope.runId) {
    return false;
  }
  return !scope.agentId || cleanText(event.owner_agent_id) === scope.agentId;
}

function taskLabelFromFacts(sharedFacts: readonly SharedFact[], sessionId: string | null): string {
  const userFact = sharedFacts.find((fact) => cleanText(fact.sender_id) === "external:http");
  if (userFact) {
    const payload = userFact.payload_json;
    const content = cleanText(payload.content) || cleanText(payload.text) || cleanText(payload.reply);
    if (content) {
      return content;
    }
  }
  const latestControllerInput = [...sharedFacts]
    .reverse()
    .find((fact) => ["run.submit", "run.controller.input"].includes(cleanText(fact.topic)));
  if (latestControllerInput) {
    const payload = latestControllerInput.payload_json;
    const content =
      cleanText(payload.content) ||
      cleanText(payload.planning_summary) ||
      cleanText(payload.plan_summary) ||
      cleanText(payload.code_summary);
    if (content) {
      return content;
    }
  }
  return sessionId ? `观察 session ${sessionId}` : "创建或加载一个 session 开始观察多 Agent";
}

export default function App() {
  const [initialScope] = useState(readScopeFromUrl);
  const [baseUrl, setBaseUrl] = useState("");
  const [ready, setReady] = useState(false);
  const [feedback, setFeedback] = useState("Resolving backend endpoint...");
  const [scope, setScope] = useState<ScopeState>(initialScope);
  const scopeRef = useRef(initialScope);
  const [promptValue, setPromptValue] = useState("");
  const [strategy, setStrategy] = useState("simple");
  const [workPath, setWorkPath] = useState("");
  const [sessionIdInput, setSessionIdInput] = useState(initialScope.sessionId || "");
  const [wsConnected, setWsConnected] = useState(false);
  const [reconnectDelay, setReconnectDelay] = useState(1000);
  const reconnectDelayRef = useRef(1000);
  const [sharedFacts, setSharedFacts] = useState<SharedFact[]>([]);
  const sharedFactsRef = useRef<SharedFact[]>([]);
  const [privateEventsByAgent, setPrivateEventsByAgent] = useState<Record<string, PrivateExecutionEvent[]>>({});
  const privateEventsByAgentRef = useRef<Record<string, PrivateExecutionEvent[]>>({});
  const [selectedExchangeId, setSelectedExchangeId] = useState<string | null>(null);
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null);
  const [autoFollow, setAutoFollow] = useState(true);
  const autoFollowRef = useRef(true);
  const [busyLoading, setBusyLoading] = useState(false);
  const [busyCreate, setBusyCreate] = useState(false);
  const [busyStop, setBusyStop] = useState(false);
  const feedRef = useRef<HTMLDivElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<number | null>(null);
  const heartbeatTimerRef = useRef<number | null>(null);
  const sessionLoadTokenRef = useRef(0);
  const initialHydrationRef = useRef(false);
  const currentSessionId = scope.sessionId;
  const currentRunId = scope.runId || null;

  const runInfo = useMemo(() => deriveRunInfo(sharedFacts, scope.runId), [sharedFacts, scope.runId]);
  const resolvedRunId = currentRunId || runInfo.runId || null;
  const actors = useMemo(
    () => deriveRunActors(sharedFacts, privateEventsByAgent, runInfo),
    [privateEventsByAgent, runInfo, sharedFacts],
  );
  const actorMap = useMemo(() => new Map(actors.map((actor) => [actor.id, actor])), [actors]);
  const exchanges = useMemo(
    () => deriveMessageExchanges(sharedFacts, actors, scope.runId),
    [actors, scope.runId, sharedFacts],
  );
  const handoffEdges = useMemo(() => deriveHandoffEdges(exchanges), [exchanges]);
  const currentPrivateEvents = useMemo(
    () => (scope.agentId ? privateEventsByAgent[scope.agentId] || [] : []),
    [privateEventsByAgent, scope.agentId],
  );
  const activeAgent = useMemo(
    () => (scope.agentId ? actorMap.get(scope.agentId) || null : null),
    [actorMap, scope.agentId],
  );
  const activeGroups = useMemo(
    () => (activeAgent ? deriveAgentTaskGroups(activeAgent.id, sharedFacts, currentPrivateEvents, runInfo) : []),
    [activeAgent, currentPrivateEvents, runInfo, sharedFacts],
  );
  const activeSteps = useMemo(() => activeGroups.flatMap((group) => group.steps), [activeGroups]);
  const selectedExchange = useMemo(
    () => exchanges.find((item) => item.id === selectedExchangeId) || null,
    [exchanges, selectedExchangeId],
  );
  const selectedEdgeKey = selectedExchange?.isGraphable
    ? `${selectedExchange.senderId}|${selectedExchange.receiverId}|${selectedExchange.topic}`
    : null;
  const selectedStep = useMemo(
    () => activeSteps.find((step) => step.id === selectedStepId) || null,
    [activeSteps, selectedStepId],
  );
  const doneCount = useMemo(
    () => actors.filter((actor) => ["completed", "settled", "done", "idle"].includes(actor.status.toLowerCase())).length,
    [actors],
  );
  const workingCount = useMemo(
    () => actors.filter((actor) => ["running", "working", "active"].includes(actor.status.toLowerCase())).length,
    [actors],
  );
  const currentTaskLabel = useMemo(
    () => taskLabelFromFacts(sharedFacts, currentSessionId),
    [currentSessionId, sharedFacts],
  );
  const wsStatusLabel = currentSessionId
    ? (wsConnected ? "WS connected" : `WS reconnecting ${Math.round(reconnectDelay / 1000)}s`)
    : "WS idle";
  const wsStatusTone = currentSessionId ? (wsConnected ? "success" : "warning") : "neutral";

  function updateScope(nextScope: ScopeState) {
    scopeRef.current = nextScope;
    setScope(nextScope);
    setSessionIdInput(nextScope.sessionId || "");
    const params = new URLSearchParams();
    if (nextScope.sessionId) {
      params.set("session_id", nextScope.sessionId);
    }
    if (nextScope.runId) {
      params.set("run_id", nextScope.runId);
    }
    if (nextScope.agentId) {
      params.set("agent_id", nextScope.agentId);
    }
    if (nextScope.toolCallId) {
      params.set("tool_call_id", nextScope.toolCallId);
    }
    window.history.replaceState(
      {},
      "",
      `${window.location.pathname}${params.toString() ? `?${params.toString()}` : ""}`,
    );
  }

  function replaceSharedFactState(nextFacts: SharedFact[]) {
    const merged = mergeSharedFacts([], nextFacts);
    sharedFactsRef.current = merged;
    setSharedFacts(merged);
  }

  function replacePrivateEventState(nextMap: Record<string, PrivateExecutionEvent[]>) {
    privateEventsByAgentRef.current = nextMap;
    setPrivateEventsByAgent(nextMap);
  }

  function replacePrivateEventsForAgent(agentId: string, events: PrivateExecutionEvent[]) {
    const merged = mergePrivateEvents([], events);
    const nextMap = { ...privateEventsByAgentRef.current, [agentId]: merged };
    replacePrivateEventState(nextMap);
  }

  function resetFactState() {
    replaceSharedFactState([]);
    replacePrivateEventState({});
  }

  function upsertSharedFact(fact: SharedFact) {
    if (!factMatchesScope(fact, scopeRef.current)) {
      return;
    }
    replaceSharedFactState(mergeSharedFacts(sharedFactsRef.current, [fact]));
  }

  function upsertPrivateEvent(event: PrivateExecutionEvent) {
    if (!privateEventMatchesScope(event, scopeRef.current)) {
      return;
    }
    const agentId = cleanText(event.owner_agent_id);
    if (!agentId) {
      return;
    }
    const existing = privateEventsByAgentRef.current[agentId] || [];
    replacePrivateEventsForAgent(agentId, mergePrivateEvents(existing, [event]));
  }

  async function hydratePrivateAgent(
    sessionId: string,
    agentId: string,
    runId: string | null,
    options: { loadToken?: number; silent?: boolean } = {},
  ) {
    if (!baseUrl || !sessionId || !agentId) {
      return;
    }
    let afterId = 0;
    let hydrated: PrivateExecutionEvent[] = [];
    while (true) {
      if (options.loadToken && options.loadToken !== sessionLoadTokenRef.current) {
        return;
      }
      const page: SessionPrivateFactsResponse = await getSessionPrivateFacts(
        baseUrl,
        sessionId,
        agentId,
        afterId,
        FACT_PAGE_SIZE,
        runId,
      );
      hydrated = mergePrivateEvents(hydrated, page.private_events);
      if (!page.private_events.length || page.private_events.length < FACT_PAGE_SIZE || page.next_after_id === afterId) {
        break;
      }
      afterId = page.next_after_id;
    }
    if (options.loadToken && options.loadToken !== sessionLoadTokenRef.current) {
      return;
    }
    const existing = privateEventsByAgentRef.current[agentId] || [];
    replacePrivateEventsForAgent(agentId, mergePrivateEvents(existing, hydrated));
    if (!options.silent) {
      setFeedback(`Agent ${agentId} private facts hydrated.`);
    }
  }

  async function hydrateSession(
    sessionId: string,
    options: Partial<ScopeState> & { clearRecords?: boolean } = {},
  ) {
    if (!baseUrl) {
      return;
    }
    const token = ++sessionLoadTokenRef.current;
    const nextScope: ScopeState = {
      sessionId,
      runId: options.runId || null,
      agentId: options.agentId || null,
      toolCallId: options.toolCallId || null,
    };
    setBusyLoading(true);
    setSessionIdInput(sessionId);
    updateScope(nextScope);
    if (options.clearRecords !== false) {
      resetFactState();
      setSelectedExchangeId(null);
      setSelectedStepId(null);
    }
    setFeedback(`Hydrating session ${sessionId}...`);
    try {
      let afterSeq = 0;
      let hydrated: SharedFact[] = [];
      while (true) {
        if (token !== sessionLoadTokenRef.current) {
          return;
        }
        const page: SessionSharedFactsResponse = await getSessionSharedFacts(
          baseUrl,
          sessionId,
          afterSeq,
          FACT_PAGE_SIZE,
          nextScope.runId,
        );
        hydrated = mergeSharedFacts(hydrated, page.shared_facts);
        if (!page.shared_facts.length || page.shared_facts.length < FACT_PAGE_SIZE || page.next_after_seq === afterSeq) {
          break;
        }
        afterSeq = page.next_after_seq;
      }
      if (token !== sessionLoadTokenRef.current) {
        return;
      }
      replaceSharedFactState(mergeSharedFacts(sharedFactsRef.current, hydrated));
      if (nextScope.agentId) {
        await hydratePrivateAgent(sessionId, nextScope.agentId, nextScope.runId, {
          loadToken: token,
          silent: true,
        });
      }
      if (token === sessionLoadTokenRef.current) {
        setFeedback(`Session ${sessionId} hydrated.`);
      }
    } catch (error) {
      if (token === sessionLoadTokenRef.current) {
        setFeedback(`Load failed: ${formatError(error)}`);
      }
    } finally {
      if (token === sessionLoadTokenRef.current) {
        setBusyLoading(false);
      }
    }
  }

  async function refreshCurrentView() {
    if (!currentSessionId) {
      return;
    }
    await hydrateSession(currentSessionId, {
      runId: scope.runId,
      agentId: scope.agentId,
      toolCallId: scope.toolCallId,
      clearRecords: false,
    });
  }

  function sendWs(payload: Record<string, unknown>) {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(payload));
    }
  }

  function applyScopeAndResume() {
    const nextScope = scopeRef.current;
    if (!nextScope.sessionId) {
      return;
    }
    sendWs({
      kind: "set_scope",
      target_session_id: nextScope.sessionId,
      selected_run_id: nextScope.runId || undefined,
      selected_agent_id: nextScope.agentId || undefined,
      include_private: Boolean(nextScope.agentId),
    });
    sendWs({
      kind: "resume_shared",
      after_seq: latestSharedSeq(sharedFactsRef.current),
      limit: FACT_PAGE_SIZE,
    });
    if (nextScope.agentId) {
      sendWs({
        kind: "resume_private",
        after_id: latestPrivateEventId(privateEventsByAgentRef.current[nextScope.agentId] || []),
        limit: FACT_PAGE_SIZE,
      });
    }
  }

  function cleanupSocket() {
    if (heartbeatTimerRef.current) {
      window.clearInterval(heartbeatTimerRef.current);
    }
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current);
    }
    heartbeatTimerRef.current = null;
    reconnectTimerRef.current = null;
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {
        // ignore
      }
    }
    wsRef.current = null;
    setWsConnected(false);
  }

  async function handleCreateRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!baseUrl || !cleanText(promptValue)) {
      setFeedback("Content is required.");
      return;
    }
    const payload: CreateRunPayload = {
      content: cleanText(promptValue),
      strategy,
      request_overrides: {},
    };
    if (cleanText(workPath)) {
      payload.work_path = cleanText(workPath);
    }
    setBusyCreate(true);
    setFeedback("Creating run...");
    try {
      const response: CreateRunResponse = await createRun(baseUrl, payload);
      await hydrateSession(response.session_id, {
        runId: response.run_id,
        clearRecords: true,
      });
      setFeedback("Run accepted. Following the live fact stream.");
    } catch (error) {
      setFeedback(`Create run failed: ${formatError(error)}`);
    } finally {
      setBusyCreate(false);
    }
  }

  useEffect(() => {
    sharedFactsRef.current = sharedFacts;
  }, [sharedFacts]);

  useEffect(() => {
    privateEventsByAgentRef.current = privateEventsByAgent;
  }, [privateEventsByAgent]);

  useEffect(() => {
    autoFollowRef.current = autoFollow;
  }, [autoFollow]);

  useEffect(() => {
    reconnectDelayRef.current = reconnectDelay;
  }, [reconnectDelay]);

  useEffect(() => {
    let active = true;
    void resolveApiBaseUrl()
      .then((resolved) => {
        if (!active) {
          return;
        }
        setBaseUrl(resolved);
        setReady(true);
        setFeedback("Backend resolved. Ready to observe.");
      })
      .catch((error) => {
        if (active) {
          setFeedback(`Failed to resolve backend: ${formatError(error)}`);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!ready || !baseUrl || initialHydrationRef.current || !initialScope.sessionId) {
      return;
    }
    initialHydrationRef.current = true;
    void hydrateSession(initialScope.sessionId, {
      runId: initialScope.runId,
      agentId: initialScope.agentId,
      toolCallId: initialScope.toolCallId,
      clearRecords: true,
    });
  }, [
    baseUrl,
    initialScope.agentId,
    initialScope.runId,
    initialScope.sessionId,
    initialScope.toolCallId,
    ready,
  ]);

  useEffect(() => {
    if (!ready || !baseUrl || !scope.sessionId || !scope.agentId) {
      return;
    }
    void hydratePrivateAgent(scope.sessionId, scope.agentId, scope.runId, { silent: true });
  }, [baseUrl, ready, scope.agentId, scope.runId, scope.sessionId]);

  useEffect(() => {
    if (!ready || !baseUrl) {
      return;
    }
    if (!scope.sessionId) {
      cleanupSocket();
      return;
    }
    let disposed = false;
    const connectSocket = () => {
      if (disposed || wsRef.current) {
        return;
      }
      const socket = new WebSocket(buildWebSocketUrl(baseUrl));
      wsRef.current = socket;
      socket.addEventListener("open", () => {
        if (disposed) {
          return;
        }
        setWsConnected(true);
        setReconnectDelay(1000);
        heartbeatTimerRef.current = window.setInterval(() => sendWs({ kind: "heartbeat" }), HEARTBEAT_MS);
        applyScopeAndResume();
      });
      socket.addEventListener("message", (event) => {
        try {
          const frame = JSON.parse(event.data) as WsFrame;
          if (frame.kind === "append.shared_fact") {
            upsertSharedFact(frame.shared_fact);
            return;
          }
          if (frame.kind === "append.private_event") {
            upsertPrivateEvent(frame.private_event);
            return;
          }
          if (frame.kind === "bootstrap.shared_facts") {
            replaceSharedFactState(mergeSharedFacts(sharedFactsRef.current, frame.shared_facts));
            return;
          }
          if (frame.kind === "bootstrap.private_events") {
            const selectedAgentId = scopeRef.current.agentId;
            if (selectedAgentId) {
              const existing = privateEventsByAgentRef.current[selectedAgentId] || [];
              replacePrivateEventsForAgent(selectedAgentId, mergePrivateEvents(existing, frame.private_events));
            }
            return;
          }
          if (frame.kind === "ack") {
            if (frame.message === "resume_shared") {
              setFeedback(`Following shared facts from seq ${latestSharedSeq(sharedFactsRef.current)}.`);
            } else if (frame.message === "resume_private" && scopeRef.current.agentId) {
              setFeedback(
                `Following ${scopeRef.current.agentId} private facts from event ${
                  latestPrivateEventId(privateEventsByAgentRef.current[scopeRef.current.agentId] || [])
                }.`,
              );
            }
            return;
          }
          if (frame.kind === "error") {
            setFeedback(`WS error: ${frame.message}`);
          }
        } catch {
          // ignore malformed frames
        }
      });
      socket.addEventListener("close", () => {
        if (disposed) {
          return;
        }
        cleanupSocket();
        reconnectTimerRef.current = window.setTimeout(() => {
          reconnectTimerRef.current = null;
          setReconnectDelay((value) => Math.min(RECONNECT_MAX_MS, value * 2));
          connectSocket();
        }, Math.min(RECONNECT_MAX_MS, reconnectDelayRef.current));
      });
      socket.addEventListener("error", () => {
        if (!disposed) {
          setFeedback("WS error. Waiting for reconnect.");
        }
      });
    };
    connectSocket();
    return () => {
      disposed = true;
      cleanupSocket();
    };
  }, [baseUrl, ready, scope.sessionId]);

  useEffect(() => {
    if (wsConnected && scope.sessionId) {
      applyScopeAndResume();
    }
  }, [scope.agentId, scope.runId, scope.sessionId, wsConnected]);

  useEffect(() => {
    if (!autoFollowRef.current || !feedRef.current) {
      return;
    }
    window.requestAnimationFrame(() => {
      if (feedRef.current) {
        feedRef.current.scrollTop = feedRef.current.scrollHeight;
      }
    });
  }, [exchanges.length]);

  useEffect(() => {
    if (!exchanges.length) {
      setSelectedExchangeId(null);
      return;
    }
    if (selectedExchangeId && exchanges.some((item) => item.id === selectedExchangeId)) {
      return;
    }
    setSelectedExchangeId(exchanges[exchanges.length - 1]?.id || null);
  }, [exchanges, selectedExchangeId]);

  useEffect(() => {
    if (!activeSteps.length) {
      setSelectedStepId(null);
      return;
    }
    if (scope.toolCallId) {
      const next = activeSteps.find((step) => step.toolCallId === scope.toolCallId) || activeSteps[activeSteps.length - 1];
      setSelectedStepId(next?.id || null);
      return;
    }
    if (selectedStepId && activeSteps.some((step) => step.id === selectedStepId)) {
      return;
    }
    setSelectedStepId(activeSteps[activeSteps.length - 1]?.id || null);
  }, [activeSteps, scope.toolCallId, selectedStepId]);

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="app-sidebar-inner">
          <section className="brand-card">
            <div className="brand-mark">TN</div>
            <div>
              <p className="panel-kicker">tauri-agent-next</p>
              <h1>Agent Handoff Workbench</h1>
              <p>{ready ? baseUrl : "resolving backend..."}</p>
            </div>
          </section>

          <div className="sidebar-nav">
            <button
              type="button"
              className={`nav-button${!activeAgent ? " is-active" : ""}`}
              onClick={() =>
                updateScope({
                  sessionId: currentSessionId,
                  runId: scope.runId,
                  agentId: null,
                  toolCallId: null,
                })
              }
            >
              <span className="nav-icon">□</span>
              <span>整体 Agent 交接</span>
            </button>
            <button
              type="button"
              className={`nav-button${activeAgent ? " is-active" : ""}`}
              onClick={() =>
                activeAgent
                  ? updateScope({
                      sessionId: currentSessionId,
                      runId: scope.runId,
                      agentId: activeAgent.id,
                      toolCallId: scope.toolCallId,
                    })
                  : undefined
              }
              disabled={!activeAgent}
            >
              <span className="nav-icon">↳</span>
              <span>当前 Agent 过程</span>
            </button>
          </div>

          <section className="rail-section">
            <div className="section-head">
              <h2>Create Run</h2>
              <span>HTTP ingress</span>
            </div>
            <form className="rail-form" onSubmit={handleCreateRun}>
              <label className="field">
                <span>Content</span>
                <textarea
                  value={promptValue}
                  onChange={(event) => setPromptValue(event.currentTarget.value)}
                  rows={6}
                  placeholder="Describe the task for the assistant."
                />
              </label>
              <div className="field-row">
                <label className="field">
                  <span>Strategy</span>
                  <select value={strategy} onChange={(event) => setStrategy(event.currentTarget.value)}>
                    <option value="simple">simple</option>
                    <option value="react">react</option>
                  </select>
                </label>
                <label className="field">
                  <span>Work Path</span>
                  <input
                    type="text"
                    value={workPath}
                    onChange={(event) => setWorkPath(event.currentTarget.value)}
                    placeholder="Optional workspace path"
                  />
                </label>
              </div>
              <div className="button-row">
                <button type="submit" className="primary-button" disabled={busyCreate || !ready}>
                  <Play size={14} />
                  <span>{busyCreate ? "Creating..." : "Create Run"}</span>
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  disabled={busyStop || !resolvedRunId}
                  onClick={() => {
                    void (async () => {
                      if (!resolvedRunId || !baseUrl) {
                        return;
                      }
                      setBusyStop(true);
                      setFeedback(`Stopping run ${resolvedRunId}...`);
                      try {
                        await stopRun(baseUrl, resolvedRunId);
                        await refreshCurrentView();
                        setFeedback(`Stop requested for run ${resolvedRunId}.`);
                      } catch (error) {
                        setFeedback(`Stop failed: ${formatError(error)}`);
                      } finally {
                        setBusyStop(false);
                      }
                    })();
                  }}
                >
                  <Square size={14} />
                  <span>{busyStop ? "Stopping..." : "Stop Run"}</span>
                </button>
              </div>
            </form>
          </section>

          <section className="rail-section">
            <div className="section-head">
              <h2>Load Session</h2>
              <span>Replay + resume</span>
            </div>
            <div className="rail-form">
              <label className="field">
                <span>Existing Session ID</span>
                <input
                  type="text"
                  value={sessionIdInput}
                  onChange={(event) => setSessionIdInput(event.currentTarget.value)}
                  placeholder="Paste a session_id"
                />
              </label>
              <div className="button-row">
                <button
                  type="button"
                  className="secondary-button"
                  disabled={busyLoading || !ready}
                  onClick={() => {
                    const sessionId = cleanText(sessionIdInput);
                    if (!sessionId) {
                      setFeedback("Enter a session_id before loading.");
                      return;
                    }
                    void hydrateSession(sessionId, { clearRecords: true });
                  }}
                >
                  <GitBranch size={14} />
                  <span>{busyLoading ? "Loading..." : "Load Session"}</span>
                </button>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => {
                    void navigator.clipboard
                      .writeText(window.location.href)
                      .then(() => setFeedback("Copied current view URL."))
                      .catch((error) => setFeedback(`Copy failed: ${formatError(error)}`));
                  }}
                >
                  <Copy size={14} />
                  <span>Copy URL</span>
                </button>
              </div>
            </div>
          </section>

          <section className="rail-section rail-section-compact">
            <div className="status-grid">
              <StatusPill
                icon={wsConnected ? <Wifi size={14} /> : <WifiOff size={14} />}
                label={wsStatusLabel}
                tone={wsStatusTone}
              />
              <StatusPill
                icon={<Cable size={14} />}
                label={`session ${currentSessionId || "none"}`}
                tone={currentSessionId ? "accent" : "neutral"}
              />
              <StatusPill
                icon={<Activity size={14} />}
                label={`status ${runInfo.status}`}
                tone={statusTone(runInfo.status)}
              />
            </div>
            <div className="metric-grid">
              <MetricTile label="latest_seq" value={String(runInfo.latestSeq)} />
              <MetricTile label="actors" value={String(actors.length)} />
              <MetricTile label="handoffs" value={String(exchanges.length)} />
              <MetricTile label="run" value={resolvedRunId || "-"} />
            </div>
          </section>

          <section className="rail-section rail-section-compact">
            <div className="section-head">
              <h2>Status</h2>
              <span>live feedback</span>
            </div>
            <p className="feedback-copy">{feedback}</p>
          </section>
        </div>
      </aside>

      <main className="workspace-shell">
        <section className="workspace-card">
          <header className="workspace-topbar">
            <div className="workspace-title">
              <span className="workspace-title-label">Multi-Agent Collaboration</span>
              <strong>真实 Agent 交接面板</strong>
            </div>
            <div className="workspace-topbar-meta">
              <span>handoffs {exchanges.length}</span>
              <span>actors {actors.length}</span>
              <span>seq {runInfo.latestSeq}</span>
              <span>{workingCount > 0 ? `${workingCount} running` : `${doneCount} settled`}</span>
            </div>
            <div className="workspace-topbar-actions">
              <span className={`detail-chip tone-${statusTone(runInfo.status)}`}>{runInfo.status}</span>
              <button
                type="button"
                className="ghost-button"
                disabled={!currentSessionId}
                onClick={() => {
                  void refreshCurrentView();
                }}
              >
                Refresh
              </button>
              <button
                type="button"
                className="ghost-button"
                onClick={() => {
                  void navigator.clipboard
                    .writeText(window.location.href)
                    .then(() => setFeedback("Copied current view URL."))
                    .catch((error) => setFeedback(`Copy failed: ${formatError(error)}`));
                }}
              >
                Copy URL
              </button>
              <button
                type="button"
                className="primary-button"
                disabled={!resolvedRunId || busyStop}
                onClick={() => {
                  void (async () => {
                    if (!resolvedRunId || !baseUrl) {
                      return;
                    }
                    setBusyStop(true);
                    setFeedback(`Stopping run ${resolvedRunId}...`);
                    try {
                      await stopRun(baseUrl, resolvedRunId);
                      await refreshCurrentView();
                      setFeedback(`Stop requested for run ${resolvedRunId}.`);
                    } catch (error) {
                      setFeedback(`Stop failed: ${formatError(error)}`);
                    } finally {
                      setBusyStop(false);
                    }
                  })();
                }}
              >
                {busyStop ? "Stopping..." : "Stop"}
              </button>
            </div>
          </header>

          <div className="task-strip">
            <div className="task-strip-main">
              <span className="task-strip-label">任务</span>
              <span className="task-strip-text">{currentTaskLabel}</span>
            </div>
            <div className="task-strip-status">
              {actors.map((actor) => (
                <span
                  key={actor.id}
                  className={`task-strip-dot tone-${statusTone(actor.status)}`}
                  title={`${actor.name}: ${actor.status}`}
                />
              ))}
            </div>
          </div>

          <div className="workspace-main">
            <section className="graph-pane">
              <div className="panel-head">
                <div>
                  <p className="panel-kicker">task graph</p>
                  <h2>Agent Handoff Graph</h2>
                </div>
                <div className="panel-hints">
                  <span>shared fact sender → target → topic</span>
                  <span>点击节点查看执行详情</span>
                </div>
              </div>
              <HandoffGraph
                actors={actors}
                edges={handoffEdges}
                selectedAgentId={activeAgent?.id || null}
                selectedEdgeKey={selectedEdgeKey}
                onSelectActor={(agentId) =>
                  updateScope({
                    sessionId: currentSessionId,
                    runId: scope.runId,
                    agentId,
                    toolCallId: null,
                  })
                }
                onSelectEdge={(edge) => setSelectedExchangeId(edge.latestExchangeId)}
              />
            </section>

            <section className="side-pane">
              {activeAgent ? (
                <AgentDetailPanel
                  actor={activeAgent}
                  groups={activeGroups}
                  selectedStepId={selectedStepId}
                  selectedStep={selectedStep}
                  onBack={() =>
                    updateScope({
                      sessionId: currentSessionId,
                      runId: scope.runId,
                      agentId: null,
                      toolCallId: null,
                    })
                  }
                  onSelectStep={(step: AgentStepView) => {
                    setSelectedStepId(step.id);
                    if (scope.toolCallId) {
                      updateScope({
                        sessionId: currentSessionId,
                        runId: scope.runId,
                        agentId: activeAgent.id,
                        toolCallId: null,
                      });
                    }
                  }}
                  onSelectToolStep={(step: AgentStepView) => {
                    setSelectedStepId(step.id);
                    updateScope({
                      sessionId: currentSessionId,
                      runId: scope.runId,
                      agentId: activeAgent.id,
                      toolCallId: step.toolCallId || null,
                    });
                  }}
                />
              ) : (
                <div
                  className="conversation-pane"
                  ref={feedRef}
                  onScroll={(event) => {
                    const node = event.currentTarget;
                    const nearBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 40;
                    if (nearBottom !== autoFollowRef.current) {
                      setAutoFollow(nearBottom);
                    }
                  }}
                >
                  <HandoffConversationPanel
                    runInfo={runInfo}
                    exchanges={exchanges}
                    selectedExchange={selectedExchange}
                    selectedExchangeId={selectedExchangeId}
                    autoFollow={autoFollow}
                    onToggleAutoFollow={() => setAutoFollow((value) => !value)}
                    onSelectExchange={setSelectedExchangeId}
                    onFocusActor={(agentId) =>
                      updateScope({
                        sessionId: currentSessionId,
                        runId: scope.runId,
                        agentId,
                        toolCallId: null,
                      })
                    }
                  />
                </div>
              )}
            </section>
          </div>
        </section>
      </main>
    </div>
  );
}
