import fs from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test";

const ROOT = process.cwd();
const OUT_DIR = path.join(ROOT, "debug", "phase6e_knowledge_import_playwright");
const TRACE_FILE = path.join(OUT_DIR, "trace.log");

const BASE_URL = process.env.PHASE6E_WEB_URL || "http://127.0.0.1:3000";
const API_URL = process.env.PHASE6E_API_URL || "http://127.0.0.1:8011/api";
const USERNAME = process.env.PHASE6E_USER || "admin";
const PASSWORD = process.env.PHASE6E_PASS || "admin123";
const SAMPLE_FILE =
  process.env.PHASE6E_FILE ||
  path.resolve(ROOT, "../debug/compact_test_files/我国董事责任保险制度建设研究_张瑞纲.pdf");

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

async function waitForChatIdle(page, timeout = 180000) {
  await page.waitForFunction(
    () => {
      return Array.from(document.querySelectorAll("span")).some(
        (n) => n.textContent?.trim() === "Idle",
      );
    },
    undefined,
    { timeout },
  );
}

async function main() {
  ensureDir(OUT_DIR);
  fs.writeFileSync(TRACE_FILE, "");

  const browser = await chromium.launch({
    channel: "chrome",
    headless: false,
    slowMo: 100,
  });

  const context = await browser.newContext({
    viewport: { width: 1600, height: 1100 },
  });
  const page = await context.newPage();

  page.on("console", (msg) => {
    console.log(`[browser:${msg.type()}] ${msg.text()}`);
  });

  let token = "";

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

    token = await page.evaluate(() => localStorage.getItem("agentd_token") || "");

    trace("[step] create fresh session");
    await page.getByRole("button", { name: /New Session/i }).click();
    await page.waitForTimeout(1200);
    await switchToFsd(page);
    await shot(page, "03_new_session_fsd.png");

    trace("[step] upload sample pdf");
    await page.getByRole("button", { name: /Files/i }).click();
    await page.locator('input[type="file"]').setInputFiles(SAMPLE_FILE);
    const sampleName = path.basename(SAMPLE_FILE);
    await page.getByText(sampleName, { exact: true }).waitFor({ timeout: 60000 });
    await shot(page, "04_file_uploaded.png");

    const prompt = [
      "请先加载 knowledge-import skill。",
      `然后把刚上传的文件《${sampleName}》导入知识库。`,
      "请走后台导入流程，不要手动跳过用户确认。",
      "当右侧 html_app 表单出现后，我会人工确认。",
      "在知识导入完成后停止。",
    ].join("\n");

    trace("[step] send import prompt");
    await page.locator("textarea").fill(prompt);
    await page.getByTitle("Send").click();
    await shot(page, "05_prompt_sent.png");

    trace("[step] wait html_app panel");
    const appTab = page.getByRole("button", { name: /App/i }).first();
    await appTab.waitFor({ timeout: 180000 });
    await page.getByText("Knowledge Import", { exact: true }).waitFor({ timeout: 180000 });
    const iframe = page.frameLocator('iframe[title="Panel App"]');
    await iframe.getByRole("heading", { name: "Import to Knowledge Base" }).waitFor({
      timeout: 180000,
    });
    await shot(page, "06_html_app_ready.png");

    trace("[step] edit permission + submit");
    await iframe.locator("#permission").selectOption("public");
    await iframe.getByRole("button", { name: /Confirm Import/i }).click();
    await page.getByText(/Submitted/i).waitFor({ timeout: 30000 });
    await shot(page, "07_form_submitted.png");

    trace("[step] wait background task completion");
    await page.getByRole("button", { name: /Task Output/i }).click();
    await waitForChatIdle(page, 240000);
    await shot(page, "08_after_task_complete.png");

    trace("[step] verify imported document via API");
    const docsRes = await fetch(`${API_URL}/knowledge/documents`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!docsRes.ok) {
      throw new Error(`knowledge documents api failed: ${docsRes.status}`);
    }
    const docsJson = await docsRes.json();
    const docs = docsJson.data || [];
    const imported = docs.find((d) => d.source_file === sampleName);
    if (!imported) {
      throw new Error(`Imported knowledge doc for ${sampleName} not found`);
    }
    trace(`[assert] imported doc found: ${imported.doc_id}`);

    const sourceRes = await fetch(`${API_URL}/knowledge/source/${encodeURIComponent(imported.doc_id)}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!sourceRes.ok) {
      throw new Error(`knowledge source api failed: ${sourceRes.status}`);
    }
    const sourceJson = await sourceRes.json();
    trace(`[assert] source resolved: ${JSON.stringify(sourceJson.data)}`);

    trace("[done] Phase 6E knowledge import smoke completed");
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((err) => {
  console.error("[fatal]", err);
  process.exit(1);
});
