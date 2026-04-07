import fs from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test";

const ROOT = process.cwd();
const OUT_DIR = path.join(ROOT, "debug", "phase6e_system_import_playwright");
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

    trace("[step] open file preview");
    await page.getByText(sampleName, { exact: true }).click();
    await page.waitForTimeout(1500);
    await shot(page, "05_file_preview.png");

    trace("[step] look for import button");
    const importBtn = page.getByRole("button", { name: /导入到知识库|Import to Knowledge|Import to Knowledge Base/i }).first();
    await importBtn.waitFor({ timeout: 10000 });
    page.once("dialog", (dialog) => dialog.accept());
    await importBtn.click();
    await shot(page, "06_import_clicked.png");

    trace("[step] wait for draft form");
    await page.getByText(/Confirm Import Metadata/i).waitFor({ timeout: 30000 });
    await shot(page, "07_draft_form_ready.png");

    trace("[step] verify draft fields");
    const titleInput = page.getByPlaceholder(/Document title/i).first();
    const descInput = page.getByPlaceholder(/Brief description of this document/i).first();
    const tagsInput = page.getByPlaceholder(/finance, report, 2026/i).first();
    const titleValue = await titleInput.inputValue();
    const descValue = await descInput.inputValue();
    const tagsValue = (await tagsInput.count()) > 0 ? await tagsInput.inputValue() : "";
    trace(`[assert] draft title=${JSON.stringify(titleValue)}`);
    trace(`[assert] draft description chars=${descValue.length}`);
    trace(`[assert] draft tags=${JSON.stringify(tagsValue)}`);
    await shot(page, "08_draft_values.png");

    trace("[step] edit permission + submit");
    await page.getByRole("button", { name: /Public/i }).click();
    const submitBtn = page.getByRole("button", { name: /^Confirm Import$/i }).last();
    await submitBtn.click();
    await shot(page, "09_form_submitted.png");

    trace("[step] wait for processing or completion state");
    await Promise.race([
      page.getByText(/Starting import|Extracting content|Writing to knowledge base/i).waitFor({
        timeout: 30000,
      }),
      page.getByText(/Import complete|Import completed/i).waitFor({ timeout: 30000 }),
    ]);
    await shot(page, "10_processing_or_completed.png");

    trace("[step] wait for completion");
    await page.getByText(/Import complete|Import completed/i).waitFor({ timeout: 180000 });
    await shot(page, "11_completed.png");

    trace("[step] resolve imported document via API");
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

    trace("[done] Phase 6E system import UI smoke completed");
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((err) => {
  console.error("[fatal]", err);
  process.exit(1);
});
