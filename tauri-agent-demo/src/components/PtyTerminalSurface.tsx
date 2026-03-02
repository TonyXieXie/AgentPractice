import { memo, useCallback, useEffect, useRef } from 'react';
import { Terminal } from 'xterm';
import { FitAddon } from 'xterm-addon-fit';
import 'xterm/css/xterm.css';
import type { PtyStoreEntry } from '../ptyStore';

const TERM_CLEAR_AND_HOME = '\x1b[2J\x1b[H';

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
  const fitRef = useRef<FitAddon | null>(null);
  const renderStateRef = useRef<TerminalRenderState>({ seq: -1, ansiLen: 0 });
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const fitRafRef = useRef<number | null>(null);
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

  const clearPendingFit = useCallback(() => {
    if (fitRafRef.current !== null && typeof window !== 'undefined') {
      window.cancelAnimationFrame(fitRafRef.current);
      fitRafRef.current = null;
    }
  }, []);

  const scheduleFit = useCallback(() => {
    if (typeof window === 'undefined') return;
    if (fitRafRef.current !== null) return;
    fitRafRef.current = window.requestAnimationFrame(() => {
      fitRafRef.current = null;
      const fit = fitRef.current;
      if (!fit) return;
      try {
        fit.fit();
      } catch {
        // ignore fit race after disposal
      }
    });
  }, []);

  const disposeTerminal = useCallback(() => {
    logLifecycle('dispose_begin', {
      hasTerm: Boolean(termRef.current),
      hasObserver: Boolean(resizeObserverRef.current),
      hasSubscription: Boolean(onDataSubscriptionRef.current)
    });
    clearPendingFit();
    const dataSubscription = onDataSubscriptionRef.current;
    if (dataSubscription) {
      try {
        dataSubscription.dispose();
      } catch {
        // ignore dispose errors
      }
      onDataSubscriptionRef.current = null;
    }
    const observer = resizeObserverRef.current;
    if (observer) {
      observer.disconnect();
      resizeObserverRef.current = null;
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
    fitRef.current = null;
    renderStateRef.current = { seq: -1, ansiLen: 0 };
    logLifecycle('dispose_end');
  }, [clearPendingFit, logLifecycle]);

  const safeWrite = useCallback((payload: string): boolean => {
    if (!payload) return false;
    const term = termRef.current;
    if (!term) return false;
    try {
      term.write(payload, () => {
        try {
          term.scrollToBottom();
        } catch {
          // ignore
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
    term.open(host);
    onDataSubscriptionRef.current = term.onData((data) => {
      onTerminalDataRef.current?.(data);
    });

    termRef.current = term;
    fitRef.current = fit;

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

    scheduleFit();
    try {
      term.focus();
    } catch {
      // ignore focus errors
    }

    if (typeof window !== 'undefined' && typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(() => {
        scheduleFit();
      });
      observer.observe(host);
      resizeObserverRef.current = observer;
    }
    logLifecycle('init_end', {
      initialLen: initial.length,
      seq: initialEntry?.seq ?? 0,
      ansiLen: initialEntry?.ansi_log?.length ?? 0
    });
  }, [logLifecycle, running, safeWrite, scheduleFit, writable]);

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
      safeWrite(payload);
    } else if (shouldAppend) {
      const delta = nextAnsi.slice(renderState.ansiLen);
      if (delta) safeWrite(delta);
    } else {
      const full = nextAnsi || nextRendered;
      const payload = full ? `${TERM_CLEAR_AND_HOME}${full}` : TERM_CLEAR_AND_HOME;
      safeWrite(payload);
    }

    renderStateRef.current = {
      seq: entry.seq,
      ansiLen: nextAnsi.length
    };
  }, [entry, ptyId, running, safeWrite, writable]);

  useEffect(() => {
    if (!writable || !running) return;
    scheduleFit();
    try {
      termRef.current?.focus();
    } catch {
      // ignore focus errors
    }
  }, [running, scheduleFit, writable]);

  if (!writable || !running) {
    const content = staticText ?? entry?.rendered_content ?? entry?.ansi_log ?? '';
    return <pre className="pty-output-static">{content}</pre>;
  }

  return <div className={className} ref={setHostRef} />;
}

export default memo(PtyTerminalSurface);
