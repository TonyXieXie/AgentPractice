import { useEffect, useRef } from 'react';
import type { MutableRefObject } from 'react';

import { getToolPermissions } from '../../shared/api/permissions';
import type { ToolPermissionRequest } from '../../types';

type UseToolPermissionPollingParams = {
  inFlightTick: number;
  getInFlightCount: () => number;
  getSessionKey: (sessionId: string | null) => string;
  bumpPermissions: () => void;
  inFlightBySessionRef: MutableRefObject<Record<string, any>>;
  pendingPermissionBySessionRef: MutableRefObject<Record<string, ToolPermissionRequest | null>>;
};

export function useToolPermissionPolling(params: UseToolPermissionPollingParams) {
  const paramsRef = useRef(params);
  paramsRef.current = params;

  useEffect(() => {
    const {
      getInFlightCount,
      getSessionKey,
      bumpPermissions,
      inFlightBySessionRef,
      pendingPermissionBySessionRef,
    } = paramsRef.current;

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
      } catch {
        if (!cancelled) {
          pendingPermissionBySessionRef.current = {};
          bumpPermissions();
        }
      } finally {
        inFlight = false;
      }
    };

    void pollPermissions();
    const timer = window.setInterval(pollPermissions, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [params.inFlightTick]);
}
