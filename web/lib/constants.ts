const LOOPBACK_API_PATTERN =
  /^https?:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?\/api\/?$/i;

const publicApiUrl = process.env.NEXT_PUBLIC_API_URL;
const normalizedPublicApiUrl = publicApiUrl?.replace(/\/$/, "");

export const API_URL =
  normalizedPublicApiUrl || "/api";

// Next.js dev/proxy rewrites can buffer or swallow SSE on local loopback setups.
// Keep normal authenticated REST calls on same-origin `/api`, but let the browser
// connect to the backend directly for event streams when a loopback backend URL
// is explicitly configured.
export const SSE_API_URL =
  typeof window !== "undefined" &&
  publicApiUrl &&
  LOOPBACK_API_PATTERN.test(publicApiUrl)
    ? normalizedPublicApiUrl
    : API_URL;

export const DEFAULT_MODEL =
  process.env.NEXT_PUBLIC_DEFAULT_MODEL ||
  "MiniMax-M2.5";

/**
 * Strip `<think>...</think>` blocks from a string.
 * Used to clean LLM reasoning traces from user-visible titles.
 */
export function stripThinkTags(text: string): string {
  // Remove complete <think>...</think> blocks (including multiline)
  let cleaned = text.replace(/<think>[\s\S]*?<\/think>/gi, "").trim();
  // Remove orphaned opening <think> that has no closing tag (truncated)
  cleaned = cleaned.replace(/<think>[\s\S]*/gi, "").trim();
  return cleaned || text;
}
