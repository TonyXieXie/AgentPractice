import { useEffect, useRef } from 'react';
import type { Dispatch, SetStateAction } from 'react';

import type { Message } from '../../types';
import { wsClient } from '../../wsClient';
import type { SubagentDoneEvent, SubagentStartedEvent } from '../../wsTypes';

type UseAppShellSessionChannelParams = {
  currentSessionId: string | null;
  getSessionKey: (sessionId: string | null) => string;
  getCurrentSessionKey: () => string;
  updateSessionMessages: (sessionKey: string, updater: (prev: Message[]) => Message[]) => Message[];
  markSessionUnread: (sessionKey: string) => void;
  setSessionRefreshTrigger: Dispatch<SetStateAction<number>>;
};

export function useAppShellSessionChannel(params: UseAppShellSessionChannelParams) {
  const wsSessionRef = useRef<string | null>(null);
  const paramsRef = useRef(params);
  paramsRef.current = params;

  useEffect(() => {
    wsClient.connect();
    const cleanupEvents = wsClient.onEvent((event) => {
      const { getSessionKey, getCurrentSessionKey, updateSessionMessages, markSessionUnread, setSessionRefreshTrigger } = paramsRef.current;
      if (event.type === 'subagent_done') {
        const payload = event as SubagentDoneEvent;
        if (!payload.session_id || !payload.message) return;
        const sessionKey = getSessionKey(payload.session_id);
        updateSessionMessages(sessionKey, (prev) => {
          if (prev.some((msg) => msg.id === payload.message.id)) return prev;
          return [...prev, payload.message];
        });
        if (sessionKey !== getCurrentSessionKey()) {
          markSessionUnread(sessionKey);
        }
        setSessionRefreshTrigger((prev) => prev + 1);
        return;
      }
      if (event.type === 'subagent_started') {
        const payload = event as SubagentStartedEvent;
        if (!payload.session_id) return;
        setSessionRefreshTrigger((prev) => prev + 1);
      }
    });

    return () => {
      cleanupEvents();
      wsClient.disconnect();
    };
  }, []);

  useEffect(() => {
    const prev = wsSessionRef.current;
    if (prev && prev !== params.currentSessionId) {
      wsClient.unsubscribe([prev]);
    }
    if (params.currentSessionId) {
      wsClient.subscribe([params.currentSessionId]);
    }
    wsSessionRef.current = params.currentSessionId;
  }, [params.currentSessionId]);
}
