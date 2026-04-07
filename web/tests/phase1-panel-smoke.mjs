import fs from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test";

const ROOT = process.cwd();
const OUT_DIR = path.join(ROOT, "..", "debug", "phase1_panel_playwright");
const BASE_URL = process.env.PHASE1_WEB_URL || "http://127.0.0.1:3000";
const USERNAME = process.env.PHASE1_USER || "admin";
const PASSWORD = process.env.PHASE1_PASS || "admin123";

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
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

async function openPanel(page) {
  await page.getByTitle("Toggle work panel").click();
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

async function waitForTaskOutputTab(page, timeout = 90000) {
  await page.getByText("Task Output", { exact: true }).waitFor({ timeout });
}

async function waitForTaskOutputLine(page, text, timeout = 90000) {
  await page.getByText(text, { exact: false }).waitFor({ timeout });
}

async function main() {
  ensureDir(OUT_DIR);

  const browser = await chromium.launch({
    channel: "chrome",
    headless: false,
    slowMo: 120,
  });

  const context = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
  });
  const page = await context.newPage();

  page.on("console", (msg) => {
    console.log(`[browser:${msg.type()}] ${msg.text()}`);
  });

  try {
    await page.goto(`${BASE_URL}/login`, { waitUntil: "domcontentloaded" });
    await page.locator('input[type="text"]').first().fill(USERNAME);
    await page.locator('input[type="password"]').first().fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL(/\/chat/, { timeout: 30000 });
    await page.waitForSelector("textarea", { timeout: 30000 });
    await shot(page, "01_logged_in.png");

    await page.getByRole("button", { name: /New Session/i }).click();
    await page.waitForTimeout(1200);
    await shot(page, "02_new_session.png");

    // Empty panel opens as overlay and shows placeholder
    await openPanel(page);
    await page.locator("div.fixed.inset-y-0.right-0.z-40").waitFor({ timeout: 10000 });
    await page.getByText("Click a file to preview, or view task output here.", { exact: false }).waitFor({ timeout: 10000 });
    const panel = page.locator("div.fixed.inset-y-0.right-0.z-40");
    const box = await panel.boundingBox();
    if (!box || box.width < 650) {
      throw new Error(`Expected half-screen panel width, got ${box?.width ?? "null"}`);
    }
    await shot(page, "03_empty_panel.png");

    // Close and reopen
    await page.getByTitle("Close panel").click();
    await page.locator("div.fixed.inset-y-0.right-0.z-40").waitFor({ state: "detached", timeout: 10000 });
    await openPanel(page);
    await page.locator("div.fixed.inset-y-0.right-0.z-40").waitFor({ timeout: 10000 });
    await shot(page, "04_reopened_panel.png");

    // Fixed panel-type tabs should always exist
    await page.getByText("File Preview", { exact: true }).waitFor({ timeout: 10000 });
    await page.getByText("Task Output", { exact: true }).waitFor({ timeout: 10000 });
    await page.getByText("App", { exact: true }).waitFor({ timeout: 10000 });

    // Upload files and preview
    await switchToFiles(page);
    const fileChooser = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: /Upload/i }).click();
    const chooser = await fileChooser;
    await chooser.setFiles([
      "/tmp/agentd_phase1/panel-smoke.txt",
      "/tmp/agentd_phase1/panel-smoke.md",
    ]);
    await page.getByText("panel-smoke.txt", { exact: true }).waitFor({ timeout: 30000 });
    await page.getByText("panel-smoke.md", { exact: true }).waitFor({ timeout: 30000 });
    await shot(page, "05_uploaded_files.png");

    await page.getByText("panel-smoke.txt", { exact: true }).click();
    await page.getByText("hello panel", { exact: false }).waitFor({ timeout: 20000 });
    await shot(page, "06_txt_preview.png");

    await page.getByText("panel-smoke.md", { exact: true }).click();
    await page.getByText("Panel Smoke", { exact: false }).waitFor({ timeout: 20000 });
    await shot(page, "07_md_preview.png");

    // File preview should remain a single fixed tab, not per-file tabs
    const filePreviewTabs = page.getByRole("button", { name: "File Preview" });
    const filePreviewTabCount = await filePreviewTabs.count();
    if (filePreviewTabCount !== 1) {
      throw new Error(`Expected exactly 1 File Preview tab, got ${filePreviewTabCount}`);
    }
    // Switching tabs should preserve current preview state
    await page.getByRole("button", { name: "Task Output" }).click();
    await page.getByText("Task output will appear here", { exact: false }).waitFor({ timeout: 10000 });
    await page.getByRole("button", { name: "File Preview" }).click();
    await page.getByText("Panel Smoke", { exact: false }).waitFor({ timeout: 10000 });

    // Delete the active preview file and ensure panel falls back safely
    const mdRow = page.getByRole("button", { name: "panel-smoke.md" }).first().locator("xpath=..");
    await mdRow.hover();
    page.once("dialog", (dialog) => dialog.accept());
    await mdRow.getByTitle("Delete file").click();
    await page.locator("div.space-y-0\\.5").getByText("panel-smoke.md", { exact: true }).waitFor({ state: "detached", timeout: 20000 }).catch(() => {});
    await page.getByText("Click a file to preview, or view task output here.", { exact: false }).waitFor({ timeout: 20000 });
    await shot(page, "08_after_delete.png");

    // HTML app placeholder should be reachable
    await page.getByRole("button", { name: "App" }).click();
    await page.getByText("Interactive App", { exact: false }).waitFor({ timeout: 10000 });
    await shot(page, "09_html_app_placeholder.png");

    // Skill area check: should be persistent in lower sidebar under Sessions.
    const sessionsTab = page.getByRole("button", { name: /^Sessions$/ });
    const filesTab = page.getByRole("button", { name: /^Files$/ });
    await sessionsTab.waitFor({ timeout: 10000 });
    await filesTab.waitFor({ timeout: 10000 });
    await sessionsTab.click();
    await page.getByText("Skills", { exact: true }).waitFor({ timeout: 10000 });
    await shot(page, "10_sidebar_skill_tab.png");

    console.log("[done] Phase 1 panel smoke completed");
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((err) => {
  console.error("[fatal]", err);
  process.exit(1);
});
