const EXTRA_WORK_PATHS_PREFIX = 'workdirExtraPaths';

const isWindowsPath = (value: string) => /^[a-zA-Z]:\//.test(value) || value.startsWith('//');

const normalizeWorkPath = (value: string) => {
  const normalized = value.replace(/[\\/]+/g, '/');
  const lowered = isWindowsPath(normalized) ? normalized.toLowerCase() : normalized;
  if (lowered === '/' || /^[A-Za-z]:\/$/.test(lowered)) {
    return lowered;
  }
  return lowered.endsWith('/') ? lowered.slice(0, -1) : lowered;
};

const sanitizeExtraWorkPaths = (paths: string[], rootPath?: string) => {
  const rootNorm = rootPath ? normalizeWorkPath(rootPath.trim()) : '';
  const seen = new Set<string>();
  const result: string[] = [];
  paths.forEach((raw) => {
    if (typeof raw !== 'string') return;
    const trimmed = raw.trim();
    if (!trimmed) return;
    const normalized = normalizeWorkPath(trimmed);
    if (!normalized) return;
    if (rootNorm && normalized === rootNorm) return;
    if (seen.has(normalized)) return;
    seen.add(normalized);
    result.push(trimmed);
  });
  return result;
};

export const getExtraWorkPathsKey = (sessionKey: string) =>
  `${EXTRA_WORK_PATHS_PREFIX}:${sessionKey || '__draft__'}`;

export const loadExtraWorkPaths = (sessionKey: string, rootPath?: string) => {
  try {
    const raw = localStorage.getItem(getExtraWorkPathsKey(sessionKey));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return sanitizeExtraWorkPaths(parsed as string[], rootPath);
  } catch {
    return [];
  }
};

export const saveExtraWorkPaths = (sessionKey: string, paths: string[], rootPath?: string) => {
  const sanitized = sanitizeExtraWorkPaths(paths, rootPath);
  try {
    localStorage.setItem(getExtraWorkPathsKey(sessionKey), JSON.stringify(sanitized));
  } catch {
    // ignore storage errors
  }
  return sanitized;
};

export const migrateExtraWorkPaths = (fromKey: string, toKey: string) => {
  if (!fromKey || !toKey || fromKey === toKey) return;
  const existing = loadExtraWorkPaths(toKey);
  const incoming = loadExtraWorkPaths(fromKey);
  if (!incoming.length) return;
  const merged = saveExtraWorkPaths(toKey, [...existing, ...incoming]);
  if (merged.length) {
    try {
      localStorage.removeItem(getExtraWorkPathsKey(fromKey));
    } catch {
      // ignore
    }
  }
};

export const dedupeWorkPaths = (paths: string[], rootPath?: string) =>
  sanitizeExtraWorkPaths(paths, rootPath);
