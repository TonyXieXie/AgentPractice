import {
  Fragment,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react';
import MarkdownIt from 'markdown-it';
import mermaid from 'mermaid';
import texmath from 'markdown-it-texmath';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { open as openDialog } from '@tauri-apps/plugin-dialog';
import { mkdir, readDir, readFile, readTextFile, watchImmediate, writeTextFile, type DirEntry, type UnwatchFn } from '@tauri-apps/plugin-fs';
import { API_BASE_URL, notifyAstChanges, getAstSettings, updateAstSettings } from '../api';
import { openPath, openUrl, revealItemInDir } from '@tauri-apps/plugin-opener';
import './WorkDirBrowser.css';

type FileKind = 'text' | 'markdown' | 'image' | 'pdf' | 'unknown';

const IS_MAC = typeof navigator !== 'undefined' && /mac/i.test(navigator.userAgent);
const MAC_ABSOLUTE_PREFIX = /^(Users|Volumes|private|System|Library|Applications|opt|etc|var|tmp)[\\/]/;
const READ_TEXT_TIMEOUT_MS = 3000;
const READ_BACKEND_TIMEOUT_MS = 4000;
const AST_DEFAULT_MAX_FILES = 500;
const AST_LANGUAGE_OPTIONS = [
  { id: 'python', label: 'Python (.py)' },
  { id: 'javascript', label: 'JavaScript (.js/.jsx/.mjs/.cjs)' },
  { id: 'typescript', label: 'TypeScript (.ts)' },
  { id: 'tsx', label: 'TSX (.tsx)' },
  { id: 'c', label: 'C (.c)' },
  { id: 'cpp', label: 'C++ (.h/.hpp/.cpp...)' },
  { id: 'rust', label: 'Rust (.rs)' },
  { id: 'json', label: 'JSON (.json)' },
];

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

type EditorState = {
  value: string;
  base: string;
  dirty: boolean;
  saving: boolean;
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
  extraRoots?: string[];
  openFileRequest?: { path: string; line?: number; column?: number; nonce: number } | null;
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

const defaultFence = markdown.renderer.rules.fence;
markdown.renderer.rules.fence = (tokens, idx, options, env, self) => {
  const token = tokens[idx];
  const info = (token.info || '').trim();
  const lang = info ? info.split(/\s+/g)[0].toLowerCase() : '';
  if (lang === 'mermaid') {
    const code = markdown.utils.escapeHtml(token.content || '');
    const encoded = encodeURIComponent(token.content || '');
    return [
      '<div class="mermaid-block">',
      '<div class="mermaid-controls">',
      '<button type="button" class="mermaid-control" data-action="zoom-in" aria-label="Zoom in">+</button>',
      '<button type="button" class="mermaid-control" data-action="zoom-out" aria-label="Zoom out">-</button>',
      '<button type="button" class="mermaid-control" data-action="reset" aria-label="Reset zoom">Reset</button>',
      '</div>',
      '<div class="mermaid-viewport">',
      `<div class="mermaid" data-raw="${encoded}">${code}</div>`,
      '</div>',
      '</div>',
    ].join('');
  }
  if (defaultFence) {
    return defaultFence(tokens, idx, options, env, self);
  }
  return self.renderToken(tokens, idx, options);
};

const MERMAID_START_RE =
  /^(?:graph|flowchart|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|gantt|journey|pie|gitGraph|mindmap|timeline|quadrantChart|sankey-beta)\b/i;
const MERMAID_HINT_RE =
  /(-->|==>|subgraph|participant|class|state|section|task|journey|pie|gantt|mindmap|timeline|quadrantChart|sankey|flowchart|graph)\b/i;

const injectMermaidFences = (content: string) => {
  if (!content) return content;
  if (/```mermaid/i.test(content)) return content;
  const normalized = content.replace(/\r\n/g, '\n');
  const parts = normalized.split(/\n{2,}/);
  let changed = false;
  const next = parts.map((part) => {
    const trimmed = part.trim();
    if (!trimmed) return part;
    if (MERMAID_START_RE.test(trimmed) && MERMAID_HINT_RE.test(trimmed)) {
      changed = true;
      return `\`\`\`mermaid\n${trimmed}\n\`\`\``;
    }
    return part;
  });
  return changed ? next.join('\n\n') : normalized;
};

const renderMarkdownHtml = (content: string) => markdown.render(injectMermaidFences(content || ''));

function WorkdirMarkdown({
  content,
  onClick,
}: {
  content: string;
  onClick?: (event: ReactMouseEvent<HTMLDivElement>) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const html = useMemo(() => renderMarkdownHtml(content), [content]);

  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    if (el.dataset.html === html) return;
    el.innerHTML = html;
    el.dataset.html = html;
  }, [html]);

  return (
    <div className="workdir-markdown workdir-viewer-body" onClick={onClick} ref={containerRef} />
  );
}

const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg', 'ico']);
const PDF_EXTENSIONS = new Set(['pdf']);
const MARKDOWN_EXTENSIONS = new Set(['md', 'markdown', 'mdx']);
const MIN_PANE_RATIO = 0.12;
const SIDEBAR_WIDTH_KEY = 'workdirSidebarWidth';
const SIDEBAR_DEFAULT_WIDTH = 240;
const SIDEBAR_MIN_WIDTH = 180;
const SIDEBAR_MAX_WIDTH = 520;

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
  if (PDF_EXTENSIONS.has(ext)) return 'pdf';
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
    case 'pdf':
      return 'application/pdf';
    default:
      return 'application/octet-stream';
  }
};

const decodeUtf8 = (bytes: Uint8Array) => {
  try {
    return new TextDecoder('utf-8', { fatal: false }).decode(bytes);
  } catch {
    return new TextDecoder().decode(bytes);
  }
};

const readTextFileSafe = async (path: string) => {
  let timer: number | null = null;
  const timeout = new Promise<Uint8Array | string>((_, reject) => {
    timer = window.setTimeout(() => reject(new Error('Read timeout')), READ_TEXT_TIMEOUT_MS);
  });
  try {
    if (IS_MAC) {
      const bytes = (await Promise.race([readFile(path), timeout])) as Uint8Array;
      return decodeUtf8(bytes);
    }
    return await Promise.race([readTextFile(path), timeout]);
  } finally {
    if (timer != null) {
      window.clearTimeout(timer);
    }
  }
};

const readTextFromBackend = async (path: string) => {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), READ_BACKEND_TIMEOUT_MS);
  try {
    const response = await fetch(`${API_BASE_URL}/local-file?path=${encodeURIComponent(path)}`, {
      signal: controller.signal,
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `Backend read failed (${response.status})`);
    }
    const data = await response.json();
    return String(data?.content ?? '');
  } finally {
    window.clearTimeout(timeout);
  }
};

const readTextFileWithFallback = async (path: string) => {
  if (IS_MAC) {
    try {
      return await readTextFromBackend(path);
    } catch {
      return await readTextFileSafe(path);
    }
  }
  try {
    return await readTextFileSafe(path);
  } catch {
    return await readTextFromBackend(path);
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

const isWindowsPath = (path: string) => /^[a-zA-Z]:\//.test(path) || path.startsWith('//');

const normalizePath = (path: string) => {
  const normalized = path.replace(/[\\/]+/g, '/');
  const normalizedCase = isWindowsPath(normalized) ? normalized.toLowerCase() : normalized;
  if (normalizedCase === '/' || /^[A-Za-z]:\/$/.test(normalizedCase)) return normalizedCase;
  return normalizedCase.endsWith('/') ? normalizedCase.slice(0, -1) : normalizedCase;
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
      if (IS_MAC && MAC_ABSOLUTE_PREFIX.test(pathname)) {
        return `/${pathname}`;
      }
      return pathname;
    } catch {
      if (IS_MAC && MAC_ABSOLUTE_PREFIX.test(path)) {
        return `/${path}`;
      }
      return path;
    }
  }
  if (IS_MAC && MAC_ABSOLUTE_PREFIX.test(path)) {
    return `/${path}`;
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
  extraRoots = [],
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
  const [editorMenu, setEditorMenu] = useState<{
    x: number;
    y: number;
    path: string;
  } | null>(null);
  const [draggingTab, setDraggingTab] = useState<{ path: string; paneId: string } | null>(null);
  const [dragOverPaneId, setDragOverPaneId] = useState<string | null>(null);
  const [dragOverIntent, setDragOverIntent] = useState<'move' | 'new-right' | null>(null);
  const [pendingJump, setPendingJump] = useState<{
    path: string;
    line: number;
    column?: number;
    nonce: number;
  } | null>(null);
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    try {
      const raw = localStorage.getItem(SIDEBAR_WIDTH_KEY);
      const value = raw ? Number(raw) : NaN;
      if (Number.isFinite(value)) {
        return Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, Math.round(value)));
      }
    } catch {
      // ignore
    }
    return SIDEBAR_DEFAULT_WIDTH;
  });
  const [isSidebarResizing, setIsSidebarResizing] = useState(false);
  const [highlightLine, setHighlightLine] = useState<{ path: string; line: number; nonce: number } | null>(null);
  const mermaidRootRef = useRef<HTMLDivElement | null>(null);
  const mermaidInitializedRef = useRef(false);
  const pendingOpenFilesRef = useRef<Set<string>>(new Set());
  const MERMAID_MIN_SCALE = 0.2;
  const MERMAID_MAX_SCALE = 4;
  const MERMAID_ZOOM_STEP = 0.15;
  const [dragGhost, setDragGhost] = useState<{ x: number; y: number; visible: boolean }>({
    x: 0,
    y: 0,
    visible: false,
  });
  const [newFolderTarget, setNewFolderTarget] = useState<string | null>(null);
  const [newFolderName, setNewFolderName] = useState('');
  const [newFolderError, setNewFolderError] = useState<string | null>(null);
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [newFileTarget, setNewFileTarget] = useState<string | null>(null);
  const [newFileName, setNewFileName] = useState('');
  const [newFileError, setNewFileError] = useState<string | null>(null);
  const [creatingFile, setCreatingFile] = useState(false);
  const [astSettingsRoot, setAstSettingsRoot] = useState<string | null>(null);
  const [astSettingsPath, setAstSettingsPath] = useState<string | null>(null);
  const [astSettingsLoading, setAstSettingsLoading] = useState(false);
  const [astSettingsSaving, setAstSettingsSaving] = useState(false);
  const [astSettingsError, setAstSettingsError] = useState<string | null>(null);
  const [astIgnorePaths, setAstIgnorePaths] = useState('');
  const [astIncludeOnlyPaths, setAstIncludeOnlyPaths] = useState('');
  const [astForceIncludePaths, setAstForceIncludePaths] = useState('');
  const [astIncludeLanguages, setAstIncludeLanguages] = useState<string[]>([]);
  const [astMaxFiles, setAstMaxFiles] = useState(String(AST_DEFAULT_MAX_FILES));
  const [watchError, setWatchError] = useState<string | null>(null);
  const [watchStatus, setWatchStatus] = useState<string | null>(null);
  const [editorByPath, setEditorByPath] = useState<Record<string, EditorState>>({});
  const previousRootRef = useRef<string>('');
  const panesRef = useRef<Pane[]>([]);
  const editorByPathRef = useRef<Record<string, EditorState>>({});
  const createFolderInputRef = useRef<HTMLInputElement>(null);
  const createFileInputRef = useRef<HTMLInputElement>(null);
  const splitContainerRef = useRef<HTMLDivElement>(null);
  const editorGutterRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const editorRefs = useRef<Record<string, HTMLTextAreaElement | null>>({});
  const dragStateRef = useRef<{
    path: string;
    paneId: string;
    startX: number;
    startY: number;
    dragging: boolean;
  } | null>(null);
  const dragOverPaneRef = useRef<string | null>(null);
  const dragOverIntentRef = useRef<'move' | 'new-right' | null>(null);
  const suppressTabClickRef = useRef(false);
  const dragGhostRef = useRef<{ x: number; y: number; visible: boolean }>({ x: 0, y: 0, visible: false });
  const dragGhostRafRef = useRef<number | null>(null);
  const dirLoadingRef = useRef<Record<string, boolean>>({});
  const expandedDirsRef = useRef<Record<string, boolean>>({});
  const refreshTimerRef = useRef<number | null>(null);
  const refreshFilesTimerRef = useRef<number | null>(null);
  const astNotifyRef = useRef<Record<string, { timer: number | null; paths: Set<string> }>>({});
  const pendingDirRefreshRef = useRef<Record<string, boolean>>({});
  const unwatchRef = useRef<UnwatchFn[]>([]);
  const sidebarResizeRef = useRef<{ startX: number; startWidth: number } | null>(null);

  const primaryRootNorm = useMemo(() => (rootPath ? normalizePath(rootPath) : ''), [rootPath]);
  const allRoots = useMemo(() => {
    const candidates = [rootPath, ...extraRoots].filter((value) => Boolean(value && value.trim()));
    const seen = new Set<string>();
    const result: string[] = [];
    candidates.forEach((value) => {
      const normalized = normalizePath(value);
      if (!normalized) return;
      if (seen.has(normalized)) return;
      seen.add(normalized);
      result.push(value);
    });
    return result;
  }, [rootPath, extraRoots]);
  const extraRootList = useMemo(
    () => allRoots.filter((value) => normalizePath(value) !== primaryRootNorm),
    [allRoots, primaryRootNorm]
  );
  const watchRoots = useMemo(() => (rootPath ? allRoots : []), [rootPath, allRoots]);

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
    setEditorByPath({});
    setPanes((prev) => {
      prev.forEach((pane) => pane.openFiles.forEach(revokeFileUrl));
      return [nextPane];
    });
    setActivePaneId(nextPane.id);
  };

  const resolveAstSettingsRoot = (targetPath: string) => {
    if (!targetPath) return rootPath;
    const normalizedTarget = normalizePath(targetPath);
    for (const root of allRoots) {
      if (isPathWithin(normalizedTarget, root)) {
        return root;
      }
    }
    return rootPath;
  };

  const parseAstPathList = (value: string) =>
    value
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);

  const pickAstFolder = async (target: 'ignore' | 'include' | 'force') => {
    if (!astSettingsRoot) return;
    try {
      const selected = await openDialog({
        directory: true,
        multiple: false,
        defaultPath: astSettingsRoot,
      });
      const path = Array.isArray(selected) ? selected[0] : selected;
      if (!path) return;
      if (target === 'ignore') {
        appendAstPath(path, astIgnorePaths, setAstIgnorePaths);
      } else if (target === 'include') {
        appendAstPath(path, astIncludeOnlyPaths, setAstIncludeOnlyPaths);
      } else {
        appendAstPath(path, astForceIncludePaths, setAstForceIncludePaths);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to pick folder.';
      setAstSettingsError(message);
    }
  };

  const normalizeAstLanguageList = (value: unknown) => {
    if (!Array.isArray(value)) return [];
    const allowed = new Set(AST_LANGUAGE_OPTIONS.map((option) => option.id));
    const seen = new Set<string>();
    const result: string[] = [];
    value.forEach((item) => {
      if (typeof item !== 'string') return;
      const key = item.trim().toLowerCase();
      if (!key || !allowed.has(key) || seen.has(key)) return;
      seen.add(key);
      result.push(key);
    });
    return result;
  };

  const toggleAstLanguage = (language: string) => {
    if (!language) return;
    setAstIncludeLanguages((prev) => {
      if (prev.includes(language)) {
        return prev.filter((item) => item !== language);
      }
      return [...prev, language];
    });
  };

  const appendAstPath = (path: string, current: string, setter: (value: string) => void) => {
    if (!path) return;
    const list = parseAstPathList(current);
    if (list.includes(path)) return;
    const next = [...list, path].join('\n');
    setter(next);
  };

  const openAstSettings = (targetPath: string) => {
    const root = resolveAstSettingsRoot(targetPath);
    setAstSettingsRoot(root || null);
    setAstSettingsPath(targetPath || null);
    setAstSettingsError(null);
  };

  const closeAstSettings = () => {
    if (astSettingsSaving) return;
    setAstSettingsRoot(null);
    setAstSettingsPath(null);
    setAstSettingsError(null);
  };

  const handleSaveAstSettings = async () => {
    if (!astSettingsRoot) return;
    setAstSettingsSaving(true);
    setAstSettingsError(null);
    const parsedMax = Number.parseInt(astMaxFiles, 10);
    const maxFiles = Number.isFinite(parsedMax) && parsedMax > 0 ? parsedMax : AST_DEFAULT_MAX_FILES;
    const includeLanguages = AST_LANGUAGE_OPTIONS
      .map((option) => option.id)
      .filter((id) => astIncludeLanguages.includes(id));
    const payload = {
      root: astSettingsRoot,
      ignore_paths: parseAstPathList(astIgnorePaths),
      include_only_paths: parseAstPathList(astIncludeOnlyPaths),
      force_include_paths: parseAstPathList(astForceIncludePaths),
      include_languages: includeLanguages,
      max_files: maxFiles,
    };
    try {
      const result = await updateAstSettings(payload);
      const settings = result?.settings || payload;
      const ignoreList = Array.isArray(settings.ignore_paths) ? settings.ignore_paths : payload.ignore_paths;
      const includeList = Array.isArray(settings.include_only_paths) ? settings.include_only_paths : payload.include_only_paths;
      const forceList = Array.isArray(settings.force_include_paths) ? settings.force_include_paths : payload.force_include_paths;
      setAstIgnorePaths(ignoreList.join('\n'));
      setAstIncludeOnlyPaths(includeList.join('\n'));
      setAstForceIncludePaths(forceList.join('\n'));
      const languageList = normalizeAstLanguageList(settings.include_languages ?? includeLanguages);
      setAstIncludeLanguages(languageList);
      setAstMaxFiles(String(settings.max_files ?? maxFiles));
      notifyAstChanges(astSettingsRoot, [astSettingsRoot]).catch(() => undefined);
      closeAstSettings();
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to save AST settings.';
      setAstSettingsError(message);
    } finally {
      setAstSettingsSaving(false);
    }
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
    if (!astSettingsRoot) return;
    let cancelled = false;
    setAstSettingsLoading(true);
    setAstSettingsError(null);
    getAstSettings(astSettingsRoot)
      .then((result) => {
        if (cancelled) return;
        const settings = result?.settings || {};
        const ignoreList = Array.isArray(settings.ignore_paths) ? settings.ignore_paths : [];
        const includeList = Array.isArray(settings.include_only_paths) ? settings.include_only_paths : [];
        const forceList = Array.isArray(settings.force_include_paths) ? settings.force_include_paths : [];
        setAstIgnorePaths(ignoreList.join('\n'));
        setAstIncludeOnlyPaths(includeList.join('\n'));
        setAstForceIncludePaths(forceList.join('\n'));
        const languageList = normalizeAstLanguageList(settings.include_languages);
        setAstIncludeLanguages(languageList);
        const maxFiles = settings.max_files ?? AST_DEFAULT_MAX_FILES;
        setAstMaxFiles(String(maxFiles));
      })
      .catch((error) => {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : 'Failed to load AST settings.';
        setAstSettingsError(message);
      })
      .finally(() => {
        if (!cancelled) setAstSettingsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [astSettingsRoot]);

  useEffect(() => {
    if (!open || !rootPath) return;
    if (allRoots.length === 0) return;
    const expandedUpdates: Record<string, boolean> = {};
    allRoots.forEach((root) => {
      if (!expandedDirs[root]) {
        expandedUpdates[root] = true;
      }
      if (!dirCache[root] && !dirLoading[root]) {
        void loadDir(root);
      }
    });
    if (Object.keys(expandedUpdates).length > 0) {
      setExpandedDirs((prev) => ({ ...prev, ...expandedUpdates }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, rootPath, allRoots]);

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_WIDTH_KEY, String(sidebarWidth));
    } catch {
      // ignore
    }
  }, [sidebarWidth]);

  const ensureMermaid = () => {
    if (mermaidInitializedRef.current) return;
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme: 'dark',
    });
    mermaidInitializedRef.current = true;
  };

  const setupMermaidInteractions = () => {
    const root = mermaidRootRef.current;
    if (!root) return;
    const blocks = root.querySelectorAll<HTMLElement>('.mermaid-block');
    blocks.forEach((block) => {
      const viewport = block.querySelector<HTMLElement>('.mermaid-viewport');
      if (!viewport) return;

      const readState = () => {
        const scale = Number.parseFloat(block.dataset.scale || '1');
        const x = Number.parseFloat(block.dataset.x || '0');
        const y = Number.parseFloat(block.dataset.y || '0');
        return {
          scale: Number.isFinite(scale) ? scale : 1,
          x: Number.isFinite(x) ? x : 0,
          y: Number.isFinite(y) ? y : 0,
        };
      };

      const applyTransform = () => {
        const svg = block.querySelector<SVGElement>('.mermaid svg');
        if (!svg) return;
        const { scale, x, y } = readState();
        svg.style.transformOrigin = '0 0';
        svg.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
        svg.style.cursor = block.dataset.dragging === 'true' ? 'grabbing' : 'grab';
      };

      if (!block.dataset.scale) {
        block.dataset.scale = '1';
        block.dataset.x = '0';
        block.dataset.y = '0';
      }

      applyTransform();

      if (block.dataset.mermaidInteractive === 'true') return;
      block.dataset.mermaidInteractive = 'true';

      const clamp = (value: number) =>
        Math.min(MERMAID_MAX_SCALE, Math.max(MERMAID_MIN_SCALE, value));

      const setState = (next: { scale: number; x: number; y: number }) => {
        block.dataset.scale = String(next.scale);
        block.dataset.x = String(next.x);
        block.dataset.y = String(next.y);
        applyTransform();
      };

      const handleWheel = (event: WheelEvent) => {
        if (!event.ctrlKey && !event.metaKey) return;
        event.preventDefault();
        const state = readState();
        const direction = event.deltaY > 0 ? -1 : 1;
        const factor = direction > 0 ? 1 + MERMAID_ZOOM_STEP : 1 - MERMAID_ZOOM_STEP;
        const nextScale = clamp(state.scale * factor);
        setState({ ...state, scale: nextScale });
      };

      const handlePointerDown = (event: PointerEvent) => {
        if (event.button !== 0) return;
        event.preventDefault();
        viewport.setPointerCapture(event.pointerId);
        const state = readState();
        block.dataset.dragging = 'true';
        block.dataset.startX = String(event.clientX);
        block.dataset.startY = String(event.clientY);
        block.dataset.originX = String(state.x);
        block.dataset.originY = String(state.y);
        applyTransform();
      };

      const handlePointerMove = (event: PointerEvent) => {
        if (block.dataset.dragging !== 'true') return;
        const startX = Number.parseFloat(block.dataset.startX || '0');
        const startY = Number.parseFloat(block.dataset.startY || '0');
        const originX = Number.parseFloat(block.dataset.originX || '0');
        const originY = Number.parseFloat(block.dataset.originY || '0');
        const dx = event.clientX - startX;
        const dy = event.clientY - startY;
        const state = readState();
        setState({ ...state, x: originX + dx, y: originY + dy });
      };

      const handlePointerUp = (event: PointerEvent) => {
        if (block.dataset.dragging !== 'true') return;
        block.dataset.dragging = 'false';
        viewport.releasePointerCapture(event.pointerId);
        applyTransform();
      };

      const handleDoubleClick = () => {
        setState({ scale: 1, x: 0, y: 0 });
      };

      const handleControlClick = (event: MouseEvent) => {
        const target = event.target as HTMLElement;
        const actionEl = target.closest<HTMLElement>('.mermaid-control');
        if (!actionEl) return;
        const action = actionEl.dataset.action || '';
        const state = readState();
        if (action === 'zoom-in') {
          setState({ ...state, scale: clamp(state.scale + MERMAID_ZOOM_STEP) });
        } else if (action === 'zoom-out') {
          setState({ ...state, scale: clamp(state.scale - MERMAID_ZOOM_STEP) });
        } else if (action === 'reset') {
          setState({ scale: 1, x: 0, y: 0 });
        }
        event.preventDefault();
        event.stopPropagation();
      };

      viewport.addEventListener('wheel', handleWheel, { passive: false });
      viewport.addEventListener('pointerdown', handlePointerDown);
      viewport.addEventListener('pointermove', handlePointerMove);
      viewport.addEventListener('pointerup', handlePointerUp);
      viewport.addEventListener('pointercancel', handlePointerUp);
      viewport.addEventListener('dblclick', handleDoubleClick);
      block.addEventListener('click', handleControlClick);
    });
  };

  const runMermaid = () => {
    const root = mermaidRootRef.current;
    if (!root) return;
    const nodes = Array.from(root.querySelectorAll<HTMLElement>('.mermaid'));
    if (!nodes.length) return;
    ensureMermaid();
    const pendingNodes = nodes.filter((node) => !node.querySelector('svg'));
    if (!pendingNodes.length) {
      setupMermaidInteractions();
      return;
    }
    pendingNodes.forEach((node) => {
      node.removeAttribute('data-processed');
      const raw = node.dataset.raw;
      if (raw) {
        try {
          node.textContent = decodeURIComponent(raw);
        } catch {
          node.textContent = raw;
        }
      }
    });
    mermaid.run({ nodes: pendingNodes }).then(
      () => {
        setupMermaidInteractions();
        pendingNodes.forEach((node, idx) => {
          if (node.querySelector('svg')) return;
          const raw = node.dataset.raw;
          if (!raw) return;
          let source = raw;
          try {
            source = decodeURIComponent(raw);
          } catch {
            // ignore decode errors
          }
          const id = `mermaid-${Date.now()}-${idx}`;
          mermaid.render(id, source).then(
            ({ svg }) => {
              node.innerHTML = svg;
              setupMermaidInteractions();
            },
            () => {
              // ignore render errors
            }
          );
        });
      },
      () => {
        // ignore render errors
      }
    );
  };

  useEffect(() => {
    if (!open) return;
    const frame = window.requestAnimationFrame(() => runMermaid());
    return () => window.cancelAnimationFrame(frame);
  }, [open, panes, expandedDirs, dirCache]);

  useEffect(() => {
    panesRef.current = panes;
  }, [panes]);

  useEffect(() => {
    editorByPathRef.current = editorByPath;
  }, [editorByPath]);

  useEffect(() => {
    if (panes.length === 0) return;
    setEditorByPath((prev) => {
      let changed = false;
      let next = prev;
      panes.forEach((pane) => {
        pane.openFiles.forEach((file) => {
          if (file.kind !== 'text' || file.content == null) return;
          const existing = prev[file.path];
          if (!existing) {
            if (!changed) next = { ...prev };
            next[file.path] = { value: file.content ?? '', base: file.content ?? '', dirty: false, saving: false };
            changed = true;
            return;
          }
          if (!existing.dirty && existing.base !== file.content) {
            if (!changed) next = { ...prev };
            next[file.path] = { ...existing, value: file.content ?? '', base: file.content ?? '', dirty: false };
            changed = true;
          }
        });
      });
      return changed ? next : prev;
    });
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
    const handleMove = (event: globalThis.MouseEvent) => {
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
    if (!editorMenu) return;
    const dismiss = () => setEditorMenu(null);
    window.addEventListener('mousedown', dismiss);
    window.addEventListener('blur', dismiss);
    return () => {
      window.removeEventListener('mousedown', dismiss);
      window.removeEventListener('blur', dismiss);
    };
  }, [editorMenu]);

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

  const shouldRefreshOpenFile = (filePath: string, changedPaths?: string[]) => {
    if (!changedPaths || changedPaths.length === 0) return false;
    const fileNorm = normalizePath(filePath);
    return changedPaths.some((changed) => {
      const changedNorm = normalizePath(changed);
      if (changedNorm === fileNorm) return true;
      return isPathWithin(fileNorm, changedNorm) || isPathWithin(changedNorm, fileNorm);
    });
  };

  const refreshOpenFileContent = async (file: OpenFile, changedPaths?: string[]) => {
    if (!shouldRefreshOpenFile(file.path, changedPaths)) return;
    const editorState = editorByPathRef.current[file.path];
    if (editorState?.dirty) return;
    if (!findFileLocation(file.path)) return;
    try {
      if (file.kind === 'image') {
        const bytes = await readFile(file.path);
        if (!findFileLocation(file.path)) return;
        const url = URL.createObjectURL(new Blob([bytes], { type: getMimeType(file.ext) }));
        updateFile(file.path, { url });
      } else {
        const text = await readTextFile(file.path);
        if (!findFileLocation(file.path)) return;
        updateFile(file.path, { content: text });
      }
    } catch (error) {
      updateFile(file.path, { error: formatError(error) });
    }
  };

  const refreshOpenFiles = (paths?: string[]) => {
    const openFiles = panesRef.current.flatMap((pane) => pane.openFiles);
    if (openFiles.length === 0) return;
    const normalized = paths
      ? paths.map((value) => normalizeWatchPath(value)).filter((value) => value.length > 0)
      : undefined;
    openFiles.forEach((file) => {
      void refreshOpenFileContent(file, normalized);
    });
  };

  const scheduleRefreshOpenFiles = (paths?: string[]) => {
    if (refreshFilesTimerRef.current != null) {
      window.clearTimeout(refreshFilesTimerRef.current);
    }
    const snapshot = paths
      ? paths.map((value) => normalizeWatchPath(value)).filter((value) => value.length > 0)
      : undefined;
    refreshFilesTimerRef.current = window.setTimeout(() => {
      refreshFilesTimerRef.current = null;
      refreshOpenFiles(snapshot);
    }, 160);
  };

  const scheduleAstNotify = (root: string, paths?: string[]) => {
    const rootNorm = normalizePath(root);
    if (!rootNorm) return;
    const normalized = paths
      ? paths.map((value) => normalizeWatchPath(value)).filter((value) => value.length > 0)
      : [];
    if (normalized.length === 0) return;

    const record = astNotifyRef.current[rootNorm] || { timer: null, paths: new Set<string>() };
    normalized.forEach((value) => record.paths.add(value));
    astNotifyRef.current[rootNorm] = record;
    if (record.timer != null) return;
    record.timer = window.setTimeout(() => {
      record.timer = null;
      const batch = Array.from(record.paths);
      record.paths.clear();
      void notifyAstChanges(root, batch).catch(() => {
        // ignore AST notify failures
      });
    }, 220);
  };

  const describeWatchPaths = (paths?: string[]) => {
    if (!paths || paths.length === 0) return '未知路径';
    const normalized = paths.map((value) => normalizeWatchPath(value));
    const names = normalized.map((value) => getBaseName(value)).filter(Boolean);
    const display = (names.length > 0 ? names : normalized).join(', ');
    return display.length > 120 ? `${display.slice(0, 117)}...` : display;
  };

  useEffect(() => {
    if (!open || watchRoots.length === 0) return;
    let active = true;

    const startWatch = async (root: string) => {
      try {
        const unwatch = await watchImmediate(
          root,
          (event) => {
            if (!active) return;
            setWatchStatus(`监听事件：${describeWatchPaths(event?.paths)}`);
            scheduleRefreshDirs(event?.paths);
            scheduleRefreshOpenFiles(event?.paths);
            scheduleAstNotify(root, event?.paths);
          },
          { recursive: true }
        );
        if (!active) {
          unwatch();
          return;
        }
        unwatchRef.current.push(unwatch);
      } catch (error) {
        setWatchError(`监听失败：${formatError(error)}`);
      }
    };

    const startAll = async () => {
      setWatchError(null);
      setWatchStatus(watchRoots.length > 1 ? `监听已启动（${watchRoots.length}）` : '监听已启动');
      unwatchRef.current.forEach((fn) => fn());
      unwatchRef.current = [];
      await Promise.all(watchRoots.map((root) => startWatch(root)));
    };

    void startAll();

    return () => {
      active = false;
      unwatchRef.current.forEach((fn) => fn());
      unwatchRef.current = [];
      if (refreshTimerRef.current != null) {
        window.clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
      if (refreshFilesTimerRef.current != null) {
        window.clearTimeout(refreshFilesTimerRef.current);
        refreshFilesTimerRef.current = null;
      }
      Object.values(astNotifyRef.current).forEach((entry) => {
        if (entry.timer != null) {
          window.clearTimeout(entry.timer);
        }
      });
      astNotifyRef.current = {};
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, watchRoots]);

  const openFileFromExternal = async (rawPath: string) => {
    const path = normalizeWatchPath(rawPath);
    if (!path) return;
    const name = getBaseName(path);
    if (!name) return;

    if (allRoots.length > 0) {
      const normalizedFile = normalizePath(path);
      const matchedRoot = allRoots.find((root) => isPathWithin(normalizedFile, normalizePath(root)));
      if (matchedRoot) {
        const normalizedRoot = normalizePath(matchedRoot);
        const relative = normalizedFile.slice(normalizedRoot.length).replace(/^\/+/, '');
        const parts = relative ? relative.split('/') : [];
        const dirParts = parts.slice(0, -1);
        const expandedUpdates: Record<string, boolean> = { [matchedRoot]: true };
        let current = matchedRoot;
        for (const part of dirParts) {
          current = joinPath(current, part);
          expandedUpdates[current] = true;
        }
        setExpandedDirs((prev) => ({ ...prev, ...expandedUpdates }));
        void loadDir(matchedRoot);
        Object.keys(expandedUpdates).forEach((dir) => {
          if (dir !== matchedRoot) {
            void loadDir(dir);
          }
        });
      }
    }

    void openFile(path, name);
  };

  const requestJumpToLine = (path: string, line?: number, column?: number) => {
    if (!line || line < 1) return;
    setPendingJump({ path, line, column, nonce: Date.now() });
  };

  const getLineOffset = (text: string, line: number, _column?: number) => {
    const lines = text.split('\n');
    const maxLine = Math.max(1, lines.length);
    const targetLine = Math.min(Math.max(1, line), maxLine);
    let startOffset = 0;
    for (let i = 0; i < targetLine - 1; i += 1) {
      startOffset += lines[i].length + 1;
    }
    const lineLength = lines[targetLine - 1]?.length ?? 0;
    const endOffset = startOffset + lineLength;
    return { startOffset, endOffset, line: targetLine };
  };

  const scrollToLine = (path: string, line: number, column?: number) => {
    const editor = editorRefs.current[path];
    if (!editor) return false;
    const text = editor.value ?? editorByPathRef.current[path]?.value ?? '';
    const { startOffset, endOffset, line: targetLine } = getLineOffset(text, line, column);
    editor.focus();
    editor.setSelectionRange(startOffset, endOffset);
    const style = getComputedStyle(editor);
    let lineHeight = parseFloat(style.lineHeight || '');
    if (!Number.isFinite(lineHeight)) {
      const fontSize = parseFloat(style.fontSize || '14');
      lineHeight = fontSize * 1.55;
    }
    const targetTop = Math.max(0, (targetLine - 1) * lineHeight - lineHeight * 2);
    editor.scrollTop = targetTop;
    const gutter = editorGutterRefs.current[path];
    if (gutter) {
      gutter.scrollTop = editor.scrollTop;
    }
    return true;
  };

  useEffect(() => {
    if (!open || !openFileRequest?.path) return;
    const normalized = normalizeWatchPath(openFileRequest.path);
    if (!normalized) return;
    void openFileFromExternal(normalized);
    if (openFileRequest.line) {
      requestJumpToLine(normalized, openFileRequest.line, openFileRequest.column);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, openFileRequest?.nonce]);

  useEffect(() => {
    if (!pendingJump) return;
    const success = scrollToLine(pendingJump.path, pendingJump.line, pendingJump.column);
    if (success) {
      setPendingJump(null);
      setHighlightLine({ path: pendingJump.path, line: pendingJump.line, nonce: Date.now() });
    }
  }, [pendingJump, panes, editorByPath]);

  useEffect(() => {
    if (!highlightLine) return;
    const openPaths = panesRef.current.flatMap((pane) => pane.openFiles.map((file) => file.path));
    if (!openPaths.includes(highlightLine.path)) {
      setHighlightLine(null);
    }
  }, [panes, highlightLine]);

  const clearHighlight = (path?: string) => {
    if (!highlightLine) return;
    if (!path || highlightLine.path === path) {
      setHighlightLine(null);
    }
  };

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
          openFiles: pane.openFiles.map((file) => {
            if (file.path !== path) return file;
            if (update.url && file.url && update.url !== file.url) {
              URL.revokeObjectURL(file.url);
            }
            return { ...file, ...update };
          }),
        };
      })
    );
  };

  const beginEditFile = (file: OpenFile) => {
    if (file.kind !== 'text') return;
    setEditorByPath((prev) => {
      const existing = prev[file.path];
      if (existing) {
        return {
          ...prev,
          [file.path]: { ...existing, error: undefined },
        };
      }
      const base = file.content ?? '';
      return {
        ...prev,
        [file.path]: { value: base, base, dirty: false, saving: false },
      };
    });
  };

  const updateEditorValue = (path: string, value: string) => {
    setEditorByPath((prev) => {
      const current = prev[path];
      if (!current) return prev;
      const dirty = value !== current.base;
      return {
        ...prev,
        [path]: { ...current, value, dirty },
      };
    });
  };

  const cancelEditFile = (path: string) => {
    setEditorByPath((prev) => {
      const current = prev[path];
      if (!current) return prev;
      return {
        ...prev,
        [path]: { ...current, value: current.base, dirty: false, error: undefined },
      };
    });
  };

  const saveEditFile = async (path: string) => {
    const current = editorByPath[path];
    if (!current || current.saving) return;
    setEditorByPath((prev) => ({
      ...prev,
      [path]: { ...current, saving: true, error: undefined },
    }));
    try {
      await writeTextFile(path, current.value);
      updateFile(path, { content: current.value });
      setEditorByPath((prev) => {
        const latest = prev[path];
        if (!latest) return prev;
        return {
          ...prev,
          [path]: { ...latest, base: current.value, dirty: false, saving: false },
        };
      });
    } catch (error) {
      setEditorByPath((prev) => {
        const latest = prev[path];
        if (!latest) return prev;
        return {
          ...prev,
          [path]: { ...latest, saving: false, error: formatError(error) },
        };
      });
    }
  };

  const handleEditorKeyDown = (path: string) => (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
      event.preventDefault();
      void saveEditFile(path);
    }
  };

  const openFile = async (path: string, name: string) => {
    if (pendingOpenFilesRef.current.has(path)) {
      const existing = findFileLocation(path);
      if (existing) {
        setPaneActivePath(existing.paneId, path);
      }
      return;
    }
    const existing = findFileLocation(path);
    if (existing) {
      setPaneActivePath(existing.paneId, path);
      return;
    }

    pendingOpenFilesRef.current.add(path);

    const ext = getFileExt(name);
    const kind = getFileKind(ext);
    const newFile: OpenFile = { path, name, ext, kind, loading: true };
    const targetPaneId = activePaneId || panesRef.current[0]?.id;
    if (!targetPaneId) {
      pendingOpenFilesRef.current.delete(path);
      return;
    }

    setPanes((prev) => {
      const next = prev.map((pane) => {
        if (pane.id !== targetPaneId) return pane;
        return {
          ...pane,
          openFiles: [...pane.openFiles, newFile],
          activePath: path,
        };
      });
      panesRef.current = next;
      return next;
    });
    setActivePaneId(targetPaneId);

    try {
      if (kind === 'image' || kind === 'pdf') {
        const bytes = await readFile(path);
        const url = URL.createObjectURL(new Blob([bytes], { type: getMimeType(ext) }));
        if (!findFileLocation(path)) {
          URL.revokeObjectURL(url);
          return;
        }
        updateFile(path, { url, loading: false });
      } else {
        const text = await readTextFileWithFallback(path);
        if (!findFileLocation(path)) return;
        updateFile(path, { content: text, loading: false });
        if (kind === 'text') {
          beginEditFile({ path, name, ext, kind, content: text });
        }
      }
    } catch (error) {
      updateFile(path, { error: formatError(error), loading: false });
    } finally {
      pendingOpenFilesRef.current.delete(path);
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
    cancelEditFile(path);
  };

  const moveFileToPane = (path: string, fromPaneId: string, toPaneId: string) => {
    if (!path || fromPaneId === toPaneId) {
      setPaneActivePath(toPaneId, path);
      return;
    }
    let movedFile: OpenFile | null = null;
    setPanes((prev) => {
      let next = prev.flatMap((pane) => {
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

  const resolvePaneFromPoint = (x: number, y: number) => {
    if (typeof document === 'undefined') return null;
    const el = document.elementFromPoint(x, y) as HTMLElement | null;
    if (!el) return null;
    const paneEl = el.closest<HTMLElement>('.workdir-pane');
    if (!paneEl) return null;
    return paneEl;
  };

  const updateDragGhost = (x: number, y: number, visible: boolean) => {
    dragGhostRef.current = { x, y, visible };
    if (dragGhostRafRef.current !== null) return;
    dragGhostRafRef.current = window.requestAnimationFrame(() => {
      dragGhostRafRef.current = null;
      setDragGhost({ ...dragGhostRef.current });
    });
  };

  const clearTabDrag = () => {
    dragStateRef.current = null;
    dragOverPaneRef.current = null;
    dragOverIntentRef.current = null;
    setDraggingTab(null);
    setDragOverPaneId(null);
    setDragOverIntent(null);
    updateDragGhost(0, 0, false);
  };

  const splitPaneAndMoveFile = (path: string, fromPaneId: string) => {
    const newPane = createPane();
    setPanes((prev) => {
      const fromIndex = prev.findIndex((pane) => pane.id === fromPaneId);
      if (fromIndex < 0) return prev;
      const fromPane = prev[fromIndex];
      const fileIndex = fromPane.openFiles.findIndex((file) => file.path === path);
      if (fileIndex < 0) return prev;
      const movedFile = fromPane.openFiles[fileIndex];
      const nextFiles = fromPane.openFiles.filter((file) => file.path !== path);
      let nextActive = fromPane.activePath;
      if (fromPane.activePath === path) {
        nextActive =
          nextFiles[fileIndex - 1]?.path ||
          nextFiles[fileIndex]?.path ||
          nextFiles[0]?.path ||
          null;
      }
      const nextPane = { ...fromPane, openFiles: nextFiles, activePath: nextActive };
      const newPaneEntry: Pane = { ...newPane, openFiles: [movedFile], activePath: path };
      const next = [...prev];
      next.splice(fromIndex, 1, nextPane);
      next.splice(fromIndex + 1, 0, newPaneEntry);
      return next;
    });
    setPaneSizes((sizes) => {
      const existingIds = panesRef.current.map((pane) => pane.id);
      const fromIndex = existingIds.indexOf(fromPaneId);
      if (fromIndex < 0) return sizes;
      const insertIndex = fromIndex + 1;
      const nextIds = [
        ...existingIds.slice(0, insertIndex),
        newPane.id,
        ...existingIds.slice(insertIndex),
      ];
      const current = sizes[fromPaneId] ?? 1 / Math.max(1, existingIds.length || 1);
      const half = current / 2;
      const next = { ...sizes, [fromPaneId]: half, [newPane.id]: half };
      return ensurePaneSizes(next, nextIds);
    });
    setActivePaneId(newPane.id);
  };

  const handleTabPointerDown = (event: ReactPointerEvent<HTMLDivElement>, paneId: string, path: string) => {
    if (event.button !== 0) return;
    dragStateRef.current = {
      paneId,
      path,
      startX: event.clientX,
      startY: event.clientY,
      dragging: false,
    };
    const handlePointerMove = (moveEvent: PointerEvent) => {
      const state = dragStateRef.current;
      if (!state) return;
      const dx = moveEvent.clientX - state.startX;
      const dy = moveEvent.clientY - state.startY;
      if (!state.dragging) {
        if (Math.hypot(dx, dy) < 4) return;
        state.dragging = true;
        setDraggingTab({ path: state.path, paneId: state.paneId });
        updateDragGhost(moveEvent.clientX, moveEvent.clientY, true);
      }
      if (state.dragging) {
        updateDragGhost(moveEvent.clientX, moveEvent.clientY, true);
      }
      const paneEl = resolvePaneFromPoint(moveEvent.clientX, moveEvent.clientY);
      const overPaneId = paneEl?.dataset?.paneId || null;
      let intent: 'move' | 'new-right' | null = overPaneId ? 'move' : null;
      if (overPaneId && panesRef.current.length === 1 && paneEl) {
        const rect = paneEl.getBoundingClientRect();
        const threshold = rect.left + rect.width * 0.6;
        if (moveEvent.clientX >= threshold) {
          intent = 'new-right';
        }
      }
      dragOverPaneRef.current = overPaneId;
      dragOverIntentRef.current = intent;
      setDragOverPaneId(overPaneId);
      setDragOverIntent(intent);
    };
    const handlePointerUp = () => {
      const state = dragStateRef.current;
      const overPaneId = dragOverPaneRef.current;
      const intent = dragOverIntentRef.current;
      if (state?.dragging) {
        if (intent === 'new-right' && overPaneId && overPaneId === state.paneId) {
          splitPaneAndMoveFile(state.path, state.paneId);
          suppressTabClickRef.current = true;
        } else if (overPaneId && overPaneId !== state.paneId) {
          moveFileToPane(state.path, state.paneId, overPaneId);
          suppressTabClickRef.current = true;
        }
        if (suppressTabClickRef.current) {
          window.setTimeout(() => {
            suppressTabClickRef.current = false;
          }, 200);
        }
      }
      updateDragGhost(0, 0, false);
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
      clearTabDrag();
    };
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp, { once: true });
  };

  const handleTabClick = (paneId: string, path: string) => {
    if (suppressTabClickRef.current) {
      suppressTabClickRef.current = false;
      return;
    }
    setPaneActivePath(paneId, path);
  };

  const handleSplitterMouseDown = (
    event: ReactMouseEvent<HTMLDivElement>,
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

  const handleSidebarResizeStart = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    event.preventDefault();
    const startWidth = sidebarWidth;
    sidebarResizeRef.current = { startX: event.clientX, startWidth };
    setIsSidebarResizing(true);

    const handlePointerMove = (moveEvent: PointerEvent) => {
      if (!sidebarResizeRef.current) return;
      const delta = moveEvent.clientX - sidebarResizeRef.current.startX;
      const nextWidth = Math.min(
        SIDEBAR_MAX_WIDTH,
        Math.max(SIDEBAR_MIN_WIDTH, Math.round(sidebarResizeRef.current.startWidth + delta))
      );
      setSidebarWidth(nextWidth);
    };

    const handlePointerUp = () => {
      sidebarResizeRef.current = null;
      setIsSidebarResizing(false);
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
    };

    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp, { once: true });
  };

  const handleMarkdownLinkClick = (event: ReactMouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    const link = target.closest<HTMLAnchorElement>('a');
    if (!link) return;
    const rawHref = link.getAttribute('href') || '';
    if (rawHref.startsWith('#')) return;
    const href = rawHref || link.href || '';
    if (!href) return;
    const normalized =
      href.startsWith('www.') || rawHref.startsWith('www.') ? `https://${rawHref || href}` : href;
    if (!/^(https?:\/\/|mailto:|tel:)/i.test(normalized)) return;
    event.preventDefault();
    event.stopPropagation();
    openUrl(normalized).catch(() => {
      // ignore open errors
    });
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

  const renderRootTree = (root: string, isPrimary: boolean) => {
    const rootLabel = getBaseName(root) || root;
    const showWatchStatus = isPrimary;
    return (
      <div key={root} className="workdir-tree-root">
        <div
          className={`workdir-tree-row root${isPrimary ? '' : ' extra'}`}
          onClick={() => toggleDir(root)}
          onContextMenu={(event) => {
            event.preventDefault();
            event.stopPropagation();
            setContextMenu({
              x: event.clientX,
              y: event.clientY,
              targetPath: root,
              revealPath: root,
            });
          }}
        >
          <span className={`workdir-tree-toggle${expandedDirs[root] ? ' expanded' : ''}`}>▸</span>
          <span className="workdir-tree-name dir">{rootLabel}</span>
          {!isPrimary && <span className="workdir-tree-tag">附加</span>}
        </div>
        {dirErrors[root] && (
          <div className="workdir-tree-error" style={{ paddingLeft: 20 }}>
            读取失败：{dirErrors[root]}
          </div>
        )}
        {dirLoading[root] && (
          <div className="workdir-tree-loading" style={{ paddingLeft: 20 }}>
            加载中...
          </div>
        )}
        {showWatchStatus && watchError && (
          <div className="workdir-tree-error" style={{ paddingLeft: 20 }}>
            {watchError}
          </div>
        )}
        {showWatchStatus && !watchError && watchStatus && (
          <div className="workdir-tree-status" style={{ paddingLeft: 20 }}>
            {watchStatus}
          </div>
        )}
        {expandedDirs[root] && renderEntries(root, 1)}
      </div>
    );
  };

  if (!open) return null;

  const closeVisible = showClose ?? mode === 'overlay';

  const headerVisible = showHeader;

  const content = (
    <div
      className={`workdir-window${mode === 'window' ? ' embedded' : ''}${headerVisible ? '' : ' no-header'}`}
      ref={mermaidRootRef}
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
        <div className={`workdir-body${isSidebarResizing ? ' resizing-sidebar' : ''}`}>
          <div className="workdir-sidebar" style={{ width: sidebarWidth }}>
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
              {rootPath && renderRootTree(rootPath, true)}
              {extraRootList.map((root) => renderRootTree(root, false))}
            </div>
          </div>

          <div
            className="workdir-sidebar-resizer"
            onPointerDown={handleSidebarResizeStart}
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize sidebar"
          />

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
                const isDragTarget =
                  draggingTab &&
                  dragOverPaneId === pane.id &&
                  (draggingTab.paneId !== pane.id || dragOverIntent === 'new-right');
                return (
                  <Fragment key={pane.id}>
                    <div
                      className={`workdir-pane${pane.id === activePaneId ? ' active' : ''}${
                        isDragTarget ? ' drag-target' : ''
                      }`}
                      style={{ flex: `0 0 ${(paneSize * 100).toFixed(4)}%` }}
                      data-pane-id={pane.id}
                      onMouseDown={() => setActivePaneId(pane.id)}
                    >
                      <div className="workdir-tabs">
                        <div className="workdir-tabs-list">
                          {pane.openFiles.length === 0 && (
                            <span className="workdir-tabs-empty">尚未打开文件</span>
                          )}
                          {pane.openFiles.map((file) => (
                            <div
                              key={file.path}
                              className={`workdir-tab${file.path === pane.activePath ? ' active' : ''}${
                                draggingTab?.path === file.path && draggingTab?.paneId === pane.id
                                  ? ' dragging'
                                  : ''
                              }`}
                              onClick={() => handleTabClick(pane.id, file.path)}
                              onPointerDown={(event) => handleTabPointerDown(event, pane.id, file.path)}
                            >
                              <span className="workdir-tab-label">{file.name}</span>
                              {editorByPath[file.path]?.dirty && (
                                <span className="workdir-tab-dirty" title="未保存" aria-label="未保存" />
                              )}
                              <button
                                type="button"
                                className="workdir-tab-close"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  closeFile(pane.id, file.path);
                                }}
                                onPointerDown={(event) => {
                                  event.stopPropagation();
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
                            <div className="workdir-image-viewer workdir-viewer-body">
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
                          paneActiveFile.kind === 'pdf' && (
                            <div className="workdir-pdf-viewer workdir-viewer-body">
                              {paneActiveFile.url ? (
                                <iframe src={paneActiveFile.url} title={paneActiveFile.name} />
                              ) : (
                                <span>PDF 加载失败。</span>
                              )}
                            </div>
                          )}
                        {paneActiveFile &&
                          !paneActiveFile.loading &&
                          !paneActiveFile.error &&
                          paneActiveFile.kind === 'markdown' && (
                            <WorkdirMarkdown
                              content={paneActiveFile.content || ''}
                              onClick={handleMarkdownLinkClick}
                            />
                          )}
                      {paneActiveFile &&
                        !paneActiveFile.loading &&
                        !paneActiveFile.error &&
                        paneActiveFile.kind === 'text' && (() => {
                          const editorState = editorByPath[paneActiveFile.path];
                          const isEditing = true;
                          const sourceText = editorState?.value ?? paneActiveFile.content ?? '';
                          const lines = sourceText.split('\n');
                          const width = String(Math.max(1, lines.length)).length;
                          const gutterWidth = Math.max(2, width + 2);
                          return (
                            <div className="workdir-text-container">
                              {editorState?.error && (
                                <div className="workdir-text-error workdir-viewer-body">保存失败：{editorState.error}</div>
                              )}
                              {isEditing ? (
                                <div className="workdir-text-editor">
                                  <div
                                      className="workdir-text-gutter-column"
                                      style={{ width: `${gutterWidth}ch` }}
                                      ref={(el) => {
                                        editorGutterRefs.current[paneActiveFile.path] = el;
                                      }}
                                      onMouseDown={() => clearHighlight(paneActiveFile.path)}
                                    >
                                      {lines.map((_, index) => {
                                        const isHighlighted =
                                          highlightLine?.path === paneActiveFile.path &&
                                          highlightLine.line === index + 1;
                                        return (
                                          <div
                                            key={`${paneActiveFile.path}-editor-line-${index + 1}`}
                                            className={`workdir-text-gutter-line${isHighlighted ? ' highlight' : ''}`}
                                          >
                                            {index + 1}
                                          </div>
                                        );
                                      })}
                                    </div>
                                    <textarea
                                      className="workdir-text-area"
                                      ref={(el) => {
                                        editorRefs.current[paneActiveFile.path] = el;
                                      }}
                                      value={editorState?.value ?? ''}
                                      onChange={(event) => updateEditorValue(paneActiveFile.path, event.currentTarget.value)}
                                      onMouseDown={() => clearHighlight(paneActiveFile.path)}
                                      onContextMenu={(event) => {
                                        event.preventDefault();
                                        event.stopPropagation();
                                        setEditorMenu({
                                        x: event.clientX,
                                        y: event.clientY,
                                        path: paneActiveFile.path,
                                      });
                                    }}
                                    onScroll={(event) => {
                                      const gutter = editorGutterRefs.current[paneActiveFile.path];
                                      if (gutter) {
                                        gutter.scrollTop = event.currentTarget.scrollTop;
                                      }
                                    }}
                                    onKeyDown={handleEditorKeyDown(paneActiveFile.path)}
                                    spellCheck={false}
                                    wrap="off"
                                    disabled={editorState?.saving}
                                  />
                                </div>
                              ) : (
                                <div className="workdir-text-viewer workdir-viewer-body">
                                  {lines.map((line, index) => (
                                    <div key={`${paneActiveFile.path}-line-${index + 1}`} className="workdir-text-line">
                                      <span className="workdir-text-gutter" style={{ width: `${gutterWidth}ch` }}>
                                        {index + 1}
                                      </span>
                                      <span className="workdir-text-content">{line || ' '}</span>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          );
                        })()}
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
            onClick={() => {
              const target = contextMenu.targetPath;
              setContextMenu(null);
              openAstSettings(target);
            }}
          >
            AST 分析设置
          </button>
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

      {editorMenu && (
        <div
          className="workdir-context-menu"
          style={{
            top: Math.min(editorMenu.y, window.innerHeight - 120),
            left: Math.min(editorMenu.x, window.innerWidth - 200),
          }}
          onClick={(event) => event.stopPropagation()}
          onContextMenu={(event) => event.preventDefault()}
        >
          <button
            type="button"
            className="workdir-context-item"
            disabled={!editorByPath[editorMenu.path]?.dirty}
            onClick={() => {
              const path = editorMenu.path;
              setEditorMenu(null);
              cancelEditFile(path);
            }}
          >
            还原未保存
          </button>
        </div>
      )}

      {astSettingsRoot && (
        <div
          className="workdir-dialog-backdrop"
          onClick={() => {
            if (!astSettingsSaving) closeAstSettings();
          }}
        >
          <div className="workdir-dialog ast-settings-dialog" onClick={(event) => event.stopPropagation()}>
            <div className="workdir-dialog-title">AST 分析设置</div>
            <div className="workdir-dialog-body">
              <div className="workdir-ast-setting-row">
                <label>工作路径</label>
                <div className="workdir-ast-setting-path">{astSettingsRoot}</div>
              </div>
              {astSettingsPath && (
                <div className="workdir-ast-setting-actions">
                  <button
                    type="button"
                    className="workdir-dialog-btn ghost"
                    onClick={() => appendAstPath(astSettingsPath, astIgnorePaths, setAstIgnorePaths)}
                    disabled={astSettingsSaving || astSettingsLoading}
                  >
                    忽略当前路径
                  </button>
                  <button
                    type="button"
                    className="workdir-dialog-btn ghost"
                    onClick={() => appendAstPath(astSettingsPath, astIncludeOnlyPaths, setAstIncludeOnlyPaths)}
                    disabled={astSettingsSaving || astSettingsLoading}
                  >
                    仅包含当前路径
                  </button>
                  <button
                    type="button"
                    className="workdir-dialog-btn ghost"
                    onClick={() => appendAstPath(astSettingsPath, astForceIncludePaths, setAstForceIncludePaths)}
                    disabled={astSettingsSaving || astSettingsLoading}
                  >
                    必定包含当前路径
                  </button>
                </div>
              )}

              <div className="workdir-ast-label-row">
                <label>忽略路径（每行一个）</label>
                <button
                  type="button"
                  className="workdir-ast-pick-btn"
                  onClick={() => void pickAstFolder('ignore')}
                  disabled={astSettingsLoading || astSettingsSaving}
                >
                  选择文件夹
                </button>
              </div>
              <textarea
                className="workdir-dialog-textarea"
                value={astIgnorePaths}
                onChange={(event) => setAstIgnorePaths(event.currentTarget.value)}
                placeholder="例如：D:\\repo\\node_modules"
                disabled={astSettingsLoading || astSettingsSaving}
              />

              <div className="workdir-ast-label-row">
                <label>仅包含路径（每行一个）</label>
                <button
                  type="button"
                  className="workdir-ast-pick-btn"
                  onClick={() => void pickAstFolder('include')}
                  disabled={astSettingsLoading || astSettingsSaving}
                >
                  选择文件夹
                </button>
              </div>
              <textarea
                className="workdir-dialog-textarea"
                value={astIncludeOnlyPaths}
                onChange={(event) => setAstIncludeOnlyPaths(event.currentTarget.value)}
                placeholder="仅扫描这些路径及其子目录"
                disabled={astSettingsLoading || astSettingsSaving}
              />

              <div className="workdir-ast-label-row">
                <label>必定包含路径（每行一个）</label>
                <button
                  type="button"
                  className="workdir-ast-pick-btn"
                  onClick={() => void pickAstFolder('force')}
                  disabled={astSettingsLoading || astSettingsSaving}
                >
                  选择文件夹
                </button>
              </div>
              <textarea
                className="workdir-dialog-textarea"
                value={astForceIncludePaths}
                onChange={(event) => setAstForceIncludePaths(event.currentTarget.value)}
                placeholder="即使被 gitignore 或忽略规则命中也会扫描"
                disabled={astSettingsLoading || astSettingsSaving}
              />

              <label>语言类型过滤（不选表示不过滤）</label>
              <div className="workdir-ast-language-grid">
                {AST_LANGUAGE_OPTIONS.map((option) => (
                  <label key={option.id} className="workdir-ast-language-option">
                    <input
                      type="checkbox"
                      checked={astIncludeLanguages.includes(option.id)}
                      onChange={() => toggleAstLanguage(option.id)}
                      disabled={astSettingsLoading || astSettingsSaving}
                    />
                    <span>{option.label}</span>
                  </label>
                ))}
              </div>
              <div className="workdir-ast-setting-hint">不选表示不过滤（全部语言）</div>

              <label>最大分析文件数</label>
              <input
                className="workdir-dialog-input"
                type="number"
                min={1}
                value={astMaxFiles}
                onChange={(event) => setAstMaxFiles(event.currentTarget.value)}
                disabled={astSettingsLoading || astSettingsSaving}
              />
              {astSettingsLoading && <div className="workdir-ast-setting-hint">正在加载设置...</div>}
              <div className="workdir-ast-setting-hint">
                优先级：必定包含 &gt; 仅包含 &gt; 忽略 &gt; Git 忽略
              </div>
              {astSettingsError && <div className="workdir-dialog-error">{astSettingsError}</div>}
            </div>
            <div className="workdir-dialog-actions">
              <button
                type="button"
                className="workdir-dialog-btn ghost"
                onClick={closeAstSettings}
                disabled={astSettingsSaving}
              >
                取消
              </button>
              <button
                type="button"
                className="workdir-dialog-btn primary"
                onClick={() => void handleSaveAstSettings()}
                disabled={astSettingsSaving || astSettingsLoading}
              >
                {astSettingsSaving ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
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

      {dragGhost.visible && (
        <div
          className="workdir-drag-ghost"
          style={{ transform: `translate3d(${dragGhost.x + 12}px, ${dragGhost.y + 12}px, 0)` }}
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path
              d="M4 2h5l3 3v9H4z"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.2"
              strokeLinejoin="round"
            />
            <path d="M9 2v3h3" fill="none" stroke="currentColor" strokeWidth="1.2" />
          </svg>
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
