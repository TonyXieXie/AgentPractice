import { memo, useCallback, useEffect, useRef } from 'react';
import { Terminal } from 'xterm';
import 'xterm/css/xterm.css';
import type { PtyStoreEntry } from '../ptyStore';

const TERM_CLEAR_AND_HOME = '\x1b[2J\x1b[H';
const PTY_FIXED_COLS = 120;
const PTY_FIXED_ROWS = 30;

type TerminalRenderState = {
  seq: number;
  ansiLen: number;
};

const isLifecycleDebugEnabled = () => {
  if (typeof window === 'undefined') return false;
  try {
    const fromStorage = window.localStorage.getItem('PTY_XTERM_LIFECYCLE_DEBUG');
    if (fromStorage === '1' || fromStorage === 'true') return true;
  } catch {
    // ignore
  }
  return Boolean((window as any).__PTY_XTERM_LIFECYCLE_DEBUG__);
};

interface PtyTerminalSurfaceProps {
  ptyId: string;
  entry?: PtyStoreEntry;
  running: boolean;
  writable: boolean;
  staticText?: string;
  className?: string;
  onActivity?: () => void;
  onTerminalData?: (data: string) => void;
  onFlushPendingInput?: () => void;
}

function PtyTerminalSurface({
  ptyId,
  entry,
  running,
  writable,
  staticText,
  className = 'pty-terminal',
  onActivity,
  onTerminalData,
  onFlushPendingInput
}: PtyTerminalSurfaceProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const entryRef = useRef<PtyStoreEntry | undefined>(entry);
  const termRef = useRef<Terminal | null>(null);
  const renderStateRef = useRef<TerminalRenderState>({ seq: -1, ansiLen: 0 });
  const onDataSubscriptionRef = useRef<{ dispose: () => void } | null>(null);
  const onTerminalDataRef = useRef(onTerminalData);
  const onFlushPendingInputRef = useRef(onFlushPendingInput);
  const onActivityRef = useRef(onActivity);
  const lifecycleTokenRef = useRef(0);

  const logLifecycle = useCallback((phase: string, payload?: Record<string, unknown>) => {
    if (!isLifecycleDebugEnabled()) return;
    const stamp = new Date().toISOString();
    console.info('[PTY XTERM LIFECYCLE]', {
      stamp,
      phase,
      ptyId,
      token: lifecycleTokenRef.current,
      ...payload
    });
  }, [ptyId]);

  useEffect(() => {
    entryRef.current = entry;
  }, [entry]);

  useEffect(() => {
    onTerminalDataRef.current = onTerminalData;
  }, [onTerminalData]);

  useEffect(() => {
    onFlushPendingInputRef.current = onFlushPendingInput;
  }, [onFlushPendingInput]);

  useEffect(() => {
    onActivityRef.current = onActivity;
  }, [onActivity]);

  const disposeTerminal = useCallback(() => {
    logLifecycle('dispose_begin', {
      hasTerm: Boolean(termRef.current),
      hasSubscription: Boolean(onDataSubscriptionRef.current)
    });
    const dataSubscription = onDataSubscriptionRef.current;
    if (dataSubscription) {
      try {
        dataSubscription.dispose();
      } catch {
        // ignore dispose errors
      }
      onDataSubscriptionRef.current = null;
    }
    const term = termRef.current;
    if (term) {
      try {
        term.dispose();
      } catch {
        // ignore dispose errors
      }
      termRef.current = null;
    }
    renderStateRef.current = { seq: -1, ansiLen: 0 };
    logLifecycle('dispose_end');
  }, [logLifecycle]);

  const safeWrite = useCallback((payload: string): boolean => {
    if (!payload) return false;
    const term = termRef.current;
    if (!term) return false;
    let shouldFollow = true;
    try {
      const activeBuffer = term.buffer.active;
      shouldFollow = activeBuffer.viewportY >= (activeBuffer.baseY - 1);
    } catch {
      shouldFollow = true;
    }
    try {
      term.write(payload, () => {
        if (shouldFollow) {
          try {
            term.scrollToBottom();
          } catch {
            // ignore
          }
        }
      });
      onActivityRef.current?.();
      return true;
    } catch {
      return false;
    }
  }, []);

  const ensureTerminal = useCallback(() => {
    if (!writable || !running) return;
    const host = hostRef.current;
    if (!host || termRef.current) {
      logLifecycle('ensure_skip', {
        hostReady: Boolean(host),
        hasTerm: Boolean(termRef.current),
        running,
        writable
      });
      return;
    }

    lifecycleTokenRef.current += 1;
    logLifecycle('init_begin', {
      running,
      writable
    });

    const initialEntry = entryRef.current;

    const term = new Terminal({
      convertEol: true,
      disableStdin: false,
      scrollback: 4000,
      cols: PTY_FIXED_COLS,
      rows: PTY_FIXED_ROWS,
      fontFamily: "'SFMono-Regular', 'Menlo', 'Consolas', 'Liberation Mono', 'Courier New', monospace",
      fontSize: 12,
      lineHeight: 1.4,
      theme: {
        background: '#0b0f14',
        foreground: '#e2e8f0',
        cursor: '#e2e8f0'
      }
    });
    term.open(host);
    onDataSubscriptionRef.current = term.onData((data) => {
      onTerminalDataRef.current?.(data);
    });

    termRef.current = term;

    // For writable xterm, always prefer raw ANSI stream to preserve control
    // semantics (CR/BS/CSI). rendered_content is display-friendly text only.
    const initial = initialEntry?.ansi_log || initialEntry?.rendered_content || '';
    if (initial) {
      safeWrite(initial);
    }
    renderStateRef.current = {
      seq: initialEntry?.seq ?? 0,
      ansiLen: initialEntry?.ansi_log?.length ?? 0
    };

    try {
      term.focus();
    } catch {
      // ignore focus errors
    }
    logLifecycle('init_end', {
      initialLen: initial.length,
      seq: initialEntry?.seq ?? 0,
      ansiLen: initialEntry?.ansi_log?.length ?? 0,
      cols: PTY_FIXED_COLS,
      rows: PTY_FIXED_ROWS
    });
  }, [logLifecycle, running, safeWrite, writable]);

  const setHostRef = useCallback((node: HTMLDivElement | null) => {
    if (!node) {
      // In strict mode, transient null refs can happen during reconciliation.
      logLifecycle('host_ref_null');
      return;
    }
    hostRef.current = node;
    logLifecycle('host_ref_set');
    ensureTerminal();
  }, [ensureTerminal, logLifecycle]);

  useEffect(() => {
    if (writable && running) {
      ensureTerminal();
      try {
        termRef.current?.focus();
      } catch {
        // ignore focus errors
      }
      return;
    }
    logLifecycle('dispose_requested_by_state', { running, writable });
    onFlushPendingInputRef.current?.();
    disposeTerminal();
  }, [disposeTerminal, ensureTerminal, logLifecycle, running, writable]);

  useEffect(() => {
    return () => {
      logLifecycle('dispose_requested_by_unmount');
      onFlushPendingInputRef.current?.();
      disposeTerminal();
    };
  }, [disposeTerminal, logLifecycle]);

  useEffect(() => {
    if (!writable || !running) return;
    const term = termRef.current;
    if (!term || !entry) return;
    const renderState = renderStateRef.current;
    if (renderState.seq === entry.seq) return;

    const nextAnsi = entry.ansi_log || '';
    const nextRendered = entry.rendered_content || '';
    const shouldAppend = !entry.reset && nextAnsi.length >= renderState.ansiLen;

    if (entry.reset) {
      const full = nextAnsi || nextRendered;
      const payload = full ? `${TERM_CLEAR_AND_HOME}${full}` : TERM_CLEAR_AND_HOME;
      logLifecycle('render_apply', {
        mode: 'reset_full',
        seq: entry.seq,
        ansiLen: nextAnsi.length,
        payloadLen: payload.length
      });
      safeWrite(payload);
    } else if (shouldAppend) {
      const delta = nextAnsi.slice(renderState.ansiLen);
      logLifecycle('render_apply', {
        mode: 'append_delta',
        seq: entry.seq,
        ansiLen: nextAnsi.length,
        deltaLen: delta.length,
        deltaEscaped: JSON.stringify(delta.length > 120 ? `${delta.slice(0, 120)}...(truncated)` : delta)
      });
      if (delta) safeWrite(delta);
    } else {
      const full = nextAnsi || nextRendered;
      const payload = full ? `${TERM_CLEAR_AND_HOME}${full}` : TERM_CLEAR_AND_HOME;
      logLifecycle('render_apply', {
        mode: 'reconcile_full',
        seq: entry.seq,
        ansiLen: nextAnsi.length,
        payloadLen: payload.length
      });
      safeWrite(payload);
    }

    renderStateRef.current = {
      seq: entry.seq,
      ansiLen: nextAnsi.length
    };
  }, [entry, logLifecycle, ptyId, running, safeWrite, writable]);

  useEffect(() => {
    if (!writable || !running) return;
    try {
      termRef.current?.focus();
    } catch {
      // ignore focus errors
    }
  }, [running, writable]);

  if (!writable || !running) {
    const content = staticText ?? entry?.rendered_content ?? entry?.ansi_log ?? '';
    return <pre className="pty-output-static">{content}</pre>;
  }

  return <div className={className} ref={setHostRef} />;
}

export default memo(PtyTerminalSurface);
