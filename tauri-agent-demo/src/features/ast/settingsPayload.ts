import type { AstPathSettings } from '../../types';

export const AST_DEFAULT_MAX_FILES = 500;

export const AST_LANGUAGE_OPTIONS = [
  { id: 'python', label: 'Python (.py)' },
  { id: 'javascript', label: 'JavaScript (.js/.jsx/.mjs/.cjs)' },
  { id: 'typescript', label: 'TypeScript (.ts)' },
  { id: 'tsx', label: 'TSX (.tsx)' },
  { id: 'c', label: 'C (.c)' },
  { id: 'cpp', label: 'C++ (.h/.hpp/.cpp...)' },
  { id: 'rust', label: 'Rust (.rs)' },
  { id: 'json', label: 'JSON (.json)' },
] as const;

const AST_ALLOWED_LANGUAGE_IDS = new Set<string>(AST_LANGUAGE_OPTIONS.map((option) => option.id));

export type AstSettingsFormState = {
  ignorePaths: string;
  includeOnlyPaths: string;
  forceIncludePaths: string;
  includeLanguages: string[];
  maxFiles: string;
};

export const parseAstPathList = (value: string): string[] =>
  value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);

export const normalizeAstImportList = (value: unknown): string[] => {
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

export const normalizeAstLanguageList = (value: unknown): string[] => {
  const list = Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : typeof value === 'string'
      ? value.split(/[,\r\n]+/)
      : [];

  const seen = new Set<string>();
  const result: string[] = [];
  list.forEach((item) => {
    const key = item.trim().toLowerCase();
    if (!key || !AST_ALLOWED_LANGUAGE_IDS.has(key) || seen.has(key)) return;
    seen.add(key);
    result.push(key);
  });
  return result;
};

export const normalizeAstImportLanguages = (value: unknown): string[] => normalizeAstLanguageList(value);

export const appendAstPath = (path: string, current: string): string => {
  if (!path) return current;
  const list = parseAstPathList(current);
  if (list.includes(path)) return current;
  return [...list, path].join('\n');
};

export const toAstSettingsFormState = (settings?: AstPathSettings | null): AstSettingsFormState => {
  const safe = settings || {};
  const ignoreList = Array.isArray(safe.ignore_paths) ? safe.ignore_paths : [];
  const includeList = Array.isArray(safe.include_only_paths) ? safe.include_only_paths : [];
  const forceList = Array.isArray(safe.force_include_paths) ? safe.force_include_paths : [];
  const maxFiles = safe.max_files ?? AST_DEFAULT_MAX_FILES;

  return {
    ignorePaths: ignoreList.join('\n'),
    includeOnlyPaths: includeList.join('\n'),
    forceIncludePaths: forceList.join('\n'),
    includeLanguages: normalizeAstLanguageList(safe.include_languages),
    maxFiles: String(maxFiles),
  };
};

export const buildAstSettingsPayload = (params: {
  root: string;
  ignorePaths: string;
  includeOnlyPaths: string;
  forceIncludePaths: string;
  includeLanguages: string[];
  maxFiles: string;
}): { root: string } & AstPathSettings => {
  const parsedMax = Number.parseInt(params.maxFiles, 10);
  const maxFiles = Number.isFinite(parsedMax) && parsedMax > 0 ? parsedMax : AST_DEFAULT_MAX_FILES;
  const includeLanguages = AST_LANGUAGE_OPTIONS
    .map((option) => option.id)
    .filter((id) => params.includeLanguages.includes(id));

  return {
    root: params.root,
    ignore_paths: parseAstPathList(params.ignorePaths),
    include_only_paths: parseAstPathList(params.includeOnlyPaths),
    force_include_paths: parseAstPathList(params.forceIncludePaths),
    include_languages: includeLanguages,
    max_files: maxFiles,
  };
};

export const extractAstImportSettings = (raw: unknown): {
  settings: Record<string, unknown> | null;
  importRoot: string;
} => {
  let settings: Record<string, unknown> | null = null;
  let importRoot = '';

  if (raw && typeof raw === 'object') {
    const record = raw as Record<string, unknown>;
    if (record.settings && typeof record.settings === 'object') {
      settings = record.settings as Record<string, unknown>;
    } else {
      settings = record;
    }
    if (typeof record.root === 'string') {
      importRoot = record.root;
    }
  }

  return { settings, importRoot };
};

export const buildAstImportPayload = (
  root: string,
  settings: Record<string, unknown>
): ({ root: string } & Partial<AstPathSettings>) | null => {
  const payload: { root: string } & Partial<AstPathSettings> = { root };

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

  return Object.keys(payload).length > 1 ? payload : null;
};
