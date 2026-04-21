import { readDir } from '@tauri-apps/plugin-fs';

import type { ContextEstimate, LLMCall, Message, SkillSummary } from '../../types';

const IS_MAC = typeof navigator !== 'undefined' && /mac/i.test(navigator.userAgent);
const WORK_PATH_MAX_LENGTH = 200;
const MAIN_WINDOW_BOUNDS_KEY = 'mainWindowBounds';
const BRANCH_WINDOW_BOUNDS_KEY = 'branchWindowBounds';
const WORKDIR_BOUNDS_KEY = 'workdirWindowBounds';
const MAIN_DEFAULT_WIDTH = 1200;
const MAIN_DEFAULT_HEIGHT = 950;

export const extractRunShellCommand = (raw?: string): string => {
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

export const parseBooleanMeta = (value: unknown): boolean | undefined => {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    if (normalized === 'true') return true;
    if (normalized === 'false') return false;
  }
  return undefined;
};

export const parseRunShellPtyIdFromContent = (content?: string): string => {
  const text = String(content || '').replace(/\r\n/g, '\n');
  const firstLine = (text.split('\n')[0] || '').trim();
  if (!firstLine) return '';
  const match = firstLine.match(/\bpty_id=([^\s\]]+)/);
  return match?.[1] || '';
};

export type WorkdirBounds = {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
};

export type MainWindowBounds = {
  width?: number;
  height?: number;
};

export const getMainWindowBounds = (): MainWindowBounds => {
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

export const getWorkdirWindowBounds = (): WorkdirBounds | null => {
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

export const getBranchWindowBounds = (): MainWindowBounds => {
  const fallback = getMainWindowBounds();
  try {
    const raw = localStorage.getItem(BRANCH_WINDOW_BOUNDS_KEY);
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

export const hashPath = (value: string) => {
  let hash = 5381;
  for (let i = 0; i < value.length; i += 1) {
    hash = (hash << 5) + hash + value.charCodeAt(i);
  }
  return (hash >>> 0).toString(16);
};

export const makeWorkdirLabel = (path: string) => {
  const normalized = path.toLowerCase();
  const base = normalized
    .replace(/[^a-zA-Z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 12);
  const safeBase = base || 'path';
  return `workdir-${safeBase}-${hashPath(normalized)}`;
};

export const makeBranchLabel = (sessionId: string) => `branch-${sessionId}`;

export const formatWorkPath = (path: string) => {
  if (!path) return '点击选择工作路径';
  if (path.length <= WORK_PATH_MAX_LENGTH) return path;
  const tailLength = Math.max(1, WORK_PATH_MAX_LENGTH - 3);
  return `...${path.slice(-tailLength)}`;
};

export const getParentPath = (filePath: string) => {
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

export const getBaseName = (filePath: string) => {
  const trimmed = filePath.replace(/[\\/]+$/, '');
  const idx = Math.max(trimmed.lastIndexOf('\\'), trimmed.lastIndexOf('/'));
  if (idx < 0) return trimmed;
  return trimmed.slice(idx + 1) || trimmed;
};

export const isAbsolutePath = (value: string) =>
  /^(?:[a-zA-Z]:[\\/]|\\\\|\/)/.test(value);

export const isBareFilename = (value: string) =>
  Boolean(value) && !/[\\/]/.test(value) && !value.startsWith('./') && !value.startsWith('../');

export const FILE_EXT_PATTERN = /\.[A-Za-z0-9]{1,10}$/;
export const FILE_HASH_PATTERN = /^(.*)#L(\d+)(?:C(\d+))?(?:-L?(\d+))?$/i;
export const FILE_LINE_PATTERN = /^(.*?)(?::(\d+))(?:-(\d+))?(?:[:#](\d+))?$/;
export const TRAILING_PUNCTUATION = /[)\],.;:!?}]+$/;

export const hasScheme = (value: string) => /^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(value);

export const ESCAPE_SEGMENTS = new Set(['n', 'r', 't', 'b', 'f', 'v', '0']);
export const MAC_ABSOLUTE_PREFIX = /^(Users|Volumes|private|System|Library|Applications|opt|etc|var|tmp)[\\/]/;

export const normalizeMacAbsolutePath = (value: string) => {
  if (!IS_MAC) return value;
  if (!value) return value;
  if (value.startsWith('/') || value.startsWith('./') || value.startsWith('../')) return value;
  if (/^[a-zA-Z]:[\\/]/.test(value)) return value;
  if (/^file:\/\//i.test(value)) return value;
  if (MAC_ABSOLUTE_PREFIX.test(value)) return `/${value}`;
  return value;
};

export const looksLikeFilePath = (value: string) => {
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

export const normalizeFileHref = (value: string) => {
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

export const stripTrailingPunctuation = (value: string) => {
  const match = value.match(TRAILING_PUNCTUATION);
  if (!match) return value;
  return value.slice(0, -match[0].length);
};

export const parseFileLocation = (rawValue: string) => {
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

export const COMMAND_TRIGGER_PATTERN = /(^|\s)\/([A-Za-z0-9_-]*)$/;

export const findCommandTrigger = (value: string, cursor: number | null) => {
  if (cursor == null || cursor < 0) return null;
  const slice = value.slice(0, cursor);
  const match = slice.match(COMMAND_TRIGGER_PATTERN);
  if (!match || match.index == null) return null;
  const start = match.index + match[1].length;
  return { start, end: cursor, query: match[2] || '' };
};

export const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

export const buildSkillCommandPattern = (items: SkillSummary[]) => {
  if (!items.length) return null;
  const names = items.map((item) => item.name).filter(Boolean);
  if (!names.length) return null;
  const alternation = names.map(escapeRegExp).sort((a, b) => b.length - a.length).join('|');
  if (!alternation) return null;
  return new RegExp(`(^|\\s)[\\\\/$](${alternation})(?=\\s|$)`, 'gi');
};

export const buildSkillInvocationPattern = (items: SkillSummary[]) => {
  if (!items.length) return null;
  const names = items.map((item) => item.name).filter(Boolean);
  if (!names.length) return null;
  const alternation = names.map(escapeRegExp).sort((a, b) => b.length - a.length).join('|');
  if (!alternation) return null;
  return new RegExp(`(^|\\s)([\\\\/$])(${alternation})(?=\\s|$)`, 'i');
};

export const findSkillInvocation = (value: string, pattern: RegExp | null) => {
  if (!value || !pattern) return null;
  const match = pattern.exec(value);
  if (!match || match.index == null) return null;
  const leading = match[1] || '';
  const prefix = match[2] || '';
  const name = match[3] || '';
  const start = match.index + leading.length;
  const end = start + prefix.length + name.length;
  return { name, start, end };
};

export const stripExistingSkillCommands = (value: string, pattern: RegExp | null) => {
  if (!value || !pattern) return value;
  return value
    .replace(pattern, (_match, leading) => (leading ? String(leading) : ''))
    .replace(/[ \t]{2,}/g, ' ');
};


export const joinPath = (base: string, child: string) => {
  if (!base) return child;
  const separator = base.includes('\\') ? '\\' : '/';
  if (base.endsWith('\\') || base.endsWith('/')) {
    return `${base}${child}`;
  }
  return `${base}${separator}${child}`;
};

export const pathExists = async (filePath: string) => {
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

export const findWorkFileByName = async (root: string, fileName: string) => {
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

export const formatTokenCount = (value: number) => {
  if (!Number.isFinite(value)) return '0';
  if (value >= 10000) {
    return `${(value / 1000).toFixed(1)}k`;
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(2)}k`;
  }
  return String(Math.max(0, Math.round(value)));
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

export const collectTextFromContent = (content: any, bucket: string[]) => {
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

export const estimateTokensForContent = (content: any) => {
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
