import { memo, useEffect, useMemo, useRef, useState } from 'react';
import type { PtyStoreEntry } from '../ptyStore';
import type { PtyInteractionController } from './ptyInteraction';
import PtyTerminalSurface from './PtyTerminalSurface';
import './PtySessionCard.css';

const runningStatuses = new Set(['running', 'waiting_input']);

interface PtySessionCardProps {
  variant: 'panel' | 'step';
  sessionId?: string | null;
  ptyId: string;
  entry?: PtyStoreEntry;
  fallbackStatus?: string;
  fallbackExitCode?: number | null;
  command?: string;
  mode?: string;
  ownerKey: string;
  ptyInteraction?: PtyInteractionController;
  frozen?: boolean;
  frozenContent?: string;
  readOnlyHint?: string;
  onActivity?: () => void;
}

function PtySessionCard({
  variant,
  sessionId,
  ptyId,
  entry,
  fallbackStatus,
  fallbackExitCode,
  command,
  mode,
  ownerKey,
  ptyInteraction,
  frozen = false,
  frozenContent,
  readOnlyHint,
  onActivity
}: PtySessionCardProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const writableRef = useRef<boolean | null>(null);

  const status = useMemo(() => {
    if (entry?.waiting_input) return 'waiting_input';
    return entry?.status || fallbackStatus || 'running';
  }, [entry?.status, entry?.waiting_input, fallbackStatus]);
  const isRunning = runningStatuses.has(status);
  const waitingInput = entry?.waiting_input || status === 'waiting_input';
  const exitCode = entry?.exit_code ?? fallbackExitCode;
  const displayCommand = entry?.command || command || '';
  const displayMode = entry?.pty_mode || mode || '';

  const ownerByPtyId = ptyInteraction?.ownerByPtyId || {};
  const activeOwner = ownerByPtyId[ptyId];
  const isOwner = Boolean(activeOwner && activeOwner === ownerKey);
  const canInteract = Boolean(sessionId && ptyInteraction);
  const writable = canInteract && isRunning && isOwner && !frozen;

  useEffect(() => {
    if (typeof window === 'undefined') return;
    let enabled = false;
    try {
      const value = window.localStorage.getItem('PTY_XTERM_LIFECYCLE_DEBUG');
      enabled = value === '1' || value === 'true' || Boolean((window as any).__PTY_XTERM_LIFECYCLE_DEBUG__);
    } catch {
      enabled = Boolean((window as any).__PTY_XTERM_LIFECYCLE_DEBUG__);
    }
    if (!enabled) return;
    const previous = writableRef.current;
    writableRef.current = writable;
    if (previous === null || previous === writable) return;
    console.info('[PTY CARD WRITABLE]', {
      ptyId,
      sessionId: sessionId || '',
      ownerKey,
      writableFrom: previous,
      writableTo: writable,
      isOwner,
      isRunning,
      frozen,
      status
    });
  }, [frozen, isOwner, isRunning, ownerKey, ptyId, sessionId, status, writable]);

  const activateOwner = () => {
    if (!ptyInteraction || !sessionId || !isRunning || frozen) return;
    if (isOwner) return;
    ptyInteraction.activateOwner({ sessionId, ptyId, ownerKey });
  };

  const handleClose = async () => {
    if (!ptyInteraction || !sessionId || !writable || busy) return;
    setBusy(true);
    setError(null);
    try {
      await ptyInteraction.closePty({ sessionId, ptyId });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Failed to close PTY');
    } finally {
      setBusy(false);
    }
  };

  const hintText = (() => {
    if (frozen && readOnlyHint) return readOnlyHint;
    if (!isRunning) return '';
    if (readOnlyHint && !writable) return readOnlyHint;
    if (!canInteract) return '';
    if (!isOwner) return '只读镜像，点击卡片激活输入';
    return '';
  })();

  const handleTerminalData = (data: string) => {
    if (!data || !ptyInteraction || !sessionId || !writable) return;
    void ptyInteraction.sendInput({ sessionId, ptyId, input: data });
  };

  const handleFlushPendingInput = () => {
    if (!ptyInteraction?.flushInput || !sessionId) return;
    void ptyInteraction.flushInput({ sessionId, ptyId });
  };

  return (
    <div
      className={`pty-card pty-session-card pty-session-card-${variant} ${isRunning ? 'running' : 'exited'}${isOwner ? ' owner' : ''}${frozen ? ' frozen' : ''}`}
      onClick={activateOwner}
    >
      <div className="pty-card-header">
        <div className="pty-card-title">
          <span className="pty-id">{ptyId}</span>
          <span className={`pty-status ${isRunning ? 'ok' : 'idle'}`}>{status}</span>
          {displayMode ? <span className="pty-mode">{displayMode}</span> : null}
          {typeof exitCode === 'number' ? (
            <span className={`pty-exit ${exitCode === 0 ? 'ok' : 'err'}`}>exit {exitCode}</span>
          ) : null}
          {isOwner && isRunning ? <span className="pty-owner-badge">active</span> : null}
        </div>
        {writable ? (
          <div className="pty-actions">
            <button type="button" className="pty-btn" onClick={() => void handleClose()} disabled={busy}>
              Close
            </button>
          </div>
        ) : null}
      </div>

      {displayCommand ? <div className="pty-command">{displayCommand}</div> : null}

      {hintText ? <div className="pty-readonly-hint">{hintText}</div> : null}
      {waitingInput ? (
        <div className="pty-waiting-reason">命令正在等待输入，请直接在终端中键入。</div>
      ) : null}

      <div className="pty-output">
        <PtyTerminalSurface
          ptyId={ptyId}
          entry={entry}
          running={isRunning}
          writable={writable}
          staticText={frozen ? (frozenContent ?? entry?.rendered_content ?? entry?.ansi_log ?? '') : frozenContent}
          onActivity={onActivity}
          onTerminalData={handleTerminalData}
          onFlushPendingInput={handleFlushPendingInput}
        />
      </div>

      {error ? <div className="pty-input-error">{error}</div> : null}
    </div>
  );
}

export default memo(PtySessionCard);
