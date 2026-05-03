import { useEffect, useRef } from "react";

type WSMessage = {
  type: string;
  payload: Record<string, unknown>;
};

type SharedSocket = {
  ws: WebSocket | null;
  subscribers: Set<(msg: WSMessage) => void>;
  retries: number;
  reconnectTimer?: ReturnType<typeof setTimeout>;
  closeTimer?: ReturnType<typeof setTimeout>;
  connect: () => void;
};

const sockets = new Map<string, SharedSocket>();

function getSharedSocket(url: string): SharedSocket {
  const existing = sockets.get(url);
  if (existing) return existing;

  const shared: SharedSocket = {
    ws: null,
    subscribers: new Set(),
    retries: 0,
    connect: () => {
      if (
        shared.ws
        && (shared.ws.readyState === WebSocket.CONNECTING || shared.ws.readyState === WebSocket.OPEN)
      ) {
        return;
      }
      try {
        const ws = new WebSocket(url);
        shared.ws = ws;

        ws.onopen = () => {
          shared.retries = 0;
        };

        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data) as WSMessage;
            shared.subscribers.forEach((subscriber) => subscriber(data));
          } catch {
            // Ignore non-JSON keepalive frames.
          }
        };

        ws.onclose = () => {
          if (shared.ws === ws) shared.ws = null;
          if (shared.subscribers.size === 0) return;
          const delay = Math.min(1000 * 2 ** shared.retries, 30000);
          shared.retries += 1;
          clearTimeout(shared.reconnectTimer);
          shared.reconnectTimer = setTimeout(shared.connect, delay);
        };

        ws.onerror = () => {
          ws.close();
        };
      } catch {
        if (shared.subscribers.size === 0) return;
        clearTimeout(shared.reconnectTimer);
        shared.reconnectTimer = setTimeout(shared.connect, 5000);
      }
    },
  };

  sockets.set(url, shared);
  return shared;
}

export function useWebSocket(
  url: string,
  onMessage: (msg: WSMessage) => void,
  enabled = true
) {
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  useEffect(() => {
    if (!enabled) return undefined;
    const shared = getSharedSocket(url);
    const subscriber = (message: WSMessage) => onMessageRef.current(message);
    clearTimeout(shared.closeTimer);
    shared.subscribers.add(subscriber);
    shared.connect();

    return () => {
      shared.subscribers.delete(subscriber);
      if (shared.subscribers.size > 0) return;
      clearTimeout(shared.reconnectTimer);
      shared.closeTimer = setTimeout(() => {
        if (shared.subscribers.size > 0) return;
        shared.ws?.close();
        shared.ws = null;
        sockets.delete(url);
      }, 250);
    };
  }, [url, enabled]);
}

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    sockets.forEach((shared) => {
      clearTimeout(shared.reconnectTimer);
      clearTimeout(shared.closeTimer);
      shared.ws?.close();
    });
    sockets.clear();
  });
}
