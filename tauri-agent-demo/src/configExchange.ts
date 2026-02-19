import { open as openDialog, save as saveDialog } from '@tauri-apps/plugin-dialog';
import { readTextFile, writeTextFile } from '@tauri-apps/plugin-fs';

export const CONFIG_EXPORT_VERSION = 1;

export interface ConfigExportEnvelope<T = unknown> {
  kind: string;
  version: number;
  exported_at: string;
  data: T;
  meta?: Record<string, any>;
}

export interface ConfigImportResult {
  kind?: string;
  version?: number;
  data: unknown;
  raw: unknown;
  path: string;
}

const normalizeSelectedPath = (selected: string | string[] | null): string => {
  if (!selected) return '';
  if (Array.isArray(selected)) return selected[0] || '';
  return selected;
};

const isRecord = (value: unknown): value is Record<string, any> =>
  Boolean(value && typeof value === 'object' && !Array.isArray(value));

export const exportConfigFile = async (
  kind: string,
  data: unknown,
  options: { title: string; defaultName: string; filters?: { name: string; extensions: string[] }[] }
): Promise<boolean> => {
  const target = await saveDialog({
    title: options.title,
    defaultPath: options.defaultName,
    filters: options.filters ?? [{ name: 'JSON', extensions: ['json'] }],
  });
  if (!target) return false;
  const envelope: ConfigExportEnvelope = {
    kind,
    version: CONFIG_EXPORT_VERSION,
    exported_at: new Date().toISOString(),
    data,
  };
  await writeTextFile(target, JSON.stringify(envelope, null, 2));
  return true;
};

export const importConfigFile = async (
  options: { title: string; filters?: { name: string; extensions: string[] }[] }
): Promise<ConfigImportResult | null> => {
  const selected = await openDialog({
    title: options.title,
    multiple: false,
    filters: options.filters ?? [{ name: 'JSON', extensions: ['json'] }],
  });
  const path = normalizeSelectedPath(selected);
  if (!path) return null;
  const text = await readTextFile(path);
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    throw new Error('文件不是合法的 JSON。');
  }
  let kind: string | undefined;
  let version: number | undefined;
  let data: unknown = parsed;
  if (isRecord(parsed)) {
    if (typeof parsed.kind === 'string' && 'data' in parsed) {
      kind = parsed.kind;
      data = parsed.data;
    }
    const rawVersion = parsed.version ?? parsed.schema_version;
    if (typeof rawVersion === 'number' && Number.isFinite(rawVersion)) {
      version = rawVersion;
    }
  }
  return { path, raw: parsed, data, kind, version };
};
