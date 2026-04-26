import {
  fetchEventSource,
  EventStreamContentType,
} from "@microsoft/fetch-event-source";
import { SSE_API_URL } from "./constants";
import { getToken } from "./api";
import type { SSEEvent } from "./types";

export type SSEHandlers = {
  onEvent: (event: SSEEvent) => void;
  onError?: (err: unknown) => void;
  onClose?: () => void;
};

let abortController: AbortController | null = null;

export function connectSSE(sessionId: string, handlers: SSEHandlers) {
  // Disconnect previous connection
  disconnectSSE();

  const ctrl = new AbortController();
  abortController = ctrl;

  const token = getToken();
  if (!token) return;

  // Track whether we just processed a 'done' event (normal server close)
  let lastWasDone = false;

  fetchEventSource(`${SSE_API_URL}/sessions/${sessionId}/events`, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "text/event-stream",
    },
    signal: ctrl.signal,

    onopen: async (response) => {
      if (
        response.ok &&
        response.headers.get("content-type")?.includes(EventStreamContentType)
      ) {
        lastWasDone = false;
        return;
      }
      throw new Error(`SSE connection failed: ${response.status}`);
    },

    onmessage: (msg) => {
      if (!msg.data) return; // keepalive comment

      try {
        const parsed = JSON.parse(msg.data) as SSEEvent;
        if (parsed.event === "done") {
          lastWasDone = true;
        }
        handlers.onEvent(parsed);
      } catch {
        // Ignore unparseable messages
      }
    },

    onerror: (err) => {
      // Intentional disconnect — stop retrying
      if (ctrl.signal.aborted) {
        throw err;
      }
      // Server closed after 'done' — silent reconnect (expected behavior)
      if (lastWasDone) {
        lastWasDone = false;
        return 1000;
      }
      // Actual error — notify user and retry
      handlers.onError?.(err);
      return 3000;
    },

    onclose: () => {
      handlers.onClose?.();
      // Server closes connection after 'done' event.
      // Throw to trigger onerror → auto-reconnect for next prompt.
      throw new Error("SSE_SERVER_CLOSE");
    },

    // Keep connection alive even when tab is hidden
    openWhenHidden: true,
  });
}

export function disconnectSSE() {
  if (abortController) {
    abortController.abort();
    abortController = null;
  }
}
