import { API_BASE_URL } from './api';
import type { WsEvent, WsStatusListener } from './wsTypes';

type EventListener = (event: WsEvent) => void;

const buildWsUrl = () => {
  const base = API_BASE_URL.replace(/^http/i, (match) => (match.toLowerCase() === 'https' ? 'wss' : 'ws'));
  return `${base}/ws`;
};

class WsClient {
  private ws: WebSocket | null = null;
  private listeners = new Set<EventListener>();
  private statusListeners = new Set<WsStatusListener>();
  private subscriptions = new Set<string>();
  private reconnectTimer: number | null = null;
  private reconnectAttempt = 0;
  private connected = false;
  private shouldReconnect = true;

  connect() {
    if (this.ws || !this.shouldReconnect) return;
    this.shouldReconnect = true;
    this.open();
  }

  disconnect() {
    this.shouldReconnect = false;
    this.clearReconnect();
    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        // ignore
      }
      this.ws = null;
    }
    this.setConnected(false);
  }

  isConnected() {
    return this.connected;
  }

  onEvent(listener: EventListener) {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  onStatus(listener: WsStatusListener) {
    this.statusListeners.add(listener);
    listener(this.connected);
    return () => {
      this.statusListeners.delete(listener);
    };
  }

  subscribe(sessionIds: string[]) {
    const next = sessionIds.filter((id) => id);
    if (!next.length) return;
    let changed = false;
    next.forEach((id) => {
      if (!this.subscriptions.has(id)) {
        this.subscriptions.add(id);
        changed = true;
      }
    });
    if (changed) {
      this.send({ type: 'subscribe', session_ids: next });
    }
  }

  unsubscribe(sessionIds: string[]) {
    const next = sessionIds.filter((id) => id);
    if (!next.length) return;
    let changed = false;
    next.forEach((id) => {
      if (this.subscriptions.delete(id)) {
        changed = true;
      }
    });
    if (changed) {
      this.send({ type: 'unsubscribe', session_ids: next });
    }
  }

  private open() {
    const ws = new WebSocket(buildWsUrl());
    this.ws = ws;

    ws.onopen = () => {
      this.reconnectAttempt = 0;
      this.setConnected(true);
      const ids = Array.from(this.subscriptions);
      if (ids.length) {
        this.send({ type: 'subscribe', session_ids: ids });
      }
    };

    ws.onmessage = (event) => {
      let payload: any = null;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }
      if (!payload || typeof payload.type !== 'string') return;
      if (payload.type === 'pong') return;
      this.listeners.forEach((listener) => listener(payload as WsEvent));
    };

    ws.onclose = () => {
      this.ws = null;
      this.setConnected(false);
      if (this.shouldReconnect) {
        this.scheduleReconnect();
      }
    };

    ws.onerror = () => {
      // rely on close event to trigger reconnect
    };
  }

  private send(payload: any) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    try {
      this.ws.send(JSON.stringify(payload));
    } catch {
      // ignore send errors
    }
  }

  private scheduleReconnect() {
    this.clearReconnect();
    this.reconnectAttempt += 1;
    const delay = Math.min(30_000, 1000 * 2 ** (this.reconnectAttempt - 1));
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.open();
    }, delay);
  }

  private clearReconnect() {
    if (this.reconnectTimer != null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private setConnected(next: boolean) {
    if (this.connected === next) return;
    this.connected = next;
    this.statusListeners.forEach((listener) => listener(next));
  }
}

export const wsClient = new WsClient();
