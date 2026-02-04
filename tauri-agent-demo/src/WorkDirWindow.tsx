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

function WorkDirWindow() {
  const [rootPath, setRootPath] = useState(getInitialPath);
  const [ping, setPing] = useState(false);
  const appWindow = useMemo(() => getCurrentWindow(), []);

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

  return (
    <div className={`workdir-standalone${ping ? ' ping' : ''}`}>
      <WorkDirBrowser open rootPath={rootPath} mode="window" showActions={false} showClose={false} />
    </div>
  );
}

export default WorkDirWindow;
