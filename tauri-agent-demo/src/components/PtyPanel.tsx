import { memo, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';
import { Terminal } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';
import 'xterm/css/xterm.css';
import { closePty, listPtys, readPty, sendPty, type PtyListItem } from '../api';
import { ptyStore, usePtySessionSnapshot, type PtyStoreEntry } from '../ptyStore';
import './PtyPanel.css';

const INITIAL_READ_MAX_OUTPUT = 8000;
const DEFAULT_MAX_EXITED = 6;

type PtyTerminal = {
  term: Terminal;
  fit: FitAddon;
  generation: number;
};

type TerminalRenderState = {
  seq: number;
  ansiLen: number;
  renderedContent: string;
};

type PtyBufferSnapshot = {
  ptyId: string;
  isAlternate: boolean;
  cols: number;
  rows: number;
  cursorX: number;
  cursorY: number;
  viewportY: number;
  baseY: number;
  length: number;
  activeTailFrom: number;
  activeTailLines: string[];
  viewportLines: string[];
};

type PtyDebugApi = {
  listTerms: () => string[];
  dumpActiveBuffer: (ptyId?: string, maxLines?: number) => PtyBufferSnapshot | null;
};

const TERM_CLEAR_AND_HOME = '\x1b[2J\x1b[H';

const captureBufferSnapshot = (term: Terminal, ptyId: string, maxLines?: number): PtyBufferSnapshot => {
  const active = term.buffer.active;
  const safeMaxLines = Number.isFinite(maxLines as number) ? Math.max(1, Math.floor(maxLines as number)) : 120;
  const tailFrom = Math.max(0, active.length - safeMaxLines);
  const activeTailLines: string[] = [];
  for (let i = tailFrom; i < active.length; i += 1) {
    const line = active.getLine(i);
    activeTailLines.push(line ? line.translateToString(true) : '');
  }

  const viewportStart = Math.max(0, active.viewportY);
  const viewportEnd = Math.min(active.length, viewportStart + term.rows);
  const viewportLines: string[] = [];
  for (let i = viewportStart; i < viewportEnd; i += 1) {
    const line = active.getLine(i);
    viewportLines.push(line ? line.translateToString(true) : '');
  }

  return {
    ptyId,
    isAlternate: active === term.buffer.alternate,
    cols: term.cols,
    rows: term.rows,
    cursorX: active.cursorX,
    cursorY: active.cursorY,
    viewportY: active.viewportY,
    baseY: active.baseY,
    length: active.length,
    activeTailFrom: tailFrom,
    activeTailLines,
    viewportLines
  };
};

interface PtyPanelProps {
  sessionId?: string | null;
  onClose: () => void;
  onWidthChange?: (width: number) => void;
  initialWidth?: number;
  onActivity?: () => void;
}

function PtyPanel({ sessionId, onClose, onWidthChange, initialWidth, onActivity }: PtyPanelProps) {
  const [panelWidth, setPanelWidth] = useState(() => Math.max(320, initialWidth ?? 380));
  const [includeExited, setIncludeExited] = useState(true);
  const [maxExited, setMaxExited] = useState(DEFAULT_MAX_EXITED);
  const [items, setItems] = useState<PtyListItem[]>([]);
  const [inputById, setInputById] = useState<Record<string, string>>({});

  const panelRef = useRef<HTMLDivElement>(null);
  const terminalsRef = useRef<Record<string, PtyTerminal>>({});
  const terminalRefCallbacks = useRef<Record<string, (node: HTMLDivElement | null) => void>>({});
  const terminalRenderStateRef = useRef<Record<string, TerminalRenderState>>({});
  const terminalGenerationRef = useRef<Record<string, number>>({});
  const ptySnapshot = usePtySessionSnapshot(sessionId);
  const snapshotRef = useRef<Record<string, PtyStoreEntry>>({});
  const hydratedBySessionRef = useRef<Record<string, boolean>>({});
  const onActivityRef = useRef(onActivity);

  const nextTerminalGeneration = (ptyId: string): number => {
    const next = (terminalGenerationRef.current[ptyId] || 0) + 1;
    terminalGenerationRef.current[ptyId] = next;
    return next;
  };

  const isTerminalAlive = (ptyId: string, generation: number, term: Terminal): boolean => {
    const current = terminalsRef.current[ptyId];
    return Boolean(current && current.generation === generation && current.term === term);
  };

  const disposeTerminal = (ptyId: string) => {
    nextTerminalGeneration(ptyId);
    const existing = terminalsRef.current[ptyId];
    if (existing) {
      try {
        existing.term.dispose();
      } catch {
        // ignore dispose errors
      }
      delete terminalsRef.current[ptyId];
    }
    delete terminalRefCallbacks.current[ptyId];
    delete terminalRenderStateRef.current[ptyId];
  };

  const safeFitTerminal = (ptyId: string) => {
    const entry = terminalsRef.current[ptyId];
    if (!entry) return;
    try {
      entry.fit.fit();
    } catch {
      // ignore fit after-dispose races
    }
  };

  const safeWriteTerminal = (ptyId: string, payload: string, reason: string): boolean => {
    if (!payload) return false;
    const entry = terminalsRef.current[ptyId];
    if (!entry) return false;
    const { term, generation } = entry;
    try {
      term.write(payload, () => {
        if (!isTerminalAlive(ptyId, generation, term)) return;
        try {
          term.scrollToBottom();
        } catch {
          return;
        }
      });
      return true;
    } catch {
      return false;
    }
  };

  useEffect(() => {
    snapshotRef.current = ptySnapshot;
  }, [ptySnapshot]);

  useEffect(() => {
    onActivityRef.current = onActivity;
  }, [onActivity]);

  useEffect(() => {
    const debugApi: PtyDebugApi = {
      listTerms: () => Object.keys(terminalsRef.current),
      dumpActiveBuffer: (ptyId?: string, maxLines?: number) => {
        const ids = Object.keys(terminalsRef.current);
        const targetId = ptyId && terminalsRef.current[ptyId] ? ptyId : ids[0];
        if (!targetId) return null;
        const entry = terminalsRef.current[targetId];
        if (!entry) return null;
        try {
          const snapshot = captureBufferSnapshot(entry.term, targetId, maxLines);
          console.log('[PTY ACTIVE BUFFER SNAPSHOT]', snapshot);
          return snapshot;
        } catch {
          return null;
        }
      }
    };

    (window as any).__ptyDebug = debugApi;
    return () => {
      if ((window as any).__ptyDebug === debugApi) {
        delete (window as any).__ptyDebug;
      }
    };
  }, []);

  useEffect(() => {
    onWidthChange?.(panelWidth);
  }, [panelWidth, onWidthChange]);

  useEffect(() => {
    setItems([]);
    setInputById({});
    Object.keys(terminalsRef.current).forEach((ptyId) => disposeTerminal(ptyId));
    terminalsRef.current = {};
    terminalRefCallbacks.current = {};
    terminalRenderStateRef.current = {};
    terminalGenerationRef.current = {};
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) {
      setItems([]);
      return;
    }
    if (hydratedBySessionRef.current[sessionId]) return;
    hydratedBySessionRef.current[sessionId] = true;
    let active = true;
    const hydrate = async () => {
      try {
        const list = await listPtys(sessionId, { includeExited: true, maxExited: 50 });
        if (!active) return;
        setItems(list);
        list.forEach((item) => ptyStore.applyListItem(sessionId, item));
        const liveSnapshot = ptyStore.getSessionSnapshot(sessionId);
        await Promise.all(
          list.map(async (item) => {
            try {
              const existing = liveSnapshot[item.pty_id];
              const hasLiveData =
                Boolean(existing?.ansi_log) ||
                Boolean(existing?.rendered_content) ||
                (typeof existing?.seq === 'number' && existing.seq > 0);
              if (hasLiveData) {
                return;
              }
              const cursor = existing?.cursor ?? snapshotRef.current[item.pty_id]?.cursor;
              const response = await readPty({
                session_id: sessionId,
                pty_id: item.pty_id,
                cursor: typeof cursor === 'number' ? cursor : undefined,
                max_output: INITIAL_READ_MAX_OUTPUT
              });
              if (!active) return;
              ptyStore.applyReadResponse(sessionId, response);
            } catch {
              // ignore
            }
          })
        );
      } catch {
        // ignore
      }
    };
    void hydrate();
    return () => {
      active = false;
    };
  }, [sessionId]);

  useEffect(() => {
    const handleResize = () => {
      Object.keys(terminalsRef.current).forEach((ptyId) => safeFitTerminal(ptyId));
    };
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, []);

  useEffect(() => {
    Object.keys(terminalsRef.current).forEach((ptyId) => safeFitTerminal(ptyId));
  }, [panelWidth, items.length]);

  const orderedItems = useMemo(() => {
    const byId = new Map<string, PtyListItem>();
    items.forEach((item) => {
      byId.set(item.pty_id, item);
    });
    Object.entries(ptySnapshot).forEach(([ptyId, entry]) => {
      if (byId.has(ptyId)) return;
      byId.set(ptyId, {
        pty_id: ptyId,
        status: entry.status || 'running',
        pty: true,
        exit_code: entry.exit_code,
        command: entry.command,
        created_at: Math.floor(entry.updated_at / 1000)
      });
    });

    const merged = Array.from(byId.values());
    const running: PtyListItem[] = [];
    const exited: PtyListItem[] = [];

    merged.forEach((item) => {
      const entry = ptySnapshot[item.pty_id];
      const waitingInput = entry?.waiting_input ?? item.waiting_input ?? false;
      const status = waitingInput ? 'waiting_input' : (entry?.status || item.status);
      const isRunning = status === 'running' || status === 'waiting_input';
      if (isRunning) running.push(item);
      else exited.push(item);
    });

    running.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
    exited.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
    if (!includeExited) return running;
    return [...running, ...exited.slice(0, Math.max(0, maxExited))];
  }, [items, ptySnapshot, includeExited, maxExited]);

  useEffect(() => {
    let hadActivity = false;
    const runningPtyIds = new Set<string>();
    orderedItems.forEach((item) => {
      const ptyId = item.pty_id;
      const entry = ptySnapshot[ptyId];
      if (!entry) return;
      const waitingInput = entry?.waiting_input ?? item.waiting_input ?? false;
      const status = waitingInput ? 'waiting_input' : (entry?.status || item.status);
      const isRunning = status === 'running' || status === 'waiting_input';
      if (!isRunning) return;
      runningPtyIds.add(ptyId);
      const terminalEntry = terminalsRef.current[ptyId];
      if (!terminalEntry) return;
      const renderState =
        terminalRenderStateRef.current[ptyId] || { seq: -1, ansiLen: 0, renderedContent: '' };
      if (renderState.seq === entry.seq) return;
      const activeBeforeRender = terminalEntry.term.buffer.active;
      if (activeBeforeRender.length === 0) {
        console.warn('[PTY EMPTY ACTIVE BUFFER BEFORE_RENDER]', {
          sessionId,
          ptyId,
          seq: entry.seq,
          status: entry.status,
          baseY: activeBeforeRender.baseY,
          viewportY: activeBeforeRender.viewportY,
          cursorX: activeBeforeRender.cursorX,
          cursorY: activeBeforeRender.cursorY,
          isAlternate: activeBeforeRender === terminalEntry.term.buffer.alternate,
          cols: terminalEntry.term.cols,
          rows: terminalEntry.term.rows
        });
      }

      const nextAnsi = entry.ansi_log || '';
      const nextRendered = entry.rendered_content || '';
      const shouldAppend =
        !entry.reset &&
        renderState.ansiLen >= 0 &&
        nextAnsi.length >= renderState.ansiLen;
      if (entry.reset) {
        const full = nextAnsi || nextRendered;
        const payload = full ? `${TERM_CLEAR_AND_HOME}${full}` : TERM_CLEAR_AND_HOME;
        if (safeWriteTerminal(ptyId, payload, 'reset')) hadActivity = true;
      } else if (shouldAppend) {
        const delta = nextAnsi.slice(renderState.ansiLen);
        if (delta) {
          if (safeWriteTerminal(ptyId, delta, 'append')) hadActivity = true;
        }
      }

      terminalRenderStateRef.current[ptyId] = {
        seq: entry.seq,
        ansiLen: nextAnsi.length,
        renderedContent: nextRendered
      };
    });
    Object.keys(terminalsRef.current).forEach((ptyId) => {
      if (!runningPtyIds.has(ptyId)) {
        disposeTerminal(ptyId);
      }
    });
    if (hadActivity) onActivityRef.current?.();
  }, [orderedItems, ptySnapshot]);

  const getTerminalRef = (ptyId: string) => {
    if (!terminalRefCallbacks.current[ptyId]) {
      terminalRefCallbacks.current[ptyId] = (node) => {
        if (!node) {
          // React strict-mode/dev reconciliation may transiently send null refs.
          // Disposal is handled by explicit session-change or non-running cleanup.
          return;
        }
        if (terminalsRef.current[ptyId]) return;
        const generation = nextTerminalGeneration(ptyId);

        const term = new Terminal({
          convertEol: true,
          disableStdin: true,
          scrollback: 4000,
          fontFamily: "'SFMono-Regular', 'Menlo', 'Consolas', 'Liberation Mono', 'Courier New', monospace",
          fontSize: 12,
          lineHeight: 1.4,
          theme: {
            background: '#0b0f14',
            foreground: '#e2e8f0',
            cursor: '#e2e8f0'
          }
        });
        const fit = new FitAddon();
        term.loadAddon(fit);
        term.open(node);
        try {
          fit.fit();
        } catch {
          // ignore fit errors on unstable mount timing
        }
        terminalsRef.current[ptyId] = { term, fit, generation };

        const existingEntry = snapshotRef.current[ptyId];
        const initial = existingEntry?.rendered_content || existingEntry?.ansi_log || '';
        if (initial) {
          safeWriteTerminal(ptyId, initial, 'initial');
        }
        terminalRenderStateRef.current[ptyId] = {
          seq: existingEntry?.seq ?? 0,
          ansiLen: existingEntry?.ansi_log?.length ?? 0,
          renderedContent: existingEntry?.rendered_content || ''
        };
      };
    }
    return terminalRefCallbacks.current[ptyId];
  };

  const handleResizeStart = (event: ReactMouseEvent) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = panelRef.current?.getBoundingClientRect().width ?? panelWidth;

    const handleMouseMove = (moveEvent: MouseEvent) => {
      const delta = startX - moveEvent.clientX;
      const maxWidth = Math.min(window.innerWidth * 0.8, window.innerWidth - 240);
      const nextWidth = Math.max(320, Math.min(maxWidth, startWidth + delta));
      setPanelWidth(nextWidth);
    };

    const handleMouseUp = () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
  };

  const handleSend = async (ptyId: string) => {
    const input = (inputById[ptyId] || '').trim();
    if (!input || !sessionId) return;
    const payload = input.endsWith('\n') ? input : `${input}\n`;
    try {
      await sendPty({ session_id: sessionId, pty_id: ptyId, input: payload });
      onActivity?.();
    } catch {
      // ignore send errors
    }
    setInputById((prev) => ({ ...prev, [ptyId]: '' }));
  };

  const handleClose = async (ptyId: string) => {
    if (!sessionId) return;
    try {
      await closePty({ session_id: sessionId, pty_id: ptyId });
      onActivity?.();
    } catch {
      // ignore close errors
    }
  };

  return (
    <div className="pty-panel" ref={panelRef} style={{ width: panelWidth }}>
      <div className="pty-resize-handle" onMouseDown={handleResizeStart} />
      <div className="pty-header">
        <div className="pty-title">
          <h2>PTY Sessions</h2>
          <span className="pty-count">{orderedItems.length}</span>
        </div>
        <button className="close-btn" onClick={onClose} aria-label="Close">
          x
        </button>
      </div>
      <div className="pty-controls">
        <label className="pty-toggle">
          <input
            type="checkbox"
            checked={includeExited}
            onChange={(e) => setIncludeExited(e.currentTarget.checked)}
          />
          <span>Show exited</span>
        </label>
        <label className="pty-max-exited">
          <span>Max exited</span>
          <input
            type="number"
            min={0}
            max={50}
            value={maxExited}
            onChange={(e) => {
              const raw = e.currentTarget ? e.currentTarget.value : '';
              setMaxExited(Math.max(0, Number(raw || 0)));
            }}
          />
        </label>
      </div>
      <div className="pty-content">
        {!sessionId && <div className="pty-empty">Session is not ready yet.</div>}
        {sessionId && orderedItems.length === 0 && <div className="pty-empty">No PTY sessions.</div>}
        {orderedItems.map((item) => {
          const entry = ptySnapshot[item.pty_id];
          const waitingInput = entry?.waiting_input ?? item.waiting_input ?? false;
          const status = waitingInput ? 'waiting_input' : (entry?.status || item.status);
          const exitCode = entry?.exit_code ?? item.exit_code;
          const command = entry?.command || item.command || '';
          const isRunning = status === 'running' || status === 'waiting_input';
          const isInteractive = isRunning;
          return (
            <div key={item.pty_id} className={`pty-card ${isRunning ? 'running' : 'exited'}`}>
              <div className="pty-card-header">
                <div className="pty-card-title">
                  <span className="pty-id">{item.pty_id}</span>
                  <span className={`pty-status ${isRunning ? 'ok' : 'idle'}`}>{status}</span>
                  {typeof exitCode === 'number' && (
                    <span className={`pty-exit ${exitCode === 0 ? 'ok' : 'err'}`}>exit {exitCode}</span>
                  )}
                </div>
                {isInteractive && (
                  <div className="pty-actions">
                    <button type="button" className="pty-btn" onClick={() => void handleClose(item.pty_id)}>
                      Close
                    </button>
                  </div>
                )}
              </div>
              {command ? <div className="pty-command">{command}</div> : null}
              <div className="pty-output">
                {isRunning ? (
                  <div className="pty-terminal" ref={getTerminalRef(item.pty_id)} />
                ) : (
                  <pre className="pty-output-static">{entry?.rendered_content || entry?.ansi_log || ''}</pre>
                )}
              </div>
              {isInteractive && (
                <div className="pty-input-row">
                  <input
                    type="text"
                    placeholder="Send input..."
                    value={inputById[item.pty_id] || ''}
                    onChange={(e) => {
                      const value = e.currentTarget ? e.currentTarget.value : '';
                      setInputById((prev) => ({ ...prev, [item.pty_id]: value }));
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        void handleSend(item.pty_id);
                      }
                    }}
                  />
                  <button type="button" className="pty-btn primary" onClick={() => void handleSend(item.pty_id)}>
                    Send
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default memo(PtyPanel);
