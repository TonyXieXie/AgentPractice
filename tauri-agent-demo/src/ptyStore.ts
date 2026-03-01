import { useMemo, useSyncExternalStore } from 'react';
import type {
  AgentStep,
  PtyDeltaEvent,
  PtyListItem,
  PtyMessageUpsertSseEvent,
  PtyReadResponse,
  PtyResyncRequiredEvent,
  PtyStateEvent
} from './api';
import { sanitizePtyChunk, stripAnsiForDisplay } from './ptyAnsi';

export type PtyMode = 'ephemeral' | 'persistent' | string;

export interface PtyStoreEntry {
  session_id: string;
  pty_id: string;
  ansi_log: string;
  rendered_content: string;
  status: string;
  waiting_input: boolean;
  wait_reason?: string;
  cursor?: number;
  reset: boolean;
  seq: number;
  screen_hash?: string;
  pty_mode?: PtyMode;
  command?: string;
  exit_code?: number | null;
  pty_message_id?: number;
  pty_live?: boolean;
  needs_resync?: boolean;
  updated_at: number;
}

type Listener = () => void;

const parseBool = (value: unknown): boolean | undefined => {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    if (normalized === 'true' || normalized === '1') return true;
    if (normalized === 'false' || normalized === '0') return false;
  }
  return undefined;
};

const parseNumber = (value: unknown): number | undefined => {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim()) {
    const num = Number(value);
    if (Number.isFinite(num)) return num;
  }
  return undefined;
};

const parseHeaderTokens = (headerLine: string): Record<string, string> => {
  const text = (headerLine || '').trim();
  if (!text) return {};
  const tokens: string[] = [];
  if (text.includes(']')) {
    const closeIndex = text.indexOf(']');
    const prefix = closeIndex >= 0 ? text.slice(0, closeIndex) : text;
    const rest = closeIndex >= 0 ? text.slice(closeIndex + 1) : '';
    if (prefix.startsWith('[')) {
      tokens.push(...prefix.slice(1).trim().split(/\s+/));
    } else {
      tokens.push(...prefix.trim().split(/\s+/));
    }
    if (rest) {
      tokens.push(...rest.trim().split(/\s+/));
    }
  } else {
    const trimmed = text.startsWith('[') && text.endsWith(']') ? text.slice(1, -1) : text;
    tokens.push(...trimmed.trim().split(/\s+/));
  }
  const parsed: Record<string, string> = {};
  tokens.forEach((token) => {
    if (!token.includes('=')) return;
    const [rawKey, ...rest] = token.split('=');
    if (!rawKey || !rest.length) return;
    const key = rawKey.trim().toLowerCase();
    const value = rest.join('=').trim();
    if (!key) return;
    parsed[key] = value;
  });
  return parsed;
};

const splitRunShellObservation = (content: string): { header: Record<string, string>; body: string } => {
  const normalized = String(content || '').replace(/\r\n/g, '\n');
  if (!normalized) return { header: {}, body: '' };
  const firstNewline = normalized.indexOf('\n');
  const firstLine = (firstNewline >= 0 ? normalized.slice(0, firstNewline) : normalized).trim();
  if (!firstLine.startsWith('[')) {
    return { header: {}, body: normalized };
  }
  const header = parseHeaderTokens(firstLine);
  const body = firstNewline >= 0 ? normalized.slice(firstNewline + 1) : '';
  return { header, body };
};

class PtyStore {
  private readonly sessions = new Map<string, Map<string, PtyStoreEntry>>();
  private readonly listeners = new Set<Listener>();
  private readonly sessionLastSeq = new Map<string, number>();
  private readonly sessionNeedsResync = new Set<string>();
  private version = 0;
  private notifyScheduled = false;

  subscribe = (listener: Listener) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getVersion = () => this.version;

  private scheduleNotify() {
    if (this.notifyScheduled) return;
    this.notifyScheduled = true;
    const runner = () => {
      this.notifyScheduled = false;
      this.version += 1;
      this.listeners.forEach((listener) => listener());
    };
    if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
      window.requestAnimationFrame(runner);
      return;
    }
    setTimeout(runner, 0);
  }

  private ensureSession(sessionId: string): Map<string, PtyStoreEntry> {
    let session = this.sessions.get(sessionId);
    if (!session) {
      session = new Map<string, PtyStoreEntry>();
      this.sessions.set(sessionId, session);
    }
    return session;
  }

  private ensureEntry(sessionId: string, ptyId: string): PtyStoreEntry {
    const session = this.ensureSession(sessionId);
    const existing = session.get(ptyId);
    if (existing) return existing;
    const created: PtyStoreEntry = {
      session_id: sessionId,
      pty_id: ptyId,
      ansi_log: '',
      rendered_content: '',
      status: 'running',
      waiting_input: false,
      reset: false,
      seq: 0,
      needs_resync: false,
      updated_at: Date.now()
    };
    session.set(ptyId, created);
    return created;
  }

  private updateEntry(
    sessionId: string,
    ptyId: string,
    patch: {
      chunk?: string;
      reset?: boolean;
      rendered_content?: string;
      status?: string;
      waiting_input?: boolean;
      wait_reason?: string;
      cursor?: number;
      seq?: number;
      screen_hash?: string;
      pty_mode?: PtyMode;
      command?: string;
      exit_code?: number | null;
      pty_message_id?: number;
      pty_live?: boolean;
      needs_resync?: boolean;
    }
  ) {
    if (!sessionId || !ptyId) return;
    const entry = this.ensureEntry(sessionId, ptyId);
    const incomingSeq = typeof patch.seq === 'number' && Number.isFinite(patch.seq) ? patch.seq : undefined;
    if (incomingSeq !== undefined && incomingSeq <= entry.seq) {
      return;
    }

    const nextReset = patch.reset === true;
    const safeChunk = sanitizePtyChunk(String(patch.chunk || ''));
    let nextAnsiLog = entry.ansi_log;
    if (nextReset) {
      nextAnsiLog = '';
    }
    if (safeChunk) {
      nextAnsiLog += safeChunk;
    }

    let nextRendered = entry.rendered_content;
    if (typeof patch.rendered_content === 'string') {
      nextRendered = stripAnsiForDisplay(patch.rendered_content);
    } else if (safeChunk || nextReset) {
      nextRendered = stripAnsiForDisplay(nextAnsiLog);
    }

    const nextStatus = patch.status || entry.status;
    const nextWaitingInput =
      typeof patch.waiting_input === 'boolean' ? patch.waiting_input : entry.waiting_input;
    const nextWaitReason =
      patch.wait_reason !== undefined ? (patch.wait_reason || undefined) : entry.wait_reason;
    const nextCursor =
      typeof patch.cursor === 'number' && Number.isFinite(patch.cursor) ? patch.cursor : entry.cursor;
    const nextSeq =
      typeof incomingSeq === 'number'
        ? incomingSeq
        : safeChunk || nextReset || typeof patch.rendered_content === 'string'
          ? entry.seq + 1
          : entry.seq;
    const nextScreenHash = patch.screen_hash !== undefined ? (patch.screen_hash || undefined) : entry.screen_hash;
    const nextPtyMode = patch.pty_mode !== undefined ? patch.pty_mode : entry.pty_mode;
    const nextCommand = patch.command !== undefined ? patch.command : entry.command;
    const nextExitCode = patch.exit_code !== undefined ? patch.exit_code : entry.exit_code;
    const nextPtyMessageId =
      patch.pty_message_id !== undefined ? patch.pty_message_id : entry.pty_message_id;
    const nextPtyLive = patch.pty_live !== undefined ? patch.pty_live : entry.pty_live;
    const nextNeedsResync =
      typeof patch.needs_resync === 'boolean' ? patch.needs_resync : entry.needs_resync;

    const visualOrStateChanged =
      nextAnsiLog !== entry.ansi_log ||
      nextRendered !== entry.rendered_content ||
      nextReset !== entry.reset ||
      nextStatus !== entry.status ||
      nextWaitingInput !== entry.waiting_input ||
      nextWaitReason !== entry.wait_reason ||
      nextCursor !== entry.cursor ||
      nextPtyMode !== entry.pty_mode ||
      nextCommand !== entry.command ||
      nextExitCode !== entry.exit_code ||
      nextPtyMessageId !== entry.pty_message_id ||
      nextPtyLive !== entry.pty_live ||
      nextNeedsResync !== entry.needs_resync;
    const seqOrHashChanged = nextSeq !== entry.seq || nextScreenHash !== entry.screen_hash;
    if (!visualOrStateChanged && !seqOrHashChanged) {
      return;
    }
    // Drop UI notifications for seq/screen_hash-only updates to avoid render flicker on high-frequency heartbeats.
    if (!visualOrStateChanged && seqOrHashChanged) {
      entry.seq = nextSeq;
      entry.screen_hash = nextScreenHash;
      entry.updated_at = Date.now();
      return;
    }

    entry.ansi_log = nextAnsiLog;
    entry.rendered_content = nextRendered;
    entry.reset = nextReset;
    entry.status = nextStatus;
    entry.waiting_input = nextWaitingInput;
    entry.wait_reason = nextWaitReason;
    entry.cursor = nextCursor;
    entry.seq = nextSeq;
    entry.screen_hash = nextScreenHash;
    entry.pty_mode = nextPtyMode;
    entry.command = nextCommand;
    entry.exit_code = nextExitCode;
    entry.pty_message_id = nextPtyMessageId;
    entry.pty_live = nextPtyLive;
    entry.needs_resync = nextNeedsResync;
    entry.updated_at = Date.now();
    this.scheduleNotify();
  }

  private touchSessionSeq(sessionId: string, seq?: number) {
    if (!sessionId || typeof seq !== 'number' || !Number.isFinite(seq)) return;
    const current = this.sessionLastSeq.get(sessionId);
    if (current === undefined || seq > current) {
      this.sessionLastSeq.set(sessionId, seq);
    }
  }

  applyListItem(sessionId: string, item: PtyListItem) {
    if (!sessionId || !item?.pty_id) return;
    this.updateEntry(sessionId, item.pty_id, {
      status: item.status || 'running',
      command: item.command,
      exit_code: item.exit_code,
      pty_mode: (item as any).pty_mode,
      waiting_input: parseBool((item as any).waiting_input),
      wait_reason: (item as any).wait_reason,
      seq: parseNumber((item as any).seq),
      screen_hash: typeof (item as any).screen_hash === 'string' ? (item as any).screen_hash : undefined
    });
  }

  applyReadResponse(sessionId: string, response: PtyReadResponse) {
    if (!sessionId || !response?.pty_id) return;
    this.updateEntry(sessionId, response.pty_id, {
      chunk: response.chunk || '',
      reset: Boolean(response.reset),
      rendered_content: typeof response.screen_text === 'string' ? response.screen_text : undefined,
      status: response.status,
      waiting_input: parseBool(response.waiting_input),
      wait_reason: typeof response.wait_reason === 'string' ? response.wait_reason : undefined,
      cursor: parseNumber(response.cursor),
      seq: parseNumber(response.seq),
      screen_hash: typeof response.screen_hash === 'string' ? response.screen_hash : undefined,
      pty_mode: (response as any).pty_mode,
      command: response.command,
      exit_code: response.exit_code,
      pty_message_id: parseNumber((response as any).pty_message_id),
      pty_live: parseBool((response as any).pty_live),
      needs_resync: false
    });
    this.touchSessionSeq(sessionId, parseNumber(response.seq));
  }

  applySseOutput(event: PtyDeltaEvent | PtyStateEvent) {
    if (!event?.session_id || !event?.pty_id) return;
    const ptySeq = parseNumber((event as any).pty_seq) ?? parseNumber(event.seq);
    const streamSeq = parseNumber((event as any).stream_seq) ?? parseNumber(event.seq);
    this.updateEntry(event.session_id, event.pty_id, {
      chunk: event.chunk || '',
      status: event.status,
      waiting_input: parseBool(event.waiting_input),
      wait_reason: typeof event.wait_reason === 'string' ? event.wait_reason : undefined,
      cursor: parseNumber(event.cursor),
      seq: ptySeq,
      screen_hash: typeof event.screen_hash === 'string' ? event.screen_hash : undefined,
      pty_mode: event.pty_mode,
      exit_code: event.exit_code,
      pty_message_id: parseNumber(event.pty_message_id),
      pty_live: parseBool(event.pty_live)
    });
    this.touchSessionSeq(event.session_id, streamSeq);
  }

  applyWsOutput(event: PtyDeltaEvent | PtyStateEvent) {
    this.applySseOutput(event);
  }

  bindPtyMessage(sessionId: string, ptyId: string, messageId?: number, ptyLive?: boolean) {
    if (!sessionId || !ptyId) return;
    const parsedMessageId = parseNumber(messageId);
    this.updateEntry(sessionId, ptyId, {
      pty_message_id: parsedMessageId,
      pty_live: typeof ptyLive === 'boolean' ? ptyLive : (parsedMessageId ? true : undefined)
    });
  }

  applyMessageUpsert(
    sessionId: string,
    ptyId: string,
    payload: { message_id?: number; content?: string; status?: string; final?: boolean; seq?: number }
  ) {
    if (!sessionId || !ptyId) return;
    const existing = this.ensureEntry(sessionId, ptyId);
    const hasRenderableState = Boolean(existing.ansi_log) || Boolean(existing.rendered_content);
    const shouldHydrateContentFromUpsert =
      !hasRenderableState && typeof payload.content === 'string' && payload.content.length > 0;
    this.updateEntry(sessionId, ptyId, {
      rendered_content: shouldHydrateContentFromUpsert ? payload.content : undefined,
      status: payload.status,
      pty_message_id: parseNumber(payload.message_id),
      pty_live: typeof payload.final === 'boolean' ? !payload.final : undefined
    });
    this.touchSessionSeq(sessionId, parseNumber(payload.seq));
  }

  applyMessageUpsertSse(event: PtyMessageUpsertSseEvent) {
    if (!event?.session_id || !event?.pty_id) return;
    this.applyMessageUpsert(event.session_id, event.pty_id, {
      message_id: event.message_id,
      content: event.content,
      status: event.status,
      final: event.final,
      seq: event.seq
    });
  }

  applyResyncRequired(event: PtyResyncRequiredEvent) {
    if (!event?.session_id) return;
    this.touchSessionSeq(event.session_id, parseNumber(event.seq));
    const ptyId = typeof event.pty_id === 'string' ? event.pty_id.trim() : '';
    if (!ptyId) {
      this.sessionNeedsResync.add(event.session_id);
      this.scheduleNotify();
      return;
    }
    this.updateEntry(event.session_id, ptyId, { needs_resync: true });
  }

  consumeResyncTargets(sessionId: string, knownPtyIds?: string[]): string[] {
    if (!sessionId) return [];
    const targets = new Set<string>();
    const session = this.sessions.get(sessionId);
    if (this.sessionNeedsResync.has(sessionId)) {
      this.sessionNeedsResync.delete(sessionId);
      if (Array.isArray(knownPtyIds)) {
        knownPtyIds.filter(Boolean).forEach((ptyId) => targets.add(ptyId));
      }
      if (session) {
        session.forEach((_entry, ptyId) => targets.add(ptyId));
      }
    }
    if (session) {
      session.forEach((entry, ptyId) => {
        if (!entry.needs_resync) return;
        targets.add(ptyId);
        entry.needs_resync = false;
      });
    }
    return Array.from(targets);
  }

  applyRunShellStep(sessionId: string, step: AgentStep) {
    if (!sessionId || !step) return;
    if (step.step_type !== 'observation' && step.step_type !== 'observation_delta') return;
    const meta = (step.metadata || {}) as Record<string, unknown>;
    const tool = String(meta.tool || '').toLowerCase();
    if (tool !== 'run_shell') return;

    const fromMeta = typeof meta.pty_id === 'string' ? meta.pty_id.trim() : '';
    const split = splitRunShellObservation(step.content || '');
    const fromHeader = split.header.pty_id || '';
    const ptyId = fromMeta || fromHeader;
    if (!ptyId) return;

    const waitingInput = parseBool(meta.waiting_input) ?? parseBool(split.header.waiting_input);
    const resetMeta = parseBool(meta.reset);
    const resetHeader = parseBool(split.header.reset);
    const reset = resetMeta ?? resetHeader ?? false;
    const incomingSeq = parseNumber(meta.seq) ?? parseNumber(split.header.seq);
    const existing = this.sessions.get(sessionId)?.get(ptyId);
    if (typeof incomingSeq === 'number' && existing && incomingSeq <= existing.seq) {
      return;
    }

    // PTY output chunk authority is SSE. For PTY-backed run_shell steps,
    // avoid appending step content again to prevent duplicate lines.
    const chunk = '';
    const hasScreenSnapshot = typeof meta.screen_text === 'string' && String(meta.screen_text).length > 0;
    // Keep step path metadata-only to avoid racing/suppressing PTY SSE chunks.
    const appliedSeq = hasScreenSnapshot ? incomingSeq : undefined;
    const appliedReset = hasScreenSnapshot ? reset : false;
    this.updateEntry(sessionId, ptyId, {
      chunk,
      reset: appliedReset,
      rendered_content: hasScreenSnapshot ? String(meta.screen_text) : undefined,
      status: String(meta.status || split.header.status || '').trim().toLowerCase() || undefined,
      waiting_input: waitingInput,
      wait_reason:
        (typeof meta.wait_reason === 'string' && meta.wait_reason) ||
        (typeof split.header.wait_reason === 'string' ? split.header.wait_reason : undefined),
      cursor: parseNumber(meta.cursor) ?? parseNumber(split.header.cursor),
      seq: appliedSeq,
      screen_hash:
        (typeof meta.screen_hash === 'string' ? meta.screen_hash : undefined) ||
        (typeof split.header.screen_hash === 'string' ? split.header.screen_hash : undefined),
      pty_mode:
        (typeof meta.pty_mode === 'string' ? meta.pty_mode : undefined) ||
        (typeof split.header.pty_mode === 'string' ? split.header.pty_mode : undefined),
      command:
        (typeof meta.command === 'string' ? meta.command : undefined) ||
        (typeof split.header.command === 'string' ? split.header.command : undefined),
      exit_code: parseNumber(meta.exit_code) ?? parseNumber(split.header.exit_code),
      pty_message_id: parseNumber(meta.pty_message_id) ?? parseNumber(split.header.pty_message_id),
      pty_live: parseBool(meta.pty_live) ?? parseBool(split.header.pty_live)
    });
    this.touchSessionSeq(sessionId, incomingSeq);
  }

  getSessionSnapshot(sessionId?: string | null): Record<string, PtyStoreEntry> {
    if (!sessionId) return {};
    const session = this.sessions.get(sessionId);
    if (!session) return {};
    const result: Record<string, PtyStoreEntry> = {};
    session.forEach((value, key) => {
      result[key] = { ...value };
    });
    return result;
  }

  clearSession(sessionId: string) {
    if (!sessionId) return;
    const removed = this.sessions.delete(sessionId);
    this.sessionLastSeq.delete(sessionId);
    this.sessionNeedsResync.delete(sessionId);
    if (!removed) return;
    this.scheduleNotify();
  }

  getSessionLastSeq(sessionId: string): number {
    if (!sessionId) return 0;
    return this.sessionLastSeq.get(sessionId) || 0;
  }
}

export const ptyStore = new PtyStore();

export const usePtySessionSnapshot = (sessionId?: string | null) => {
  const version = useSyncExternalStore(ptyStore.subscribe, ptyStore.getVersion, ptyStore.getVersion);
  return useMemo(() => ptyStore.getSessionSnapshot(sessionId), [version, sessionId]);
};
