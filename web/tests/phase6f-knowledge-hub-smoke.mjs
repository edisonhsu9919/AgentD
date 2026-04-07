import fs from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test";

const ROOT = process.cwd();
const OUT_DIR = path.join(ROOT, "debug", "phase6f_knowledge_hub_playwright");
const TRACE_FILE = path.join(OUT_DIR, "trace.log");

const BASE_URL = process.env.PHASE6F_WEB_URL || "http://127.0.0.1:3000";
const USERNAME = process.env.PHASE6F_USER || "admin";
const PASSWORD = process.env.PHASE6F_PASS || "admin123";
const SEARCH_TEXT = process.env.PHASE6F_SEARCH || "董事责任保险";

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function trace(msg) {
  fs.appendFileSync(TRACE_FILE, `${new Date().toISOString()} ${msg}\n`);
  console.log(msg);
}

async function shot(page, name) {
  const file = path.join(OUT_DIR, name);
  await page.screenshot({ path: file, fullPage: true });
  console.log(`[shot] ${file}`);
}

async function main() {
  ensureDir(OUT_DIR);
  fs.writeFileSync(TRACE_FILE, "");

  const browser = await chromium.launch({
    channel: "chrome",
    headless: false,
    slowMo: 80,
  });
  const context = await browser.newContext({
    viewport: { width: 1600, height: 1100 },
  });
  const page = await context.newPage();

  page.on("console", (msg) => {
    console.log(`[browser:${msg.type()}] ${msg.text()}`);
  });

  try {
    trace("[step] open login");
    await page.goto(`${BASE_URL}/login`, { waitUntil: "networkidle" });
    await shot(page, "01_login.png");

    trace("[step] sign in");
    await page.locator('input[type="text"]').first().fill(USERNAME);
    await page.locator('input[type="password"]').first().fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL(/\/chat/, { timeout: 30000 });
    await shot(page, "02_chat_ready.png");

    trace("[step] open knowledge hub");
    await page.goto(`${BASE_URL}/knowledge`, { waitUntil: "networkidle" });
    await page.getByText("Knowledge Hub", { exact: true }).waitFor({ timeout: 30000 });
    await shot(page, "03_knowledge_hub.png");

    trace("[step] search");
    const search = page.getByPlaceholder(/Search by title or tags/i);
    await search.fill(SEARCH_TEXT);
    await page.waitForTimeout(1200);
    await shot(page, "04_search_results.png");

    trace("[step] open first matching doc");
    const firstDocButton = page.locator("button").filter({ hasText: SEARCH_TEXT }).first();
    await firstDocButton.waitFor({ timeout: 10000 });
    await firstDocButton.click();
    await page.waitForTimeout(1500);
    await shot(page, "05_after_doc_click.png");

    trace("[step] assert panel visible");
    await page.getByText(/Knowledge Document|Unable to load document content|No file selected/i).waitFor({
      timeout: 10000,
    });
    await shot(page, "06_panel_visible.png");

    trace("[done] Phase 6F knowledge hub smoke completed");
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((err) => {
  console.error("[fatal]", err);
  process.exit(1);
});
