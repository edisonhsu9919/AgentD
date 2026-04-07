import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const BASE_URL = "http://127.0.0.1:3000";
const API_BASE = "http://127.0.0.1:8011/api";
const OUT_DIR = path.resolve("./debug/phase6d_knowledge_playwright_manual");
fs.mkdirSync(OUT_DIR, { recursive: true });

function shotPath(name) {
  return path.join(OUT_DIR, name);
}

async function main() {
  const browser = await chromium.launch({
    channel: "chrome",
    headless: false,
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });

  try {
    await page.goto(`${BASE_URL}/login`);
    await page.getByLabel("Username").fill("admin");
    await page.getByLabel("Password").fill("admin123");
    await page.getByRole("button", { name: "Sign In" }).click();
    await page.waitForURL(/\/chat/);

    await page.goto(`${BASE_URL}/chat?s=5061d514-0678-4c7c-9dc8-c781676909f9`);
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: shotPath("01_chat_loaded.png") });

    const citationButton = page.getByRole("button", { name: /1 P6D Public Source pdf/ }).last();
    await citationButton.waitFor({ state: "visible", timeout: 15000 });
    await citationButton.click();

    const panel = page.locator("div.fixed.inset-y-0.right-0.z-40");
    await panel.waitFor({ state: "visible", timeout: 15000 });
    await page.screenshot({ path: shotPath("02_panel_opened.png") });

    // Accept either raw pdf path or markdown knowledge path as a visible indicator.
    const rawIndicator = panel.getByText("knowledge/raw/p6d_public.pdf", { exact: true });
    const mdIndicator = panel.getByText("knowledge:p6dpub", { exact: true });
    const downloadButton = panel.getByRole("button", { name: /Download/i });

    let ok = false;
    try {
      await rawIndicator.waitFor({ state: "visible", timeout: 5000 });
      ok = true;
    } catch {}
    if (!ok) {
      try {
        await mdIndicator.waitFor({ state: "visible", timeout: 5000 });
        ok = true;
      } catch {}
    }
    if (!ok) {
      try {
        await downloadButton.waitFor({ state: "visible", timeout: 5000 });
        ok = true;
      } catch {}
    }

    await page.screenshot({ path: shotPath("03_panel_verified.png") });
    if (!ok) {
      throw new Error("knowledge preview indicator not found");
    }

    console.log("[done] citation manual smoke passed");
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error("[fatal]", err);
  process.exit(1);
});
