import fs from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test";

const ROOT = process.cwd();
const OUT_DIR = path.join(ROOT, "debug", "phase6d_knowledge_playwright");
const TRACE_FILE = path.join(OUT_DIR, "trace.log");

const BASE_URL = process.env.PHASE6D_WEB_URL || "http://127.0.0.1:3000";
const USERNAME = process.env.PHASE6D_USER || "admin";
const PASSWORD = process.env.PHASE6D_PASS || "admin123";

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

async function switchToFsd(page) {
  const modeButton = page
    .locator("button")
    .filter({ hasText: /Manual|Autopilot|FSD/ })
    .last();
  await modeButton.click();
  await page.getByRole("button", { name: /FSD/i }).click();
  await page.getByText("FSD", { exact: true }).waitFor({ timeout: 15000 });
}

async function main() {
  ensureDir(OUT_DIR);
  fs.writeFileSync(TRACE_FILE, "");

  const browser = await chromium.launch({
    channel: "chrome",
    headless: false,
    slowMo: 120,
  });
  const context = await browser.newContext({
    viewport: { width: 1600, height: 1100 },
  });
  const page = await context.newPage();

  page.on("console", (msg) => {
    console.log(`[browser:${msg.type()}] ${msg.text()}`);
  });

  const prompt = [
    "请直接使用知识库工具完成一次轻量知识检索，不要使用子代理。",
    "目标文档标题是 P6D Public Source。",
    "请先用 knowledge_catalog 确认文档，再用 knowledge_search 搜索正文短语 public source-backed knowledge document，必要时用 knowledge_read 局部读取。",
    "最后用中文回答这份知识文档讲的是什么，并给出来源。",
  ].join("\n");

  try {
    trace("[step] open login");
    await page.goto(`${BASE_URL}/login`, { waitUntil: "networkidle" });
    await shot(page, "01_login.png");

    trace("[step] sign in");
    await page.locator('input[type="text"]').first().fill(USERNAME);
    await page.locator('input[type="password"]').first().fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL(/\/chat/, { timeout: 30000 });
    await page.waitForSelector("textarea", { timeout: 30000 });
    await shot(page, "02_chat_ready.png");

    trace("[step] new session + fsd");
    await page.getByRole("button", { name: /New Session/i }).click();
    await page.waitForTimeout(1200);
    await switchToFsd(page);
    await shot(page, "03_new_session_fsd.png");

    trace("[step] send knowledge prompt");
    await page.locator("textarea").fill(prompt);
    await page.getByTitle("Send").click();
    const sourcesToggle = page.getByRole("button", { name: /Sources \(/ }).last();
    await sourcesToggle.waitFor({ timeout: 60000 });
    await shot(page, "04_answer_ready.png");

    trace("[step] expand sources");
    await sourcesToggle.click();
    const citationButton = page.getByRole("button", {
      name: /1 P6D Public Source pdf/i,
    }).last();
    await citationButton.waitFor({ timeout: 30000 });
    await shot(page, "05_sources_expanded.png");

    trace("[step] click source -> open raw file preview");
    await citationButton.click();
    const panel = page.locator("div.fixed.inset-y-0.right-0.z-40");
    await panel.waitFor({
      timeout: 20000,
    });
    const rawPath = panel.getByText("knowledge/raw/p6d_public.pdf", { exact: true });
    const mdPath = panel.getByText("knowledge:p6dpub", { exact: true });
    const downloadButton = panel.getByRole("button", { name: /download/i });
    let verified = false;
    try {
      await rawPath.waitFor({ timeout: 10000 });
      verified = true;
    } catch {}
    if (!verified) {
      try {
        await mdPath.waitFor({ timeout: 10000 });
        verified = true;
      } catch {}
    }
    if (!verified) {
      await downloadButton.waitFor({ timeout: 10000 });
    }
    await shot(page, "06_panel_raw_pdf.png");

    trace("[done] Phase 6D knowledge source smoke completed");
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((err) => {
  console.error("[fatal]", err);
  process.exit(1);
});
