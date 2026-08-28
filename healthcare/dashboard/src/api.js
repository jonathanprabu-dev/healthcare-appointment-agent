// Everything that talks to the dashboard API lives here, so a change of host
// or transport is one edit rather than a hunt through components.

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

export async function getJSON(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`${path} -> ${res.status} ${res.statusText}`);
  return res.json();
}

/**
 * Subscribe to the live event stream.
 *
 * Reconnects on drop with a backoff. The agent pushes events only while a
 * call is in progress, so a socket can sit idle for a long time and still be
 * healthy -- do not add an idle timeout here.
 *
 * Returns a teardown function.
 */
export function subscribeToEvents({ onEvent, onStatus }) {
  const url = `${API_BASE.replace(/^http/, "ws")}/api/events`;
  let socket = null;
  let closed = false;
  let retry = 0;
  let timer = null;

  const connect = () => {
    if (closed) return;
    onStatus?.(retry === 0 ? "connecting" : "reconnecting");
    socket = new WebSocket(url);

    socket.onopen = () => {
      retry = 0;
      onStatus?.("live");
      // The server's receive loop exists to notice us going away; a periodic
      // ping keeps intermediaries from reaping an idle socket between calls.
      timer = setInterval(() => {
        if (socket?.readyState === WebSocket.OPEN) socket.send("ping");
      }, 25000);
    };

    socket.onmessage = (msg) => {
      try {
        onEvent(JSON.parse(msg.data));
      } catch {
        // A frame we cannot parse is not worth tearing the stream down for.
      }
    };

    socket.onclose = () => {
      clearInterval(timer);
      if (closed) return;
      onStatus?.("offline");
      retry += 1;
      setTimeout(connect, Math.min(1000 * 2 ** retry, 15000));
    };

    socket.onerror = () => socket?.close();
  };

  connect();

  return () => {
    closed = true;
    clearInterval(timer);
    socket?.close();
  };
}

export const endpoints = {
  summary: () => getJSON("/api/summary"),
  doctors: () => getJSON("/api/doctors"),
  patients: () => getJSON("/api/patients"),
  appointments: () => getJSON("/api/appointments"),
  billing: () => getJSON("/api/billing"),
  history: (limit = 100) => getJSON(`/api/events/history?limit=${limit}`),
};
