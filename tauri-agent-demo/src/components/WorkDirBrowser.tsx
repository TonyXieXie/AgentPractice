import { Fragment, useEffect, useMemo, useRef, useState, type DragEvent, type MouseEvent } from 'react';
import MarkdownIt from 'markdown-it';
import texmath from 'markdown-it-texmath';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { mkdir, readDir, readFile, readTextFile, watch, writeTextFile, type DirEntry, type UnwatchFn } from '@tauri-apps/plugin-fs';
import { openPath, revealItemInDir } from '@tauri-apps/plugin-opener';
import './WorkDirBrowser.css';

type FileKind = 'text' | 'markdown' | 'image' | 'unknown';

type OpenFile = {
  path: string;
  name: string;
  ext: string;
  kind: FileKind;
  content?: string;
  url?: string;
  loading?: boolean;
  error?: string;
};

type Pane = {
  id: string;
  openFiles: OpenFile[];
  activePath: string | null;
};

type WorkDirBrowserProps = {
  open: boolean;
  rootPath: string;
  openFileRequest?: { path: string; nonce: number } | null;
  onClose?: () => void;
  onPickWorkPath?: () => void;
  onOpenInExplorer?: () => void;
  mode?: 'overlay' | 'window';
  showActions?: boolean;
  showClose?: boolean;
  showHeader?: boolean;
};

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
});

markdown.use(texmath, {
  engine: katex,
  delimiters: ['brackets', 'dollars', 'beg_end'],
  katexOptions: { throwOnError: false, errorColor: '#f87171' },
});

const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg', 'ico']);
const MARKDOWN_EXTENSIONS = new Set(['md', 'markdown', 'mdx']);
const MIN_PANE_RATIO = 0.12;

const createPane = (): Pane => ({
  id: `pane-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
  openFiles: [],
  activePath: null,
});

const ensurePaneSizes = (sizes: Record<string, number>, paneIds: string[]) => {
  if (paneIds.length === 0) return {};
  const next: Record<string, number> = {};
  let total = 0;
  for (const id of paneIds) {
    const value = sizes[id];
    if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
      next[id] = value;
      total += value;
    }
  }
  const missing = paneIds.filter((id) => next[id] == null);
  if (missing.length > 0) {
    const remaining = Math.max(0, 1 - total);
    const fill = remaining > 0 ? remaining / missing.length : 1 / paneIds.length;
    for (const id of missing) {
      next[id] = fill;
    }
    total = paneIds.reduce((sum, id) => sum + (next[id] ?? 0), 0);
  }
  if (total <= 0) {
    const equal = 1 / paneIds.length;
    for (const id of paneIds) {
      next[id] = equal;
    }
    return next;
  }
  for (const id of paneIds) {
    next[id] = (next[id] ?? 0) / total;
  }
  return next;
};

const getFileExt = (name: string) => {
  const trimmed = name.trim();
  const idx = trimmed.lastIndexOf('.');
  if (idx <= 0 || idx === trimmed.length - 1) return '';
  return trimmed.slice(idx + 1).toLowerCase();
};

const getFileKind = (ext: string): FileKind => {
  if (!ext) return 'text';
  if (MARKDOWN_EXTENSIONS.has(ext)) return 'markdown';
  if (IMAGE_EXTENSIONS.has(ext)) return 'image';
  return 'text';
};

const getMimeType = (ext: string) => {
  switch (ext) {
    case 'png':
      return 'image/png';
    case 'jpg':
    case 'jpeg':
      return 'image/jpeg';
    case 'gif':
      return 'image/gif';
    case 'bmp':
      return 'image/bmp';
    case 'webp':
      return 'image/webp';
    case 'svg':
      return 'image/svg+xml';
    case 'ico':
      return 'image/x-icon';
    default:
      return 'application/octet-stream';
  }
};

const joinPath = (base: string, child: string) => {
  if (!base) return child;
  const separator = base.includes('\\') ? '\\' : '/';
  if (base.endsWith('\\') || base.endsWith('/')) {
    return `${base}${child}`;
  }
  return `${base}${separator}${child}`;
};

const normalizePath = (path: string) => {
  const normalized = path.replace(/[\\/]+/g, '/');
  if (normalized === '/' || /^[A-Za-z]:\/$/.test(normalized)) return normalized;
  return normalized.endsWith('/') ? normalized.slice(0, -1) : normalized;
};

const isPathWithin = (child: string, parent: string) => {
  const normalizedChild = normalizePath(child);
  const normalizedParent = normalizePath(parent);
  return normalizedChild === normalizedParent || normalizedChild.startsWith(`${normalizedParent}/`);
};

const normalizeWatchPath = (path: string) => {
  if (!path) return '';
  if (path.startsWith('file://')) {
    try {
      const url = new URL(path);
      let pathname = decodeURIComponent(url.pathname || '');
      if (/^\/[A-Za-z]:\//.test(pathname)) {
        pathname = pathname.slice(1);
      }
      return pathname;
    } catch {
      return path;
    }
  }
  return path;
};

const getBaseName = (path: string) => {
  const trimmed = path.replace(/[\\/]+$/, '');
  const idx = Math.max(trimmed.lastIndexOf('\\'), trimmed.lastIndexOf('/'));
  if (idx < 0) return trimmed;
  return trimmed.slice(idx + 1) || trimmed;
};

const formatError = (error: unknown) => {
  if (!error) return 'Unknown error';
  if (error instanceof Error) return error.message || 'Unknown error';
  return String(error);
};

function WorkDirBrowser({
  open,
  rootPath,
  openFileRequest,
  onClose,
  onPickWorkPath,
  onOpenInExplorer,
  mode = 'overlay',
  showActions = true,
  showClose,
  showHeader = true,
}: WorkDirBrowserProps) {
  const [dirCache, setDirCache] = useState<Record<string, DirEntry[]>>({});
  const [dirLoading, setDirLoading] = useState<Record<string, boolean>>({});
  const [dirErrors, setDirErrors] = useState<Record<string, string>>({});
  const [expandedDirs, setExpandedDirs] = useState<Record<string, boolean>>({});
  const initialPaneRef = useRef<Pane | null>(null);
  if (!initialPaneRef.current) {
    initialPaneRef.current = createPane();
  }
  const [panes, setPanes] = useState<Pane[]>(() => [initialPaneRef.current as Pane]);
  const [activePaneId, setActivePaneId] = useState<string>(() => (initialPaneRef.current as Pane).id);
  const [paneSizes, setPaneSizes] = useState<Record<string, number>>(() => ({
    [(initialPaneRef.current as Pane).id]: 1,
  }));
  const paneSizesRef = useRef<Record<string, number>>({});
  const resizeRef = useRef<{
    leftId: string;
    rightId: string;
    startX: number;
    containerWidth: number;
    leftSize: number;
    rightSize: number;
  } | null>(null);
  const [isResizing, setIsResizing] = useState(false);
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    targetPath: string;
    revealPath: string | null;
  } | null>(null);
  const [newFolderTarget, setNewFolderTarget] = useState<string | null>(null);
  const [newFolderName, setNewFolderName] = useState('');
  const [newFolderError, setNewFolderError] = useState<string | null>(null);
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [newFileTarget, setNewFileTarget] = useState<string | null>(null);
  const [newFileName, setNewFileName] = useState('');
  const [newFileError, setNewFileError] = useState<string | null>(null);
  const [creatingFile, setCreatingFile] = useState(false);
  const previousRootRef = useRef<string>('');
  const panesRef = useRef<Pane[]>([]);
  const createFolderInputRef = useRef<HTMLInputElement>(null);
  const createFileInputRef = useRef<HTMLInputElement>(null);
  const splitContainerRef = useRef<HTMLDivElement>(null);
  const dirLoadingRef = useRef<Record<string, boolean>>({});
  const expandedDirsRef = useRef<Record<string, boolean>>({});
  const refreshTimerRef = useRef<number | null>(null);
  const pendingDirRefreshRef = useRef<Record<string, boolean>>({});
  const unwatchRef = useRef<UnwatchFn | null>(null);

  const rootName = useMemo(() => (rootPath ? getBaseName(rootPath) : ''), [rootPath]);

  const revokeFileUrl = (file: OpenFile) => {
    if (file.url) {
      URL.revokeObjectURL(file.url);
    }
  };

  const resetState = () => {
    const nextPane = createPane();
    setDirCache({});
    setDirLoading({});
    setDirErrors({});
    setExpandedDirs({});
    setPanes((prev) => {
      prev.forEach((pane) => pane.openFiles.forEach(revokeFileUrl));
      return [nextPane];
    });
    setActivePaneId(nextPane.id);
  };

  useEffect(() => {
    if (!open) return;
    if (!rootPath) return;
    if (previousRootRef.current && previousRootRef.current !== rootPath) {
      resetState();
    }
    previousRootRef.current = rootPath;
  }, [open, rootPath]);

  useEffect(() => {
    if (!open || !rootPath) return;
    if (!expandedDirs[rootPath]) {
      setExpandedDirs((prev) => ({ ...prev, [rootPath]: true }));
    }
    if (!dirCache[rootPath] && !dirLoading[rootPath]) {
      void loadDir(rootPath);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, rootPath]);

  useEffect(() => {
    panesRef.current = panes;
  }, [panes]);

  useEffect(() => {
    paneSizesRef.current = paneSizes;
  }, [paneSizes]);

  useEffect(() => {
    dirLoadingRef.current = dirLoading;
  }, [dirLoading]);

  useEffect(() => {
    expandedDirsRef.current = expandedDirs;
  }, [expandedDirs]);

  useEffect(() => {
    if (panes.length === 0) {
      const fallback = createPane();
      setPanes([fallback]);
      setActivePaneId(fallback.id);
      return;
    }
    if (!panes.some((pane) => pane.id === activePaneId)) {
      setActivePaneId(panes[0].id);
    }
  }, [panes, activePaneId]);

  useEffect(() => {
    const ids = panes.map((pane) => pane.id);
    setPaneSizes((prev) => {
      const next = ensurePaneSizes(prev, ids);
      let changed = false;
      if (Object.keys(prev).length !== Object.keys(next).length) {
        changed = true;
      } else {
        for (const id of ids) {
          if (Math.abs((prev[id] ?? 0) - (next[id] ?? 0)) > 0.0001) {
            changed = true;
            break;
          }
        }
      }
      return changed ? next : prev;
    });
  }, [panes]);

  useEffect(() => {
    if (!isResizing) return;
    const handleMove = (event: MouseEvent) => {
      const state = resizeRef.current;
      if (!state) return;
      const delta = (event.clientX - state.startX) / state.containerWidth;
      const pairTotal = state.leftSize + state.rightSize;
      const min = Math.min(MIN_PANE_RATIO, pairTotal * 0.45);
      const clampedLeft = Math.max(min, Math.min(pairTotal - min, state.leftSize + delta));
      const clampedRight = pairTotal - clampedLeft;
      setPaneSizes((prev) => {
        const next = {
          ...prev,
          [state.leftId]: clampedLeft,
          [state.rightId]: clampedRight,
        };
        return ensurePaneSizes(next, panesRef.current.map((pane) => pane.id));
      });
    };
    const handleUp = () => {
      setIsResizing(false);
      resizeRef.current = null;
    };
    window.addEventListener('mousemove', handleMove);
    window.addEventListener('mouseup', handleUp);
    document.body.style.cursor = 'col-resize';
    return () => {
      window.removeEventListener('mousemove', handleMove);
      window.removeEventListener('mouseup', handleUp);
      document.body.style.cursor = '';
    };
  }, [isResizing]);

  useEffect(() => {
    return () => {
      panesRef.current.forEach((pane) => pane.openFiles.forEach(revokeFileUrl));
    };
  }, []);

  useEffect(() => {
    if (!contextMenu) return;
    const dismiss = () => setContextMenu(null);
    window.addEventListener('click', dismiss);
    window.addEventListener('blur', dismiss);
    return () => {
      window.removeEventListener('click', dismiss);
      window.removeEventListener('blur', dismiss);
    };
  }, [contextMenu]);

  useEffect(() => {
    if (!newFolderTarget) return;
    window.setTimeout(() => {
      createFolderInputRef.current?.focus();
      createFolderInputRef.current?.select();
    }, 0);
  }, [newFolderTarget]);

  useEffect(() => {
    if (!newFileTarget) return;
    window.setTimeout(() => {
      createFileInputRef.current?.focus();
      createFileInputRef.current?.select();
    }, 0);
  }, [newFileTarget]);

  const loadDir = async (path: string) => {
    if (dirLoadingRef.current[path]) {
      pendingDirRefreshRef.current[path] = true;
      return;
    }
    dirLoadingRef.current[path] = true;
    setDirLoading((prev) => ({ ...prev, [path]: true }));
    try {
      const entries = await readDir(path);
      const sorted = [...entries].sort((a, b) => {
        if (a.isDirectory !== b.isDirectory) return a.isDirectory ? -1 : 1;
        return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
      });
      setDirCache((prev) => ({ ...prev, [path]: sorted }));
      setDirErrors((prev) => {
        const next = { ...prev };
        delete next[path];
        return next;
      });
    } catch (error) {
      setDirErrors((prev) => ({ ...prev, [path]: formatError(error) }));
    } finally {
      setDirLoading((prev) => {
        const next = { ...prev };
        delete next[path];
        return next;
      });
      delete dirLoadingRef.current[path];
      if (pendingDirRefreshRef.current[path]) {
        delete pendingDirRefreshRef.current[path];
        void loadDir(path);
      }
    }
  };

  const toggleDir = (path: string) => {
    const nextExpanded = !expandedDirs[path];
    setExpandedDirs((prev) => ({ ...prev, [path]: nextExpanded }));
    if (nextExpanded) {
      void loadDir(path);
    }
  };

  const refreshDirsForPaths = (paths?: string[]) => {
    const expanded = expandedDirsRef.current;
    const expandedList = Object.keys(expanded).filter((entryPath) => expanded[entryPath]);
    if (expandedList.length === 0) return;

    if (!paths || paths.length === 0) {
      expandedList.forEach((entryPath) => {
        void loadDir(entryPath);
      });
      return;
    }

    const refreshTargets = expandedList.filter((entryPath) =>
      paths.some((changedPath) => isPathWithin(changedPath, entryPath))
    );

    if (refreshTargets.length === 0) {
      expandedList.forEach((entryPath) => {
        void loadDir(entryPath);
      });
      return;
    }

    refreshTargets.forEach((entryPath) => {
      void loadDir(entryPath);
    });
  };

  const scheduleRefreshDirs = (paths?: string[]) => {
    if (refreshTimerRef.current != null) {
      window.clearTimeout(refreshTimerRef.current);
    }
    const snapshot = paths
      ? paths.map((value) => normalizeWatchPath(value)).filter((value) => value.length > 0)
      : undefined;
    refreshTimerRef.current = window.setTimeout(() => {
      refreshTimerRef.current = null;
      refreshDirsForPaths(snapshot);
    }, 120);
  };

  useEffect(() => {
    if (!open || !rootPath) return;
    let active = true;

    const startWatch = async () => {
      try {
        const unwatch = await watch(
          rootPath,
          (event) => {
            if (!active) return;
            scheduleRefreshDirs(event?.paths);
          },
          { recursive: true, delayMs: 200 }
        );
        if (!active) {
          unwatch();
          return;
        }
        if (unwatchRef.current) {
          unwatchRef.current();
        }
        unwatchRef.current = unwatch;
      } catch (error) {
        // Ignore watch errors; the tree can still be refreshed manually by re-expanding.
      }
    };

    void startWatch();

    return () => {
      active = false;
      if (unwatchRef.current) {
        unwatchRef.current();
        unwatchRef.current = null;
      }
      if (refreshTimerRef.current != null) {
        window.clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, rootPath]);

  const openFileFromExternal = async (rawPath: string) => {
    const path = normalizeWatchPath(rawPath);
    if (!path) return;
    const name = getBaseName(path);
    if (!name) return;

    if (rootPath) {
      const normalizedRoot = normalizePath(rootPath);
      const normalizedFile = normalizePath(path);
      if (isPathWithin(normalizedFile, normalizedRoot)) {
        const relative = normalizedFile.slice(normalizedRoot.length).replace(/^\/+/, '');
        const parts = relative ? relative.split('/') : [];
        const dirParts = parts.slice(0, -1);
        const expandedUpdates: Record<string, boolean> = { [rootPath]: true };
        let current = rootPath;
        for (const part of dirParts) {
          current = joinPath(current, part);
          expandedUpdates[current] = true;
        }
        setExpandedDirs((prev) => ({ ...prev, ...expandedUpdates }));
        void loadDir(rootPath);
        Object.keys(expandedUpdates).forEach((dir) => {
          if (dir !== rootPath) {
            void loadDir(dir);
          }
        });
      }
    }

    void openFile(path, name);
  };

  useEffect(() => {
    if (!open || !openFileRequest?.path) return;
    void openFileFromExternal(openFileRequest.path);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, openFileRequest?.nonce]);

  const findFileLocation = (path: string, list = panesRef.current) => {
    for (const pane of list) {
      const idx = pane.openFiles.findIndex((file) => file.path === path);
      if (idx >= 0) return { paneId: pane.id, index: idx };
    }
    return null;
  };

  const setPaneActivePath = (paneId: string, path: string | null) => {
    setPanes((prev) =>
      prev.map((pane) => (pane.id === paneId ? { ...pane, activePath: path } : pane))
    );
    setActivePaneId(paneId);
  };

  const updateFile = (path: string, update: Partial<OpenFile>) => {
    setPanes((prev) =>
      prev.map((pane) => {
        if (!pane.openFiles.some((file) => file.path === path)) return pane;
        return {
          ...pane,
          openFiles: pane.openFiles.map((file) =>
            file.path === path ? { ...file, ...update } : file
          ),
        };
      })
    );
  };

  const openFile = async (path: string, name: string) => {
    const existing = findFileLocation(path);
    if (existing) {
      setPaneActivePath(existing.paneId, path);
      return;
    }

    const ext = getFileExt(name);
    const kind = getFileKind(ext);
    const newFile: OpenFile = { path, name, ext, kind, loading: true };
    const targetPaneId = activePaneId || panesRef.current[0]?.id;
    if (!targetPaneId) return;

    setPanes((prev) =>
      prev.map((pane) => {
        if (pane.id !== targetPaneId) return pane;
        return {
          ...pane,
          openFiles: [...pane.openFiles, newFile],
          activePath: path,
        };
      })
    );
    setActivePaneId(targetPaneId);

    try {
      if (kind === 'image') {
        const bytes = await readFile(path);
        const url = URL.createObjectURL(new Blob([bytes], { type: getMimeType(ext) }));
        if (!findFileLocation(path)) {
          URL.revokeObjectURL(url);
          return;
        }
        updateFile(path, { url, loading: false });
      } else {
        const text = await readTextFile(path);
        if (!findFileLocation(path)) return;
        updateFile(path, { content: text, loading: false });
      }
    } catch (error) {
      updateFile(path, { error: formatError(error), loading: false });
    }
  };

  const closeFile = (paneId: string, path: string) => {
    let nextActivePaneId: string | null = activePaneId;
    setPanes((prev) => {
      let removedIndex = -1;
      const next = prev.flatMap((pane, idx) => {
        if (pane.id !== paneId) return [pane];
        const fileIndex = pane.openFiles.findIndex((file) => file.path === path);
        if (fileIndex < 0) return [pane];
        revokeFileUrl(pane.openFiles[fileIndex]);
        const nextFiles = pane.openFiles.filter((file) => file.path !== path);
        let nextActive = pane.activePath;
        if (pane.activePath === path) {
          nextActive = nextFiles[fileIndex - 1]?.path || nextFiles[fileIndex]?.path || nextFiles[0]?.path || null;
        }
        if (nextFiles.length === 0 && prev.length > 1) {
          removedIndex = idx;
          return [];
        }
        return [{ ...pane, openFiles: nextFiles, activePath: nextActive }];
      });

      if (removedIndex >= 0 && nextActivePaneId === paneId) {
        if (next.length > 0) {
          const fallbackIndex = Math.min(removedIndex, next.length - 1);
          nextActivePaneId = next[fallbackIndex].id;
        } else {
          nextActivePaneId = null;
        }
      }

      return next;
    });

    if (nextActivePaneId && nextActivePaneId !== activePaneId) {
      setActivePaneId(nextActivePaneId);
    }
  };

  const moveFileToPane = (path: string, fromPaneId: string, toPaneId: string) => {
    if (!path || fromPaneId === toPaneId) {
      setPaneActivePath(toPaneId, path);
      return;
    }
    let movedFile: OpenFile | null = null;
    setPanes((prev) => {
      let removedIndex = -1;
      let next = prev.flatMap((pane, idx) => {
        if (pane.id !== fromPaneId) return [pane];
        const fileIndex = pane.openFiles.findIndex((file) => file.path === path);
        if (fileIndex < 0) return [pane];
        movedFile = pane.openFiles[fileIndex];
        const nextFiles = pane.openFiles.filter((file) => file.path !== path);
        let nextActive = pane.activePath;
        if (pane.activePath === path) {
          nextActive = nextFiles[fileIndex - 1]?.path || nextFiles[fileIndex]?.path || nextFiles[0]?.path || null;
        }
        if (nextFiles.length === 0 && prev.length > 1) {
          removedIndex = idx;
          return [];
        }
        return [{ ...pane, openFiles: nextFiles, activePath: nextActive }];
      });

      if (movedFile) {
        next = next.map((pane) => {
          if (pane.id !== toPaneId) return pane;
          if (pane.openFiles.some((file) => file.path === path)) {
            return { ...pane, activePath: path };
          }
          return {
            ...pane,
            openFiles: [...pane.openFiles, movedFile as OpenFile],
            activePath: path,
          };
        });
      }

      return next;
    });
    setActivePaneId(toPaneId);
  };

  const addSplitPane = () => {
    const newPane = createPane();
    setPanes((prev) => {
      if (prev.length === 0) {
        setPaneSizes({ [newPane.id]: 1 });
        return [newPane];
      }
      const idx = prev.findIndex((pane) => pane.id === activePaneId);
      const insertIndex = idx >= 0 ? idx + 1 : prev.length;
      const nextPanes = [...prev.slice(0, insertIndex), newPane, ...prev.slice(insertIndex)];
      const targetId = idx >= 0 ? prev[idx].id : prev[0].id;
      setPaneSizes((sizes) => {
        const current = sizes[targetId] ?? 1 / Math.max(1, prev.length);
        const half = current / 2;
        const next = {
          ...sizes,
          [targetId]: half,
          [newPane.id]: half,
        };
        return ensurePaneSizes(next, nextPanes.map((pane) => pane.id));
      });
      return nextPanes;
    });
    setActivePaneId(newPane.id);
  };

  const openCreateFolderDialog = (targetPath: string) => {
    if (!targetPath) return;
    setContextMenu(null);
    setNewFolderTarget(targetPath);
    setNewFolderName('');
    setNewFolderError(null);
    setNewFileTarget(null);
    setNewFileName('');
    setNewFileError(null);
  };

  const handleCreateFolder = async () => {
    if (creatingFolder) return;
    const basePath = newFolderTarget || rootPath;
    if (!basePath) return;
    const trimmed = newFolderName.trim();
    if (!trimmed) {
      setNewFolderError('请输入文件夹名称。');
      return;
    }
    if (/[\\/]/.test(trimmed)) {
      setNewFolderError('名称不能包含路径分隔符。');
      return;
    }
    setCreatingFolder(true);
    setNewFolderError(null);
    try {
      const nextPath = joinPath(basePath, trimmed);
      await mkdir(nextPath, { recursive: false });
      setExpandedDirs((prev) => ({ ...prev, [basePath]: true }));
      await loadDir(basePath);
      setNewFolderTarget(null);
      setNewFolderName('');
    } catch (error) {
      setNewFolderError(formatError(error));
    } finally {
      setCreatingFolder(false);
    }
  };

  const openCreateFileDialog = (targetPath: string) => {
    if (!targetPath) return;
    setContextMenu(null);
    setNewFileTarget(targetPath);
    setNewFileName('');
    setNewFileError(null);
    setNewFolderTarget(null);
    setNewFolderName('');
    setNewFolderError(null);
  };

  const handleCreateFile = async () => {
    if (creatingFile) return;
    const basePath = newFileTarget || rootPath;
    if (!basePath) return;
    const trimmed = newFileName.trim();
    if (!trimmed) {
      setNewFileError('请输入文件名称。');
      return;
    }
    if (/[\\/]/.test(trimmed)) {
      setNewFileError('名称不能包含路径分隔符。');
      return;
    }
    setCreatingFile(true);
    setNewFileError(null);
    try {
      const nextPath = joinPath(basePath, trimmed);
      await writeTextFile(nextPath, '', { createNew: true });
      setExpandedDirs((prev) => ({ ...prev, [basePath]: true }));
      await loadDir(basePath);
      setNewFileTarget(null);
      setNewFileName('');
    } catch (error) {
      setNewFileError(formatError(error));
    } finally {
      setCreatingFile(false);
    }
  };

  const handleOpenInExplorer = async (path: string) => {
    try {
      await revealItemInDir(path);
      return;
    } catch (error) {
      try {
        await openPath(path);
      } catch (inner) {
        console.error('Failed to open in explorer:', inner ?? error);
      }
    }
  };

  const activePane = panes.find((pane) => pane.id === activePaneId) || panes[0] || null;
  const activePath = activePane?.activePath ?? null;

  const handleTabDragStart = (event: DragEvent<HTMLDivElement>, paneId: string, path: string) => {
    event.dataTransfer.setData('application/x-workdir-file', JSON.stringify({ paneId, path }));
    event.dataTransfer.setData('text/plain', path);
    event.dataTransfer.effectAllowed = 'move';
  };

  const handlePaneDrop = (event: DragEvent<HTMLDivElement>, paneId: string) => {
    event.preventDefault();
    const raw =
      event.dataTransfer.getData('application/x-workdir-file') || event.dataTransfer.getData('text/plain');
    if (!raw) return;
    let payload: { paneId?: string; path?: string } | null = null;
    try {
      payload = JSON.parse(raw);
    } catch {
      payload = { path: raw };
    }
    if (!payload?.path || !payload.paneId) return;
    moveFileToPane(payload.path, payload.paneId, paneId);
  };

  const handleSplitterMouseDown = (
    event: MouseEvent<HTMLDivElement>,
    leftId: string,
    rightId: string
  ) => {
    if (event.button !== 0) return;
    event.preventDefault();
    const container = splitContainerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const sizes = paneSizesRef.current;
    const defaultSize = 1 / Math.max(1, panesRef.current.length || 1);
    const leftSize = sizes[leftId] ?? defaultSize;
    const rightSize = sizes[rightId] ?? defaultSize;
    resizeRef.current = {
      leftId,
      rightId,
      startX: event.clientX,
      containerWidth: rect.width,
      leftSize,
      rightSize,
    };
    setIsResizing(true);
  };

  const renderEntries = (parentPath: string, depth: number) => {
    const entries = dirCache[parentPath];
    if (!entries) return null;

    if (!entries.length) {
      return (
        <div className="workdir-tree-empty" style={{ paddingLeft: depth * 14 + 10 }}>
          (空目录)
        </div>
      );
    }

    return entries.map((entry) => {
      const entryPath = joinPath(parentPath, entry.name);
      const isExpanded = !!expandedDirs[entryPath];
      const isActive = activePath === entryPath;
      const isDir = entry.isDirectory;

      return (
        <div key={entryPath}>
          <div
            className={`workdir-tree-row${isActive ? ' active' : ''}`}
            style={{ paddingLeft: depth * 14 + 6 }}
            onClick={() => {
              if (isDir) {
                toggleDir(entryPath);
              } else {
                void openFile(entryPath, entry.name);
              }
            }}
            onContextMenu={(event) => {
              if (!rootPath) return;
              event.preventDefault();
              event.stopPropagation();
              const targetPath = isDir ? entryPath : parentPath;
              setContextMenu({
                x: event.clientX,
                y: event.clientY,
                targetPath,
                revealPath: isDir ? entryPath : null,
              });
            }}
          >
            <span className={`workdir-tree-toggle${isDir ? '' : ' hidden'}${isExpanded ? ' expanded' : ''}`}>
              ▸
            </span>
            <span className={`workdir-tree-name${isDir ? ' dir' : ''}`}>{entry.name}</span>
          </div>
          {isDir && isExpanded && (
            <div className="workdir-tree-children">
              {dirErrors[entryPath] && (
                <div className="workdir-tree-error" style={{ paddingLeft: (depth + 1) * 14 + 6 }}>
                  读取失败：{dirErrors[entryPath]}
                </div>
              )}
              {dirLoading[entryPath] && (
                <div className="workdir-tree-loading" style={{ paddingLeft: (depth + 1) * 14 + 6 }}>
                  加载中...
                </div>
              )}
              {renderEntries(entryPath, depth + 1)}
            </div>
          )}
        </div>
      );
    });
  };

  if (!open) return null;

  const closeVisible = showClose ?? mode === 'overlay';

  const headerVisible = showHeader;

  const content = (
    <div
      className={`workdir-window${mode === 'window' ? ' embedded' : ''}${headerVisible ? '' : ' no-header'}`}
      onClick={(event) => {
        event.stopPropagation();
        if (contextMenu) setContextMenu(null);
      }}
    >
      {headerVisible && (
        <div className="workdir-header">
          <div className="workdir-header-title">
            <h2>工作目录</h2>
            <span className="workdir-path" title={rootPath}>
              {rootPath || '未设置'}
            </span>
          </div>
          <div className="workdir-header-actions">
            {showActions && onOpenInExplorer && rootPath && (
              <button type="button" className="workdir-header-btn" onClick={onOpenInExplorer}>
                在资源管理器打开
              </button>
            )}
            {showActions && onPickWorkPath && (
              <button type="button" className="workdir-header-btn" onClick={onPickWorkPath}>
                重新选择
              </button>
            )}
            {closeVisible && onClose && (
              <button type="button" className="workdir-close-btn" onClick={onClose}>
                ×
              </button>
            )}
          </div>
        </div>
      )}

      {!rootPath ? (
        <div className="workdir-empty-state">
          <p>请先选择一个工作目录。</p>
          {showActions && onPickWorkPath && (
            <button type="button" className="workdir-primary-btn" onClick={onPickWorkPath}>
              选择工作目录
            </button>
          )}
        </div>
      ) : (
        <div className="workdir-body">
          <div className="workdir-sidebar">
            <div className="workdir-sidebar-title">资源管理器</div>
            <div
              className="workdir-tree"
              onContextMenu={(event) => {
                if (!rootPath) return;
                const target = event.target as HTMLElement;
                if (target.closest('.workdir-tree-row')) return;
                event.preventDefault();
                setContextMenu({
                  x: event.clientX,
                  y: event.clientY,
                  targetPath: rootPath,
                  revealPath: rootPath,
                });
              }}
            >
              <div
                className="workdir-tree-row root"
                onClick={() => toggleDir(rootPath)}
                onContextMenu={(event) => {
                  if (!rootPath) return;
                  event.preventDefault();
                  event.stopPropagation();
                  setContextMenu({
                    x: event.clientX,
                    y: event.clientY,
                    targetPath: rootPath,
                    revealPath: rootPath,
                  });
                }}
              >
                <span className={`workdir-tree-toggle${expandedDirs[rootPath] ? ' expanded' : ''}`}>▸</span>
                <span className="workdir-tree-name dir">{rootName || rootPath}</span>
              </div>
              {dirErrors[rootPath] && (
                <div className="workdir-tree-error" style={{ paddingLeft: 20 }}>
                  读取失败：{dirErrors[rootPath]}
                </div>
              )}
              {dirLoading[rootPath] && (
                <div className="workdir-tree-loading" style={{ paddingLeft: 20 }}>
                  加载中...
                </div>
              )}
              {expandedDirs[rootPath] && renderEntries(rootPath, 1)}
            </div>
          </div>

          <div className="workdir-editor">
            <div
              className={`workdir-split-container${isResizing ? ' resizing' : ''}`}
              ref={splitContainerRef}
            >
              {panes.map((pane, index) => {
                const paneActiveFile = pane.activePath
                  ? pane.openFiles.find((file) => file.path === pane.activePath) || null
                  : null;
                const paneSize = paneSizes[pane.id] ?? 1 / Math.max(1, panes.length);
                return (
                  <Fragment key={pane.id}>
                    <div
                      className={`workdir-pane${pane.id === activePaneId ? ' active' : ''}`}
                      style={{ flex: `0 0 ${(paneSize * 100).toFixed(4)}%` }}
                      onMouseDown={() => setActivePaneId(pane.id)}
                      onDragOver={(event) => {
                        event.preventDefault();
                        event.dataTransfer.dropEffect = 'move';
                      }}
                      onDrop={(event) => handlePaneDrop(event, pane.id)}
                    >
                      <div className="workdir-tabs">
                        <div className="workdir-tabs-list">
                          {pane.openFiles.length === 0 && (
                            <span className="workdir-tabs-empty">尚未打开文件</span>
                          )}
                          {pane.openFiles.map((file) => (
                            <div
                              key={file.path}
                              className={`workdir-tab${file.path === pane.activePath ? ' active' : ''}`}
                              onClick={() => setPaneActivePath(pane.id, file.path)}
                              draggable
                              onDragStart={(event) => handleTabDragStart(event, pane.id, file.path)}
                            >
                              <span className="workdir-tab-label">{file.name}</span>
                              <button
                                type="button"
                                className="workdir-tab-close"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  closeFile(pane.id, file.path);
                                }}
                                aria-label={`关闭 ${file.name}`}
                                title="关闭"
                              >
                                ×
                              </button>
                            </div>
                          ))}
                        </div>
                        <div className="workdir-tabs-actions">
                          <button
                            type="button"
                            className="workdir-split-btn"
                            onClick={addSplitPane}
                            aria-label="一键分屏"
                            title="一键分屏"
                          >
                            <svg viewBox="0 0 16 16" aria-hidden="true">
                              <rect
                                x="2.3"
                                y="3"
                                width="4.9"
                                height="10"
                                rx="1"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="1.2"
                              />
                              <rect
                                x="8.8"
                                y="3"
                                width="4.9"
                                height="10"
                                rx="1"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="1.2"
                              />
                            </svg>
                          </button>
                        </div>
                      </div>

                      <div className="workdir-viewer">
                        {!paneActiveFile && (
                          <div className="workdir-placeholder">
                            <p>从左侧选择文件进行预览。</p>
                          </div>
                        )}
                        {paneActiveFile && paneActiveFile.loading && (
                          <div className="workdir-placeholder">
                            <p>正在加载 {paneActiveFile.name} ...</p>
                          </div>
                        )}
                        {paneActiveFile && paneActiveFile.error && (
                          <div className="workdir-error">
                            无法打开文件：{paneActiveFile.error}
                          </div>
                        )}
                        {paneActiveFile &&
                          !paneActiveFile.loading &&
                          !paneActiveFile.error &&
                          paneActiveFile.kind === 'image' && (
                            <div className="workdir-image-viewer">
                              {paneActiveFile.url ? (
                                <img src={paneActiveFile.url} alt={paneActiveFile.name} />
                              ) : (
                                <span>图片加载失败。</span>
                              )}
                            </div>
                          )}
                        {paneActiveFile &&
                          !paneActiveFile.loading &&
                          !paneActiveFile.error &&
                          paneActiveFile.kind === 'markdown' && (
                            <div
                              className="workdir-markdown"
                              dangerouslySetInnerHTML={{ __html: markdown.render(paneActiveFile.content || '') }}
                            />
                          )}
                      {paneActiveFile &&
                        !paneActiveFile.loading &&
                        !paneActiveFile.error &&
                        paneActiveFile.kind === 'text' && (
                          <div className="workdir-text-viewer">
                            {(() => {
                              const lines = (paneActiveFile.content ?? '').split('\n');
                              const width = String(Math.max(1, lines.length)).length;
                              return lines.map((line, index) => (
                                <div key={`${paneActiveFile.path}-line-${index + 1}`} className="workdir-text-line">
                                  <span className="workdir-text-gutter" style={{ width: `${width}ch` }}>
                                    {index + 1}
                                  </span>
                                  <span className="workdir-text-content">{line || ' '}</span>
                                </div>
                              ));
                            })()}
                          </div>
                        )}
                      </div>
                    </div>
                    {index < panes.length - 1 && (
                      <div
                        className="workdir-splitter"
                        onMouseDown={(event) => handleSplitterMouseDown(event, pane.id, panes[index + 1].id)}
                      />
                    )}
                  </Fragment>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {contextMenu && (
        <div
          className="workdir-context-menu"
          style={{
            top: Math.min(contextMenu.y, window.innerHeight - 120),
            left: Math.min(contextMenu.x, window.innerWidth - 200),
          }}
          onClick={(event) => event.stopPropagation()}
          onContextMenu={(event) => event.preventDefault()}
        >
          {contextMenu.revealPath && (
            <button
              type="button"
              className="workdir-context-item"
              onClick={() => {
                const path = contextMenu.revealPath;
                setContextMenu(null);
                if (path) void handleOpenInExplorer(path);
              }}
            >
              在资源管理器打开
            </button>
          )}
          <button
            type="button"
            className="workdir-context-item"
            onClick={() => openCreateFolderDialog(contextMenu.targetPath)}
          >
            新建文件夹
          </button>
          <button
            type="button"
            className="workdir-context-item"
            onClick={() => openCreateFileDialog(contextMenu.targetPath)}
          >
            新建文件
          </button>
        </div>
      )}

      {newFolderTarget && (
        <div
          className="workdir-dialog-backdrop"
          onClick={() => {
            if (!creatingFolder) setNewFolderTarget(null);
          }}
        >
          <div className="workdir-dialog" onClick={(event) => event.stopPropagation()}>
            <div className="workdir-dialog-title">新建文件夹</div>
            <div className="workdir-dialog-body">
              <input
                ref={createFolderInputRef}
                type="text"
                value={newFolderName}
                onChange={(event) => setNewFolderName(event.currentTarget.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    void handleCreateFolder();
                  }
                  if (event.key === 'Escape') {
                    event.preventDefault();
                    if (!creatingFolder) setNewFolderTarget(null);
                  }
                }}
                placeholder="输入文件夹名称"
                className="workdir-dialog-input"
              />
              {newFolderError && <div className="workdir-dialog-error">{newFolderError}</div>}
            </div>
            <div className="workdir-dialog-actions">
              <button
                type="button"
                className="workdir-dialog-btn ghost"
                onClick={() => setNewFolderTarget(null)}
                disabled={creatingFolder}
              >
                取消
              </button>
              <button
                type="button"
                className="workdir-dialog-btn primary"
                onClick={() => void handleCreateFolder()}
                disabled={creatingFolder}
              >
                {creatingFolder ? '创建中...' : '创建'}
              </button>
            </div>
          </div>
        </div>
      )}

      {newFileTarget && (
        <div
          className="workdir-dialog-backdrop"
          onClick={() => {
            if (!creatingFile) setNewFileTarget(null);
          }}
        >
          <div className="workdir-dialog" onClick={(event) => event.stopPropagation()}>
            <div className="workdir-dialog-title">新建文件</div>
            <div className="workdir-dialog-body">
              <input
                ref={createFileInputRef}
                type="text"
                value={newFileName}
                onChange={(event) => setNewFileName(event.currentTarget.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    void handleCreateFile();
                  }
                  if (event.key === 'Escape') {
                    event.preventDefault();
                    if (!creatingFile) setNewFileTarget(null);
                  }
                }}
                placeholder="输入文件名称"
                className="workdir-dialog-input"
              />
              {newFileError && <div className="workdir-dialog-error">{newFileError}</div>}
            </div>
            <div className="workdir-dialog-actions">
              <button
                type="button"
                className="workdir-dialog-btn ghost"
                onClick={() => setNewFileTarget(null)}
                disabled={creatingFile}
              >
                取消
              </button>
              <button
                type="button"
                className="workdir-dialog-btn primary"
                onClick={() => void handleCreateFile()}
                disabled={creatingFile}
              >
                {creatingFile ? '创建中...' : '创建'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );

  if (mode === 'overlay') {
    return (
      <div className="workdir-overlay" onClick={() => onClose && onClose()}>
        {content}
      </div>
    );
  }

  return <div className="workdir-embedded">{content}</div>;
}

export default WorkDirBrowser;
