export const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8011/api";

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
