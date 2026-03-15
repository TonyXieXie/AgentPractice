import { useEffect, useRef } from 'react';
import type { Dispatch, SetStateAction } from 'react';

import type { Message } from '../../types';
import { wsClient } from '../../wsClient';
import type { SessionMessageEvent, SubagentDoneEvent, SubagentStartedEvent } from '../../wsTypes';

type UseAppShellSessionChannelParams = {
  currentSessionId: string | null;
  subscribedSessionIds: string[];
  getSessionKey: (sessionId: string | null) => string;
  getCurrentSessionKey: () => string;
  updateSessionMessages: (sessionKey: string, updater: (prev: Message[]) => Message[]) => Message[];
  markSessionUnread: (sessionKey: string) => void;
  setSessionRefreshTrigger: Dispatch<SetStateAction<number>>;
  applyIncomingActiveAgent: (sessionId: string, profileId?: string | null) => void;
  onSessionMessage?: (payload: SessionMessageEvent) => void;
};

export function useAppShellSessionChannel(params: UseAppShellSessionChannelParams) {
  const wsSessionIdsRef = useRef<Set<string>>(new Set());
  const paramsRef = useRef(params);
  paramsRef.current = params;

  useEffect(() => {
    wsClient.connect();
    const cleanupEvents = wsClient.onEvent((event) => {
      const {
        getSessionKey,
        getCurrentSessionKey,
        updateSessionMessages,
        markSessionUnread,
        setSessionRefreshTrigger,
        applyIncomingActiveAgent,
      } = paramsRef.current;
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
        return;
      }
      if (event.type === 'session_message') {
        const payload = event as SessionMessageEvent;
        if (!payload.session_id || !payload.message) return;
        const sessionKey = getSessionKey(payload.session_id);
        updateSessionMessages(sessionKey, (prev) => {
          const index = prev.findIndex((msg) => msg.id === payload.message.id);
          if (index >= 0) {
            const next = [...prev];
            next[index] = {
              ...next[index],
              ...payload.message,
              metadata: payload.message.metadata ?? next[index].metadata,
            };
            return next;
          }
          return [...prev, payload.message];
        });
        applyIncomingActiveAgent(payload.session_id, payload.active_agent_profile);
        paramsRef.current.onSessionMessage?.(payload);
        if (sessionKey !== getCurrentSessionKey()) {
          markSessionUnread(sessionKey);
        }
        setSessionRefreshTrigger((prev) => prev + 1);
      }
    });

    return () => {
      cleanupEvents();
      wsClient.disconnect();
    };
  }, []);

  useEffect(() => {
    const next = new Set((params.subscribedSessionIds || []).filter(Boolean));
    const prev = wsSessionIdsRef.current;
    const toUnsubscribe = Array.from(prev).filter((sessionId) => !next.has(sessionId));
    const toSubscribe = Array.from(next).filter((sessionId) => !prev.has(sessionId));

    if (toUnsubscribe.length > 0) {
      wsClient.unsubscribe(toUnsubscribe);
    }
    if (toSubscribe.length > 0) {
      wsClient.subscribe(toSubscribe);
    }

    wsSessionIdsRef.current = next;
  }, [params.subscribedSessionIds]);
}
