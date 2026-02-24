import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';
import { Terminal } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';
import 'xterm/css/xterm.css';
import { closePty, listPtys, readPty, sendPty, type PtyListItem } from '../api';
import './PtyPanel.css';

type PtyBuffer = {
  text: string;
  cursor: number;
  status?: string;
  command?: string;
  exit_code?: number | null;
  pty?: boolean;
};

const DEFAULT_MAX_EXITED = 6;
const LIST_POLL_MS = 2000;
const READ_POLL_MS = 500;
const READ_MAX_OUTPUT = 8000;

type PtyTerminal = {
  term: Terminal;
  fit: FitAddon;
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
  const buffersRef = useRef<Record<string, PtyBuffer>>({});
  const [inputById, setInputById] = useState<Record<string, string>>({});
  const panelRef = useRef<HTMLDivElement>(null);
  const itemsRef = useRef<PtyListItem[]>([]);
  const terminalsRef = useRef<Record<string, PtyTerminal>>({});
  const terminalContainersRef = useRef<Record<string, HTMLDivElement | null>>({});
  const terminalRefCallbacks = useRef<Record<string, (node: HTMLDivElement | null) => void>>({});
  const listSignatureRef = useRef<string>('');

  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  useEffect(() => {
    onWidthChange?.(panelWidth);
  }, [panelWidth, onWidthChange]);

  useEffect(() => {
    setItems([]);
    buffersRef.current = {};
    setInputById({});
    for (const entry of Object.values(terminalsRef.current)) {
      entry.term.dispose();
    }
    terminalsRef.current = {};
    terminalContainersRef.current = {};
    terminalRefCallbacks.current = {};
  }, [sessionId]);

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

  useEffect(() => {
    if (!sessionId) {
      setItems([]);
      listSignatureRef.current = '';
      return;
    }
    let active = true;
    const fetchList = async () => {
      try {
        const list = await listPtys(sessionId, { includeExited, maxExited });
        if (!active) return;
        const signature = list
          .map((item) => `${item.pty_id}|${item.status}|${item.exit_code ?? ''}|${item.command ?? ''}|${item.pty}`)
          .join('||');
        if (signature !== listSignatureRef.current) {
          listSignatureRef.current = signature;
          setItems(list);
        }
        const next: Record<string, PtyBuffer> = {};
        const keep = new Set(list.map((item) => item.pty_id));
        for (const item of list) {
          const existing = buffersRef.current[item.pty_id] || { text: '', cursor: 0 };
          const command = item.command || existing.command;
            next[item.pty_id] = {
              ...existing,
              command,
              status: item.status,
              exit_code: item.exit_code ?? existing.exit_code,
              pty: item.pty,
              text: existing.text
            };
          }
          for (const key of Object.keys(buffersRef.current)) {
            if (!keep.has(key)) {
              // drop stale buffers
              continue;
            }
          }
        buffersRef.current = next;
      } catch {
        // ignore polling errors
      }
    };
    fetchList();
    const interval = window.setInterval(fetchList, LIST_POLL_MS);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [sessionId, includeExited, maxExited]);

  useEffect(() => {
    if (!sessionId) return;
    let active = true;
    const tick = async () => {
      if (!active) return;
      const currentItems = itemsRef.current;
      if (!currentItems.length) return;
      let hadNewData = false;
      for (const item of currentItems) {
        try {
          const current = buffersRef.current[item.pty_id] || { text: '', cursor: 0 };
          const resp = await readPty({
            session_id: sessionId,
            pty_id: item.pty_id,
            cursor: current.cursor,
            max_output: READ_MAX_OUTPUT
          });
          let text = current.text;
          if (resp.reset) {
            text = resp.chunk || '';
          } else if (resp.chunk) {
            text = text + resp.chunk;
          }
          const command = item.command || resp.command || current.command;
          const terminalEntry = terminalsRef.current[item.pty_id];
          if (terminalEntry) {
            if (resp.reset) {
              terminalEntry.term.reset();
            }
            if (resp.chunk) {
              terminalEntry.term.write(resp.chunk, () => terminalEntry.term.scrollToBottom());
            }
          }
          if (resp.chunk || resp.reset || resp.status === 'running') {
            onActivity?.();
          }
          const nextBuffer = {
            ...current,
            text,
            cursor: resp.cursor,
            status: resp.status,
            exit_code: resp.exit_code,
            pty: resp.pty,
            command
          };
          if (resp.chunk || resp.reset || resp.cursor !== current.cursor) {
            hadNewData = true;
          }
          buffersRef.current[item.pty_id] = nextBuffer;
        } catch {
          // ignore read errors
        }
      }
      if (hadNewData) {
        // no state updates required; xterm renders directly
      }
    };
    tick();
    const interval = window.setInterval(tick, READ_POLL_MS);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [sessionId]);

  useEffect(() => {
    const handleResize = () => {
      for (const entry of Object.values(terminalsRef.current)) {
        entry.fit.fit();
      }
    };
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, []);

  useEffect(() => {
    for (const entry of Object.values(terminalsRef.current)) {
      entry.fit.fit();
    }
  }, [panelWidth, items.length]);

  const handleSend = async (ptyId: string) => {
    const input = (inputById[ptyId] || '').trim();
    if (!input || !sessionId) return;
    const payload = input.endsWith('\n') ? input : `${input}\n`;
    try {
      await sendPty({ session_id: sessionId, pty_id: ptyId, input: payload });
    } catch {
      // ignore send errors
    }
    setInputById((prev) => ({ ...prev, [ptyId]: '' }));
  };

  const handleClose = async (ptyId: string) => {
    if (!sessionId) return;
    try {
      await closePty({ session_id: sessionId, pty_id: ptyId });
    } catch {
      // ignore close errors
    }
  };

  const orderedItems = useMemo(() => {
    return [...items].sort((a, b) => {
      const aRank = a.status === 'running' ? 0 : 1;
      const bRank = b.status === 'running' ? 0 : 1;
      if (aRank !== bRank) return aRank - bRank;
      const aTime = a.created_at || 0;
      const bTime = b.created_at || 0;
      return bTime - aTime;
    });
  }, [items]);

  const getTerminalRef = (ptyId: string) => {
    if (!terminalRefCallbacks.current[ptyId]) {
      terminalRefCallbacks.current[ptyId] = (node) => {
        terminalContainersRef.current[ptyId] = node;
        if (!node) {
          const existing = terminalsRef.current[ptyId];
          if (existing) {
            existing.term.dispose();
            delete terminalsRef.current[ptyId];
          }
          delete terminalRefCallbacks.current[ptyId];
          return;
        }
        if (terminalsRef.current[ptyId]) {
          return;
        }
        const term = new Terminal({
          convertEol: true,
          disableStdin: true,
          scrollback: 2000,
          fontFamily:
            "'SFMono-Regular', 'Menlo', 'Consolas', 'Liberation Mono', 'Courier New', monospace",
          fontSize: 12,
          lineHeight: 1.4,
          theme: {
            background: '#0b0f14',
            foreground: '#e2e8f0',
            cursor: '#e2e8f0',
            selection: 'rgba(148, 163, 184, 0.35)'
          }
        });
        const fit = new FitAddon();
        term.loadAddon(fit);
        term.open(node);
        fit.fit();
        const existingText = buffersRef.current[ptyId]?.text;
        if (existingText) {
          term.write(existingText, () => term.scrollToBottom());
        }
        terminalsRef.current[ptyId] = { term, fit };
      };
    }
    return terminalRefCallbacks.current[ptyId];
  };

  return (
    <div className="pty-panel" ref={panelRef} style={{ width: panelWidth }}>
      <div className="pty-resize-handle" onMouseDown={handleResizeStart} />
      <div className="pty-header">
        <div className="pty-title">
          <h2>PTY Sessions</h2>
          <span className="pty-count">{orderedItems.length}</span>
        </div>
        <button className="close-btn" onClick={onClose} aria-label="Close">×</button>
      </div>
      <div className="pty-controls">
        <label className="pty-toggle">
          <input
            type="checkbox"
            checked={includeExited}
            onChange={(e) => setIncludeExited(e.currentTarget.checked)}
          />
          <span>显示已退出</span>
        </label>
        <label className="pty-max-exited">
          <span>最多已退出</span>
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
        {!sessionId && (
          <div className="pty-empty">当前会话尚未创建。</div>
        )}
        {sessionId && orderedItems.length === 0 && (
          <div className="pty-empty">暂无存活的 PTY。</div>
        )}
        {orderedItems.map((item) => {
          const status = item.status;
          const exitCode = item.exit_code;
          const isRunning = status === 'running';
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
                    <button type="button" className="pty-btn" onClick={() => handleClose(item.pty_id)}>
                      Close
                    </button>
                  </div>
                )}
              </div>
              <div className="pty-output">
                <div
                  className="pty-terminal"
                  ref={getTerminalRef(item.pty_id)}
                />
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
                        handleSend(item.pty_id);
                      }
                    }}
                  />
                  <button type="button" className="pty-btn primary" onClick={() => handleSend(item.pty_id)}>
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

export default PtyPanel;
