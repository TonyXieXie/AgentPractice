import { useEffect, useRef } from 'react';

import { DRAFT_SESSION_KEY } from '../app/shared';
import type { Message } from '../types';
import { wsClient } from '../wsClient';
import type { SubagentDoneEvent, SubagentStartedEvent } from '../wsTypes';

type UseSessionWebSocketParams = {
  bumpSessionRefresh: () => void;
  currentSessionId: string | null;
  markSessionUnread: (sessionKey: string) => void;
  updateSessionMessages: (sessionKey: string, updater: (prev: Message[]) => Message[]) => void;
};

export function useSessionWebSocket({
  bumpSessionRefresh,
  currentSessionId,
  markSessionUnread,
  updateSessionMessages,
}: UseSessionWebSocketParams) {
  const activeSessionRef = useRef<string | null>(currentSessionId);
  const subscribedSessionRef = useRef<string | null>(null);
  const callbacksRef = useRef({ bumpSessionRefresh, markSessionUnread, updateSessionMessages });

  useEffect(() => {
    activeSessionRef.current = currentSessionId;
  }, [currentSessionId]);

  useEffect(() => {
    callbacksRef.current = { bumpSessionRefresh, markSessionUnread, updateSessionMessages };
  }, [bumpSessionRefresh, markSessionUnread, updateSessionMessages]);

  useEffect(() => {
    wsClient.connect();
    const cleanupEvents = wsClient.onEvent((event) => {
      if (event.type === 'subagent_done') {
        const payload = event as SubagentDoneEvent;
        if (!payload.session_id || !payload.message) return;
        const sessionKey = payload.session_id ?? DRAFT_SESSION_KEY;
        callbacksRef.current.updateSessionMessages(sessionKey, (prev) => {
          if (prev.some((msg) => msg.id === payload.message.id)) return prev;
          return [...prev, payload.message];
        });
        if (sessionKey !== (activeSessionRef.current ?? DRAFT_SESSION_KEY)) {
          callbacksRef.current.markSessionUnread(sessionKey);
        }
        callbacksRef.current.bumpSessionRefresh();
        return;
      }
      if (event.type === 'subagent_started') {
        const payload = event as SubagentStartedEvent;
        if (!payload.session_id) return;
        callbacksRef.current.bumpSessionRefresh();
      }
    });
    return () => {
      cleanupEvents();
      wsClient.disconnect();
    };
  }, []);

  useEffect(() => {
    const prev = subscribedSessionRef.current;
    if (prev && prev !== currentSessionId) {
      wsClient.unsubscribe([prev]);
    }
    if (currentSessionId) {
      wsClient.subscribe([currentSessionId]);
    }
    subscribedSessionRef.current = currentSessionId;
  }, [currentSessionId]);
}
