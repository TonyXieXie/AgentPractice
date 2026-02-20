import { useState, useEffect, useLayoutEffect, useRef, useMemo, useCallback } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { open as openDialog } from '@tauri-apps/plugin-dialog';
import { readDir, watchImmediate, type UnwatchFn } from '@tauri-apps/plugin-fs';
import { openPath, revealItemInDir } from '@tauri-apps/plugin-opener';
import { getCurrentWindow, LogicalSize } from '@tauri-apps/api/window';
import { WebviewWindow } from '@tauri-apps/api/webviewWindow';
import './App.css';
import { exportConfigFile, importConfigFile } from './configExchange';
import {
  Message,
  MessageAttachment,
  LLMConfig,
  LLMCall,
  ToolPermissionRequest,
  ReasoningEffort,
  AgentMode,
  AgentConfig,
  ContextEstimate
} from './types';
import {
  sendMessageAgentStream,
  getDefaultConfig,
  getAppConfig,
  getConfig,
  getConfigs,
  getSession,
  getSessionMessages,
  getSessionLLMCalls,
  getSessionAgentSteps,
  stopAgentStream,
  rollbackSession,
  revertPatch,
  getToolPermissions,
  updateToolPermission,
  updateConfig,
  updateSession,
  AgentStep,
  AgentStepWithMessage,
  getAttachmentUrl,
  getAstSettings,
  updateAstSettings,
  notifyAstChanges,
} from './api';
import ConfigManager from './components/ConfigManager';
import SessionList from './components/SessionList';
import DebugPanel from './components/DebugPanel';
import AgentStepView from './components/AgentStepView';
import ConfirmDialog from './components/ConfirmDialog';
import { loadExtraWorkPaths, migrateExtraWorkPaths, saveExtraWorkPaths } from './workdirStorage';

const DRAFT_SESSION_KEY = '__draft__';

const REASONING_OPTIONS: { value: ReasoningEffort; label: string }[] = [
  { value: 'none', label: 'none' },
  { value: 'minimal', label: 'minimal' },
  { value: 'low', label: 'low' },
  { value: 'medium', label: 'medium' },
  { value: 'high', label: 'high' },
  { value: 'xhigh', label: 'xhigh' },
];

const AGENT_MODE_OPTIONS: { value: AgentMode; label: string; description: string }[] = [
  { value: 'default', label: '默认', description: '使用默认安全策略' },
  { value: 'super', label: '超级', description: '允许所有操作' },
];

const IS_MAC = typeof navigator !== 'undefined' && /mac/i.test(navigator.userAgent);
const MAX_CONCURRENT_STREAMS = 10;
const WORK_PATH_MAX_LENGTH = 200;
const MAIN_WINDOW_BOUNDS_KEY = 'mainWindowBounds';
const WORKDIR_BOUNDS_KEY = 'workdirWindowBounds';
const MAIN_DEFAULT_WIDTH = 1200;
const MAIN_DEFAULT_HEIGHT = 950;
const WORKDIR_DEFAULT_WIDTH = 1200;
const WORKDIR_DEFAULT_HEIGHT = 800;
const DEFAULT_MAX_CONTEXT_TOKENS = 200000;
const CONTEXT_RING_RADIUS = 10;
const AST_DEFAULT_MAX_FILES = 500;
const STREAM_STALL_MS = 90_000;
const STREAM_STALL_CHECK_MS = 5_000;
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
const SESSION_SWITCH_PROFILE = true;
const MESSAGE_PAGE_SIZE = 80;
const MESSAGE_LOAD_THRESHOLD_PX = 240;
const MESSAGE_OVERSCAN_PX = 600;
const MESSAGE_ESTIMATED_HEIGHT = 220;

const extractRunShellCommand = (raw?: string): string => {
  if (!raw) return '';
  let text = String(raw).trim();
  if (!text) return '';
  const bracketStart = text.indexOf('[');
  const bracketEnd = text.lastIndexOf(']');
  if (text.startsWith('run_shell[') && bracketStart >= 0 && bracketEnd > bracketStart) {
    text = text.slice(bracketStart + 1, bracketEnd).trim();
  }
  try {
    const parsed = JSON.parse(text) as { command?: string } | null;
    if (parsed && typeof parsed === 'object' && typeof parsed.command === 'string') {
      return parsed.command;
    }
  } catch {
    // fall through
  }
  const match = text.match(/"command"\s*:\s*"((?:\\.|[^"])*)"/);
  if (!match) return '';
  try {
    return JSON.parse(`"${match[1].replace(/"/g, '\\"')}"`);
  } catch {
    return match[1];
  }
};

type WorkdirBounds = {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
};

type MainWindowBounds = {
  width?: number;
  height?: number;
};

const getMainWindowBounds = (): MainWindowBounds => {
  const fallback: MainWindowBounds = {
    width: MAIN_DEFAULT_WIDTH,
    height: MAIN_DEFAULT_HEIGHT,
  };
  try {
    const raw = localStorage.getItem(MAIN_WINDOW_BOUNDS_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as Partial<MainWindowBounds> | null;
    if (!parsed || typeof parsed !== 'object') return fallback;
    const next: MainWindowBounds = { ...fallback };
    if (Number.isFinite(parsed.width)) next.width = Math.max(800, Math.round(parsed.width as number));
    if (Number.isFinite(parsed.height)) next.height = Math.max(600, Math.round(parsed.height as number));
    return next;
  } catch {
    return fallback;
  }
};

const getWorkdirWindowBounds = (): WorkdirBounds | null => {
  try {
    const raw = localStorage.getItem(WORKDIR_BOUNDS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<WorkdirBounds> | null;
    if (!parsed || typeof parsed !== 'object') return null;
    const next: WorkdirBounds = {};
    if (Number.isFinite(parsed.width)) next.width = Math.max(640, Math.round(parsed.width as number));
    if (Number.isFinite(parsed.height)) next.height = Math.max(480, Math.round(parsed.height as number));
    if (Number.isFinite(parsed.x)) next.x = Math.round(parsed.x as number);
    if (Number.isFinite(parsed.y)) next.y = Math.round(parsed.y as number);
    return next;
  } catch {
    return null;
  }
};

const hashPath = (value: string) => {
  let hash = 5381;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash << 5) + hash + value.charCodeAt(i);
  }
  return (hash >>> 0).toString(16);
};

const makeWorkdirLabel = (path: string) => {
  const normalized = path.toLowerCase();
  const base = normalized
    .replace(/[^a-zA-Z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 12);
  const safeBase = base || 'path';
  return `workdir-${safeBase}-${hashPath(normalized)}`;
};

const formatWorkPath = (path: string) => {
  if (!path) return '点击选择工作路径';
  if (path.length <= WORK_PATH_MAX_LENGTH) return path;
  const tailLength = Math.max(1, WORK_PATH_MAX_LENGTH - 3);
  return `...${path.slice(-tailLength)}`;
};

const getParentPath = (filePath: string) => {
  const trimmed = filePath.replace(/[\\/]+$/, '');
  const idx = Math.max(trimmed.lastIndexOf('\\'), trimmed.lastIndexOf('/'));
  if (idx < 0) {
    return trimmed;
  }
  const parent = trimmed.slice(0, idx);
  if (/^[a-zA-Z]:$/.test(parent)) {
    return `${parent}\\`;
  }
  return parent || trimmed;
};

const getBaseName = (filePath: string) => {
  const trimmed = filePath.replace(/[\\/]+$/, '');
  const idx = Math.max(trimmed.lastIndexOf('\\'), trimmed.lastIndexOf('/'));
  if (idx < 0) return trimmed;
  return trimmed.slice(idx + 1) || trimmed;
};

const isAbsolutePath = (value: string) =>
  /^(?:[a-zA-Z]:[\\/]|\\\\|\/)/.test(value);

const isBareFilename = (value: string) =>
  Boolean(value) && !/[\\/]/.test(value) && !value.startsWith('./') && !value.startsWith('../');

const FILE_EXT_PATTERN = /\.[A-Za-z0-9]{1,10}$/;
const FILE_HASH_PATTERN = /^(.*)#L(\d+)(?:C(\d+))?(?:-L?(\d+))?$/i;
const FILE_LINE_PATTERN = /^(.*?)(?::(\d+))(?:-(\d+))?(?:[:#](\d+))?$/;
const TRAILING_PUNCTUATION = /[)\],.;:!?}]+$/;

const hasScheme = (value: string) => /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(value);

const ESCAPE_SEGMENTS = new Set(['n', 'r', 't', 'b', 'f', 'v', '0']);
const MAC_ABSOLUTE_PREFIX = /^(Users|Volumes|private|System|Library|Applications|opt|etc|var|tmp)[\\/]/;

const normalizeMacAbsolutePath = (value: string) => {
  if (!IS_MAC) return value;
  if (!value) return value;
  if (value.startsWith('/') || value.startsWith('./') || value.startsWith('../')) return value;
  if (/^[a-zA-Z]:[\\/]/.test(value)) return value;
  if (/^file:\/\//i.test(value)) return value;
  if (MAC_ABSOLUTE_PREFIX.test(value)) return `/${value}`;
  return value;
};

const looksLikeFilePath = (value: string) => {
  if (!value) return false;
  if (/^www\./i.test(value)) return false;
  if (/^file:\/\//i.test(value)) return true;
  if (/^[a-zA-Z]:[\\/]/.test(value)) {
    const rest = value.slice(3);
    const firstSegment = rest.split(/[\\/]/)[0];
    if (firstSegment.length === 1 && ESCAPE_SEGMENTS.has(firstSegment.toLowerCase())) {
      return false;
    }
    return true;
  }
  if (value.startsWith('\\\\')) return true;
  if (value.startsWith('/') || value.startsWith('./') || value.startsWith('../')) return true;
  if (value.includes('/') || value.includes('\\')) return true;
  return FILE_EXT_PATTERN.test(value);
};

const normalizeFileHref = (value: string) => {
  if (!value) return '';
  if (value.startsWith('file://')) {
    try {
      const url = new URL(value);
      let pathname = decodeURIComponent(url.pathname || '');
      if (/^\/[A-Za-z]:\//.test(pathname)) {
        pathname = pathname.slice(1);
      }
      return normalizeMacAbsolutePath(pathname);
    } catch {
      return normalizeMacAbsolutePath(value);
    }
  }
  return normalizeMacAbsolutePath(value);
};

const stripTrailingPunctuation = (value: string) => {
  const match = value.match(TRAILING_PUNCTUATION);
  if (!match) return value;
  return value.slice(0, -match[0].length);
};

const parseFileLocation = (rawValue: string) => {
  const trimmed = rawValue.trim();
  if (!trimmed) return { path: '' };
  const normalized = stripTrailingPunctuation(trimmed);
  const candidate = normalized || trimmed;
  const hashMatch = candidate.match(FILE_HASH_PATTERN);
  if (hashMatch) {
    const path = hashMatch[1];
    if (looksLikeFilePath(path) && (!hasScheme(path) || /^[a-zA-Z]:[\\/]/.test(path) || /^file:\/\//i.test(path))) {
      return {
        path,
        line: Number(hashMatch[2]),
        column: hashMatch[3] ? Number(hashMatch[3]) : undefined,
      };
    }
  }
  const lineMatch = candidate.match(FILE_LINE_PATTERN);
  if (lineMatch) {
    const path = lineMatch[1];
    if (looksLikeFilePath(path) && (!hasScheme(path) || /^[a-zA-Z]:[\\/]/.test(path) || /^file:\/\//i.test(path))) {
      return {
        path,
        line: Number(lineMatch[2]),
        column: lineMatch[4] ? Number(lineMatch[4]) : undefined,
      };
    }
  }
  if (looksLikeFilePath(candidate) && (!hasScheme(candidate) || /^[a-zA-Z]:[\\/]/.test(candidate) || /^file:\/\//i.test(candidate))) {
    return { path: candidate };
  }
  return { path: '' };
};

const joinPath = (base: string, child: string) => {
  if (!base) return child;
  const separator = base.includes('\\') ? '\\' : '/';
  if (base.endsWith('\\') || base.endsWith('/')) {
    return `${base}${child}`;
  }
  return `${base}${separator}${child}`;
};

const pathExists = async (filePath: string) => {
  const parent = getParentPath(filePath);
  const name = getBaseName(filePath);
  if (!parent || !name) return false;
  try {
    const entries = await readDir(parent);
    return entries.some((entry) => entry.name === name);
  } catch {
    return false;
  }
};

const findWorkFileByName = async (root: string, fileName: string) => {
  const target = fileName.trim().toLowerCase();
  if (!root || !target) return '';

  const queue: Array<{ path: string; depth: number }> = [{ path: root, depth: 0 }];
  const maxDepth = 8;
  const maxNodes = 5000;
  let visited = 0;

  while (queue.length > 0) {
    const current = queue.shift();
    if (!current) break;
    let entries;
    try {
      entries = await readDir(current.path);
    } catch {
      continue;
    }

    const sorted = [...entries].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
    for (const entry of sorted) {
      visited += 1;
      if (visited > maxNodes) {
        return '';
      }
      const entryPath = joinPath(current.path, entry.name);
      if (!entry.isDirectory) {
        if (entry.name.toLowerCase() === target) {
          return entryPath;
        }
        continue;
      }
      if (current.depth < maxDepth) {
        queue.push({ path: entryPath, depth: current.depth + 1 });
      }
    }
  }

  return '';
};

const resolveRelativePath = async (relativePath: string, roots: string[]) => {
  if (!relativePath || roots.length === 0) return relativePath;
  if (isBareFilename(relativePath)) {
    for (const root of roots) {
      const matched = await findWorkFileByName(root, relativePath);
      if (matched) return matched;
    }
    return joinPath(roots[0], relativePath);
  }
  for (const root of roots) {
    const candidate = joinPath(root, relativePath);
    if (await pathExists(candidate)) {
      return candidate;
    }
  }
  return joinPath(roots[0], relativePath);
};

const estimateTokensForText = (text: string) => {
  if (!text) return 0;
  let ascii = 0;
  let nonAscii = 0;
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    if (code <= 0x7f) {
      ascii += 1;
    } else {
      nonAscii += 1;
    }
  }
  return Math.ceil(ascii / 4) + nonAscii;
};

const formatTokenCount = (value: number) => {
  if (!Number.isFinite(value)) return '0';
  if (value >= 10000) {
    return `${(value / 1000).toFixed(1)}k`;
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(2)}k`;
  }
  return String(Math.max(0, Math.round(value)));
};

const normalizeContextEstimate = (estimate: any, fallbackTime?: string | null): ContextEstimate => ({
  total: Number(estimate?.total) || 0,
  system: Number(estimate?.system) || 0,
  history: Number(estimate?.history) || 0,
  tools: Number(estimate?.tools) || 0,
  other: Number(estimate?.other) || 0,
  max_tokens: Number.isFinite(Number(estimate?.max_tokens)) ? Number(estimate?.max_tokens) : undefined,
  updated_at: typeof estimate?.updated_at === 'string' ? estimate.updated_at : (fallbackTime || undefined),
});

const collectTextFromContent = (content: any, bucket: string[]) => {
  if (!content) return;
  if (typeof content === 'string') {
    if (content.trim()) bucket.push(content);
    return;
  }
  if (Array.isArray(content)) {
    content.forEach((item) => collectTextFromContent(item, bucket));
    return;
  }
  if (typeof content === 'object') {
    if (typeof content.text === 'string') {
      if (content.text.trim()) bucket.push(content.text);
    }
    if (typeof content.content === 'string') {
      if (content.content.trim()) bucket.push(content.content);
    }
    if (Array.isArray(content.content)) {
      content.content.forEach((item: any) => collectTextFromContent(item, bucket));
    }
  }
};

const estimateTokensForContent = (content: any) => {
  if (!content) return 0;
  const texts: string[] = [];
  collectTextFromContent(content, texts);
  return texts.reduce((sum, text) => sum + estimateTokensForText(text), 0);
};

const estimateTokensFromRequestBreakdown = (request: Record<string, any> | null) => {
  const breakdown = { total: 0, system: 0, history: 0, tools: 0, other: 0 };
  if (!request) return breakdown;

  const addMessageTokens = (role: string, content: any) => {
    const tokens = estimateTokensForContent(content) + 4;
    if (role === 'system' || role === 'developer') {
      breakdown.system += tokens;
    } else {
      breakdown.history += tokens;
    }
  };

  if (Array.isArray(request.messages)) {
    request.messages.forEach((msg: any) => {
      if (!msg) return;
      const role = typeof msg.role === 'string' ? msg.role : '';
      addMessageTokens(role, msg.content);
    });
  }

  const handleInputItem = (item: any) => {
    if (!item) return;
    if (typeof item === 'string') {
      breakdown.other += estimateTokensForText(item) + 4;
      return;
    }
    if (Array.isArray(item)) {
      item.forEach(handleInputItem);
      return;
    }
    if (typeof item === 'object') {
      if (typeof item.role === 'string') {
        addMessageTokens(item.role, item.content);
        return;
      }
      if (Object.prototype.hasOwnProperty.call(item, 'content')) {
        breakdown.other += estimateTokensForContent(item.content) + 4;
        return;
      }
      breakdown.other += estimateTokensForContent(item) + 4;
    }
  };

  if (request.input) {
    if (Array.isArray(request.input)) {
      request.input.forEach(handleInputItem);
    } else {
      handleInputItem(request.input);
    }
  }

  if (typeof request.instructions === 'string') {
    breakdown.system += estimateTokensForText(request.instructions);
  }

  if (typeof request.prompt === 'string') {
    breakdown.other += estimateTokensForText(request.prompt);
  }

  if (request.tools) {
    try {
      breakdown.tools += estimateTokensForText(JSON.stringify(request.tools));
    } catch {
      // ignore tool serialization errors
    }
  }

  breakdown.total = breakdown.system + breakdown.history + breakdown.tools + breakdown.other;
  return breakdown;
};

const getLatestRequestPayload = (calls: LLMCall[], history: Message[]) => {
  for (let i = calls.length - 1; i >= 0; i -= 1) {
    const payload = calls[i]?.request_json;
    if (payload) return payload as Record<string, any>;
  }
  for (let i = history.length - 1; i >= 0; i -= 1) {
    const payload = history[i]?.raw_request;
    if (payload) return payload as Record<string, any>;
  }
  return null;
};

const buildEstimatedRequestPayload = (
  history: Message[],
  userMessage: string,
  baseSystemPrompt?: string,
  maxHistory: number = 20
) => {
  const trimmed = history.length > maxHistory ? history.slice(-maxHistory) : history;
  const messages = trimmed.map((msg) => ({
    role: msg.role,
    content: msg.content,
  }));
  if (baseSystemPrompt && baseSystemPrompt.trim()) {
    messages.unshift({ role: 'system', content: baseSystemPrompt });
  }
  messages.push({ role: 'user', content: userMessage });
  return { messages };
};

const findItemIndexByOffset = (offsets: number[], value: number) => {
  if (offsets.length <= 1) return 0;
  let low = 0;
  let high = offsets.length - 1;
  while (low < high) {
    const mid = Math.floor((low + high) / 2);
    if (offsets[mid] <= value) {
      low = mid + 1;
    } else {
      high = mid;
    }
  }
  return Math.max(0, low - 1);
};

type MeasuredMessageProps = {
  rowKey: string;
  className: string;
  onHeight: (key: string, height: number) => void;
  children: ReactNode;
};

const MeasuredMessage = ({ rowKey, className, onHeight, children }: MeasuredMessageProps) => {
  const ref = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => onHeight(rowKey, el.offsetHeight);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => measure());
    observer.observe(el);
    return () => observer.disconnect();
  }, [rowKey, onHeight]);

  return (
    <div ref={ref} className={className}>
      {children}
    </div>
  );
};

type PendingAttachment = {
  id: string;
  name: string;
  mime: string;
  size: number;
  width?: number;
  height?: number;
  previewUrl: string;
  dataBase64: string;
};

type QueueItem = {
  id: string;
  message: string;
  sessionId: string | null;
  sessionKey: string;
  configId: string;
  agentMode: AgentMode;
  agentProfileId?: string | null;
  workPath?: string;
  extraWorkPaths?: string[];
  enqueuedAt: number;
  attachments?: PendingAttachment[];
  estimatedRequest?: Record<string, any>;
};

type InFlightState = {
  abortController: AbortController;
  stopRequested: boolean;
  activeAssistantId: number | null;
  tempAssistantId: number;
  sessionKey: string;
  lastEventAt: number;
  stalled?: boolean;
};

type PendingContextEstimate = {
  sessionKey: string;
  queueId: string;
  payload: Record<string, any>;
};

function App() {
  const [inputMsg, setInputMsg] = useState('');
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);
  const [dragActive, setDragActive] = useState(false);
  const [imagePreview, setImagePreview] = useState<{ src: string; name?: string } | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [currentConfig, setCurrentConfig] = useState<LLMConfig | null>(null);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const [currentWorkPath, setCurrentWorkPath] = useState('');
  const [showConfigManager, setShowConfigManager] = useState(false);
  const [sessionRefreshTrigger, setSessionRefreshTrigger] = useState(0);
  const [showSidebar] = useState(true);
  const [allConfigs, setAllConfigs] = useState<LLMConfig[]>([]);
  const [showConfigSelector, setShowConfigSelector] = useState(false);
  const [showDebugPanel, setShowDebugPanel] = useState(false);
  const showDebugPanelRef = useRef(false);
  const [debugFocus, setDebugFocus] = useState<{ key: string; messageId: number; iteration: number; callId?: number } | null>(null);
  const [llmCalls, setLlmCalls] = useState<LLMCall[]>([]);
  const [pendingContextEstimate, setPendingContextEstimate] = useState<PendingContextEstimate | null>(null);
  const [contextEstimateBySession, setContextEstimateBySession] = useState<Record<string, ContextEstimate>>({});
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const [heightTick, setHeightTick] = useState(0);
  const [virtualRange, setVirtualRange] = useState({ start: 0, end: 0 });
  const debugRefreshRef = useRef<Record<string, { last: number; timer: number | null }>>({});
  const [agentMode, setAgentMode] = useState<AgentMode>('default');
  const [agentConfig, setAgentConfig] = useState<AgentConfig | null>(null);
  const [currentAgentProfileId, setCurrentAgentProfileId] = useState<string | null>(null);

  const astEnabled = agentConfig?.ast_enabled ?? true;
  const [showProfileSelector, setShowProfileSelector] = useState(false);
  const [showReasoningSelector, setShowReasoningSelector] = useState(false);
  const [showAgentModeSelector, setShowAgentModeSelector] = useState(false);
  const [queueTick, setQueueTick] = useState(0);
  const [inFlightTick, setInFlightTick] = useState(0);
  const [permissionTick, setPermissionTick] = useState(0);
  const [unreadBySession, setUnreadBySession] = useState<Record<string, boolean>>({});
  const [patchRevertBusy, setPatchRevertBusy] = useState(false);
  const [rollbackTarget, setRollbackTarget] = useState<{ messageId: number; keepInput?: boolean } | null>(null);
  const [workPathMenu, setWorkPathMenu] = useState<{ x: number; y: number } | null>(null);
  const [workPathMenuPlacement, setWorkPathMenuPlacement] = useState<{ x: number; y: number } | null>(null);
  const workPathMenuRef = useRef<HTMLDivElement | null>(null);
  const [showAstSettings, setShowAstSettings] = useState(false);
  const [astSettingsRoot, setAstSettingsRoot] = useState('');
  const [astSettingsLoading, setAstSettingsLoading] = useState(false);
  const [astSettingsSaving, setAstSettingsSaving] = useState(false);
  const [astSettingsError, setAstSettingsError] = useState<string | null>(null);
  const [astIgnorePaths, setAstIgnorePaths] = useState('');
  const [astIncludeOnlyPaths, setAstIncludeOnlyPaths] = useState('');
  const [astForceIncludePaths, setAstForceIncludePaths] = useState('');
  const [astIncludeLanguages, setAstIncludeLanguages] = useState<string[]>([]);
  const [astMaxFiles, setAstMaxFiles] = useState(String(AST_DEFAULT_MAX_FILES));
  const [isMaximized, setIsMaximized] = useState(false);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);
  const streamingRef = useRef(false);
  const scrollRafRef = useRef<number | null>(null);
  const forceAutoScrollRef = useRef(false);
  const userScrollRef = useRef(false);
  const lastUserScrollAtRef = useRef(0);
  const lastScrollTopRef = useRef(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const messagesCacheRef = useRef<Record<string, Message[]>>({});
  const messagePagingRef = useRef<Record<string, { loading: boolean; hasMore: boolean; oldestId: number | null }>>({});
  const pendingPrependRef = useRef<{ anchorKey: string; anchorOffset: number } | null>(null);
  const heightUpdateRef = useRef<number | null>(null);
  const rowHeightsRef = useRef<Map<string, number>>(new Map());
  const containerPaddingRef = useRef<{ top: number; bottom: number }>({ top: 0, bottom: 0 });
  const workPathBySessionRef = useRef<Record<string, string>>({});
  const currentSessionIdRef = useRef<string | null>(null);
  const queueBySessionRef = useRef<Record<string, QueueItem[]>>({});
  const inFlightBySessionRef = useRef<Record<string, InFlightState>>({});
  const pendingPermissionBySessionRef = useRef<Record<string, ToolPermissionRequest | null>>({});
  const permissionBusyBySessionRef = useRef<Record<string, boolean>>({});
  const processingQueueRef = useRef(false);
  const pendingQueueRunRef = useRef(false);
  const lastWorkFileOpenRef = useRef<{ key: string; at: number } | null>(null);
  const astWatchRef = useRef<UnwatchFn | null>(null);
  const astNotifyRef = useRef<{ timer: number | null; paths: Set<string> }>({ timer: null, paths: new Set() });
  const appWindow = useMemo(() => getCurrentWindow(), []);

  useEffect(() => {
    loadDefaultConfig();
    loadAllConfigs();
    loadAgentConfig();
  }, []);

  useEffect(() => {
    if (!IS_MAC) return;
    appWindow.setDecorations(true).catch(() => undefined);
    document.body.dataset.platform = 'mac';
  }, [appWindow]);

  useEffect(() => {
    const bounds = getMainWindowBounds();
    if (!bounds?.width || !bounds?.height) return;
    appWindow.setSize(new LogicalSize(bounds.width, bounds.height)).catch(() => undefined);
  }, [appWindow]);

  useEffect(() => {
    currentSessionIdRef.current = currentSessionId;
  }, [currentSessionId]);

  const isImageFile = (file: File) => {
    if (file.type && file.type.startsWith('image/')) return true;
    const lower = file.name.toLowerCase();
    return /\.(png|jpe?g|gif|bmp|webp|svg|tiff?|heic|heif|avif|ico)$/.test(lower);
  };

  const readFileAsDataUrl = (file: File) => new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new Error('Failed to read file'));
    reader.readAsDataURL(file);
  });

  const getImageDimensions = (url: string) => new Promise<{ width: number; height: number }>((resolve) => {
    const img = new Image();
    img.onload = () => resolve({ width: img.naturalWidth, height: img.naturalHeight });
    img.onerror = () => resolve({ width: 0, height: 0 });
    img.src = url;
  });

  const buildPendingAttachment = async (file: File): Promise<PendingAttachment | null> => {
    if (!isImageFile(file)) return null;
    const previewUrl = URL.createObjectURL(file);
    try {
      const dataUrl = await readFileAsDataUrl(file);
      const commaIndex = dataUrl.indexOf(',');
      const base64 = commaIndex >= 0 ? dataUrl.slice(commaIndex + 1) : '';
      if (!base64) {
        URL.revokeObjectURL(previewUrl);
        return null;
      }
      const match = /^data:(.*?);base64,/i.exec(dataUrl);
      const mime = file.type || (match ? match[1] : 'application/octet-stream');
      const dims = await getImageDimensions(previewUrl);
      const width = dims.width || undefined;
      const height = dims.height || undefined;
      return {
        id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
        name: file.name || 'image',
        mime,
        size: file.size,
        width,
        height,
        previewUrl,
        dataBase64: base64,
      };
    } catch (error) {
      URL.revokeObjectURL(previewUrl);
      return null;
    }
  };

  const addPendingAttachments = async (files: File[]) => {
    const candidates = files.filter((file) => isImageFile(file));
    if (!candidates.length) return;
    const built = await Promise.all(candidates.map((file) => buildPendingAttachment(file)));
    const next = built.filter((item): item is PendingAttachment => Boolean(item));
    if (next.length > 0) {
      setPendingAttachments((prev) => [...prev, ...next]);
    }
  };

  const removePendingAttachment = (attachmentId: string) => {
    setPendingAttachments((prev) => {
      const target = prev.find((item) => item.id === attachmentId);
      if (target) {
        URL.revokeObjectURL(target.previewUrl);
      }
      return prev.filter((item) => item.id !== attachmentId);
    });
  };

  const mapPendingToMessageAttachments = (items: PendingAttachment[]): MessageAttachment[] =>
    items.map((item) => ({
      name: item.name,
      mime: item.mime,
      width: item.width,
      height: item.height,
      size: item.size,
      preview_url: item.previewUrl,
      local_id: item.id,
    }));

  const mapPendingToPayload = (items: PendingAttachment[]) =>
    items.map((item) => ({
      name: item.name,
      mime: item.mime,
      data_base64: item.dataBase64,
      width: item.width,
      height: item.height,
      size: item.size,
    }));

  const getAttachmentPreviewSrc = (attachment: MessageAttachment) => {
    if (attachment.preview_url) return attachment.preview_url;
    if (attachment.id) return getAttachmentUrl(attachment.id, { thumbnail: true, maxSize: 320 });
    return '';
  };

  const getAttachmentFullSrc = (attachment: MessageAttachment) => {
    if (attachment.preview_url) return attachment.preview_url;
    if (attachment.id) return getAttachmentUrl(attachment.id);
    return '';
  };

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

  useEffect(() => {
    let timer: number | null = null;
    let unlisten: (() => void) | null = null;

    const saveBounds = async () => {
      try {
        const isMax = await appWindow.isMaximized();
        if (isMax) return;
        const size = await appWindow.outerSize();
        const payload = {
          width: Math.max(800, Math.round(size.width)),
          height: Math.max(600, Math.round(size.height)),
        };
        localStorage.setItem(MAIN_WINDOW_BOUNDS_KEY, JSON.stringify(payload));
      } catch {
        // ignore
      }
    };

    const scheduleSave = () => {
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        void saveBounds();
      }, 300);
    };

    appWindow.onResized(scheduleSave).then((stop) => {
      unlisten = stop;
    });
    window.addEventListener('beforeunload', scheduleSave);

    return () => {
      if (timer) window.clearTimeout(timer);
      if (unlisten) unlisten();
      window.removeEventListener('beforeunload', scheduleSave);
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
    if (target.closest('.titlebar-actions') || target.closest('.titlebar-btn')) {
      return;
    }
    handleTitlebarMaximize();
  };

  const getMessageKey = useCallback((msg: Message, index: number) => {
    if ((msg as any).metadata?.streaming_placeholder) return 'streaming';
    if (typeof msg.id === 'number') return `id:${msg.id}`;
    return `idx:${index}`;
  }, []);

  const scheduleHeightRecalc = useCallback(() => {
    if (heightUpdateRef.current != null) return;
    heightUpdateRef.current = window.requestAnimationFrame(() => {
      heightUpdateRef.current = null;
      setHeightTick((tick) => tick + 1);
    });
  }, []);

  const updateRowHeight = useCallback(
    (key: string, height: number) => {
      const rounded = Math.max(1, Math.ceil(height));
      const prev = rowHeightsRef.current.get(key);
      if (prev === rounded) return;
      rowHeightsRef.current.set(key, rounded);
      scheduleHeightRecalc();
    },
    [scheduleHeightRecalc]
  );

  const updateContainerPadding = useCallback(() => {
    const container = messagesContainerRef.current;
    if (!container) return;
    const style = window.getComputedStyle(container);
    containerPaddingRef.current = {
      top: parseFloat(style.paddingTop || '0') || 0,
      bottom: parseFloat(style.paddingBottom || '0') || 0,
    };
  }, []);

  const getPagingState = useCallback((sessionKey: string) => {
    if (!messagePagingRef.current[sessionKey]) {
      messagePagingRef.current[sessionKey] = { loading: false, hasMore: true, oldestId: null };
    }
    return messagePagingRef.current[sessionKey];
  }, []);

  const updateScrollToBottomVisibility = useCallback(() => {
    const container = messagesContainerRef.current;
    if (!container) return;
    const scrollable = container.scrollHeight - container.clientHeight;
    if (scrollable <= 0) {
      setShowScrollToBottom(false);
      return;
    }
    const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
    const ratio = distanceToBottom / scrollable;
    const shouldShow = !autoScrollRef.current && ratio >= 0.05;
    setShowScrollToBottom((prev) => (prev === shouldShow ? prev : shouldShow));
  }, []);

  const markUserScroll = useCallback(() => {
    userScrollRef.current = true;
    lastUserScrollAtRef.current = Date.now();
  }, []);

  const scheduleScrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    if (!autoScrollRef.current) return;
    const container = messagesContainerRef.current;
    if (!container) return;
    const distanceToBottom = Math.abs(container.scrollHeight - container.scrollTop - container.clientHeight);
    const forced = forceAutoScrollRef.current;
    const requested: ScrollBehavior = forced ? 'auto' : behavior;
    const useBehavior: ScrollBehavior = requested === 'smooth' && distanceToBottom > 800 ? 'auto' : requested;
    forceAutoScrollRef.current = false;
    if (scrollRafRef.current !== null) {
      window.cancelAnimationFrame(scrollRafRef.current);
    }
    scrollRafRef.current = window.requestAnimationFrame(() => {
      container.scrollTo({ top: container.scrollHeight, behavior: useBehavior });
      lastScrollTopRef.current = container.scrollTop;
      scrollRafRef.current = null;
    });
  }, []);

  const handleScrollToBottomClick = () => {
    autoScrollRef.current = true;
    userScrollRef.current = false;
    lastUserScrollAtRef.current = 0;
    scheduleScrollToBottom('smooth');
    updateScrollToBottomVisibility();
  };

  useEffect(() => {
    if (!autoScrollRef.current) return;
    const container = messagesContainerRef.current;
    if (!container) return;
    const hasStreaming = messages.some((msg) => msg.metadata?.agent_streaming);
    streamingRef.current = hasStreaming;
    const behavior: ScrollBehavior = hasStreaming
      ? 'auto'
      : container.scrollHeight > container.clientHeight
        ? 'smooth'
        : 'auto';
    scheduleScrollToBottom(behavior);
  }, [messages, scheduleScrollToBottom]);

  useEffect(() => {
    updateScrollToBottomVisibility();
  }, [messages, updateScrollToBottomVisibility]);

  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) return;
    const observer = new MutationObserver(() => {
      if (!autoScrollRef.current) return;
      scheduleScrollToBottom(streamingRef.current ? 'auto' : 'smooth');
    });
    observer.observe(container, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, [scheduleScrollToBottom]);

  useEffect(() => {
    if (!showConfigSelector && !showReasoningSelector && !showAgentModeSelector && !showProfileSelector) return;
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (showConfigSelector && !target.closest('.model-selector-inline')) {
        setShowConfigSelector(false);
      }
      if (showReasoningSelector && !target.closest('.reasoning-selector-inline')) {
        setShowReasoningSelector(false);
      }
      if (showAgentModeSelector && !target.closest('.agent-mode-selector-inline')) {
        setShowAgentModeSelector(false);
      }
      if (showProfileSelector && !target.closest('.agent-profile-selector-inline')) {
        setShowProfileSelector(false);
      }
    };

    document.addEventListener('click', handleClickOutside);

    return () => {
      document.removeEventListener('click', handleClickOutside);
    };
  }, [showConfigSelector, showReasoningSelector, showAgentModeSelector, showProfileSelector]);

  useEffect(() => {
    showDebugPanelRef.current = showDebugPanel;
  }, [showDebugPanel]);

  useEffect(() => {
    if (!workPathMenu) {
      setWorkPathMenuPlacement(null);
      return;
    }
    setWorkPathMenuPlacement({ x: workPathMenu.x, y: workPathMenu.y });
  }, [workPathMenu]);

  useLayoutEffect(() => {
    if (!workPathMenu || !workPathMenuPlacement) return;
    const el = workPathMenuRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const margin = 12;
    const maxX = Math.max(margin, window.innerWidth - rect.width - margin);
    const maxY = Math.max(margin, window.innerHeight - rect.height - margin);
    const nextX = Math.min(Math.max(workPathMenu.x, margin), maxX);
    const nextY = Math.min(Math.max(workPathMenu.y, margin), maxY);
    if (nextX === workPathMenuPlacement.x && nextY === workPathMenuPlacement.y) return;
    setWorkPathMenuPlacement({ x: nextX, y: nextY });
  }, [workPathMenu, workPathMenuPlacement]);

  useEffect(() => {
    if (!workPathMenu) return;
    const dismiss = () => setWorkPathMenu(null);
    window.addEventListener('click', dismiss);
    window.addEventListener('blur', dismiss);
    return () => {
      window.removeEventListener('click', dismiss);
      window.removeEventListener('blur', dismiss);
    };
  }, [workPathMenu]);

  useEffect(() => {
    const root = currentWorkPath;
    if (!root || !astEnabled) {
      if (astWatchRef.current) {
        astWatchRef.current();
        astWatchRef.current = null;
      }
      if (astNotifyRef.current.timer != null) {
        window.clearTimeout(astNotifyRef.current.timer);
        astNotifyRef.current.timer = null;
      }
      astNotifyRef.current.paths.clear();
      return;
    }

    scheduleAstNotify(root, [root]);
    let active = true;

    const startWatch = async () => {
      try {
        const unwatch = await watchImmediate(
          root,
          (event) => {
            if (!active) return;
            const paths = Array.isArray(event?.paths) ? event.paths : [];
            if (paths.length > 0) {
              scheduleAstNotify(root, paths);
            } else {
              scheduleAstNotify(root, [root]);
            }
          },
          { recursive: true }
        );
        if (!active) {
          unwatch();
          return;
        }
        if (astWatchRef.current) {
          astWatchRef.current();
        }
        astWatchRef.current = unwatch;
      } catch (error) {
        console.warn('Failed to watch AST root:', error);
      }
    };

    void startWatch();

    return () => {
      active = false;
      if (astWatchRef.current) {
        astWatchRef.current();
        astWatchRef.current = null;
      }
      if (astNotifyRef.current.timer != null) {
        window.clearTimeout(astNotifyRef.current.timer);
        astNotifyRef.current.timer = null;
      }
      astNotifyRef.current.paths.clear();
    };
  }, [currentWorkPath, astEnabled]);

  useEffect(() => {
    if (!showAstSettings || !astSettingsRoot) return;
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
  }, [showAstSettings, astSettingsRoot]);

  useEffect(() => {
    if (showDebugPanel && currentSessionId) {
      refreshSessionDebug(currentSessionId);
    }
  }, [showDebugPanel, currentSessionId, sessionRefreshTrigger]);

  useEffect(() => {
    if (showDebugPanel) return;
    Object.values(debugRefreshRef.current).forEach((entry) => {
      if (entry.timer != null) {
        window.clearTimeout(entry.timer);
      }
    });
    debugRefreshRef.current = {};
  }, [showDebugPanel]);

  useEffect(() => {
    return () => {
      Object.values(debugRefreshRef.current).forEach((entry) => {
        if (entry.timer != null) {
          window.clearTimeout(entry.timer);
        }
      });
      debugRefreshRef.current = {};
    };
  }, []);

  useEffect(() => {
    if (!showDebugPanel && debugFocus) {
      setDebugFocus(null);
    }
  }, [showDebugPanel, debugFocus]);

  const applyAstSettingsPayload = async (
    payload: { root: string } & Record<string, any>,
    options?: { close?: boolean }
  ) => {
    if (!payload.root) return false;
    setAstSettingsSaving(true);
    setAstSettingsError(null);
    try {
      const result = await updateAstSettings(payload);
      const settings = result?.settings || payload;
      const ignoreList = Array.isArray(settings.ignore_paths) ? settings.ignore_paths : [];
      const includeList = Array.isArray(settings.include_only_paths) ? settings.include_only_paths : [];
      const forceList = Array.isArray(settings.force_include_paths) ? settings.force_include_paths : [];
      setAstIgnorePaths(ignoreList.join('\n'));
      setAstIncludeOnlyPaths(includeList.join('\n'));
      setAstForceIncludePaths(forceList.join('\n'));
      const languageList = normalizeAstLanguageList(settings.include_languages);
      setAstIncludeLanguages(languageList);
      if (settings.max_files !== undefined && settings.max_files !== null) {
        setAstMaxFiles(String(settings.max_files));
      }
      scheduleAstNotify(payload.root, [payload.root]);
      if (options?.close !== false) {
        closeAstSettings();
      }
      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to save AST settings.';
      setAstSettingsError(message);
      return false;
    } finally {
      setAstSettingsSaving(false);
    }
  };

  const handleSaveAstSettings = async () => {
    if (!astSettingsRoot) return;
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
    await applyAstSettingsPayload(payload, { close: true });
  };

  useEffect(() => {
    if (getInFlightCount() === 0) {
      if (Object.keys(pendingPermissionBySessionRef.current).length > 0) {
        pendingPermissionBySessionRef.current = {};
        bumpPermissions();
      }
      return;
    }
    let cancelled = false;
    let inFlight = false;

    const pollPermissions = async () => {
      if (inFlight || cancelled) return;
      inFlight = true;
      try {
        const pending = await getToolPermissions('pending');
        const nextBySession: Record<string, ToolPermissionRequest | null> = {};
        const inFlightKeys = Object.keys(inFlightBySessionRef.current);
        const fallbackKey = inFlightKeys.length === 1 ? inFlightKeys[0] : null;

        for (const item of pending) {
          if (item.tool_name !== 'run_shell') continue;
          const sessionKey = item.session_id ? getSessionKey(item.session_id) : fallbackKey;
          if (!sessionKey) continue;
          if (!nextBySession[sessionKey]) {
            nextBySession[sessionKey] = item;
          }
        }

        if (!cancelled) {
          pendingPermissionBySessionRef.current = nextBySession;
          bumpPermissions();
        }
      } catch (error) {
        if (!cancelled) {
          pendingPermissionBySessionRef.current = {};
          bumpPermissions();
        }
      } finally {
        inFlight = false;
      }
    };

    pollPermissions();
    const timer = window.setInterval(pollPermissions, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [inFlightTick]);

  const loadDefaultConfig = async () => {
    try {
      const config = await getDefaultConfig();
      setCurrentConfig(config);
    } catch (error) {
      console.error('Failed to load default config:', error);
      setShowConfigManager(true);
    }
  };

  const loadAllConfigs = async () => {
    try {
      const configs = await getConfigs();
      setAllConfigs(configs);
    } catch (error) {
      console.error('Failed to load configs:', error);
    }
  };

  const resolveAgentProfileId = (config: AgentConfig | null, desired?: string | null) => {
    if (!config) return desired ?? null;
    const profiles = config.profiles || [];
    if (!profiles.length) return desired ?? null;
    if (desired && profiles.some((profile) => profile.id === desired)) return desired;
    if (config.default_profile && profiles.some((profile) => profile.id === config.default_profile)) {
      return config.default_profile;
    }
    return profiles[0].id;
  };

  const loadAgentConfig = async () => {
    try {
      const appConfig = await getAppConfig();
      const agent = (appConfig?.agent || null) as AgentConfig | null;
      setAgentConfig(agent);
      setCurrentAgentProfileId((prev) => resolveAgentProfileId(agent, prev));
    } catch (error) {
      console.error('Failed to load agent config:', error);
    }
  };

  const getSessionKey = (sessionId: string | null) => sessionId ?? DRAFT_SESSION_KEY;

  const getCurrentSessionKey = () => getSessionKey(currentSessionIdRef.current);

  const getSessionWorkPath = (sessionKey: string) => workPathBySessionRef.current[sessionKey] || '';

  const setSessionWorkPath = (sessionKey: string, nextPath: string) => {
    workPathBySessionRef.current[sessionKey] = nextPath;
    if (sessionKey === getCurrentSessionKey()) {
      setCurrentWorkPath(nextPath);
    }
  };

  const pickWorkPath = async () => {
    try {
      const selected = await openDialog({
        directory: true,
        multiple: false,
        title: '\u9009\u62e9\u5de5\u4f5c\u8def\u5f84'
      });
      if (!selected) return '';
      return Array.isArray(selected) ? (selected[0] || '') : selected;
    } catch (error) {
      console.error('Failed to pick work path:', error);
      return '';
    }
  };

  const scheduleAstNotify = (root: string, paths?: string[]) => {
    if (!root || !astEnabled) return;
    const record = astNotifyRef.current;
    const normalized = paths && paths.length > 0 ? paths : [root];
    normalized.filter(Boolean).forEach((value) => record.paths.add(value));
    if (record.timer != null) return;
    record.timer = window.setTimeout(() => {
      record.timer = null;
      const batch = Array.from(record.paths);
      record.paths.clear();
      void notifyAstChanges(root, batch).catch(() => undefined);
    }, 240);
  };

  const applyWorkPath = async (sessionKey: string, sessionId: string | null, nextPath: string) => {
    if (!nextPath) return;
    setSessionWorkPath(sessionKey, nextPath);
    if (sessionId) {
      try {
        await updateSession(sessionId, { work_path: nextPath });
        setSessionRefreshTrigger((prev) => prev + 1);
      } catch (error) {
        console.error('Failed to update work path:', error);
      }
    }
    if (sessionKey === getCurrentSessionKey()) {
      scheduleAstNotify(nextPath, [nextPath]);
    }
  };

  const bumpQueue = () => setQueueTick((prev) => prev + 1);
  const bumpInFlight = () => setInFlightTick((prev) => prev + 1);
  const bumpPermissions = () => setPermissionTick((prev) => prev + 1);

  const markSessionUnread = (sessionKey: string) => {
    if (!sessionKey || sessionKey === DRAFT_SESSION_KEY) return;
    if (sessionKey === getCurrentSessionKey()) return;
    setUnreadBySession((prev) => (prev[sessionKey] ? prev : { ...prev, [sessionKey]: true }));
  };

  const clearSessionUnread = (sessionKey: string) => {
    if (!sessionKey) return;
    setUnreadBySession((prev) => {
      if (!prev[sessionKey]) return prev;
      const next = { ...prev };
      delete next[sessionKey];
      return next;
    });
  };

  const inFlightBySession = useMemo(() => {
    const next: Record<string, boolean> = {};
    Object.keys(inFlightBySessionRef.current).forEach((key) => {
      if (key !== DRAFT_SESSION_KEY) {
        next[key] = true;
      }
    });
    return next;
  }, [inFlightTick]);

  const pendingPermissionBySession = useMemo(() => {
    const next: Record<string, boolean> = {};
    Object.entries(pendingPermissionBySessionRef.current).forEach(([key, pending]) => {
      if (key !== DRAFT_SESSION_KEY && pending) {
        next[key] = true;
      }
    });
    return next;
  }, [permissionTick]);

  const clearPendingContextForItem = (queueId: string) => {
    setPendingContextEstimate((prev) => {
      if (!prev || prev.queueId !== queueId) return prev;
      return null;
    });
  };

  const clearPendingContextForSession = (sessionKey: string) => {
    setPendingContextEstimate((prev) => {
      if (!prev || prev.sessionKey !== sessionKey) return prev;
      return null;
    });
  };

  const applyContextEstimate = (sessionKey: string, estimate?: ContextEstimate | null) => {
    if (!sessionKey) return;
    setContextEstimateBySession((prev) => {
      if (!estimate) {
        if (!prev[sessionKey]) return prev;
        const next = { ...prev };
        delete next[sessionKey];
        return next;
      }
      const current = prev[sessionKey];
      if (
        current &&
        current.total === estimate.total &&
        current.system === estimate.system &&
        current.history === estimate.history &&
        current.tools === estimate.tools &&
        current.other === estimate.other &&
        current.max_tokens === estimate.max_tokens &&
        current.updated_at === estimate.updated_at
      ) {
        return prev;
      }
      return { ...prev, [sessionKey]: estimate };
    });
  };

  const getInFlightCount = () => Object.keys(inFlightBySessionRef.current).length;

  const getSessionQueue = (sessionKey: string) => queueBySessionRef.current[sessionKey] || [];

  const setSessionQueue = (sessionKey: string, next: QueueItem[]) => {
    if (next.length > 0) {
      queueBySessionRef.current[sessionKey] = next;
    } else {
      delete queueBySessionRef.current[sessionKey];
    }
    bumpQueue();
  };

  const enqueueSessionQueue = (sessionKey: string, item: QueueItem) => {
    const queue = getSessionQueue(sessionKey);
    setSessionQueue(sessionKey, [...queue, item]);
  };

  const removeSessionQueueItem = (sessionKey: string, itemId: string) => {
    const queue = getSessionQueue(sessionKey);
    const next = queue.filter((item) => item.id !== itemId);
    setSessionQueue(sessionKey, next);
  };

  const setSessionMessages = (sessionKey: string, next: Message[]) => {
    messagesCacheRef.current[sessionKey] = next;
    if (sessionKey === getCurrentSessionKey()) {
      setMessages(next);
    }
  };

  const updateSessionMessages = (sessionKey: string, updater: (prev: Message[]) => Message[]) => {
    const prev = messagesCacheRef.current[sessionKey] || [];
    const next = updater(prev);
    messagesCacheRef.current[sessionKey] = next;
    if (sessionKey === getCurrentSessionKey()) {
      setMessages(next);
    }
    return next;
  };

  const stashCurrentMessages = () => {
    const key = getCurrentSessionKey();
    messagesCacheRef.current[key] = messages;
  };

  const clearSessionCache = (sessionKey: string) => {
    delete messagesCacheRef.current[sessionKey];
    delete messagePagingRef.current[sessionKey];
  };

  const migrateSessionKey = (fromKey: string, toKey: string) => {
    if (!fromKey || !toKey || fromKey === toKey) return;

    const cached = messagesCacheRef.current[fromKey];
    if (cached) {
      if (!messagesCacheRef.current[toKey]) {
        messagesCacheRef.current[toKey] = cached;
      } else {
        messagesCacheRef.current[toKey] = [...messagesCacheRef.current[toKey], ...cached];
      }
      delete messagesCacheRef.current[fromKey];
    }

    const paging = messagePagingRef.current[fromKey];
    if (paging) {
      messagePagingRef.current[toKey] = { ...paging };
      delete messagePagingRef.current[fromKey];
    }

    const queued = queueBySessionRef.current[fromKey];
    if (queued && queued.length > 0) {
      const updatedQueue = queued.map((item) => ({
        ...item,
        sessionId: toKey,
        sessionKey: toKey,
      }));
      const existing = queueBySessionRef.current[toKey] || [];
      queueBySessionRef.current[toKey] = [...existing, ...updatedQueue];
      delete queueBySessionRef.current[fromKey];
      bumpQueue();
    }

    const inflight = inFlightBySessionRef.current[fromKey];
    if (inflight) {
      inFlightBySessionRef.current[toKey] = { ...inflight, sessionKey: toKey };
      delete inFlightBySessionRef.current[fromKey];
      bumpInFlight();
    }

    const pending = pendingPermissionBySessionRef.current[fromKey];
    if (pending) {
      pendingPermissionBySessionRef.current[toKey] = pending;
      delete pendingPermissionBySessionRef.current[fromKey];
      bumpPermissions();
    }

    if (fromKey in permissionBusyBySessionRef.current) {
      permissionBusyBySessionRef.current[toKey] = permissionBusyBySessionRef.current[fromKey];
      delete permissionBusyBySessionRef.current[fromKey];
      bumpPermissions();
    }

    if (fromKey in workPathBySessionRef.current) {
      workPathBySessionRef.current[toKey] = workPathBySessionRef.current[fromKey];
      delete workPathBySessionRef.current[fromKey];
      if (toKey === getCurrentSessionKey()) {
        setCurrentWorkPath(workPathBySessionRef.current[toKey] || '');
      }
    }

    migrateExtraWorkPaths(fromKey, toKey);
  };

  const runStreamForItem = async (
    item: QueueItem,
    startSessionKey: string,
    tempUserId: number,
    tempAssistantId: number,
    abortController: AbortController
  ) => {
    const userMessage = item.message;
    const pendingItems = item.attachments || [];
    const tempAttachments = pendingItems.length ? mapPendingToMessageAttachments(pendingItems) : undefined;
    const targetSessionId = item.sessionId;
    let activeSessionKey = startSessionKey;
    let newSessionId: string | null = targetSessionId;
    let currentAssistantId = tempAssistantId;

    const tempUserMsg: Message = {
      id: tempUserId,
      session_id: targetSessionId || '',
      role: 'user',
      content: userMessage,
      timestamp: new Date().toISOString(),
      attachments: tempAttachments,
      raw_request: item.estimatedRequest,
    };

    const tempAssistantMsg: Message = {
      id: tempAssistantId,
      session_id: targetSessionId || '',
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
      metadata: {
        agent_steps: [],
        agent_streaming: true,
        agent_answer_buffers: {},
        agent_thought_buffers: {},
        agent_action_buffers: {},
      }
    };

    clearPendingContextForSession(activeSessionKey);
    updateSessionMessages(activeSessionKey, (prev) => [...prev, tempUserMsg, tempAssistantMsg]);

    try {
      const attachmentPayload = pendingItems.length ? mapPendingToPayload(pendingItems) : undefined;
      const streamGenerator = sendMessageAgentStream(
        {
          message: userMessage,
          session_id: targetSessionId || undefined,
          config_id: item.configId,
          agent_mode: item.agentMode,
          agent_profile: item.agentProfileId || undefined,
          work_path: item.workPath || undefined,
          extra_work_paths: item.extraWorkPaths && item.extraWorkPaths.length > 0 ? item.extraWorkPaths : undefined,
          attachments: attachmentPayload,
        },
        abortController.signal
      );

      for await (const chunk of streamGenerator) {
        const inflightState = inFlightBySessionRef.current[activeSessionKey];
        if (inflightState) {
          inflightState.lastEventAt = Date.now();
        }
        if ((chunk as any).keepalive) {
          continue;
        }
        if (inflightState?.stopRequested && !('session_id' in chunk)) {
          continue;
        }

        if ('session_id' in chunk && typeof chunk.session_id === 'string') {
          newSessionId = chunk.session_id;
          const incomingUserId = (chunk as any).user_message_id;
          const incomingAssistantId = (chunk as any).assistant_message_id;
          if (typeof incomingAssistantId === 'number') {
            currentAssistantId = incomingAssistantId;
            const currentState = inFlightBySessionRef.current[activeSessionKey];
            if (currentState) {
              currentState.activeAssistantId = incomingAssistantId;
              if (currentState.stopRequested) {
                stopAgentStream({ messageId: incomingAssistantId }).catch(() => undefined);
                currentState.abortController.abort();
              }
            }
          }
          if (incomingUserId || incomingAssistantId) {
            const resolvedSessionId = newSessionId || '';
            const incomingAttachments = Array.isArray((chunk as any).user_attachments)
              ? (chunk as any).user_attachments
              : null;
            const hasIncomingAttachments = Boolean(incomingAttachments && incomingAttachments.length > 0);
            updateSessionMessages(activeSessionKey, (prev) =>
              prev.map((msg) => {
                if (typeof incomingUserId === 'number' && msg.id === tempUserId) {
                  return {
                    ...msg,
                    id: incomingUserId,
                    session_id: resolvedSessionId,
                    attachments: hasIncomingAttachments ? incomingAttachments : msg.attachments,
                  };
                }
                if (typeof incomingAssistantId === 'number' && msg.id === tempAssistantId) {
                  return { ...msg, id: incomingAssistantId, session_id: resolvedSessionId };
                }
                if (!msg.session_id && newSessionId) {
                  return { ...msg, session_id: resolvedSessionId };
                }
                return msg;
              })
            );
            if (hasIncomingAttachments && pendingItems.length) {
              pendingItems.forEach((attachment) => {
                URL.revokeObjectURL(attachment.previewUrl);
              });
            }
          }
          if (!targetSessionId && newSessionId && currentSessionIdRef.current === null) {
            currentSessionIdRef.current = newSessionId;
            setCurrentSessionId(newSessionId);
            setSessionRefreshTrigger((prev) => prev + 1);
          }
          if (activeSessionKey === DRAFT_SESSION_KEY && newSessionId) {
            migrateSessionKey(activeSessionKey, newSessionId);
            activeSessionKey = newSessionId;
          } else if (newSessionId) {
            activeSessionKey = newSessionId;
          }
          continue;
        }

        if ('done' in chunk) {
          break;
        }

        const step = chunk as AgentStep;
        if (step.step_type === 'context_estimate') {
          const sessionKeyForEstimate = newSessionId || activeSessionKey;
          applyContextEstimate(sessionKeyForEstimate, normalizeContextEstimate(step.metadata));
          continue;
        }

        updateSessionMessages(activeSessionKey, (prev) =>
          prev.map((msg) => {
            if (msg.id !== currentAssistantId) return msg;

            const existingSteps = (msg.metadata?.agent_steps || []) as AgentStep[];
            let nextSteps = [...existingSteps];
            let nextMetadata = { ...(msg.metadata || {}) } as any;
            const stepMeta = (step.metadata || {}) as any;
            const toolName = String(stepMeta.tool || '').toLowerCase();
            const isRunShellStep = toolName === 'run_shell';
            if (step.step_type === 'action' && isRunShellStep) {
              const command = extractRunShellCommand(stepMeta.input || step.content);
              const streamKey = String(stepMeta.stream_key || '');
              if (command && streamKey) {
                const nextCommands = { ...(nextMetadata.agent_shell_commands || {}) } as Record<string, string>;
                nextCommands[streamKey] = command;
                nextMetadata.agent_shell_commands = nextCommands;
              }
            }
            if ((step.step_type === 'observation' || step.step_type === 'observation_delta') && isRunShellStep) {
              const streamKey = String(stepMeta.stream_key || '');
              const lookupKey = streamKey.endsWith('-obs') ? streamKey.slice(0, -4) : streamKey;
              const commandMap = (nextMetadata.agent_shell_commands || {}) as Record<string, string>;
              const resolved = commandMap[lookupKey] || commandMap[streamKey] || '';
              if (resolved && !stepMeta.command) {
                stepMeta.command = resolved;
              }
            }

            if (step.step_type === 'answer_delta') {
              const streamKey = String(step.metadata?.stream_key || 'answer_default');
              const buffers = { ...(nextMetadata.agent_answer_buffers || {}) } as Record<string, string>;
              const buffer = String(buffers[streamKey] || '') + (step.content || '');
              buffers[streamKey] = buffer;
              nextMetadata.agent_answer_buffers = buffers;
              nextMetadata.agent_streaming = true;

              const streamingIndex = streamKey
                ? nextSteps.findIndex((s) => s.step_type === 'answer' && s.metadata?.streaming && s.metadata?.stream_key === streamKey)
                : nextSteps.findIndex((s) => s.step_type === 'answer' && s.metadata?.streaming);
              if (streamingIndex >= 0) {
                nextSteps[streamingIndex] = {
                  ...nextSteps[streamingIndex],
                  content: buffer
                };
              } else {
                nextSteps.push({ step_type: 'answer', content: buffer, metadata: { streaming: true, stream_key: streamKey } });
              }

              nextMetadata.agent_steps = nextSteps;
              return { ...msg, metadata: nextMetadata };
            }

            if (step.step_type === 'thought_delta') {
              const streamKey = String(step.metadata?.stream_key || 'assistant_content');
              const buffers = { ...(nextMetadata.agent_thought_buffers || {}) } as Record<string, string>;
              let baseBuffer = String(buffers[streamKey] || '');
              const fallbackAnswerIndex = nextSteps.findIndex(
                (s) => s.step_type === 'answer' && s.metadata?.streaming && s.metadata?.stream_key === streamKey
              );
              if (!baseBuffer && fallbackAnswerIndex >= 0) {
                baseBuffer = String(nextSteps[fallbackAnswerIndex].content || '');
              }
              const buffer = baseBuffer + (step.content || '');
              buffers[streamKey] = buffer;
              nextMetadata.agent_thought_buffers = buffers;
              nextMetadata.agent_streaming = true;
              if (fallbackAnswerIndex >= 0) {
                if (nextMetadata.agent_answer_buffers) {
                  nextMetadata.agent_answer_buffers = { ...(nextMetadata.agent_answer_buffers || {}), [streamKey]: '' };
                }
              }

              const streamingIndex = nextSteps.findIndex(
                (s) => s.step_type === 'thought' && s.metadata?.streaming && s.metadata?.stream_key === streamKey
              );
              const fallbackIndex = streamingIndex >= 0 ? -1 : fallbackAnswerIndex;
              if (streamingIndex >= 0) {
                nextSteps[streamingIndex] = {
                  ...nextSteps[streamingIndex],
                  content: buffer,
                  metadata: { ...(nextSteps[streamingIndex].metadata || {}), stream_key: streamKey, streaming: true }
                };
              } else if (fallbackIndex >= 0) {
                nextSteps[fallbackIndex] = {
                  step_type: 'thought',
                  content: buffer,
                  metadata: { ...(nextSteps[fallbackIndex].metadata || {}), stream_key: streamKey, streaming: true }
                };
              } else {
                nextSteps.push({ step_type: 'thought', content: buffer, metadata: { stream_key: streamKey, streaming: true } });
              }

              nextMetadata.agent_steps = nextSteps;
              return { ...msg, metadata: nextMetadata };
            }

            if (step.step_type === 'action_delta') {
              const streamKey = String(step.metadata?.stream_key || 'tool-0');
              const toolName = String(step.metadata?.tool || '');
              const buffers = { ...(nextMetadata.agent_action_buffers || {}) } as Record<string, string>;
              const buffer = String(buffers[streamKey] || '') + (step.content || '');
              buffers[streamKey] = buffer;
              nextMetadata.agent_action_buffers = buffers;
              nextMetadata.agent_streaming = true;

              const display = toolName ? `${toolName}[${buffer}]` : buffer;
              const streamingIndex = nextSteps.findIndex(
                (s) => s.step_type === 'action' && s.metadata?.streaming && s.metadata?.stream_key === streamKey
              );
              if (streamingIndex >= 0) {
                nextSteps[streamingIndex] = {
                  ...nextSteps[streamingIndex],
                  content: display,
                  metadata: { ...(nextSteps[streamingIndex].metadata || {}), stream_key: streamKey, streaming: true, tool: toolName }
                };
              } else {
                nextSteps.push({
                  step_type: 'action',
                  content: display,
                  metadata: { stream_key: streamKey, streaming: true, tool: toolName }
                });
              }

              nextMetadata.agent_steps = nextSteps;
              return { ...msg, metadata: nextMetadata };
            }

            if (step.step_type === 'observation_delta') {
              const streamKey = String(step.metadata?.stream_key || 'obs-0');
              const toolName = String(step.metadata?.tool || '');
              const reset = Boolean(step.metadata?.reset);
              const buffers = { ...(nextMetadata.agent_observation_buffers || {}) } as Record<string, string>;
              let baseBuffer = String(buffers[streamKey] || '');
              if (!baseBuffer) {
                const streamingIndex = nextSteps.findIndex(
                  (s) => s.step_type === 'observation' && s.metadata?.streaming && s.metadata?.stream_key === streamKey
                );
                if (streamingIndex >= 0) {
                  baseBuffer = String(nextSteps[streamingIndex].content || '');
                }
              }
              let buffer = '';
              if (reset) {
                const headerLine = baseBuffer.split('\n')[0] || '';
                buffer = headerLine ? `${headerLine}\n${step.content || ''}` : (step.content || '');
              } else {
                buffer = baseBuffer + (step.content || '');
              }
              buffers[streamKey] = buffer;
              nextMetadata.agent_observation_buffers = buffers;
              nextMetadata.agent_streaming = true;

              const streamingIndex = nextSteps.findIndex(
                (s) => s.step_type === 'observation' && s.metadata?.streaming && s.metadata?.stream_key === streamKey
              );
              if (streamingIndex >= 0) {
                nextSteps[streamingIndex] = {
                  ...nextSteps[streamingIndex],
                  content: buffer,
                  metadata: { ...(nextSteps[streamingIndex].metadata || {}), stream_key: streamKey, streaming: true, tool: toolName }
                };
              } else {
                nextSteps.push({
                  step_type: 'observation',
                  content: buffer,
                  metadata: { stream_key: streamKey, streaming: true, tool: toolName }
                });
              }

              nextMetadata.agent_steps = nextSteps;
              return { ...msg, metadata: nextMetadata };
            }

            if (step.step_type === 'answer') {
              nextMetadata.agent_streaming = false;
              if (step.metadata?.stream_key && nextMetadata.agent_answer_buffers) {
                nextMetadata.agent_answer_buffers = {
                  ...(nextMetadata.agent_answer_buffers || {}),
                  [String(step.metadata.stream_key)]: ''
                };
              }

              const streamKey = step.metadata?.stream_key;
              const streamingIndex = streamKey
                ? nextSteps.findIndex((s) => s.metadata?.streaming && s.metadata?.stream_key === streamKey)
                : nextSteps.findIndex((s) => s.step_type === 'answer' && s.metadata?.streaming);
              if (streamingIndex >= 0) {
                nextSteps[streamingIndex] = { ...step, metadata: { ...step.metadata } };
              } else {
                nextSteps.push(step);
              }
              nextMetadata.agent_steps = nextSteps;

              return { ...msg, metadata: nextMetadata, content: step.content };
            }

            if (step.step_type === 'error') {
              nextMetadata.agent_streaming = false;
              nextSteps.push(step);
              nextMetadata.agent_steps = nextSteps;
              return { ...msg, metadata: nextMetadata, content: step.content };
            }

            if (step.step_type === 'thought' || step.step_type === 'action' || step.step_type === 'observation') {
              const streamKey = step.metadata?.stream_key;
              if (streamKey) {
                const streamingIndex = nextSteps.findIndex(
                  (s) => s.metadata?.streaming && s.metadata?.stream_key === streamKey
                );
                if (streamingIndex >= 0) {
                  nextSteps[streamingIndex] = { ...step, metadata: { ...step.metadata } };
                } else {
                  nextSteps.push(step);
                }
              } else {
                nextSteps.push(step);
              }
              if (step.step_type === 'observation' && streamKey && nextMetadata.agent_observation_buffers) {
                const nextBuffers = { ...(nextMetadata.agent_observation_buffers || {}) } as Record<string, string>;
                delete nextBuffers[String(streamKey)];
                nextMetadata.agent_observation_buffers = nextBuffers;
              }
            } else {
              nextSteps.push(step);
            }
            nextMetadata.agent_steps = nextSteps;
            nextMetadata.agent_streaming = true;
            return { ...msg, metadata: nextMetadata };
          })
        );

        const debugSessionId = activeSessionKey === DRAFT_SESSION_KEY ? null : activeSessionKey;
        scheduleDebugRefresh(debugSessionId);
      }

      updateSessionMessages(activeSessionKey, (prev) =>
        prev.map((msg) =>
          msg.id === currentAssistantId
            ? { ...msg, metadata: { ...(msg.metadata || {}), agent_streaming: false, agent_answer_buffers: {} } }
            : msg
        )
      );

      if (newSessionId) {
        setSessionRefreshTrigger((prev) => prev + 1);
        await refreshSessionDebug(newSessionId);
      }

      markSessionUnread(activeSessionKey);
    } catch (error: any) {
      const stopped = inFlightBySessionRef.current[activeSessionKey]?.stopRequested;
      if (error?.name === 'AbortError' || stopped) {
        // User stopped streaming
      } else {
        console.error('Failed to send message:', error);
        const errorMsg: Message = {
          id: Date.now() + 2,
          session_id: newSessionId || targetSessionId || '',
          role: 'assistant',
          content: `Chat error: ${error.message || 'Please check whether the backend is running.'}`,
          timestamp: new Date().toISOString(),
        };
        updateSessionMessages(activeSessionKey, (prev) => [...prev.filter((m) => m.id !== currentAssistantId), errorMsg]);
        markSessionUnread(activeSessionKey);
      }
    } finally {
      delete inFlightBySessionRef.current[activeSessionKey];
      bumpInFlight();
      processQueues();
    }
  };

  const startStreamForItem = (item: QueueItem, sessionKey: string) => {
    if (inFlightBySessionRef.current[sessionKey]) return;
    const tempBase = Date.now() + Math.floor(Math.random() * 1000);
    const tempUserId = -tempBase;
    const tempAssistantId = -(tempBase + 1);
    const abortController = new AbortController();
    inFlightBySessionRef.current[sessionKey] = {
      abortController,
      stopRequested: false,
      activeAssistantId: null,
      tempAssistantId,
      sessionKey,
      lastEventAt: Date.now(),
      stalled: false,
    };
    bumpInFlight();
    void runStreamForItem(item, sessionKey, tempUserId, tempAssistantId, abortController);
  };

  const processQueues = () => {
    if (processingQueueRef.current) {
      pendingQueueRunRef.current = true;
      return;
    }
    processingQueueRef.current = true;
    try {
      let available = MAX_CONCURRENT_STREAMS - getInFlightCount();
      if (available <= 0) return;

      const candidates = Object.entries(queueBySessionRef.current)
        .filter(([sessionKey, queue]) => queue.length > 0 && !inFlightBySessionRef.current[sessionKey])
        .map(([sessionKey, queue]) => ({ sessionKey, item: queue[0] }))
        .sort((a, b) => a.item.enqueuedAt - b.item.enqueuedAt);

      for (const candidate of candidates) {
        if (available <= 0) break;
        const queue = queueBySessionRef.current[candidate.sessionKey] || [];
        if (!queue.length || queue[0].id !== candidate.item.id) continue;
        const nextQueue = queue.slice(1);
        if (nextQueue.length > 0) {
          queueBySessionRef.current[candidate.sessionKey] = nextQueue;
        } else {
          delete queueBySessionRef.current[candidate.sessionKey];
        }
        bumpQueue();
        startStreamForItem(candidate.item, candidate.sessionKey);
        available -= 1;
      }
    } finally {
      processingQueueRef.current = false;
      if (pendingQueueRunRef.current) {
        pendingQueueRunRef.current = false;
        processQueues();
      }
    }
  };

  const refreshSessionDebug = async (sessionId: string) => {
    try {
      const calls = await getSessionLLMCalls(sessionId);
      setLlmCalls(calls);
    } catch (error) {
      console.error('Failed to load LLM calls:', error);
    }
  };

  const scheduleDebugRefresh = (sessionId: string | null | undefined) => {
    if (!showDebugPanelRef.current || !sessionId) return;
    if (currentSessionIdRef.current && sessionId !== currentSessionIdRef.current) return;
    const key = sessionId;
    const now = Date.now();
    const minInterval = 600;
    const entry = debugRefreshRef.current[key] || { last: 0, timer: null };
    const elapsed = now - entry.last;
    if (elapsed >= minInterval) {
      entry.last = now;
      debugRefreshRef.current[key] = entry;
      void refreshSessionDebug(key);
      return;
    }
    if (entry.timer != null) return;
    entry.timer = window.setTimeout(() => {
      entry.timer = null;
      entry.last = Date.now();
      debugRefreshRef.current[key] = entry;
      if (showDebugPanelRef.current) {
        void refreshSessionDebug(key);
      }
    }, Math.max(0, minInterval - elapsed));
    debugRefreshRef.current[key] = entry;
  };

  const handleOpenDebugCall = (messageId: number, iteration: number) => {
    if (!showDebugPanel) return;
    const call = llmCalls.find((item) => item.message_id === messageId && item.iteration === iteration);
    const key = `${messageId}-${iteration}-${call?.id ?? 'none'}-${Date.now()}`;
    setDebugFocus({ key, messageId, iteration, callId: call?.id });
  };

  const handleSwitchConfig = async (configId: string) => {
    try {
      const config = await getConfig(configId);
      const sessionId = currentSessionIdRef.current;
      if (sessionId) {
        await updateSession(sessionId, { config_id: config.id });
        setSessionRefreshTrigger((prev) => prev + 1);
      }
      setCurrentConfig(config);
      setShowConfigSelector(false);
    } catch (error) {
      console.error('Failed to switch config:', error);
      alert('Failed to switch config.');
    }
  };

  const handleReasoningChange = async (value: ReasoningEffort) => {
    if (!currentConfig) return;
    try {
      const updated = await updateConfig(currentConfig.id, { reasoning_effort: value });
      setCurrentConfig(updated);
      setAllConfigs((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      setShowReasoningSelector(false);
    } catch (error) {
      console.error('Failed to update reasoning:', error);
      alert('Failed to update reasoning.');
    }
  };

  const handleWorkPathPick = async () => {
    const selected = await pickWorkPath();
    if (!selected) return;
    const sessionKey = getCurrentSessionKey();
    await applyWorkPath(sessionKey, currentSessionIdRef.current, selected);
  };

  const parseAstPathList = (value: string) =>
    value
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);

  const normalizeAstImportList = (value: unknown) => {
    if (Array.isArray(value)) {
      return value
        .filter((item): item is string => typeof item === 'string')
        .map((item) => item.trim())
        .filter(Boolean);
    }
    if (typeof value === 'string') {
      return parseAstPathList(value);
    }
    return [];
  };

  const normalizeAstImportLanguages = (value: unknown) => {
    if (Array.isArray(value)) {
      return normalizeAstLanguageList(value);
    }
    if (typeof value === 'string') {
      const list = value
        .split(/[,\r\n]+/)
        .map((item) => item.trim())
        .filter(Boolean);
      return normalizeAstLanguageList(list);
    }
    return [];
  };

  const handleExportAstSettings = async () => {
    if (!astSettingsRoot) return;
    const parsedMax = Number.parseInt(astMaxFiles, 10);
    const maxFiles = Number.isFinite(parsedMax) && parsedMax > 0 ? parsedMax : AST_DEFAULT_MAX_FILES;
    const includeLanguages = AST_LANGUAGE_OPTIONS
      .map((option) => option.id)
      .filter((id) => astIncludeLanguages.includes(id));
    const payload = {
      root: astSettingsRoot,
      settings: {
        ignore_paths: parseAstPathList(astIgnorePaths),
        include_only_paths: parseAstPathList(astIncludeOnlyPaths),
        force_include_paths: parseAstPathList(astForceIncludePaths),
        include_languages: includeLanguages,
        max_files: maxFiles,
      },
    };
    try {
      const ok = await exportConfigFile('ast_settings', payload, {
        title: '导出 AST 设置',
        defaultName: `ast-settings-${new Date().toISOString().slice(0, 10)}.json`,
      });
      if (ok) alert('AST 设置已导出。');
    } catch (error: any) {
      console.error('Failed to export AST settings:', error);
      alert(`导出失败: ${error?.message || 'Unknown error'}`);
    }
  };

  const handleImportAstSettings = async () => {
    if (!astSettingsRoot) return;
    let result;
    try {
      result = await importConfigFile({ title: '导入 AST 设置' });
    } catch (error: any) {
      console.error('Failed to import AST settings:', error);
      alert(`导入失败: ${error?.message || 'Unknown error'}`);
      return;
    }
    if (!result) return;
    if (result.kind && !['ast_settings', 'ast', 'ast_config'].includes(result.kind)) {
      alert(`导入失败: 文件类型为 "${result.kind}"，不是 AST 设置。`);
      return;
    }

    const raw = result.data;
    let settings: Record<string, any> | null = null;
    let importRoot = '';
    if (raw && typeof raw === 'object') {
      const record = raw as Record<string, any>;
      if (record.settings && typeof record.settings === 'object') {
        settings = record.settings as Record<string, any>;
        if (typeof record.root === 'string') {
          importRoot = record.root;
        }
      } else {
        settings = record;
        if (typeof record.root === 'string') {
          importRoot = record.root;
        }
      }
    }

    if (!settings) {
      alert('未识别到有效的 AST 设置。');
      return;
    }

    if (importRoot && importRoot !== astSettingsRoot) {
      const proceed = window.confirm(
        `导入配置来自路径：\n${importRoot}\n\n将应用到当前路径：\n${astSettingsRoot}\n\n是否继续？`
      );
      if (!proceed) return;
    }

    const payload: Record<string, any> = { root: astSettingsRoot };
    if ('ignore_paths' in settings) {
      payload.ignore_paths = normalizeAstImportList(settings.ignore_paths);
    }
    if ('include_only_paths' in settings) {
      payload.include_only_paths = normalizeAstImportList(settings.include_only_paths);
    }
    if ('force_include_paths' in settings) {
      payload.force_include_paths = normalizeAstImportList(settings.force_include_paths);
    }
    if ('include_languages' in settings) {
      payload.include_languages = normalizeAstImportLanguages(settings.include_languages);
    }
    if ('max_files' in settings) {
      const parsedMax = Number.parseInt(String(settings.max_files), 10);
      if (Number.isFinite(parsedMax) && parsedMax > 0) {
        payload.max_files = parsedMax;
      }
    }

    if (Object.keys(payload).length <= 1) {
      alert('导入文件中没有可用的 AST 设置字段。');
      return;
    }

    const ok = await applyAstSettingsPayload(payload, { close: true });
    if (ok) alert('AST 设置已导入。');
  };

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
    setter([...list, path].join('\n'));
  };

  const openAstSettings = (root: string) => {
    if (!root) return;
    setAstSettingsRoot(root);
    setAstSettingsError(null);
    setShowAstSettings(true);
  };

  const closeAstSettings = () => {
    if (astSettingsSaving) return;
    setShowAstSettings(false);
    setAstSettingsError(null);
  };

  const openWorkDirWindow = async (
    path: string,
    openFilePath?: string,
    openLine?: number,
    openColumn?: number,
    sessionKeyOverride?: string
  ) => {
    const sessionKey = sessionKeyOverride || getCurrentSessionKey();
    const label = makeWorkdirLabel(path);
    const existing = await WebviewWindow.getByLabel(label);
    if (existing) {
      try {
        await existing.show();
        await existing.setFocus();
      } catch {
        // ignore focus errors
      }
      void existing.emit('workdir:ping', { target: label });
      void existing.emit('workdir:set', { path, target: label, sessionKey });
      if (openFilePath) {
        void existing.emit('workdir:open-file', { path: openFilePath, line: openLine, column: openColumn, target: label });
      }
      return;
    }

    const bounds = getWorkdirWindowBounds();
    const sessionParam = sessionKey ? `&session=${encodeURIComponent(sessionKey)}` : '';
    const url = openFilePath
      ? `/?window=workdir&path=${encodeURIComponent(path)}${sessionParam}&open=${encodeURIComponent(openFilePath)}${
        openLine ? `&line=${encodeURIComponent(String(openLine))}` : ''
      }${openColumn ? `&col=${encodeURIComponent(String(openColumn))}` : ''}`
      : `/?window=workdir&path=${encodeURIComponent(path)}${sessionParam}`;
    const win = new WebviewWindow(label, {
      title: 'GYY',
      url,
      width: bounds?.width ?? WORKDIR_DEFAULT_WIDTH,
      height: bounds?.height ?? WORKDIR_DEFAULT_HEIGHT,
      x: bounds?.x,
      y: bounds?.y,
      decorations: IS_MAC,
    });

    win.once('tauri://created', () => {
      void win.emit('workdir:set', { path, target: label, sessionKey });
    });

    win.once('tauri://error', (event) => {
      console.error('Failed to create workdir window:', event);
    });
  };

  const openWorkdirForFile = async (filePath: string, line?: number, column?: number) => {
    const parsed = parseFileLocation(filePath);
    const rawPath = parsed.path || filePath;
    const normalizedInput = normalizeFileHref(rawPath);
    const targetLine = line ?? parsed.line;
    const targetColumn = column ?? parsed.column;
    const trimmed = normalizedInput.trim();
    if (!trimmed) return;

    const sessionKey = getCurrentSessionKey();
    const extraRoots = loadExtraWorkPaths(sessionKey, currentWorkPath);
    const rootCandidates = [currentWorkPath, ...extraRoots].filter((value) => value);

    let resolvedPath = trimmed;
    if (!isAbsolutePath(trimmed) && rootCandidates.length > 0) {
      resolvedPath = await resolveRelativePath(trimmed, rootCandidates);
    }

    const root =
      currentWorkPath ||
      rootCandidates[0] ||
      (isAbsolutePath(resolvedPath) ? getParentPath(resolvedPath) : '');
    if (!root) {
      alert('请先选择工作路径。');
      return;
    }
    const key = `${resolvedPath}::${targetLine ?? ''}:${targetColumn ?? ''}`;
    const now = Date.now();
    const lastOpen = lastWorkFileOpenRef.current;
    if (lastOpen && lastOpen.key === key && now - lastOpen.at < 400) {
      return;
    }
    lastWorkFileOpenRef.current = { key, at: now };
    await openWorkDirWindow(root, resolvedPath, targetLine, targetColumn, sessionKey);
  };

  const openWorkPathInExplorer = async (path: string) => {
    const trimmed = path.trim();
    if (!trimmed) return;
    try {
      await revealItemInDir(trimmed);
      return;
    } catch (error) {
      try {
        await openPath(trimmed);
        return;
      } catch (inner) {
        console.error('Failed to open work path in explorer:', inner ?? error);
        alert('无法打开资源管理器，请确认路径存在。');
      }
    }
  };

  const handleWorkPathClick = async () => {
    if (!currentWorkPath) {
      await handleWorkPathPick();
      return;
    }
    await openWorkDirWindow(currentWorkPath, undefined, undefined, undefined, getCurrentSessionKey());
  };

  const handleWorkPathContextMenu = (event: React.MouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setWorkPathMenu({ x: event.clientX, y: event.clientY });
  };

  const handlePaste = async (event: React.ClipboardEvent<HTMLInputElement>) => {
    const items = event.clipboardData?.items;
    if (!items) return;
    const files: File[] = [];
    Array.from(items).forEach((item) => {
      if (item.kind === 'file') {
        const file = item.getAsFile();
        if (file && isImageFile(file)) {
          files.push(file);
        }
      }
    });
    if (files.length > 0) {
      event.preventDefault();
      await addPendingAttachments(files);
    }
  };

  const handleDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (!dragActive) {
      setDragActive(true);
    }
  };

  const handleDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    const nextTarget = event.relatedTarget as Node | null;
    if (nextTarget && event.currentTarget.contains(nextTarget)) return;
    setDragActive(false);
  };

  const handleDrop = async (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragActive(false);
    const files = Array.from(event.dataTransfer?.files || []);
    if (files.length > 0) {
      await addPendingAttachments(files);
    }
  };

  const handleAttachmentPreview = (attachment: MessageAttachment) => {
    const src = getAttachmentFullSrc(attachment);
    if (!src) return;
    setImagePreview({ src, name: attachment.name });
  };

  const enqueueMessage = async (message: string, sessionId: string | null, attachments: PendingAttachment[] = []) => {
    if (!message.trim() && attachments.length === 0) return;
    if (!currentConfig) {
      alert('Please configure an LLM first.');
      return;
    }
    autoScrollRef.current = true;

    const sessionKey = getSessionKey(sessionId);
    let workPath = getSessionWorkPath(sessionKey);
    if (!sessionId && !workPath) {
      const selected = await pickWorkPath();
      if (selected) {
        await applyWorkPath(sessionKey, null, selected);
        workPath = selected;
      }
    }
    const extraWorkPaths = loadExtraWorkPaths(sessionKey, workPath);
    const historyForEstimate = messagesCacheRef.current[sessionKey] || [];
    const estimatedRequest = buildEstimatedRequestPayload(
      historyForEstimate,
      message,
      agentConfig?.base_system_prompt || ''
    );

    const queueItem: QueueItem = {
      id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      message,
      sessionId,
      sessionKey,
      configId: currentConfig.id,
      agentMode,
      agentProfileId: resolveAgentProfileId(agentConfig, currentAgentProfileId),
      workPath,
      extraWorkPaths,
      enqueuedAt: Date.now(),
      attachments: attachments.length ? [...attachments] : undefined,
      estimatedRequest,
    };

    setPendingContextEstimate({
      sessionKey,
      queueId: queueItem.id,
      payload: estimatedRequest,
    });
    enqueueSessionQueue(sessionKey, queueItem);
    processQueues();
  };

  const handleSend = async () => {
    const userMessage = inputMsg.trim();
    const hasAttachments = pendingAttachments.length > 0;
    if (!userMessage && !hasAttachments) return;
    const attachments = hasAttachments ? [...pendingAttachments] : [];
    setInputMsg('');
    setPendingAttachments([]);
    await enqueueMessage(userMessage, currentSessionIdRef.current, attachments);
  };

  const handleRetryMessage = async (messageId: number | null, message: string) => {
    if (!message) return;
    if (isStreamingCurrent) {
      alert('请先停止当前输出再重试。');
      return;
    }
    let rollbackOk = true;
    if (messageId && currentSessionIdRef.current) {
      rollbackOk = await rollbackToMessage(messageId, { keepInput: false });
    }
    if (!rollbackOk) return;
    await enqueueMessage(message, currentSessionIdRef.current);
  };

  const handleRemoveQueuedItem = (itemId: string) => {
    const sessionKey = getCurrentSessionKey();
    removeSessionQueueItem(sessionKey, itemId);
    clearPendingContextForItem(itemId);
    processQueues();
  };

  const applyStopNoteToMessage = (msg: Message) => {
    const note = '\n\n[用户主动停止输出]';
    const nextMetadata = { ...(msg.metadata || {}) } as any;
    const steps = (nextMetadata.agent_steps || []) as AgentStep[];
    if (steps.length > 0) {
      const lastIndex = [...steps].reverse().findIndex(
        (step) => step.step_type === 'answer' || step.step_type === 'answer_delta'
      );
      const idx = lastIndex >= 0 ? steps.length - 1 - lastIndex : -1;
      if (idx >= 0) {
        const target = steps[idx];
        steps[idx] = {
          ...target,
          content: `${target.content || ''}${note}`,
          metadata: { ...(target.metadata || {}), stopped_by_user: true }
        };
      } else {
        steps.push({ step_type: 'answer', content: note, metadata: { stopped_by_user: true } });
      }
      nextMetadata.agent_steps = steps;
      nextMetadata.agent_streaming = false;
      nextMetadata.agent_answer_buffers = {};
      nextMetadata.agent_thought_buffers = {};
      nextMetadata.agent_action_buffers = {};
      return {
        ...msg,
        content: `${msg.content || ''}${note}`,
        metadata: nextMetadata
      };
    }

    return {
      ...msg,
      content: `${msg.content || ''}${note}`,
      metadata: { ...nextMetadata, agent_streaming: false }
    };
  };

  const resolvePendingPermission = async (
    sessionKey: string,
    status: 'approved' | 'approved_once' | 'denied',
    options?: { silent?: boolean }
  ) => {
    const pending = pendingPermissionBySessionRef.current[sessionKey];
    if (!pending || permissionBusyBySessionRef.current[sessionKey]) return;
    permissionBusyBySessionRef.current[sessionKey] = true;
    bumpPermissions();
    try {
      await updateToolPermission(pending.id, status);
      if (pendingPermissionBySessionRef.current[sessionKey]?.id === pending.id) {
        delete pendingPermissionBySessionRef.current[sessionKey];
      }
    } catch (error) {
      console.error('Failed to update permission:', error);
      if (!options?.silent) {
        alert('权限更新失败');
      }
    } finally {
      permissionBusyBySessionRef.current[sessionKey] = false;
      bumpPermissions();
    }
  };

  const handleStop = async () => {
    const sessionKey = getCurrentSessionKey();
    const inflight = inFlightBySessionRef.current[sessionKey];
    if (!inflight) return;
    if (pendingPermissionBySessionRef.current[sessionKey]) {
      await resolvePendingPermission(sessionKey, 'denied', { silent: true });
    }
    inflight.stopRequested = true;
    inflight.abortController.abort();
    const activeAssistantId = inflight.activeAssistantId;
    let assistantId: number | null = activeAssistantId ?? inflight.tempAssistantId;
    if (activeAssistantId) {
      try {
        await stopAgentStream({ messageId: activeAssistantId });
      } catch (error) {
        console.error('Failed to stop stream:', error);
      }
    } else if (currentSessionIdRef.current) {
      try {
        await stopAgentStream({ sessionId: currentSessionIdRef.current });
      } catch (error) {
        console.error('Failed to stop stream:', error);
      }
    }
    updateSessionMessages(sessionKey, (prev) => {
      if (!assistantId) {
        const lastAssistant = [...prev].reverse().find((msg) => msg.role === 'assistant');
        assistantId = lastAssistant?.id ?? null;
      }
      if (!assistantId) return prev;
      return prev.map((msg) => (msg.id === assistantId ? applyStopNoteToMessage(msg) : msg));
    });
  };

  const handlePermissionDecision = async (status: 'approved' | 'approved_once' | 'denied') => {
    const sessionKey = getCurrentSessionKey();
    await resolvePendingPermission(sessionKey, status);
  };

  const handleSelectSession = async (
    sessionId: string,
    options?: { forceReload?: boolean; skipStash?: boolean }
  ) => {
    const perfEnabled = SESSION_SWITCH_PROFILE && typeof performance !== 'undefined';
    const perfStart = perfEnabled ? performance.now() : 0;
    const perfMarks: Array<{ label: string; t: number }> = [];
    const perfMeta: Record<string, any> = perfEnabled ? { sessionId } : {};
    const mark = (label: string) => {
      if (!perfEnabled) return;
      perfMarks.push({ label, t: performance.now() });
    };
    const schedulePerfLog = (status: 'ok' | 'error', error?: unknown) => {
      if (!perfEnabled) return;
      const logNow = () => {
        const end = performance.now();
        const rows: Array<{ stage: string; deltaMs: string; totalMs: string }> = [];
        let prev = perfStart;
        perfMarks.forEach((entry) => {
          rows.push({
            stage: entry.label,
            deltaMs: (entry.t - prev).toFixed(1),
            totalMs: (entry.t - perfStart).toFixed(1),
          });
          prev = entry.t;
        });
        perfMeta.status = status;
        perfMeta.totalMs = (end - perfStart).toFixed(1);
        if (error) {
          perfMeta.error = String((error as Error)?.message || error);
        }
        console.groupCollapsed(
          `[Session Switch] ${sessionId} ${status} ${perfMeta.totalMs}ms`
        );
        console.info(perfMeta);
        if (rows.length > 0) {
          console.table(rows);
        }
        console.groupEnd();
      };
      requestAnimationFrame(() => requestAnimationFrame(logNow));
    };
    try {
      autoScrollRef.current = true;
      forceAutoScrollRef.current = true;
      if (!options?.skipStash) {
        stashCurrentMessages();
      }
      currentSessionIdRef.current = sessionId;
      setCurrentSessionId(sessionId);
      setShowConfigSelector(false);
      setShowReasoningSelector(false);
      mark('setup');

      const sessionKey = getSessionKey(sessionId);
      clearSessionUnread(sessionKey);
      if (options?.forceReload) {
        clearSessionCache(sessionKey);
      }
      const cached = messagesCacheRef.current[sessionKey];
      const isStreamingSession = Boolean(inFlightBySessionRef.current[sessionKey]);
      const hasStreaming = Boolean(cached?.some((msg) => msg.metadata?.agent_streaming));
      if (cached) {
        setSessionMessages(sessionKey, cached);
      } else {
        setSessionMessages(sessionKey, []);
      }
      if (perfEnabled) {
        perfMeta.cachedMessages = cached?.length || 0;
        perfMeta.cachedStreaming = isStreamingSession || hasStreaming;
      }
      mark('cache-check');

      setLlmCalls([]);
      const sessionFetchStart = perfEnabled ? performance.now() : 0;
      const session = await getSession(sessionId, { includeCount: false });
      if (perfEnabled) {
        perfMeta.sessionFetchMs = (performance.now() - sessionFetchStart).toFixed(1);
      }
      mark('session');
      const sessionWorkPath = session.work_path || '';
      workPathBySessionRef.current[session.id] = sessionWorkPath;
      setCurrentWorkPath(sessionWorkPath);
      if (session.context_estimate) {
        applyContextEstimate(session.id, normalizeContextEstimate(session.context_estimate, session.context_estimate_at || null));
      } else {
        applyContextEstimate(session.id, null);
      }
      if (sessionWorkPath) {
        scheduleAstNotify(sessionWorkPath, [sessionWorkPath]);
      }
      setCurrentAgentProfileId(resolveAgentProfileId(agentConfig, session.agent_profile || null));
      mark('session-apply');

      if (!cached || cached.length === 0) {
        const messagesFetchStart = perfEnabled ? performance.now() : 0;
        const stepsFetchStart = perfEnabled ? performance.now() : 0;
        const messagesPromise = getSessionMessages(sessionId, { limit: MESSAGE_PAGE_SIZE }).then((result) => {
          if (perfEnabled) {
            perfMeta.messagesFetchMs = (performance.now() - messagesFetchStart).toFixed(1);
            perfMeta.messagesCount = result?.length || 0;
          }
          return result;
        });
        const msgs = await messagesPromise;
        const stepIds = msgs.map((msg) => msg.id).filter((id) => typeof id === 'number');
        const stepsPromise = stepIds.length
          ? getSessionAgentSteps(sessionId, stepIds).then((result) => {
              if (perfEnabled) {
                perfMeta.stepsFetchMs = (performance.now() - stepsFetchStart).toFixed(1);
                perfMeta.stepsCount = result?.length || 0;
              }
              return result;
            })
          : Promise.resolve([]);
        const steps = await stepsPromise;
        mark('messages+steps');

        const hydrateStart = perfEnabled ? performance.now() : 0;
        const hydratedMessages = hydrateMessagesWithSteps(msgs, steps);
        if (perfEnabled) {
          perfMeta.hydrateMs = (performance.now() - hydrateStart).toFixed(1);
        }
        mark('hydrate');

        setSessionMessages(sessionKey, hydratedMessages);
        const paging = getPagingState(sessionKey);
        paging.oldestId = hydratedMessages[0]?.id ?? null;
        paging.hasMore = hydratedMessages.length >= MESSAGE_PAGE_SIZE;
        mark('set-messages');
        forceAutoScrollRef.current = true;
      } else {
        const paging = getPagingState(sessionKey);
        if (!paging.oldestId && cached && cached.length > 0) {
          paging.oldestId = cached[0].id;
        }
        if (paging.hasMore && cached && cached.length < MESSAGE_PAGE_SIZE) {
          paging.hasMore = false;
        }
      }

      const cachedConfig =
        (currentConfig && currentConfig.id === session.config_id ? currentConfig : null) ||
        allConfigs.find((item) => item.id === session.config_id) ||
        null;
      if (cachedConfig) {
        if (perfEnabled) {
          perfMeta.configCache = true;
        }
        setCurrentConfig(cachedConfig);
      } else {
        const configFetchStart = perfEnabled ? performance.now() : 0;
        const config = await getConfig(session.config_id);
        if (perfEnabled) {
          perfMeta.configFetchMs = (performance.now() - configFetchStart).toFixed(1);
          perfMeta.configCache = false;
        }
        setCurrentConfig(config);
      }
      mark('config');
      schedulePerfLog('ok');
    } catch (error) {
      console.error('Failed to load session:', error);
      alert('Failed to load session.');
      schedulePerfLog('error', error);
    }
  };

  const handleNewChat = async () => {
    const sourceKey = getCurrentSessionKey();
    const sourcePath = getSessionWorkPath(sourceKey);
    const sourceExtraPaths = loadExtraWorkPaths(sourceKey, sourcePath);

    stashCurrentMessages();
    currentSessionIdRef.current = null;
    setCurrentSessionId(null);
    setSessionMessages(DRAFT_SESSION_KEY, []);
    setLlmCalls([]);
    messagePagingRef.current[DRAFT_SESSION_KEY] = { loading: false, hasMore: true, oldestId: null };
    autoScrollRef.current = true;

    if (sourcePath) {
      setSessionWorkPath(DRAFT_SESSION_KEY, sourcePath);
      saveExtraWorkPaths(DRAFT_SESSION_KEY, sourceExtraPaths, sourcePath);
      return;
    }

    setSessionWorkPath(DRAFT_SESSION_KEY, '');
    saveExtraWorkPaths(DRAFT_SESSION_KEY, sourceExtraPaths);
    const selected = await pickWorkPath();
    if (selected) {
      await applyWorkPath(DRAFT_SESSION_KEY, null, selected);
    }
  };

  const handleRollback = (messageId: number) => {
    setRollbackTarget({ messageId, keepInput: true });
  };

  const rollbackToMessage = async (
    messageId: number,
    options?: { keepInput?: boolean }
  ) => {
    if (!currentSessionId) return false;
    const currentKey = getCurrentSessionKey();
    if (inFlightBySessionRef.current[currentKey]) {
      alert('请先停止当前输出再回撤。');
      return false;
    }

    try {
      const result = await rollbackSession(currentSessionId, messageId);
      await handleSelectSession(currentSessionId, { forceReload: true, skipStash: true });
      autoScrollRef.current = true;
      forceAutoScrollRef.current = true;
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          scheduleScrollToBottom('auto');
        });
      });
      if (options?.keepInput) {
        setInputMsg(result.input_message || '');
        inputRef.current?.focus();
      } else {
        setInputMsg('');
      }
      setSessionRefreshTrigger((prev) => prev + 1);
      await refreshSessionDebug(currentSessionId);
      return true;
    } catch (error) {
      console.error('Failed to rollback session:', error);
      alert('回撤失败');
      return false;
    }
  };

  const handleConfirmRollback = async () => {
    if (!rollbackTarget) return;
    const { messageId, keepInput } = rollbackTarget;
    setRollbackTarget(null);
    await rollbackToMessage(messageId, { keepInput });
  };

  const handleRevertPatch = async (revertPatchContent: string, messageId?: number) => {
    if (!currentSessionId) {
      alert('请先选择会话。');
      return;
    }
    if (patchRevertBusy) return;
    const currentKey = getCurrentSessionKey();
    if (inFlightBySessionRef.current[currentKey]) {
      alert('请先停止当前输出再撤销。');
      return;
    }
    setPatchRevertBusy(true);
    try {
      await revertPatch(currentSessionId, revertPatchContent, messageId);
      await handleSelectSession(currentSessionId);
      setSessionRefreshTrigger((prev) => prev + 1);
      await refreshSessionDebug(currentSessionId);
    } catch (error) {
      console.error('Failed to revert patch:', error);
      alert('撤销失败');
    } finally {
      setPatchRevertBusy(false);
    }
  };

  const currentSessionKey = getSessionKey(currentSessionId);
  const isStreamingCurrent = useMemo(
    () => Boolean(inFlightBySessionRef.current[currentSessionKey]),
    [currentSessionKey, inFlightTick]
  );
  const renderMessages = useMemo(() => {
    if (!isStreamingCurrent) return messages;
    return [
      ...messages,
      {
        id: -1,
        session_id: currentSessionId || '',
        role: 'assistant',
        content: '',
        timestamp: new Date().toISOString(),
        metadata: { streaming_placeholder: true }
      } as Message
    ];
  }, [messages, isStreamingCurrent, currentSessionId]);

  const offsets = useMemo(() => {
    const list = renderMessages;
    const nextOffsets = new Array(list.length + 1);
    nextOffsets[0] = 0;
    for (let i = 0; i < list.length; i += 1) {
      const key = getMessageKey(list[i], i);
      const height = rowHeightsRef.current.get(key) ?? MESSAGE_ESTIMATED_HEIGHT;
      nextOffsets[i + 1] = nextOffsets[i] + height;
    }
    return nextOffsets;
  }, [renderMessages, heightTick, getMessageKey]);

  const totalHeight = offsets[offsets.length - 1] || 0;

  const updateVirtualRange = useCallback(() => {
    const container = messagesContainerRef.current;
    if (!container) return;
    const { top: paddingTop, bottom: paddingBottom } = containerPaddingRef.current;
    const viewHeight = Math.max(0, container.clientHeight - paddingTop - paddingBottom);
    const scrollTop = Math.max(0, (container.scrollTop || 0) - paddingTop);
    const start = Math.max(0, findItemIndexByOffset(offsets, Math.max(0, scrollTop - MESSAGE_OVERSCAN_PX)));
    const end = Math.min(
      renderMessages.length - 1,
      findItemIndexByOffset(offsets, scrollTop + viewHeight + MESSAGE_OVERSCAN_PX)
    );
    setVirtualRange((prev) => {
      if (prev.start === start && prev.end === end) return prev;
      return { start, end };
    });
  }, [offsets, renderMessages.length]);

  useLayoutEffect(() => {
    updateContainerPadding();
    updateVirtualRange();
  }, [updateContainerPadding, updateVirtualRange]);

  useEffect(() => {
    const handleResize = () => {
      updateContainerPadding();
      updateVirtualRange();
    };
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, [updateContainerPadding, updateVirtualRange]);

  useLayoutEffect(() => {
    const pending = pendingPrependRef.current;
    const container = messagesContainerRef.current;
    if (!pending || !container) return;
    const anchorIndex = renderMessages.findIndex((msg, idx) => getMessageKey(msg, idx) === pending.anchorKey);
    if (anchorIndex >= 0) {
      const newTopSpacer = offsets[anchorIndex] || 0;
      const { top: paddingTop } = containerPaddingRef.current;
      container.scrollTop = newTopSpacer + pending.anchorOffset + paddingTop;
    }
    pendingPrependRef.current = null;
  }, [offsets, renderMessages, getMessageKey]);

  const hydrateMessagesWithSteps = useCallback((msgs: Message[], steps: AgentStepWithMessage[]) => {
    const stepMap = new Map<number, AgentStep[]>();
    (steps as AgentStepWithMessage[]).forEach((step) => {
      const list = stepMap.get(step.message_id) || [];
      list.push({ step_type: step.step_type as AgentStep['step_type'], content: step.content, metadata: step.metadata });
      stepMap.set(step.message_id, list);
    });
    return msgs.map((msg) => {
      const agentSteps = stepMap.get(msg.id) || [];
      if (!agentSteps.length) return msg;
      return {
        ...msg,
        metadata: { ...(msg.metadata || {}), agent_steps: agentSteps, agent_streaming: false }
      };
    });
  }, []);

  const refreshSessionMessages = useCallback(
    async (sessionId: string) => {
      const sessionKey = getSessionKey(sessionId);
      const msgs = await getSessionMessages(sessionId, { limit: MESSAGE_PAGE_SIZE });
      const stepIds = msgs.map((msg) => msg.id).filter((id) => typeof id === 'number');
      const steps = stepIds.length ? await getSessionAgentSteps(sessionId, stepIds) : [];
      const hydratedMessages = hydrateMessagesWithSteps(msgs, steps);
      setSessionMessages(sessionKey, hydratedMessages);
      const paging = getPagingState(sessionKey);
      paging.oldestId = hydratedMessages[0]?.id ?? null;
      paging.hasMore = hydratedMessages.length >= MESSAGE_PAGE_SIZE;
    },
    [hydrateMessagesWithSteps]
  );

  useEffect(() => {
    const timer = window.setInterval(() => {
      const now = Date.now();
      const entries = Object.entries(inFlightBySessionRef.current);
      if (!entries.length) return;
      entries.forEach(([sessionKey, inflight]) => {
        if (inflight.stalled) return;
        if (now - inflight.lastEventAt < STREAM_STALL_MS) return;
        inflight.stalled = true;

        const sessionId = sessionKey === DRAFT_SESSION_KEY ? null : sessionKey;
        const assistantId = inflight.activeAssistantId ?? inflight.tempAssistantId ?? null;

        if (assistantId) {
          stopAgentStream({ messageId: assistantId }).catch(() => undefined);
        } else if (sessionId) {
          stopAgentStream({ sessionId }).catch(() => undefined);
        }
        inflight.abortController.abort();

        updateSessionMessages(sessionKey, (prev) =>
          prev.map((msg) => {
            if (assistantId && msg.id !== assistantId) return msg;
            if (!assistantId && msg.id !== inflight.tempAssistantId) return msg;
            const nextSteps = [...((msg.metadata?.agent_steps as AgentStep[]) || [])];
            nextSteps.push({ step_type: 'error', content: 'Streaming interrupted. Please retry.' });
            return {
              ...msg,
              metadata: { ...(msg.metadata || {}), agent_steps: nextSteps, agent_streaming: false }
            };
          })
        );

        if (sessionId) {
          refreshSessionMessages(sessionId).catch(() => undefined);
        }
        delete inFlightBySessionRef.current[sessionKey];
        bumpInFlight();
      });
    }, STREAM_STALL_CHECK_MS);
    return () => window.clearInterval(timer);
  }, [refreshSessionMessages, updateSessionMessages]);

  const loadOlderMessages = useCallback(async (anchor?: { key: string; offset: number }) => {
    const sessionId = currentSessionIdRef.current;
    if (!sessionId) return;
    const sessionKey = getSessionKey(sessionId);
    const paging = getPagingState(sessionKey);
    if (paging.loading || !paging.hasMore) return;
    const cached = messagesCacheRef.current[sessionKey] || [];
    const oldestId = paging.oldestId ?? cached[0]?.id ?? null;
    if (!oldestId) {
      paging.hasMore = false;
      return;
    }
    paging.loading = true;
    const container = messagesContainerRef.current;
    let anchorKey = anchor?.key || '';
    let anchorOffset = anchor?.offset || 0;
    if (!anchorKey) {
      const anchorIndex = virtualRange.start;
      anchorKey = renderMessages[anchorIndex] ? getMessageKey(renderMessages[anchorIndex], anchorIndex) : '';
      const prevTopSpacer = offsets[anchorIndex] || 0;
      if (container) {
        const { top: paddingTop } = containerPaddingRef.current;
        anchorOffset = container.scrollTop - paddingTop - prevTopSpacer;
      }
    }
    try {
      const older = await getSessionMessages(sessionId, { limit: MESSAGE_PAGE_SIZE, beforeId: oldestId });
      if (!older.length) {
        paging.hasMore = false;
        return;
      }
      const stepIds = older.map((msg) => msg.id).filter((id) => typeof id === 'number');
      const steps = stepIds.length ? await getSessionAgentSteps(sessionId, stepIds) : [];
      const hydrated = hydrateMessagesWithSteps(older, steps);
      updateSessionMessages(sessionKey, (prev) => [...hydrated, ...prev]);
      paging.oldestId = hydrated[0]?.id ?? paging.oldestId;
      if (older.length < MESSAGE_PAGE_SIZE) {
        paging.hasMore = false;
      }
      if (container && anchorKey) {
        pendingPrependRef.current = { anchorKey, anchorOffset };
      }
    } catch (error) {
      console.error('Failed to load older messages:', error);
    } finally {
      paging.loading = false;
    }
  }, [
    getPagingState,
    getMessageKey,
    hydrateMessagesWithSteps,
    offsets,
    renderMessages,
    updateSessionMessages,
    virtualRange.start,
  ]);

  const handleMessagesScroll = () => {
    const container = messagesContainerRef.current;
    if (!container) return;
    const now = Date.now();
    const userScrollActive = userScrollRef.current && now - lastUserScrollAtRef.current < 200;
    const threshold = 10;
    const currentScrollTop = container.scrollTop;
    const distanceToBottom = container.scrollHeight - currentScrollTop - container.clientHeight;
    const nearBottom = distanceToBottom <= threshold;
    if (nearBottom) {
      autoScrollRef.current = true;
      userScrollRef.current = false;
    } else if (currentScrollTop < lastScrollTopRef.current) {
      if (userScrollActive) {
        autoScrollRef.current = false;
      }
    }
    lastScrollTopRef.current = currentScrollTop;
    updateScrollToBottomVisibility();
    updateVirtualRange();
    if (currentScrollTop <= MESSAGE_LOAD_THRESHOLD_PX) {
      const { top: paddingTop } = containerPaddingRef.current;
      const logicalScrollTop = Math.max(0, currentScrollTop - paddingTop);
      const anchorIndex = Math.max(0, findItemIndexByOffset(offsets, logicalScrollTop));
      const anchorKey = renderMessages[anchorIndex] ? getMessageKey(renderMessages[anchorIndex], anchorIndex) : '';
      const prevTopSpacer = offsets[anchorIndex] || 0;
      const anchorOffset = currentScrollTop - paddingTop - prevTopSpacer;
      void loadOlderMessages(anchorKey ? { key: anchorKey, offset: anchorOffset } : undefined);
    }
  };

  const latestAssistantId =
    [...messages].reverse().find((msg) => msg.role === 'assistant')?.id ?? null;
  const debugExtraWorkPaths = useMemo(
    () => loadExtraWorkPaths(currentSessionKey, currentWorkPath),
    [currentSessionKey, currentWorkPath]
  );
  const currentSessionQueue = useMemo(
    () => getSessionQueue(currentSessionKey),
    [currentSessionKey, queueTick]
  );
  const currentPendingPermission = useMemo(
    () => pendingPermissionBySessionRef.current[currentSessionKey] || null,
    [currentSessionKey, permissionTick]
  );
  const currentPermissionBusy = useMemo(
    () => Boolean(permissionBusyBySessionRef.current[currentSessionKey]),
    [currentSessionKey, permissionTick]
  );
  const agentProfiles = agentConfig?.profiles || [];
  const resolvedAgentProfileId = resolveAgentProfileId(agentConfig, currentAgentProfileId);
  const currentAgentProfile = agentProfiles.find((profile) => profile.id === resolvedAgentProfileId) || null;
  const currentReasoning = (currentConfig?.reasoning_effort || 'medium') as ReasoningEffort;
  const workPathDisplay = useMemo(() => formatWorkPath(currentWorkPath), [currentWorkPath]);
  const visibleMessages = useMemo(() => {
    if (renderMessages.length === 0) return [];
    const start = Math.max(0, Math.min(virtualRange.start, renderMessages.length - 1));
    const end = Math.max(start, Math.min(virtualRange.end, renderMessages.length - 1));
    return renderMessages.slice(start, end + 1).map((msg, offset) => ({
      msg,
      index: start + offset
    }));
  }, [renderMessages, virtualRange]);
  const topSpacerHeight = renderMessages.length > 0 ? offsets[Math.min(virtualRange.start, offsets.length - 1)] || 0 : 0;
  const bottomSpacerHeight =
    renderMessages.length > 0
      ? Math.max(
          0,
          totalHeight - (offsets[Math.min(virtualRange.end + 1, offsets.length - 1)] || 0)
        )
      : 0;
  const contextUsage = useMemo(() => {
    const pendingRequest =
      pendingContextEstimate && pendingContextEstimate.sessionKey === getCurrentSessionKey()
        ? pendingContextEstimate.payload
        : null;
    const cachedEstimate = contextEstimateBySession[currentSessionKey];
    if (pendingRequest) {
      const maxTokens = currentConfig?.max_context_tokens || DEFAULT_MAX_CONTEXT_TOKENS;
      const breakdown = estimateTokensFromRequestBreakdown(pendingRequest);
      const usedTokens = breakdown.total;
      const ratio = maxTokens > 0 ? Math.min(1, usedTokens / maxTokens) : 0;
      return { usedTokens, maxTokens, ratio, breakdown };
    }
    if (cachedEstimate) {
      const maxTokens = cachedEstimate.max_tokens || currentConfig?.max_context_tokens || DEFAULT_MAX_CONTEXT_TOKENS;
      const breakdown = {
        total: cachedEstimate.total || 0,
        system: cachedEstimate.system || 0,
        history: cachedEstimate.history || 0,
        tools: cachedEstimate.tools || 0,
        other: cachedEstimate.other || 0,
      };
      if (!breakdown.total) {
        breakdown.total = breakdown.system + breakdown.history + breakdown.tools + breakdown.other;
      }
      const usedTokens = breakdown.total;
      const ratio = maxTokens > 0 ? Math.min(1, usedTokens / maxTokens) : 0;
      return { usedTokens, maxTokens, ratio, breakdown };
    }
    const maxTokens = currentConfig?.max_context_tokens || DEFAULT_MAX_CONTEXT_TOKENS;
    const lastRequest = getLatestRequestPayload(llmCalls, messages);
    const breakdown = estimateTokensFromRequestBreakdown(lastRequest);
    const usedTokens = breakdown.total;
    const ratio = maxTokens > 0 ? Math.min(1, usedTokens / maxTokens) : 0;
    return { usedTokens, maxTokens, ratio, breakdown };
  }, [currentConfig?.max_context_tokens, llmCalls, messages, pendingContextEstimate, contextEstimateBySession, currentSessionKey]);
  const contextRing = useMemo(() => {
    const circumference = 2 * Math.PI * CONTEXT_RING_RADIUS;
    const dashOffset = circumference * (1 - contextUsage.ratio);
    return { circumference, dashOffset };
  }, [contextUsage.ratio]);

  return (
    <div className="app-shell">
      <div
        className="app-titlebar"
        data-tauri-drag-region
        onDoubleClick={handleTitlebarDoubleClick}
      >
        <div
          className="titlebar-left"
        >
          <div className="titlebar-appname">GYY</div>
          <div className="titlebar-divider" />
          <div className="titlebar-subtitle">Agent Chat</div>
        </div>
        <div className="titlebar-actions" data-tauri-drag-region="false">
          <button
            type="button"
            className="titlebar-btn"
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
            className="titlebar-btn"
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
            className="titlebar-btn close"
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
      <div className="app-container">
      {showSidebar && (
        <SessionList
          currentSessionId={currentSessionId}
          onSelectSession={handleSelectSession}
          onNewChat={handleNewChat}
          onOpenConfig={() => setShowConfigManager(true)}
          onToggleDebug={() => setShowDebugPanel((prev) => !prev)}
          debugActive={showDebugPanel}
          refreshTrigger={sessionRefreshTrigger}
          inFlightBySession={inFlightBySession}
          unreadBySession={unreadBySession}
          pendingPermissionBySession={pendingPermissionBySession}
        />
      )}

      <div className="main-content">
        <div className="chat-container">
          <div className="messages-wrapper">
            <div
              className="messages"
              ref={messagesContainerRef}
              onScroll={handleMessagesScroll}
              onWheel={markUserScroll}
              onTouchMove={markUserScroll}
            >
              {renderMessages.length === 0 ? (
                <div className="welcome-message">
                  <h2>Welcome to Agent Chat</h2>
                  <p>Type a message to get started.</p>
                  {!currentConfig && <p className="warning">Please configure an LLM.</p>}
                </div>
              ) : (
                <>
                  {topSpacerHeight > 0 && (
                    <div className="message-spacer" style={{ height: topSpacerHeight }} />
                  )}
                  {visibleMessages.map(({ msg, index }) => {
                    const rowKey = getMessageKey(msg, index);
                    if ((msg as any).metadata?.streaming_placeholder) {
                      return (
                      <MeasuredMessage
                        key={rowKey}
                        rowKey={rowKey}
                        onHeight={updateRowHeight}
                        className="message assistant loading"
                      >
                          <div className="message-content">
                            <span className="typing-indicator">
                              <span></span>
                              <span></span>
                              <span></span>
                            </span>
                          </div>
                        </MeasuredMessage>
                      );
                    }
                    const steps = (msg.metadata?.agent_steps || []) as AgentStep[];
                    const streaming = Boolean(msg.metadata?.agent_streaming);
                    const showPermission = Boolean(currentPendingPermission && msg.id === latestAssistantId);
                    const previousUser = (() => {
                      for (let i = index - 1; i >= 0; i -= 1) {
                        if (messages[i]?.role === 'user') return messages[i];
                      }
                      return null;
                    })();

                    return (
                      <MeasuredMessage
                        key={rowKey}
                        rowKey={rowKey}
                        onHeight={updateRowHeight}
                        className={`message ${msg.role}`}
                      >
                        <div className="message-content">
                          {msg.role === 'assistant' && (steps.length > 0 || showPermission) ? (
                            <AgentStepView
                              steps={steps}
                              messageId={msg.id}
                              streaming={streaming}
                              pendingPermission={showPermission ? currentPendingPermission : null}
                              onPermissionDecision={handlePermissionDecision}
                              permissionBusy={currentPermissionBusy}
                              onRollbackMessage={
                                previousUser?.id ? () => handleRollback(previousUser.id) : undefined
                              }
                              onRetryMessage={
                                previousUser?.content
                                  ? () => handleRetryMessage(previousUser.id, previousUser.content)
                                  : undefined
                              }
                              onRevertPatch={handleRevertPatch}
                              patchRevertBusy={patchRevertBusy}
                              onOpenWorkFile={openWorkdirForFile}
                              currentWorkPath={currentWorkPath}
                              debugActive={showDebugPanel}
                              onOpenDebugCall={(iteration) => handleOpenDebugCall(msg.id, iteration)}
                            />
                          ) : msg.role === 'user' ? (
                            <>
                              {msg.content && <div className="message-text">{msg.content}</div>}
                              {msg.attachments && msg.attachments.length > 0 && (
                                <div className="message-attachments">
                                  {msg.attachments.map((attachment, idx) => {
                                    const src = getAttachmentPreviewSrc(attachment);
                                    if (!src) return null;
                                    return (
                                      <button
                                        key={`${attachment.id || attachment.local_id || 'att'}-${idx}`}
                                        type="button"
                                        className="attachment-thumb"
                                        onClick={() => handleAttachmentPreview(attachment)}
                                        title={attachment.name || 'image'}
                                        aria-label={attachment.name || 'image'}
                                      >
                                        <img src={src} alt={attachment.name || 'attachment'} loading="lazy" />
                                      </button>
                                    );
                                  })}
                                </div>
                              )}
                              <button
                                className="message-action-btn icon inline"
                                onClick={() => handleRollback(msg.id)}
                                title={'\u56de\u64a4\u5230\u6b64\u6d88\u606f'}
                                aria-label={'\u56de\u64a4\u5230\u6b64\u6d88\u606f'}
                              >
                                <svg className="icon-undo" viewBox="0 0 24 24" aria-hidden="true">
                                  <path
                                    d="M7 8L3 12l4 4M3 12h11a5 5 0 0 1 0 10h-4"
                                    fill="none"
                                    stroke="currentColor"
                                    strokeWidth="2"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                  />
                                </svg>
                              </button>
                            </>
                          ) : (
                            msg.content
                          )}
                        </div>
                        <div className="message-time">{new Date(msg.timestamp).toLocaleTimeString()}</div>
                      </MeasuredMessage>
                    );
                  })}
                  {bottomSpacerHeight > 0 && (
                    <div className="message-spacer" style={{ height: bottomSpacerHeight }} />
                  )}
                </>
              )}
            </div>
            {showScrollToBottom && (
              <button
                type="button"
                className="scroll-to-bottom-btn"
                onClick={handleScrollToBottomClick}
                aria-label="Scroll to bottom"
                title="Scroll to bottom"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path
                    d="M6 9l6 6 6-6"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
            )}
          </div>

          <div
            className={`input-area${dragActive ? ' drag-active' : ''}`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            {currentSessionQueue.length > 0 && (
              <div className="queue-panel">
                <div className="queue-header">
                  <span>{'\u6392\u961f\u6d88\u606f'}</span>
                  <span className="queue-count">{currentSessionQueue.length}</span>
                </div>
                <div className="queue-list">
                  {currentSessionQueue.map((item) => (
                    <div key={item.id} className="queue-item">
                      <span className="queue-text">{item.message}</span>
                      <button
                        type="button"
                        className="queue-remove"
                        onClick={() => handleRemoveQueuedItem(item.id)}
                        aria-label={'\u5220\u9664\u6392\u961f\u6d88\u606f'}
                        title={'\u5220\u9664\u6392\u961f\u6d88\u606f'}
                      >
                        {'\u5220\u9664'}
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {pendingAttachments.length > 0 && (
              <div className="input-attachments">
                {pendingAttachments.map((attachment) => (
                  <div key={attachment.id} className="input-attachment">
                    <button
                      type="button"
                      className="attachment-remove"
                      onClick={() => removePendingAttachment(attachment.id)}
                      aria-label="Remove image"
                      title="Remove"
                    >
                      ×
                    </button>
                    <button
                      type="button"
                      className="attachment-thumb"
                      onClick={() => setImagePreview({ src: attachment.previewUrl, name: attachment.name })}
                      aria-label={attachment.name || 'image'}
                      title={attachment.name || 'image'}
                    >
                      <img src={attachment.previewUrl} alt={attachment.name || 'attachment'} />
                    </button>
                  </div>
                ))}
              </div>
            )}

            <input
              onChange={(e) => {
                setInputMsg(e.currentTarget.value);
                autoScrollRef.current = true;
              }}
              onPaste={handlePaste}
              onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend()}
              value={inputMsg}
              placeholder={currentConfig ? 'Type a message...' : 'Please configure an LLM'}
              disabled={!currentConfig}
              ref={inputRef}
            />

            <div className="input-footer">
              {currentConfig && (
                <div className="input-controls">
                  <div className="model-selector-inline">
                    <button
                      className={`model-selector-btn ${showConfigSelector ? 'active' : ''}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setShowConfigSelector(!showConfigSelector);
                      }}
                      aria-label={`Select model: ${currentConfig.name}`}
                      title={`Model: ${currentConfig.name}`}
                    >
                      <span className="selector-text">{currentConfig.name}</span>
                      <span className="dropdown-arrow">{'\u25be'}</span>
                    </button>

                    {showConfigSelector && (
                      <div className="config-dropdown-inline">
                        {allConfigs.map((config) => (
                          <div
                            key={config.id}
                            className={`config-option ${config.id === currentConfig.id ? 'active' : ''}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              handleSwitchConfig(config.id);
                              setShowConfigSelector(false);
                            }}
                          >
                            <div className="config-name">{config.name}</div>
                            <div className="config-meta">
                              {config.api_format} / {config.api_profile} / {config.model}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  <div className="agent-mode-selector-inline">
                    <button
                      type="button"
                      className={`agent-mode-selector-btn ${showAgentModeSelector ? 'active' : ''}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setShowAgentModeSelector(!showAgentModeSelector);
                      }}
                      disabled={!currentConfig}
                      aria-label={`Agent mode: ${agentMode}`}
                      title={`Agent\u6a21\u5f0f: ${agentMode}`}
                    >
                      <span className="selector-text">
                        {AGENT_MODE_OPTIONS.find((opt) => opt.value === agentMode)?.label || agentMode}
                      </span>
                      <span className="dropdown-arrow">{'\u25be'}</span>
                    </button>

                    {showAgentModeSelector && (
                      <div className="agent-mode-dropdown-inline">
                        {AGENT_MODE_OPTIONS.map((option) => (
                          <div
                            key={option.value}
                            className={`agent-mode-option ${option.value === agentMode ? 'active' : ''}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              setAgentMode(option.value);
                              setShowAgentModeSelector(false);
                            }}
                            title={option.description}
                          >
                            <div className="agent-mode-label">{option.label}</div>
                            <div className="agent-mode-desc">{option.description}</div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {agentProfiles.length > 0 && (
                    <div className="agent-profile-selector-inline">
                      <button
                        type="button"
                        className={`agent-profile-selector-btn ${showProfileSelector ? 'active' : ''}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          setShowProfileSelector(!showProfileSelector);
                        }}
                        disabled={!currentConfig}
                        aria-label={`Agent profile: ${currentAgentProfile?.name || resolvedAgentProfileId || ''}`}
                        title={`Agent profile: ${currentAgentProfile?.name || resolvedAgentProfileId || ''}`}
                      >
                        <span className="selector-text">
                          {currentAgentProfile?.name || resolvedAgentProfileId || 'Profile'}
                        </span>
                        <span className="dropdown-arrow">{'\u25be'}</span>
                      </button>

                      {showProfileSelector && (
                        <div className="agent-profile-dropdown-inline">
                          {agentProfiles.map((profile) => (
                            <div
                              key={profile.id}
                              className={`agent-profile-option ${profile.id === resolvedAgentProfileId ? 'active' : ''}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                setCurrentAgentProfileId(profile.id);
                                setShowProfileSelector(false);
                              }}
                            >
                              <div className="agent-profile-name">{profile.name}</div>
                              {profile.id === agentConfig?.default_profile && (
                                <div className="agent-profile-meta">Default</div>
                              )}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  <div className="reasoning-selector-inline">
                    <button
                      className={`reasoning-selector-btn ${showReasoningSelector ? 'active' : ''}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setShowReasoningSelector(!showReasoningSelector);
                      }}
                      disabled={!currentConfig}
                      aria-label={`Reasoning: ${currentReasoning}`}
                      title={`Reasoning: ${currentReasoning}`}
                    >
                      <span className="selector-text">{currentReasoning}</span>
                      <span className="dropdown-arrow">{'\u25be'}</span>
                    </button>

                    {showReasoningSelector && (
                      <div className="reasoning-dropdown-inline">
                        {REASONING_OPTIONS.map((option) => (
                          <div
                            key={option.value}
                            className={`reasoning-option ${option.value === currentReasoning ? 'active' : ''}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              handleReasoningChange(option.value);
                            }}
                          >
                            {option.label}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                    <button
                      type="button"
                      className="work-path-row"
                      onClick={handleWorkPathClick}
                      onContextMenu={handleWorkPathContextMenu}
                      title={currentWorkPath || '\u70b9\u51fb\u9009\u62e9\u5de5\u4f5c\u8def\u5f84'}
                      aria-label={'\u9009\u62e9\u5de5\u4f5c\u8def\u5f84'}
                    >
                    <span className={`work-path-value${currentWorkPath ? '' : ' empty'}`}>
                      {workPathDisplay}
                    </span>
                  </button>
                  {workPathMenu &&
                    typeof document !== 'undefined' &&
                    createPortal(
                      <div
                        ref={workPathMenuRef}
                        className="work-path-menu"
                        style={{
                          top: (workPathMenuPlacement || workPathMenu).y,
                          left: (workPathMenuPlacement || workPathMenu).x
                        }}
                        onClick={(event) => event.stopPropagation()}
                        onContextMenu={(event) => event.preventDefault()}
                      >
                        <button
                          type="button"
                          className="work-path-menu-item"
                          disabled={!currentWorkPath}
                          onClick={() => {
                            if (!currentWorkPath) return;
                            setWorkPathMenu(null);
                            openAstSettings(currentWorkPath);
                          }}
                        >
                          AST 分析设置
                        </button>
                        <button
                          type="button"
                          className="work-path-menu-item"
                          onClick={async () => {
                            setWorkPathMenu(null);
                            await handleWorkPathPick();
                          }}
                        >
                          重新选择工作路径
                        </button>
                        <button
                          type="button"
                          className="work-path-menu-item"
                          disabled={!currentWorkPath}
                          onClick={async () => {
                            if (!currentWorkPath) return;
                            setWorkPathMenu(null);
                            await openWorkPathInExplorer(currentWorkPath);
                          }}
                        >
                          在资源管理器打开
                        </button>
                      </div>,
                      document.body
                    )}
                </div>
              )}

              <div className="input-actions">
                {currentConfig && (
                <div
                  className={`context-usage${contextUsage.ratio >= 0.8 ? ' warn' : contextUsage.ratio >= 0.6 ? ' mid' : ''}`}
                  title={`Context ${Math.round(contextUsage.ratio * 100)}%\n总用量: ${formatTokenCount(contextUsage.usedTokens)} / ${formatTokenCount(contextUsage.maxTokens)}\n系统提示词: ${formatTokenCount(contextUsage.breakdown.system)}\n历史对话(含压缩): ${formatTokenCount(contextUsage.breakdown.history)}\n工具声明: ${formatTokenCount(contextUsage.breakdown.tools)}\n其余: ${formatTokenCount(contextUsage.breakdown.other)}`}
                  aria-label={`Context usage ${Math.round(contextUsage.ratio * 100)}%`}
                >
                    <svg viewBox="0 0 36 36" aria-hidden="true">
                      <circle
                        className="context-ring-bg"
                        cx="18"
                        cy="18"
                        r={CONTEXT_RING_RADIUS}
                        fill="none"
                        strokeWidth="4"
                      />
                      <circle
                        className="context-ring-value"
                        cx="18"
                        cy="18"
                        r={CONTEXT_RING_RADIUS}
                        fill="none"
                        strokeWidth="4"
                        strokeDasharray={contextRing.circumference}
                        strokeDashoffset={contextRing.dashOffset}
                      />
                    </svg>
                    <div className="context-usage-details">
                      <div className="context-usage-title">Context 预估</div>
                      <div className="context-usage-row">
                        <span>总用量</span>
                        <span>{formatTokenCount(contextUsage.usedTokens)} / {formatTokenCount(contextUsage.maxTokens)}</span>
                      </div>
                      <div className="context-usage-row">
                        <span>系统提示词</span>
                        <span>{formatTokenCount(contextUsage.breakdown.system)}</span>
                      </div>
                      <div className="context-usage-row">
                        <span>历史对话(含压缩)</span>
                        <span>{formatTokenCount(contextUsage.breakdown.history)}</span>
                      </div>
                      <div className="context-usage-row">
                        <span>工具声明</span>
                        <span>{formatTokenCount(contextUsage.breakdown.tools)}</span>
                      </div>
                      <div className="context-usage-row">
                        <span>其余</span>
                        <span>{formatTokenCount(contextUsage.breakdown.other)}</span>
                      </div>
                    </div>
                  </div>
                )}
                <button
                  type="button"
                  className="send-btn"
                  onClick={handleSend}
                  disabled={!currentConfig || (!inputMsg.trim() && pendingAttachments.length === 0)}
                  aria-label="Send"
                  title="Send"
                >
                  {isStreamingCurrent ? (
                    <span className="send-spinner" aria-hidden="true" />
                  ) : (
                    <svg className="send-icon" viewBox="0 0 24 24" aria-hidden="true">
                      <path
                        d="M4 12l16-7-7 16-2.5-6L4 12z"
                        fill="currentColor"
                      />
                    </svg>
                  )}
                </button>
                {isStreamingCurrent && (
                  <button
                    type="button"
                    className="stop-btn"
                    onClick={handleStop}
                    aria-label="Stop"
                    title="Stop"
                  >
                    <svg className="stop-icon" viewBox="0 0 24 24" aria-hidden="true">
                      <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />
                    </svg>
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {showDebugPanel && (
        <DebugPanel
          messages={messages}
          llmCalls={llmCalls}
          currentSessionId={currentSessionId}
          workPath={currentWorkPath}
          extraWorkPaths={debugExtraWorkPaths}
          agentMode={agentMode}
          onOpenWorkFile={openWorkdirForFile}
          onClose={() => {
            setShowDebugPanel(false);
            setDebugFocus(null);
          }}
          focusTarget={debugFocus}
        />
      )}

      {showConfigManager && (
        <ConfigManager
          onClose={() => {
            setShowConfigManager(false);
            loadAllConfigs();
            loadAgentConfig();
          }}
          onConfigCreated={() => {
            loadDefaultConfig();
            setSessionRefreshTrigger((prev) => prev + 1);
            loadAllConfigs();
            loadAgentConfig();
          }}
        />
      )}

      {showAstSettings && (
        <div
          className="ast-settings-backdrop"
          onClick={() => {
            if (!astSettingsSaving) closeAstSettings();
          }}
        >
          <div className="ast-settings-dialog" onClick={(event) => event.stopPropagation()}>
            <div className="ast-settings-title">AST 分析设置</div>
            <div className="ast-settings-body">
              <div className="ast-settings-row">
                <label>工作路径</label>
                <div className="ast-settings-path">{astSettingsRoot}</div>
              </div>

              <div className="ast-settings-label-row">
                <label>忽略路径（每行一个）</label>
                <button
                  type="button"
                  className="ast-settings-pick-btn"
                  onClick={() => void pickAstFolder('ignore')}
                  disabled={astSettingsLoading || astSettingsSaving}
                >
                  选择文件夹
                </button>
              </div>
              <textarea
                className="ast-settings-textarea"
                value={astIgnorePaths}
                onChange={(event) => setAstIgnorePaths(event.currentTarget.value)}
                placeholder="例如：D:\\repo\\node_modules"
                disabled={astSettingsLoading || astSettingsSaving}
              />

              <div className="ast-settings-label-row">
                <label>仅包含路径（每行一个）</label>
                <button
                  type="button"
                  className="ast-settings-pick-btn"
                  onClick={() => void pickAstFolder('include')}
                  disabled={astSettingsLoading || astSettingsSaving}
                >
                  选择文件夹
                </button>
              </div>
              <textarea
                className="ast-settings-textarea"
                value={astIncludeOnlyPaths}
                onChange={(event) => setAstIncludeOnlyPaths(event.currentTarget.value)}
                placeholder="仅扫描这些路径及其子目录"
                disabled={astSettingsLoading || astSettingsSaving}
              />

              <div className="ast-settings-label-row">
                <label>必定包含路径（每行一个）</label>
                <button
                  type="button"
                  className="ast-settings-pick-btn"
                  onClick={() => void pickAstFolder('force')}
                  disabled={astSettingsLoading || astSettingsSaving}
                >
                  选择文件夹
                </button>
              </div>
              <textarea
                className="ast-settings-textarea"
                value={astForceIncludePaths}
                onChange={(event) => setAstForceIncludePaths(event.currentTarget.value)}
                placeholder="即使被 gitignore 或忽略规则命中也会扫描"
                disabled={astSettingsLoading || astSettingsSaving}
              />

              <label>语言类型过滤（不选表示不过滤）</label>
              <div className="ast-settings-language-grid">
                {AST_LANGUAGE_OPTIONS.map((option) => (
                  <label key={option.id} className="ast-settings-language-option">
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
              <div className="ast-settings-hint">不选表示不过滤（全部语言）</div>

              <label>最大分析文件数</label>
              <input
                className="ast-settings-input"
                type="number"
                min={1}
                value={astMaxFiles}
                onChange={(event) => setAstMaxFiles(event.currentTarget.value)}
                disabled={astSettingsLoading || astSettingsSaving}
              />
              {astSettingsLoading && <div className="ast-settings-hint">正在加载设置...</div>}
              <div className="ast-settings-hint">优先级：必定包含 &gt; 仅包含 &gt; 忽略 &gt; Git 忽略</div>
              {astSettingsError && <div className="ast-settings-error">{astSettingsError}</div>}
            </div>
            <div className="ast-settings-io">
              <button
                type="button"
                className="ast-settings-btn ghost"
                onClick={handleImportAstSettings}
                disabled={astSettingsSaving || astSettingsLoading}
              >
                导入
              </button>
              <button
                type="button"
                className="ast-settings-btn ghost"
                onClick={handleExportAstSettings}
                disabled={astSettingsSaving || astSettingsLoading}
              >
                导出
              </button>
            </div>
            <div className="ast-settings-actions">
              <button
                type="button"
                className="ast-settings-btn ghost"
                onClick={closeAstSettings}
                disabled={astSettingsSaving}
              >
                取消
              </button>
              <button
                type="button"
                className="ast-settings-btn primary"
                onClick={() => void handleSaveAstSettings()}
                disabled={astSettingsSaving || astSettingsLoading}
              >
                {astSettingsSaving ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={Boolean(rollbackTarget)}
        title="回撤消息"
        message="确定回撤到这条消息吗？"
        confirmLabel="回撤"
        cancelLabel="取消"
        danger
        onCancel={() => setRollbackTarget(null)}
        onConfirm={handleConfirmRollback}
      />

      {imagePreview && (
        <div className="image-preview-overlay" onClick={() => setImagePreview(null)}>
          <div className="image-preview-modal" onClick={(event) => event.stopPropagation()}>
            <button
              type="button"
              className="image-preview-close"
              onClick={() => setImagePreview(null)}
              aria-label="Close preview"
            >
              ×
            </button>
            <img src={imagePreview.src} alt={imagePreview.name || 'image'} />
            {imagePreview.name && <div className="image-preview-name">{imagePreview.name}</div>}
          </div>
        </div>
      )}

    </div>
    </div>
  );
}

export default App;
