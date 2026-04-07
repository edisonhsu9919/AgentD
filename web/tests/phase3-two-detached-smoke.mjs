import fs from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test";

const ROOT = process.cwd();
const OUT_DIR = path.join(ROOT, "debug", "phase3_task_output_playwright");
const TRACE_FILE = path.join(OUT_DIR, "trace.log");
const BASE_URL = process.env.PHASE3_WEB_URL || "http://127.0.0.1:3000";
const USERNAME = process.env.PHASE3_USER || "admin";
const PASSWORD = process.env.PHASE3_PASS || "admin123";
const CDP_URL = process.env.PHASE3_CDP_URL || "";

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

async function switchToFiles(page) {
  await page.getByRole("button", { name: /Files/i }).click();
  await page.getByRole("button", { name: /Upload/i }).waitFor({ timeout: 15000 });
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

async function openPanel(page) {
  await page.getByTitle("Toggle work panel").click();
  await page.locator("div.fixed.inset-y-0.right-0.z-40").waitFor({ timeout: 10000 });
}

async function main() {
  ensureDir(OUT_DIR);
  fs.writeFileSync(TRACE_FILE, "");

  let browser;
  let context;
  let page;

  if (CDP_URL) {
    trace(`[step] connect browser over CDP ${CDP_URL}`);
    browser = await chromium.connectOverCDP(CDP_URL);
    context = browser.contexts()[0] || (await browser.newContext({
      viewport: { width: 1600, height: 1000 },
    }));
    page = await context.newPage();
  } else {
    browser = await chromium.launch({
      headless: true,
      slowMo: 0,
    });
    context = await browser.newContext({
      viewport: { width: 1600, height: 1000 },
    });
    page = await context.newPage();
  }

  page.on("console", (msg) => {
    console.log(`[browser:${msg.type()}] ${msg.text()}`);
  });

  try {
    trace("[step] open login");
    await page.goto(`${BASE_URL}/login`, { waitUntil: "domcontentloaded" });
    trace("[step] sign in");
    await page.locator('input[type="text"]').first().fill(USERNAME);
    await page.locator('input[type="password"]').first().fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL(/\/chat/, { timeout: 30000 });
    await page.waitForSelector("textarea", { timeout: 30000 });
    await shot(page, "01_logged_in.png");

    trace("[step] new session");
    await page.getByRole("button", { name: /New Session/i }).click();
    await page.waitForTimeout(1200);
    await shot(page, "02_new_session.png");

    trace("[step] switch to fsd + open panel");
    await switchToFsd(page);
    await openPanel(page);

    trace("[step] upload preview file");
    await switchToFiles(page);
    const fileChooser = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: /Upload/i }).click();
    const chooser = await fileChooser;
    await chooser.setFiles(["/tmp/agentd_phase3/preview.txt"]);

    await page.getByText("preview.txt", { exact: true }).waitFor({ timeout: 30000 });
    trace("[step] preview file");
    await page.getByText("preview.txt", { exact: true }).click();
    await page.getByText("phase3 preview file", { exact: false }).waitFor({ timeout: 20000 });
    await shot(page, "03_file_preview_active.png");

    const prompt = [
      "Use launch_detached_process exactly twice right now.",
      "Start exactly two detached background processes in the session root.",
      "Do not use bash for foreground execution.",
      "Do not use launch_subagent.",
      "Process 1 title: alpha-job.",
      "Process 1 command: /bin/sh -lc 'for i in 1 2 3 4 5; do echo ALPHA-$i; sleep 2; done; printf done > alpha_done.txt'",
      "Process 2 title: beta-job.",
      "Process 2 command: /bin/sh -lc 'for i in 1 2 3 4 5; do echo BETA-$i; sleep 2; done; printf done > beta_done.txt'",
      "After both tools report launched, stop.",
    ].join("\n");

    trace("[step] send two detached tasks prompt");
    const textarea = page.locator("textarea").first();
    await textarea.fill(prompt);
    await textarea.press("Enter");
    await shot(page, "04_prompt_sent.png");

    // While tasks are starting, file preview should remain active.
    trace("[step] wait preview remains active");
    await page.getByText("phase3 preview file", { exact: false }).waitFor({ timeout: 120000 });
    await shot(page, "05_preview_not_preempted.png");

    trace("[step] wait task output attention");
    const taskOutputTab = page.getByRole("button", { name: /Task Output/i }).first();
    await taskOutputTab.waitFor({ timeout: 120000 });

    // Wait until attention dot appears, indicating tasks started without stealing focus.
    await taskOutputTab.locator("span.bg-accent").waitFor({ timeout: 120000 });
    await shot(page, "06_task_output_attention.png");

    trace("[step] open task output and wait 2 tasks");
    await taskOutputTab.click();
    await page.getByText(/Tasks \([12]\)/).waitFor({ timeout: 120000 });

    const taskButtons = page.locator("div.w-48 button");
    await taskButtons.nth(1).waitFor({ timeout: 120000 });
    const taskCount = await taskButtons.count();
    if (taskCount < 2) {
      throw new Error(`Expected at least 2 task rows, got ${taskCount}`);
    }
    await shot(page, "07_two_tasks_visible.png");

    // Switch between the two tasks.
    trace("[step] switch between tasks");
    await taskButtons.nth(0).click();
    await page.waitForTimeout(500);
    await shot(page, "08_first_task_selected.png");

    await taskButtons.nth(1).click();
    await page.waitForTimeout(500);
    await shot(page, "09_second_task_selected.png");

    // Wait for both tasks to finish and remain listed.
    trace("[step] wait tasks finish");
    await page.waitForTimeout(13000);
    const taskCountAfter = await taskButtons.count();
    if (taskCountAfter < 2) {
      throw new Error(`Expected task rows to persist after completion, got ${taskCountAfter}`);
    }
    await shot(page, "10_tasks_after_completion.png");

    trace("[done] Phase 3 two-detached smoke completed");
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((err) => {
  console.error("[fatal]", err);
  process.exit(1);
});
