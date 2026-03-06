import { useLayoutEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { readDir } from '@tauri-apps/plugin-fs';

import type {
  AgentMode,
  ContextEstimate,
  LLMCall,
  Message,
  ReasoningEffort,
  SkillSummary,
} from '../types';

export const DRAFT_SESSION_KEY = '__draft__';

export const REASONING_OPTIONS: { value: ReasoningEffort; label: string }[] = [
  { value: 'none', label: 'none' },
  { value: 'minimal', label: 'minimal' },
  { value: 'low', label: 'low' },
  { value: 'medium', label: 'medium' },
  { value: 'high', label: 'high' },
  { value: 'xhigh', label: 'xhigh' },
];

export const AGENT_MODE_OPTIONS: { value: AgentMode; label: string; description: string }[] = [
  { value: 'default', label: '默认', description: '使用默认安全策略' },
  { value: 'super', label: '超级', description: '允许所有操作' },
];

export const IS_MAC = typeof navigator !== 'undefined' && /mac/i.test(navigator.userAgent);
export const MAX_CONCURRENT_STREAMS = 10;
export const WORK_PATH_MAX_LENGTH = 200;
export const MAIN_WINDOW_BOUNDS_KEY = 'mainWindowBounds';
export const WORKDIR_BOUNDS_KEY = 'workdirWindowBounds';
export const SIDEBAR_OPEN_KEY = 'sessionSidebarOpen';
export const MAIN_DEFAULT_WIDTH = 1200;
export const MAIN_DEFAULT_HEIGHT = 950;
export const WORKDIR_DEFAULT_WIDTH = 1200;
export const WORKDIR_DEFAULT_HEIGHT = 800;
export const DEFAULT_PTY_PANEL_WIDTH = 380;
export const DEFAULT_DEBUG_PANEL_WIDTH = 400;
export const DEFAULT_MAX_CONTEXT_TOKENS = 200000;
export const CONTEXT_RING_RADIUS = 10;
export const AST_DEFAULT_MAX_FILES = 500;
export const LEGACY_USE_TASK_CENTER_KEY = 'legacyUseTaskCenter';
export const STREAM_STALL_MS = 90_000;
export const STREAM_STALL_CHECK_MS = 5_000;
export const PTY_RESYNC_READ_MAX_OUTPUT = 8000;
export const PTY_INPUT_BATCH_MS = 16;
export const AST_LANGUAGE_OPTIONS = [
  { id: 'python', label: 'Python (.py)' },
  { id: 'javascript', label: 'JavaScript (.js/.jsx/.mjs/.cjs)' },
  { id: 'typescript', label: 'TypeScript (.ts)' },
  { id: 'tsx', label: 'TSX (.tsx)' },
  { id: 'c', label: 'C (.c)' },
  { id: 'cpp', label: 'C++ (.h/.hpp/.cpp...)' },
  { id: 'rust', label: 'Rust (.rs)' },
  { id: 'json', label: 'JSON (.json)' },
];
export const SESSION_SWITCH_PROFILE = true;
export const MESSAGE_PAGE_SIZE = 80;
export const MESSAGE_LOAD_THRESHOLD_PX = 240;
export const MESSAGE_OVERSCAN_PX = 600;
export const MESSAGE_ESTIMATED_HEIGHT = 220;

export const extractRunShellCommand = (raw?: string): string => {
  const text = String(raw || '');
  const patterns = [
    /run_shell\s*\(([^)]*)\)/i,
    /run_shell\s*:\s*(.+)$/i,
    /command\s*[:=]\s*(.+)$/i,
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (!match) continue;
    const value = (match[1] || '').trim();
    if (!value) continue;
    return value.replace(/^['"]|['"]$/g, '');
  }
  return text.trim();
};

export const parseBooleanMeta = (value: unknown): boolean | undefined => {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  if (typeof value !== 'string') return undefined;
  const normalized = value.trim().toLowerCase();
  if (!normalized) return undefined;
  if (['1', 'true', 'yes', 'on'].includes(normalized)) return true;
  if (['0', 'false', 'no', 'off'].includes(normalized)) return false;
  return undefined;
};

export const parseRunShellPtyIdFromContent = (content?: string): string => {
  const text = String(content || '');
  const firstLine = (text.split('\n')[0] || '').trim();
  const match = firstLine.match(/pty[_-]?id\s*[:=]\s*([A-Za-z0-9._-]+)/i);
  return match?.[1] || '';
};

export type WorkdirBounds = {
  x?: number;
  y?: number;
  width: number;
  height: number;
};

export type MainWindowBounds = {
  width: number;
  height: number;
};

export const getMainWindowBounds = (): MainWindowBounds => {
  try {
    const raw = localStorage.getItem(MAIN_WINDOW_BOUNDS_KEY);
    if (!raw) {
      return { width: MAIN_DEFAULT_WIDTH, height: MAIN_DEFAULT_HEIGHT };
    }
    const parsed = JSON.parse(raw) as Partial<MainWindowBounds>;
    return {
      width: Math.max(800, Number(parsed?.width) || MAIN_DEFAULT_WIDTH),
      height: Math.max(600, Number(parsed?.height) || MAIN_DEFAULT_HEIGHT),
    };
  } catch {
    return { width: MAIN_DEFAULT_WIDTH, height: MAIN_DEFAULT_HEIGHT };
  }
};

export const getWorkdirWindowBounds = (): WorkdirBounds | null => {
  try {
    const raw = localStorage.getItem(WORKDIR_BOUNDS_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<WorkdirBounds>;
    return {
      width: Math.max(720, Number(parsed?.width) || WORKDIR_DEFAULT_WIDTH),
      height: Math.max(500, Number(parsed?.height) || WORKDIR_DEFAULT_HEIGHT),
    };
  } catch {
    return null;
  }
};

export const hashPath = (value: string) => {
  let hash = 0;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash * 31 + value.charCodeAt(i)) >>> 0;
  }
  return hash.toString(36);
};

export const makeWorkdirLabel = (path: string) => {
  const normalized = path.replace(/[\\/]+$/, '');
  const safeBase = normalized.split(/[\\/]/).pop() || 'workspace';
  return `workdir-${safeBase}-${hashPath(normalized)}`;
};

export const formatWorkPath = (path: string) => {
  const trimmed = path.trim();
  if (!trimmed) return '未设置';
  if (trimmed.length <= WORK_PATH_MAX_LENGTH) return trimmed;
  return `…${trimmed.slice(-(WORK_PATH_MAX_LENGTH - 1))}`;
};

export const getParentPath = (filePath: string) => {
  const normalized = filePath.replace(/[\\/]+$/, '');
  const index = Math.max(normalized.lastIndexOf('/'), normalized.lastIndexOf('\\'));
  if (index <= 0) return '';
  return normalized.slice(0, index);
};

export const getBaseName = (filePath: string) => {
  const normalized = filePath.replace(/[\\/]+$/, '');
  const index = Math.max(normalized.lastIndexOf('/'), normalized.lastIndexOf('\\'));
  return index >= 0 ? normalized.slice(index + 1) : normalized;
};

export const isAbsolutePath = (value: string) =>
  /^[A-Za-z]:[\\/]/.test(value) || /^\\\\/.test(value) || value.startsWith('/');

const isBareFilename = (value: string) =>
  !!value && !value.includes('/') && !value.includes('\\') && !/^[A-Za-z]:$/.test(value);

const FILE_EXT_PATTERN = /\.[A-Za-z0-9]{1,10}$/;
const FILE_HASH_PATTERN = /^(.*)#L(\d+)(?:C(\d+))?(?:-L?(\d+))?$/i;
const FILE_LINE_PATTERN = /^(.*?)(?::(\d+))(?:-(\d+))?(?:[:#](\d+))?$/;
const TRAILING_PUNCTUATION = /[)\],.;:!?}]+$/;

const hasScheme = (value: string) => /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(value);
const MAC_ABSOLUTE_PREFIX = /^(Users|Volumes|private|System|Library|Applications|opt|etc|var|tmp)[\\/]/;

const normalizeMacAbsolutePath = (value: string) => {
  if (!IS_MAC) return value;
  const normalized = value.replace(/\\/g, '/');
  if (normalized.startsWith('/')) return normalized;
  return MAC_ABSOLUTE_PREFIX.test(normalized) ? `/${normalized}` : normalized;
};

export const looksLikeFilePath = (value: string) => {
  const trimmed = value.trim();
  if (!trimmed) return false;
  if (isAbsolutePath(trimmed)) return true;
  if (trimmed.startsWith('./') || trimmed.startsWith('../')) return true;
  if (trimmed.includes('/') || trimmed.includes('\\')) return true;
  return FILE_EXT_PATTERN.test(trimmed);
};

export const normalizeFileHref = (value: string) => {
  if (!value) return value;
  if (value.startsWith('file://')) {
    try {
      const url = new URL(value);
      const pathname = decodeURIComponent(url.pathname || '');
      return normalizeMacAbsolutePath(pathname);
    } catch {
      return normalizeMacAbsolutePath(value);
    }
  }
  return normalizeMacAbsolutePath(value);
};

export const stripTrailingPunctuation = (value: string) => {
  let current = value;
  while (TRAILING_PUNCTUATION.test(current)) {
    current = current.replace(TRAILING_PUNCTUATION, '');
  }
  return current;
};

export const parseFileLocation = (rawValue: string) => {
  const trimmed = rawValue.trim();
  if (!trimmed) return null;
  const normalized = stripTrailingPunctuation(trimmed);

  const fromHash = normalized.match(FILE_HASH_PATTERN);
  if (fromHash) {
    const [, rawPath, line, column] = fromHash;
    const path = normalizeFileHref(rawPath.trim());
    if (looksLikeFilePath(path) && (!hasScheme(path) || /^[a-zA-Z]:[\\/]/.test(path) || /^file:\/\//i.test(path))) {
      return {
        path,
        line: Number(line),
        column: column ? Number(column) : undefined,
      };
    }
  }

  const fromColon = normalized.match(FILE_LINE_PATTERN);
  if (fromColon) {
    const [, rawPath, line, , column] = fromColon;
    const path = normalizeFileHref(rawPath.trim());
    if (looksLikeFilePath(path) && (!hasScheme(path) || /^[a-zA-Z]:[\\/]/.test(path) || /^file:\/\//i.test(path))) {
      return {
        path,
        line: Number(line),
        column: column ? Number(column) : undefined,
      };
    }
  }

  const candidate = normalizeFileHref(normalized);
  if (looksLikeFilePath(candidate) && (!hasScheme(candidate) || /^[a-zA-Z]:[\\/]/.test(candidate) || /^file:\/\//i.test(candidate))) {
    return { path: candidate };
  }
  return null;
};

const COMMAND_TRIGGER_PATTERN = /(^|\s)\/([A-Za-z0-9_-]*)$/;

export const findCommandTrigger = (value: string, cursor: number | null) => {
  const end = cursor == null ? value.length : cursor;
  const text = value.slice(0, end);
  const match = text.match(COMMAND_TRIGGER_PATTERN);
  if (!match || match.index == null) return null;
  const full = match[0];
  const query = match[2] || '';
  const start = match.index + full.length - query.length - 1;
  return { start, end, query };
};

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

export const buildSkillCommandPattern = (items: SkillSummary[]) => {
  const names = items.map((item) => item?.name?.trim()).filter(Boolean) as string[];
  if (!names.length) return null;
  return new RegExp(`(^|\\s)\\$(?:${names.map(escapeRegExp).join('|')})(?=\\s|$)`, 'ig');
};

export const buildSkillInvocationPattern = (items: SkillSummary[]) => {
  const names = items.map((item) => item?.name?.trim()).filter(Boolean) as string[];
  if (!names.length) return null;
  return new RegExp(`(^|\\s)\\$(?<name>${names.map(escapeRegExp).join('|')})(?=\\s|$)`, 'i');
};

export const findSkillInvocation = (value: string, pattern: RegExp | null) => {
  if (!pattern || !value) return null;
  const match = value.match(pattern);
  if (!match) return null;
  const groups = match.groups as { name?: string } | undefined;
  const name = groups?.name || match[0].trim().replace(/^\$/, '');
  return name ? { name } : null;
};

export const stripExistingSkillCommands = (value: string, pattern: RegExp | null) => {
  if (!pattern || !value) return value;
  return value.replace(pattern, (_full, prefix) => (prefix ? prefix : '')).replace(/\s{2,}/g, ' ').trimStart();
};

export const joinPath = (base: string, child: string) => {
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

export const resolveRelativePath = async (relativePath: string, roots: string[]) => {
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

export const estimateTokensForText = (text: string) => {
  const normalized = String(text || '').trim();
  if (!normalized) return 0;
  return Math.max(1, Math.ceil(normalized.length / 4));
};

export const formatTokenCount = (value: number) => {
  if (!Number.isFinite(value)) return '0';
  return Intl.NumberFormat('en-US').format(Math.max(0, Math.round(value)));
};

export const normalizeContextEstimate = (estimate: any, fallbackTime?: string | null): ContextEstimate => ({
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

export const estimateTokensFromRequestBreakdown = (request: Record<string, any> | null) => {
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

export const getLatestRequestPayload = (calls: LLMCall[], history: Message[]) => {
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

export const buildEstimatedRequestPayload = (
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

export const findItemIndexByOffset = (offsets: number[], value: number) => {
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

export const MeasuredMessage = ({ rowKey, className, onHeight, children }: MeasuredMessageProps) => {
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

export type PendingAttachment = {
  id: string;
  name: string;
  mime: string;
  size: number;
  width?: number;
  height?: number;
  previewUrl: string;
  dataBase64: string;
};

export type QueueItem = {
  id: string;
  message: string;
  sessionId: string | null;
  sessionKey: string;
  configId: string;
  agentMode: AgentMode;
  agentProfileId?: string | null;
  useTaskCenter?: boolean;
  workPath?: string;
  extraWorkPaths?: string[];
  enqueuedAt: number;
  attachments?: PendingAttachment[];
  estimatedRequest?: Record<string, any>;
};

export type InFlightState = {
  abortController: AbortController;
  stopRequested: boolean;
  activeAssistantId: number | null;
  tempAssistantId: number;
  sessionKey: string;
  lastEventAt: number;
  stalled?: boolean;
};

export type PendingContextEstimate = {
  sessionKey: string;
  queueId: string;
  payload: Record<string, any>;
};

export type PtyOwnerMapBySession = Record<string, Record<string, string>>;

export type PtyInputQueueItem = {
  sessionId: string;
  ptyId: string;
  buffer: string;
  timerId: number | null;
  inFlight: boolean;
  lastErrorAt?: number;
};

export type CommandItem = {
  kind: 'skill';
  id: string;
  label: string;
  description?: string;
  insertText: string;
};
