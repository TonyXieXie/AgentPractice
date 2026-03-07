import { useEffect, useRef } from 'react';

import { listPtys, readPty, sendPtyStream } from '../../shared/api';
import type { PtyDeltaEvent, PtyMessageUpsertSseEvent, PtyResyncRequiredEvent, PtySseEvent, PtyStateEvent } from '../../shared/api';
import { ptyStore } from '../../ptyStore';

const PTY_RESYNC_READ_MAX_OUTPUT = 8000;

type UseAppShellPtyStreamParams = {
  currentSessionId: string | null;
  markStreamActivity: (sessionId?: string | null) => void;
};

export function useAppShellPtyStream({ currentSessionId, markStreamActivity }: UseAppShellPtyStreamParams) {
  const ptyStreamAbortRef = useRef<AbortController | null>(null);
  const ptyStreamSessionRef = useRef<string | null>(null);
  const ptyResyncInFlightRef = useRef<Record<string, boolean>>({});

  useEffect(() => {
    const previous = ptyStreamAbortRef.current;
    if (previous) {
      previous.abort();
      ptyStreamAbortRef.current = null;
    }

    if (!currentSessionId) {
      ptyStreamSessionRef.current = null;
      ptyResyncInFlightRef.current = {};
      return;
    }

    const sessionId = currentSessionId;
    const abortController = new AbortController();
    ptyStreamAbortRef.current = abortController;
    ptyStreamSessionRef.current = sessionId;

    const run = async () => {
      try {
        const initialSeq = ptyStore.getSessionLastSeq(sessionId);
        const streamGenerator = sendPtyStream(
          {
            session_id: sessionId,
            last_seq: initialSeq > 0 ? initialSeq : undefined,
          },
          abortController.signal
        );

        for await (const event of streamGenerator) {
          if (abortController.signal.aborted) break;
          if ((event as any).keepalive) continue;

          const payload = event as PtySseEvent;
          if (payload.event === 'pty_delta' || payload.event === 'pty_state') {
            ptyStore.applySseOutput(payload as PtyDeltaEvent | PtyStateEvent);
            markStreamActivity(payload.session_id);
            continue;
          }
          if (payload.event === 'pty_message_upsert') {
            ptyStore.applyMessageUpsertSse(payload as PtyMessageUpsertSseEvent);
            continue;
          }
          if (payload.event === 'pty_resync_required') {
            const resyncEvent = payload as PtyResyncRequiredEvent;
            ptyStore.applyResyncRequired(resyncEvent);
            const targetPtyId = typeof resyncEvent.pty_id === 'string' ? resyncEvent.pty_id.trim() : '';
            const pendingTargets = ptyStore.consumeResyncTargets(
              resyncEvent.session_id,
              targetPtyId ? [targetPtyId] : undefined
            );
            const key = `${resyncEvent.session_id}:${targetPtyId || '*'}`;
            if (ptyResyncInFlightRef.current[key]) {
              continue;
            }
            ptyResyncInFlightRef.current[key] = true;
            void (async () => {
              try {
                const snapshot = ptyStore.getSessionSnapshot(resyncEvent.session_id);
                const targets = (targetPtyId ? [targetPtyId] : pendingTargets).filter((ptyId) => {
                  if (!ptyId) return false;
                  const status = String(snapshot[ptyId]?.status || '').toLowerCase();
                  if (!status) return true;
                  return status === 'running' || status === 'waiting_input';
                });
                if (targets.length > 0) {
                  await Promise.all(
                    targets.map(async (ptyId) => {
                      try {
                        const cursor = snapshot[ptyId]?.cursor;
                        const response = await readPty({
                          session_id: resyncEvent.session_id,
                          pty_id: ptyId,
                          cursor: typeof cursor === 'number' ? cursor : undefined,
                          max_output: PTY_RESYNC_READ_MAX_OUTPUT,
                        });
                        ptyStore.applyReadResponse(resyncEvent.session_id, response);
                      } catch {
                        // ignore per-pty resync errors
                      }
                    })
                  );
                } else {
                  const list = await listPtys(resyncEvent.session_id, { includeExited: false, maxExited: 0 });
                  const nextSnapshot = ptyStore.getSessionSnapshot(resyncEvent.session_id);
                  await Promise.all(
                    list.map(async (item) => {
                      try {
                        const cursor = nextSnapshot[item.pty_id]?.cursor;
                        const response = await readPty({
                          session_id: resyncEvent.session_id,
                          pty_id: item.pty_id,
                          cursor: typeof cursor === 'number' ? cursor : undefined,
                          max_output: PTY_RESYNC_READ_MAX_OUTPUT,
                        });
                        ptyStore.applyReadResponse(resyncEvent.session_id, response);
                      } catch {
                        // ignore per-pty resync errors
                      }
                    })
                  );
                }
              } catch {
                // ignore resync errors
              } finally {
                delete ptyResyncInFlightRef.current[key];
              }
            })();
          }
        }
      } catch (error) {
        if (abortController.signal.aborted) return;
        console.warn('PTY SSE stream stopped:', error);
      }
    };

    void run();

    return () => {
      abortController.abort();
      if (ptyStreamAbortRef.current === abortController) {
        ptyStreamAbortRef.current = null;
      }
      if (ptyStreamSessionRef.current === sessionId) {
        ptyStreamSessionRef.current = null;
      }
      Object.keys(ptyResyncInFlightRef.current).forEach((key) => {
        if (key.startsWith(`${sessionId}:`)) {
          delete ptyResyncInFlightRef.current[key];
        }
      });
    };
  }, [currentSessionId, markStreamActivity]);
}
