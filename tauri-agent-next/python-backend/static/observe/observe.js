(function () {
  const API_BASE = window.location.origin;
  const WS_URL = API_BASE.replace(/^http/i, function (value) {
    return value.toLowerCase() === "https" ? "wss" : "ws";
  }) + "/ws";
  const EVENT_PAGE_SIZE = 200;
  const HEARTBEAT_MS = 25000;
  const RECONNECT_MAX_MS = 8000;

  const state = {
    ws: null,
    wsConnected: false,
    reconnectDelayMs: 1000,
    reconnectTimer: null,
    heartbeatTimer: null,
    loadVersion: 0,
    currentRunId: null,
    currentAgentId: null,
    currentToolCallId: null,
    selectedSeq: null,
    detailTitle: "Snapshot",
    detailData: {},
    snapshot: null,
    runProjection: null,
    agentProjections: {},
    toolCallProjections: {},
    records: new Map(),
    autoFollow: true,
    feedback: "Waiting for a run.",
  };

  const elements = {
    form: document.getElementById("create-run-form"),
    contentInput: document.getElementById("content-input"),
    strategySelect: document.getElementById("strategy-select"),
    workPathInput: document.getElementById("work-path-input"),
    stopRunButton: document.getElementById("stop-run-button"),
    loadRunButton: document.getElementById("load-run-button"),
    runIdInput: document.getElementById("run-id-input"),
    copyUrlButton: document.getElementById("copy-url-button"),
    viewRunButton: document.getElementById("view-run-button"),
    followToggleButton: document.getElementById("follow-toggle-button"),
    timelineList: document.getElementById("timeline-list"),
    wsStatusChip: document.getElementById("ws-status-chip"),
    scopeStatusChip: document.getElementById("scope-status-chip"),
    runStatusChip: document.getElementById("run-status-chip"),
    currentRunId: document.getElementById("current-run-id"),
    latestSeqValue: document.getElementById("latest-seq-value"),
    visibleCountValue: document.getElementById("visible-count-value"),
    feedbackText: document.getElementById("feedback-text"),
    detailSubtitle: document.getElementById("detail-subtitle"),
    scopePills: document.getElementById("scope-pills"),
    runProjectionGrid: document.getElementById("run-projection-grid"),
    agentCountLabel: document.getElementById("agent-count-label"),
    toolCountLabel: document.getElementById("tool-count-label"),
    agentList: document.getElementById("agent-list"),
    toolList: document.getElementById("tool-list"),
    rawJsonView: document.getElementById("raw-json-view"),
  };

  function init() {
    bindEvents();
    const params = new URLSearchParams(window.location.search);
    const runId = cleanText(params.get("run_id"));
    const agentId = cleanText(params.get("agent_id"));
    const toolCallId = cleanText(params.get("tool_call_id"));
    connectWebSocket();
    render();
    if (runId) {
      elements.runIdInput.value = runId;
      void loadRun(runId, {
        agentId: agentId,
        toolCallId: toolCallId,
        clearRecords: true,
      });
    }
  }

  function bindEvents() {
    elements.form.addEventListener("submit", function (event) {
      event.preventDefault();
      void createRun();
    });
    elements.loadRunButton.addEventListener("click", function () {
      const runId = cleanText(elements.runIdInput.value);
      if (!runId) {
        setFeedback("Enter a run_id before loading.");
        return;
      }
      void loadRun(runId, { clearRecords: true });
    });
    elements.stopRunButton.addEventListener("click", function () {
      void stopRun();
    });
    elements.copyUrlButton.addEventListener("click", function () {
      void copyCurrentUrl();
    });
    elements.viewRunButton.addEventListener("click", function () {
      if (!state.currentRunId) {
        return;
      }
      state.currentAgentId = null;
      state.currentToolCallId = null;
      state.selectedSeq = null;
      state.detailTitle = "Snapshot";
      state.detailData = buildRunDetail();
      updateUrl();
      render();
      void applyScopeAndResume();
    });
    elements.followToggleButton.addEventListener("click", function () {
      state.autoFollow = !state.autoFollow;
      render();
      if (state.autoFollow) {
        scrollTimelineToBottom();
      }
    });
    elements.timelineList.addEventListener("scroll", function () {
      const node = elements.timelineList;
      const nearBottom = node.scrollHeight - node.scrollTop - node.clientHeight < 32;
      state.autoFollow = nearBottom;
      renderHeaderState();
    });
  }

  async function createRun() {
    const content = cleanText(elements.contentInput.value);
    if (!content) {
      setFeedback("Content is required.");
      return;
    }
    const payload = {
      content: content,
      strategy: elements.strategySelect.value,
      request_overrides: {},
    };
    const workPath = cleanText(elements.workPathInput.value);
    if (workPath) {
      payload.work_path = workPath;
    }

    setFeedback("Creating run...");
    try {
      const response = await fetchJson("/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      elements.runIdInput.value = response.run_id;
      await loadRun(response.run_id, { clearRecords: true });
      setFeedback("Run accepted. Hydrating history and following live stream.");
    } catch (error) {
      setFeedback("Create run failed: " + formatError(error));
    }
  }

  async function stopRun() {
    if (!state.currentRunId) {
      return;
    }
    setFeedback("Stopping run " + state.currentRunId + "...");
    try {
      await fetchJson("/runs/" + encodeURIComponent(state.currentRunId) + "/stop", {
        method: "POST",
      });
      await refreshSnapshot(state.currentRunId);
      setFeedback("Stop requested for run " + state.currentRunId + ".");
    } catch (error) {
      setFeedback("Stop failed: " + formatError(error));
    }
  }

  async function loadRun(runId, options) {
    const resolvedOptions = options || {};
    const loadVersion = ++state.loadVersion;
    state.currentRunId = runId;
    state.currentAgentId = resolvedOptions.agentId || null;
    state.currentToolCallId = resolvedOptions.toolCallId || null;
    state.selectedSeq = null;
    if (resolvedOptions.clearRecords !== false) {
      state.records = new Map();
    }
    updateUrl();
    render();
    setFeedback("Hydrating run " + runId + "...");
    try {
      await refreshSnapshot(runId);
      let afterSeq = 0;
      while (true) {
        if (loadVersion !== state.loadVersion) {
          return;
        }
        const page = await fetchJson(
          "/runs/" +
            encodeURIComponent(runId) +
            "/events?after_seq=" +
            afterSeq +
            "&limit=" +
            EVENT_PAGE_SIZE,
        );
        for (const event of page.events) {
          upsertRecord(normalizeEventRecord(event));
        }
        if (!page.events.length || page.events.length < EVENT_PAGE_SIZE || page.next_after_seq === afterSeq) {
          break;
        }
        afterSeq = page.next_after_seq;
      }
      if (!state.detailData || state.detailTitle === "Snapshot") {
        state.detailTitle = "Snapshot";
        state.detailData = buildRunDetail();
      }
      render();
      await applyScopeAndResume();
      setFeedback("Run " + runId + " hydrated.");
    } catch (error) {
      setFeedback("Load failed: " + formatError(error));
    }
  }

  async function refreshSnapshot(runId) {
    const payload = await fetchJson("/runs/" + encodeURIComponent(runId) + "/snapshot");
    state.snapshot = payload.snapshot;
    state.runProjection = payload.run_projection || null;
    state.agentProjections = payload.agent_projections || {};
    state.toolCallProjections = payload.tool_call_projections || {};
    if (!state.currentToolCallId && !state.currentAgentId) {
      state.detailTitle = "Snapshot";
      state.detailData = buildRunDetail();
    } else if (state.currentToolCallId && state.toolCallProjections[state.currentToolCallId]) {
      setToolScope(state.currentToolCallId, { skipResume: true });
    } else if (state.currentAgentId && state.agentProjections[state.currentAgentId]) {
      setAgentScope(state.currentAgentId, { skipResume: true });
    }
  }

  function connectWebSocket() {
    clearReconnectTimer();
    if (state.ws) {
      return;
    }
    const ws = new WebSocket(WS_URL);
    state.ws = ws;
    renderHeaderState();

    ws.addEventListener("open", function () {
      state.wsConnected = true;
      state.reconnectDelayMs = 1000;
      startHeartbeat();
      renderHeaderState();
      if (state.currentRunId) {
        void rehydrateCurrentRun();
      }
    });

    ws.addEventListener("message", function (event) {
      let payload = null;
      try {
        payload = JSON.parse(event.data);
      } catch (_error) {
        return;
      }
      handleWsMessage(payload);
    });

    ws.addEventListener("close", function () {
      cleanupSocket();
      scheduleReconnect();
    });

    ws.addEventListener("error", function () {
      setFeedback("WS error. Waiting for reconnect.");
    });
  }

  function handleWsMessage(payload) {
    if (!payload || typeof payload.kind !== "string") {
      return;
    }
    if (payload.kind === "chunk") {
      if (!payload.run_id || payload.run_id !== state.currentRunId) {
        return;
      }
      upsertRecord(normalizeChunkRecord(payload));
      if (!state.currentAgentId && !state.currentToolCallId) {
        state.detailTitle = "Snapshot";
        state.detailData = buildRunDetail();
      } else if (state.currentToolCallId && payload.tool_call_id === state.currentToolCallId) {
        state.selectedSeq = payload.seq;
        state.detailTitle = "Timeline Item";
        state.detailData = payload;
      } else if (state.currentAgentId && payload.agent_id === state.currentAgentId) {
        state.selectedSeq = payload.seq;
        state.detailTitle = "Timeline Item";
        state.detailData = payload;
      }
      render();
      return;
    }
    if (payload.kind === "ack") {
      if (payload.message === "resume") {
        setFeedback("Following live stream from seq " + latestSeq() + ".");
      }
      return;
    }
    if (payload.kind === "error") {
      setFeedback("WS error: " + payload.message);
      return;
    }
    if (payload.kind === "heartbeat") {
      renderHeaderState();
    }
  }

  function cleanupSocket() {
    stopHeartbeat();
    if (state.ws) {
      try {
        state.ws.close();
      } catch (_error) {
        // ignore
      }
    }
    state.ws = null;
    state.wsConnected = false;
    renderHeaderState();
  }

  function scheduleReconnect() {
    clearReconnectTimer();
    state.wsConnected = false;
    renderHeaderState();
    state.reconnectTimer = window.setTimeout(function () {
      state.reconnectTimer = null;
      state.reconnectDelayMs = Math.min(RECONNECT_MAX_MS, state.reconnectDelayMs * 2);
      connectWebSocket();
    }, state.reconnectDelayMs);
  }

  function clearReconnectTimer() {
    if (state.reconnectTimer) {
      window.clearTimeout(state.reconnectTimer);
      state.reconnectTimer = null;
    }
  }

  function startHeartbeat() {
    stopHeartbeat();
    state.heartbeatTimer = window.setInterval(function () {
      sendWs({ kind: "heartbeat" });
    }, HEARTBEAT_MS);
  }

  function stopHeartbeat() {
    if (state.heartbeatTimer) {
      window.clearInterval(state.heartbeatTimer);
      state.heartbeatTimer = null;
    }
  }

  async function rehydrateCurrentRun() {
    if (!state.currentRunId) {
      return;
    }
    await loadRun(state.currentRunId, {
      agentId: state.currentAgentId,
      toolCallId: state.currentToolCallId,
      clearRecords: false,
    });
  }

  async function applyScopeAndResume() {
    if (!state.wsConnected || !state.currentRunId) {
      renderHeaderState();
      return;
    }
    sendWs({
      kind: "set_scope",
      scopes: [currentScope()],
    });
    sendWs({
      kind: "resume",
      after_seq: latestSeq(),
    });
    renderHeaderState();
  }

  function currentScope() {
    if (state.currentToolCallId) {
      const toolProjection = state.toolCallProjections[state.currentToolCallId];
      const scopedAgentId = toolProjection ? cleanText(toolProjection.agent_id) : "";
      if (scopedAgentId) {
        return { run_id: state.currentRunId, agent_id: scopedAgentId };
      }
    }
    if (state.currentAgentId) {
      return { run_id: state.currentRunId, agent_id: state.currentAgentId };
    }
    return { run_id: state.currentRunId };
  }

  function sendWs(payload) {
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
      return;
    }
    state.ws.send(JSON.stringify(payload));
  }

  function upsertRecord(record) {
    if (!record || record.runId !== state.currentRunId) {
      return;
    }
    state.records.set(record.seq, record);
  }

  function normalizeEventRecord(event) {
    return {
      seq: Number(event.seq || 0),
      runId: cleanText(event.run_id),
      agentId: cleanText(event.agent_id),
      toolCallId: cleanText(event.tool_call_id),
      stream: streamForEventType(event.event_type),
      eventType: cleanText(event.event_type),
      level: cleanText(event.level) || "info",
      visibility: cleanText(event.visibility) || "public",
      done: isDoneEvent(event),
      summary: summarizePayload(event.payload || {}),
      raw: event,
      payload: event.payload || {},
    };
  }

  function normalizeChunkRecord(chunk) {
    return {
      seq: Number(chunk.seq || 0),
      runId: cleanText(chunk.run_id),
      agentId: cleanText(chunk.agent_id),
      toolCallId: cleanText(chunk.tool_call_id),
      stream: cleanText(chunk.stream),
      eventType: cleanText(chunk.event_type),
      level: cleanText(chunk.level) || "info",
      visibility: cleanText(chunk.visibility) || "public",
      done: Boolean(chunk.done),
      summary: summarizePayload(chunk.payload || {}),
      raw: chunk,
      payload: chunk.payload || {},
    };
  }

  function render() {
    renderHeaderState();
    renderTimeline();
    renderScopePills();
    renderRunProjection();
    renderAgentList();
    renderToolList();
    renderRawJson();
  }

  function renderHeaderState() {
    elements.wsStatusChip.textContent = state.wsConnected ? "WS connected" : "WS connecting";
    elements.scopeStatusChip.textContent = "Scope " + scopeLabel();
    const runStatus = state.snapshot ? cleanText(state.snapshot.status) || "idle" : "idle";
    elements.runStatusChip.textContent = "Run " + runStatus;
    elements.currentRunId.textContent = state.currentRunId || "none";
    elements.latestSeqValue.textContent = String(latestSeq());
    elements.visibleCountValue.textContent = String(filteredRecords().length);
    elements.feedbackText.textContent = state.feedback;
    elements.followToggleButton.textContent = state.autoFollow ? "Auto Follow On" : "Auto Follow Off";
    elements.stopRunButton.disabled = !state.currentRunId;
  }

  function renderTimeline() {
    const records = filteredRecords();
    const container = elements.timelineList;
    container.innerHTML = "";
    if (!records.length) {
      container.innerHTML = '<div class="timeline-item"><div class="timeline-summary">No events for the current view.</div></div>';
      return;
    }

    for (const record of records) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "timeline-item" + (record.seq === state.selectedSeq ? " is-selected" : "");
      button.addEventListener("click", function () {
        state.selectedSeq = record.seq;
        state.detailTitle = "Timeline Item";
        state.detailData = record.raw;
        render();
      });

      const metadata = [
        "seq " + record.seq,
        record.stream || "agent_event",
        record.eventType || "event",
      ];
      if (record.agentId) {
        metadata.push("agent " + record.agentId);
      }
      if (record.toolCallId) {
        metadata.push("tool " + record.toolCallId);
      }

      const linkMarkup = [];
      if (record.agentId) {
        linkMarkup.push(
          '<button type="button" class="timeline-link" data-agent-id="' +
            escapeHtml(record.agentId) +
            '">View agent</button>',
        );
      }
      if (record.toolCallId) {
        linkMarkup.push(
          '<button type="button" class="timeline-link" data-tool-call-id="' +
            escapeHtml(record.toolCallId) +
            '">View tool</button>',
        );
      }

      button.innerHTML =
        '<div class="timeline-topline">' +
        '<div class="timeline-metadata">' +
        metadata.map(tagMarkup).join("") +
        '</div><span class="timeline-tag level-' +
        escapeHtml(record.level) +
        '">' +
        escapeHtml(record.level) +
        "</span></div>" +
        '<div class="timeline-summary">' +
        escapeHtml(record.summary) +
        "</div>" +
        '<div class="timeline-links">' +
        linkMarkup.join("") +
        "</div>";

      container.appendChild(button);
    }

    container.querySelectorAll("[data-agent-id]").forEach(function (node) {
      node.addEventListener("click", function (event) {
        event.stopPropagation();
        setAgentScope(node.getAttribute("data-agent-id"));
      });
    });

    container.querySelectorAll("[data-tool-call-id]").forEach(function (node) {
      node.addEventListener("click", function (event) {
        event.stopPropagation();
        setToolScope(node.getAttribute("data-tool-call-id"));
      });
    });

    if (state.autoFollow) {
      scrollTimelineToBottom();
    }
  }

  function renderScopePills() {
    const pills = [
      {
        label: "run",
        active: !state.currentAgentId && !state.currentToolCallId,
        onClick: function () {
          if (!state.currentRunId) {
            return;
          }
          state.currentAgentId = null;
          state.currentToolCallId = null;
          state.detailTitle = "Snapshot";
          state.detailData = buildRunDetail();
          updateUrl();
          render();
          void applyScopeAndResume();
        },
      },
    ];
    if (state.currentAgentId) {
      pills.push({ label: "agent " + state.currentAgentId, active: true, onClick: function () {} });
    }
    if (state.currentToolCallId) {
      pills.push({ label: "tool " + state.currentToolCallId, active: true, onClick: function () {} });
    }

    elements.scopePills.innerHTML = "";
    for (const pill of pills) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "scope-pill" + (pill.active ? " is-active" : "");
      button.textContent = pill.label;
      button.addEventListener("click", pill.onClick);
      elements.scopePills.appendChild(button);
    }
  }

  function renderRunProjection() {
    const projection = state.runProjection;
    const grid = elements.runProjectionGrid;
    grid.innerHTML = "";
    const fields = [
      ["run_id", state.currentRunId || "none"],
      ["status", projection ? projection.status : "idle"],
      ["latest_seq", projection ? String(projection.latest_seq) : String(latestSeq())],
      ["strategy", projection ? projection.strategy || "-" : "-"],
      ["started_at", projection ? projection.started_at || "-" : "-"],
      ["finished_at", projection ? projection.finished_at || "-" : "-"],
    ];
    for (const entry of fields) {
      const card = document.createElement("article");
      card.className = "key-value-card";
      card.innerHTML = "<span>" + escapeHtml(entry[0]) + "</span><strong>" + escapeHtml(entry[1]) + "</strong>";
      card.addEventListener("click", function () {
        state.detailTitle = "Snapshot";
        state.detailData = buildRunDetail();
        renderRawJson();
      });
      grid.appendChild(card);
    }
    elements.detailSubtitle.textContent = state.detailTitle;
  }

  function renderAgentList() {
    const agentEntries = Object.values(state.agentProjections || {});
    elements.agentCountLabel.textContent = String(agentEntries.length);
    elements.agentList.innerHTML = "";
    if (!agentEntries.length) {
      elements.agentList.innerHTML = '<div class="projection-meta">No agents yet.</div>';
      return;
    }
    for (const projection of agentEntries) {
      const button = document.createElement("button");
      button.type = "button";
      button.className =
        "projection-button" + (projection.agent_id === state.currentAgentId ? " is-selected" : "");
      button.innerHTML =
        '<span class="projection-title">' +
        escapeHtml(projection.agent_id) +
        '</span><span class="projection-meta">' +
        escapeHtml(projection.status || "idle") +
        " | " +
        escapeHtml(projection.role || "-") +
        "</span>";
      button.addEventListener("click", function () {
        setAgentScope(projection.agent_id);
      });
      elements.agentList.appendChild(button);
    }
  }

  function renderToolList() {
    const toolEntries = Object.values(state.toolCallProjections || {});
    elements.toolCountLabel.textContent = String(toolEntries.length);
    elements.toolList.innerHTML = "";
    if (!toolEntries.length) {
      elements.toolList.innerHTML = '<div class="projection-meta">No tool calls yet.</div>';
      return;
    }
    for (const projection of toolEntries) {
      const button = document.createElement("button");
      button.type = "button";
      button.className =
        "projection-button" + (projection.tool_call_id === state.currentToolCallId ? " is-selected" : "");
      button.innerHTML =
        '<span class="projection-title">' +
        escapeHtml(projection.tool_call_id) +
        '</span><span class="projection-meta">' +
        escapeHtml(projection.status || "pending") +
        " | " +
        escapeHtml(projection.tool_name || "-") +
        "</span>";
      button.addEventListener("click", function () {
        setToolScope(projection.tool_call_id);
      });
      elements.toolList.appendChild(button);
    }
  }

  function renderRawJson() {
    elements.detailSubtitle.textContent = state.detailTitle;
    elements.rawJsonView.textContent = JSON.stringify(state.detailData || {}, null, 2);
  }

  function setAgentScope(agentId, options) {
    const resolvedOptions = options || {};
    if (!agentId || !state.currentRunId) {
      return;
    }
    state.currentAgentId = agentId;
    state.currentToolCallId = null;
    state.selectedSeq = null;
    state.detailTitle = "Agent Projection";
    state.detailData = state.agentProjections[agentId] || {};
    updateUrl();
    render();
    if (!resolvedOptions.skipResume) {
      void applyScopeAndResume();
    }
  }

  function setToolScope(toolCallId, options) {
    const resolvedOptions = options || {};
    if (!toolCallId || !state.currentRunId) {
      return;
    }
    const projection = state.toolCallProjections[toolCallId] || {};
    state.currentToolCallId = toolCallId;
    state.currentAgentId = cleanText(projection.agent_id) || null;
    state.selectedSeq = null;
    state.detailTitle = "Tool Call Projection";
    state.detailData = projection;
    updateUrl();
    render();
    if (!resolvedOptions.skipResume) {
      void applyScopeAndResume();
    }
  }

  function filteredRecords() {
    const records = Array.from(state.records.values()).sort(function (left, right) {
      return left.seq - right.seq;
    });
    return records.filter(function (record) {
      if (state.currentAgentId && record.agentId !== state.currentAgentId) {
        return false;
      }
      if (state.currentToolCallId && record.toolCallId !== state.currentToolCallId) {
        return false;
      }
      return true;
    });
  }

  function scopeLabel() {
    if (!state.currentRunId) {
      return "none";
    }
    if (state.currentToolCallId) {
      return "tool:" + state.currentToolCallId;
    }
    if (state.currentAgentId) {
      return "agent:" + state.currentAgentId;
    }
    return "run:" + state.currentRunId;
  }

  function buildRunDetail() {
    return {
      snapshot: state.snapshot,
      run_projection: state.runProjection,
      agent_projections: state.agentProjections,
      tool_call_projections: state.toolCallProjections,
    };
  }

  function latestSeq() {
    let next = 0;
    state.records.forEach(function (_value, key) {
      next = Math.max(next, Number(key || 0));
    });
    if (state.snapshot && state.snapshot.latest_seq) {
      next = Math.max(next, Number(state.snapshot.latest_seq || 0));
    }
    return next;
  }

  function scrollTimelineToBottom() {
    window.requestAnimationFrame(function () {
      elements.timelineList.scrollTop = elements.timelineList.scrollHeight;
    });
  }

  function updateUrl() {
    const params = new URLSearchParams();
    if (state.currentRunId) {
      params.set("run_id", state.currentRunId);
    }
    if (state.currentAgentId) {
      params.set("agent_id", state.currentAgentId);
    }
    if (state.currentToolCallId) {
      params.set("tool_call_id", state.currentToolCallId);
    }
    const nextUrl = window.location.pathname + (params.toString() ? "?" + params.toString() : "");
    window.history.replaceState({}, "", nextUrl);
  }

  async function copyCurrentUrl() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setFeedback("Copied current view URL.");
    } catch (error) {
      setFeedback("Copy failed: " + formatError(error));
    }
  }

  async function fetchJson(path, options) {
    const response = await fetch(API_BASE + path, options);
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || ("HTTP " + response.status));
    }
    return response.json();
  }

  function setFeedback(message) {
    state.feedback = message;
    renderHeaderState();
  }

  function summarizePayload(payload) {
    const candidates = [
      payload.content,
      payload.reply,
      payload.error,
      payload.message,
      payload.status,
      payload.topic,
    ];
    for (const candidate of candidates) {
      const text = cleanText(candidate);
      if (text) {
        return trimText(text, 180);
      }
    }
    return "No payload summary";
  }

  function streamForEventType(eventType) {
    const value = cleanText(eventType);
    if (!value) {
      return "agent_event";
    }
    if (value.indexOf("run.") === 0) {
      return "run_event";
    }
    if (value.indexOf("tool.") === 0) {
      return "tool_chunk";
    }
    if (value.indexOf("llm.") === 0) {
      return "llm_chunk";
    }
    return "agent_event";
  }

  function isDoneEvent(event) {
    const eventType = cleanText(event.event_type);
    if (eventType === "run.finished" || eventType === "run.error") {
      return true;
    }
    const status = cleanText(event.payload && event.payload.status);
    return status === "completed" || status === "error" || status === "stopped";
  }

  function tagMarkup(text) {
    return '<span class="timeline-tag">' + escapeHtml(text) + "</span>";
  }

  function cleanText(value) {
    if (value === null || value === undefined) {
      return "";
    }
    return String(value).trim();
  }

  function trimText(value, maxLength) {
    if (value.length <= maxLength) {
      return value;
    }
    return value.slice(0, maxLength - 3) + "...";
  }

  function formatError(error) {
    if (!error) {
      return "unknown error";
    }
    if (typeof error === "string") {
      return error;
    }
    if (error.message) {
      return error.message;
    }
    return String(error);
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  init();
})();
