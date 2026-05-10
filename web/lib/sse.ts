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
const SERVER_CLOSE_RETRY = "SSE_SERVER_CLOSE";

export function connectSSE(sessionId: string, handlers: SSEHandlers) {
  // Disconnect previous connection
  disconnectSSE();

  const ctrl = new AbortController();
  abortController = ctrl;

  const token = getToken();
  if (!token) return;

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
        return;
      }
      throw new Error(`SSE connection failed: ${response.status}`);
    },

    onmessage: (msg) => {
      if (!msg.data) return; // keepalive comment

      try {
        const parsed = JSON.parse(msg.data) as SSEEvent;
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
      if (err instanceof Error && err.message === SERVER_CLOSE_RETRY) {
        return 1000;
      }
      // Actual error — notify user and retry
      handlers.onError?.(err);
      return 3000;
    },

    onclose: () => {
      handlers.onClose?.();
      if (ctrl.signal.aborted) {
        return;
      }
      // Session SSE is expected to be long-lived. Any server-side close should
      // trigger fetch-event-source's retry path without showing a user error.
      throw new Error(SERVER_CLOSE_RETRY);
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
