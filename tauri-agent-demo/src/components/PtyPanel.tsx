import { memo, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';
import { listPtys, readPty, type PtyListItem } from '../api';
import { ptyStore, usePtySessionSnapshot, type PtyStoreEntry } from '../ptyStore';
import type { PtyInteractionController } from './ptyInteraction';
import PtySessionCard from './PtySessionCard';
import './PtyPanel.css';

const INITIAL_READ_MAX_OUTPUT = 8000;
const DEFAULT_MAX_EXITED = 6;

interface PtyPanelProps {
  sessionId?: string | null;
  onClose: () => void;
  onWidthChange?: (width: number) => void;
  initialWidth?: number;
  onActivity?: () => void;
  ptyInteraction: PtyInteractionController;
}

function PtyPanel({
  sessionId,
  onClose,
  onWidthChange,
  initialWidth,
  onActivity,
  ptyInteraction
}: PtyPanelProps) {
  const [panelWidth, setPanelWidth] = useState(() => Math.max(320, initialWidth ?? 380));
  const [includeExited, setIncludeExited] = useState(true);
  const [maxExited, setMaxExited] = useState(DEFAULT_MAX_EXITED);
  const [items, setItems] = useState<PtyListItem[]>([]);

  const panelRef = useRef<HTMLDivElement>(null);
  const ptySnapshot = usePtySessionSnapshot(sessionId);
  const snapshotRef = useRef<Record<string, PtyStoreEntry>>({});
  const hydratedBySessionRef = useRef<Record<string, boolean>>({});

  useEffect(() => {
    snapshotRef.current = ptySnapshot;
  }, [ptySnapshot]);

  useEffect(() => {
    onWidthChange?.(panelWidth);
  }, [panelWidth, onWidthChange]);

  useEffect(() => {
    setItems([]);
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
              if (hasLiveData) return;
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
      if (isRunning) {
        running.push(item);
      } else {
        exited.push(item);
      }
    });

    running.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
    exited.sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
    if (!includeExited) return running;
    return [...running, ...exited.slice(0, Math.max(0, maxExited))];
  }, [includeExited, items, maxExited, ptySnapshot]);

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
            onChange={(event) => setIncludeExited(event.currentTarget.checked)}
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
            onChange={(event) => {
              const raw = event.currentTarget.value || '';
              setMaxExited(Math.max(0, Number(raw || 0)));
            }}
          />
        </label>
      </div>

      <div className="pty-content">
        {!sessionId ? <div className="pty-empty">Session is not ready yet.</div> : null}
        {sessionId && orderedItems.length === 0 ? <div className="pty-empty">No PTY sessions.</div> : null}
        {orderedItems.map((item) => {
          const entry = ptySnapshot[item.pty_id];
          const status = entry?.waiting_input ? 'waiting_input' : (entry?.status || item.status);
          const isRunning = status === 'running' || status === 'waiting_input';
          const ownerKey = `panel:${item.pty_id}`;
          const activeOwner = ptyInteraction.ownerByPtyId[item.pty_id];
          const readOnlyHint = isRunning
            ? activeOwner
              ? (activeOwner !== ownerKey ? '该 PTY 当前在其他视图激活，点击切换输入控制' : undefined)
              : '点击卡片激活该 PTY 输入控制'
            : undefined;

          return (
            <PtySessionCard
              key={item.pty_id}
              variant="panel"
              sessionId={sessionId}
              ptyId={item.pty_id}
              entry={entry}
              fallbackStatus={item.status}
              fallbackExitCode={item.exit_code}
              command={item.command}
              mode={item.pty_mode}
              ownerKey={ownerKey}
              ptyInteraction={ptyInteraction}
              readOnlyHint={readOnlyHint}
              onActivity={onActivity}
            />
          );
        })}
      </div>
    </div>
  );
}

export default memo(PtyPanel);
