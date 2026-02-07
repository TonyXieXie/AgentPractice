import { useEffect, useMemo, useState } from 'react';
import { listen } from '@tauri-apps/api/event';
import { getCurrentWindow } from '@tauri-apps/api/window';
import WorkDirBrowser from './components/WorkDirBrowser';
import './WorkDirWindow.css';

const WORKDIR_BOUNDS_KEY = 'workdirWindowBounds';

const getInitialPath = () => {
  const params = new URLSearchParams(window.location.search);
  const raw = params.get('path');
  if (!raw) return '';
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
};

const getInitialOpenFile = () => {
  const params = new URLSearchParams(window.location.search);
  const raw = params.get('open');
  if (!raw) return '';
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
};

const getInitialOpenLine = () => {
  const params = new URLSearchParams(window.location.search);
  const raw = params.get('line');
  if (!raw) return undefined;
  const value = Number(raw);
  return Number.isFinite(value) ? value : undefined;
};

const getInitialOpenColumn = () => {
  const params = new URLSearchParams(window.location.search);
  const raw = params.get('col');
  if (!raw) return undefined;
  const value = Number(raw);
  return Number.isFinite(value) ? value : undefined;
};

function WorkDirWindow() {
  const [rootPath, setRootPath] = useState(getInitialPath);
  const [openFileRequest, setOpenFileRequest] = useState(() => {
    const initial = getInitialOpenFile();
    const line = getInitialOpenLine();
    const column = getInitialOpenColumn();
    return initial ? { path: initial, line, column, nonce: Date.now() } : null;
  });
  const [ping, setPing] = useState(false);
  const [isMaximized, setIsMaximized] = useState(false);
  const appWindow = useMemo(() => getCurrentWindow(), []);

  useEffect(() => {
    if (!/mac/i.test(navigator.userAgent)) return;
    document.body.dataset.platform = 'mac';
  }, []);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    const label = (appWindow as { label?: string }).label;
    listen<{ path: string; target?: string }>('workdir:set', (event) => {
      if (label && event.payload?.target && event.payload.target !== label) return;
      setRootPath(event.payload?.path || '');
    }).then((stop) => {
      unlisten = stop;
    });
    return () => {
      if (unlisten) unlisten();
    };
  }, [appWindow]);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    const label = (appWindow as { label?: string }).label;
    listen<{ target?: string }>('workdir:ping', (event) => {
      if (label && event.payload?.target && event.payload.target !== label) return;
      setPing(true);
      window.setTimeout(() => setPing(false), 800);
    }).then((stop) => {
      unlisten = stop;
    });
    return () => {
      if (unlisten) unlisten();
    };
  }, [appWindow]);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    const label = (appWindow as { label?: string }).label;
    listen<{ path: string; line?: number; column?: number; target?: string }>('workdir:open-file', (event) => {
      if (label && event.payload?.target && event.payload.target !== label) return;
      const path = event.payload?.path;
      if (!path) return;
      setOpenFileRequest({ path, line: event.payload?.line, column: event.payload?.column, nonce: Date.now() });
    }).then((stop) => {
      unlisten = stop;
    });
    return () => {
      if (unlisten) unlisten();
    };
  }, [appWindow]);

  useEffect(() => {
    let stopped = false;
    const saveBounds = async () => {
      try {
        const [pos, size] = await Promise.all([appWindow.outerPosition(), appWindow.outerSize()]);
        const payload = { x: pos.x, y: pos.y, width: size.width, height: size.height };
        localStorage.setItem(WORKDIR_BOUNDS_KEY, JSON.stringify(payload));
      } catch {
        // ignore
      }
    };
    const interval = window.setInterval(() => {
      if (stopped) return;
      void saveBounds();
    }, 1500);
    const handleBeforeUnload = () => {
      void saveBounds();
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => {
      stopped = true;
      window.clearInterval(interval);
      window.removeEventListener('beforeunload', handleBeforeUnload);
    };
  }, [appWindow]);

  useEffect(() => {
    let cancelled = false;
    const syncMaximize = async () => {
      try {
        const next = await appWindow.isMaximized();
        if (!cancelled) {
          setIsMaximized(next);
        }
      } catch {
        // ignore
      }
    };
    syncMaximize();
    let unlisten: (() => void) | null = null;
    appWindow.onResized(() => {
      syncMaximize();
    }).then((stop) => {
      unlisten = stop;
    });
    return () => {
      cancelled = true;
      if (unlisten) unlisten();
    };
  }, [appWindow]);

  const handleTitlebarMinimize = async () => {
    try {
      await appWindow.minimize();
    } catch {
      // ignore
    }
  };

  const handleTitlebarMaximize = async () => {
    try {
      await appWindow.toggleMaximize();
      const next = await appWindow.isMaximized();
      setIsMaximized(next);
    } catch {
      // ignore
    }
  };

  const handleTitlebarClose = async () => {
    try {
      await appWindow.close();
    } catch {
      // ignore
    }
  };

  const handleTitlebarDoubleClick = (event: React.MouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    if (target.closest('.workdir-titlebar-actions') || target.closest('.workdir-titlebar-btn')) {
      return;
    }
    handleTitlebarMaximize();
  };

  const titlePath = rootPath || '未设置';

  return (
    <div className={`workdir-shell${ping ? ' ping' : ''}`}>
      <div className="workdir-titlebar" data-tauri-drag-region onDoubleClick={handleTitlebarDoubleClick}>
        <div className="workdir-titlebar-left">
          <div className="workdir-titlebar-appname">GYY</div>
          <div className="workdir-titlebar-divider" />
          <div className="workdir-titlebar-path" title={titlePath}>
            {titlePath}
          </div>
        </div>
        <div className="workdir-titlebar-actions" data-tauri-drag-region="false">
          <button
            type="button"
            className="workdir-titlebar-btn"
            data-tauri-drag-region="false"
            onClick={handleTitlebarMinimize}
            aria-label="Minimize"
            title="Minimize"
          >
            <svg viewBox="0 0 12 12" aria-hidden="true">
              <rect x="2" y="6" width="8" height="1.2" rx="0.6" fill="currentColor" />
            </svg>
          </button>
          <button
            type="button"
            className="workdir-titlebar-btn"
            data-tauri-drag-region="false"
            onClick={handleTitlebarMaximize}
            aria-label={isMaximized ? 'Restore' : 'Maximize'}
            title={isMaximized ? 'Restore' : 'Maximize'}
          >
            {isMaximized ? (
              <svg viewBox="0 0 12 12" aria-hidden="true">
                <path
                  d="M4 3h5a1 1 0 0 1 1 1v5M3 4a1 1 0 0 1 1-1h4v1H4v4H3z"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1"
                />
                <rect x="3" y="4" width="5" height="5" fill="none" stroke="currentColor" strokeWidth="1" />
              </svg>
            ) : (
              <svg viewBox="0 0 12 12" aria-hidden="true">
                <rect x="3" y="3" width="6" height="6" fill="none" stroke="currentColor" strokeWidth="1" />
              </svg>
            )}
          </button>
          <button
            type="button"
            className="workdir-titlebar-btn close"
            data-tauri-drag-region="false"
            onClick={handleTitlebarClose}
            aria-label="Close"
            title="Close"
          >
            <svg viewBox="0 0 12 12" aria-hidden="true">
              <path
                d="M3.2 3.2l5.6 5.6M8.8 3.2l-5.6 5.6"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.4"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </div>
      </div>
      <div className="workdir-content">
        <WorkDirBrowser
          open
          rootPath={rootPath}
          openFileRequest={openFileRequest}
          mode="window"
          showActions={false}
          showClose={false}
          showHeader={false}
        />
      </div>
    </div>
  );
}

export default WorkDirWindow;
