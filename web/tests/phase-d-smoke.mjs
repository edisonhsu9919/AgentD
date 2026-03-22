import fs from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test";

const ROOT = path.resolve(process.cwd(), "..");
const OUT_DIR = path.join(ROOT, "debug", "phase_d_playwright");
const FIXTURE_DIR = path.join(OUT_DIR, "fixtures");
const SAMPLE_MD = path.join(FIXTURE_DIR, "phase_d_sample.md");

const BASE_URL = process.env.PHASE_D_WEB_URL || "http://127.0.0.1:3000";
const USERNAME = process.env.PHASE_D_USER || "admin";
const PASSWORD = process.env.PHASE_D_PASS || "admin123";

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

async function shot(page, name) {
  const file = path.join(OUT_DIR, name);
  await page.screenshot({ path: file, fullPage: true });
  console.log(`[shot] ${file}`);
}

async function waitForStatus(page, expected, timeout = 30000) {
  await page.waitForFunction(
    (value) => {
      const badges = Array.from(document.querySelectorAll("span"));
      return badges.some((n) => n.textContent?.trim() === value);
    },
    expected,
    { timeout },
  );
}

async function waitForOneOfStatuses(page, expectedList, timeout = 30000) {
  return page.waitForFunction(
    (values) => {
      const badges = Array.from(document.querySelectorAll("span"));
      const hit = badges.find((n) => values.includes(n.textContent?.trim() || ""));
      return hit ? hit.textContent?.trim() : null;
    },
    expectedList,
    { timeout },
  );
}

async function main() {
  ensureDir(OUT_DIR);
  ensureDir(FIXTURE_DIR);
  fs.writeFileSync(
    SAMPLE_MD,
    [
      "# Phase D Sample",
      "",
      "This file is uploaded by the Playwright smoke script.",
      "",
      "- item 1",
      "- item 2",
    ].join("\n"),
    "utf8",
  );

  const browser = await chromium.launch({
    channel: "chrome",
    headless: true,
  });

  const context = await browser.newContext({
    viewport: { width: 1600, height: 1100 },
  });
  const page = await context.newPage();

  page.on("console", (msg) => {
    console.log(`[browser:${msg.type()}] ${msg.text()}`);
  });

  try {
    console.log(`[step] open login: ${BASE_URL}/login`);
    await page.goto(`${BASE_URL}/login`, { waitUntil: "networkidle" });
    await shot(page, "01_login.png");

    console.log("[step] login");
    await page.locator('input[type="text"]').first().fill(USERNAME);
    await page.locator('input[type="password"]').first().fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL(/\/chat/, { timeout: 30000 });
    await page.waitForSelector("textarea", { timeout: 30000 });
    await shot(page, "02_chat_ready.png");

    console.log("[step] send greeting prompt");
    await page.locator("textarea").fill("你好");
    await page.getByTitle("Send").click();
    await shot(page, "03_after_greeting_send.png");
    await page.getByText("你好", { exact: true }).waitFor({ timeout: 15000 });
    const statusHandle = await waitForOneOfStatuses(page, ["idle", "error"], 60000);
    const finalStatus = await statusHandle.jsonValue();
    if (finalStatus === "error") {
      await shot(page, "99_failure_greeting_error.png");
      throw new Error("Greeting flow reached error status");
    }
    await page
      .locator("div")
      .filter({ hasText: /^Agent$/ })
      .first()
      .waitFor({ timeout: 60000 });
    await shot(page, "03_after_greeting.png");

    console.log("[step] send permission-triggering prompt");
    const permissionPrompt =
      "请务必使用 bash 命令 ls -la 检查当前目录里有什么文件，然后告诉我。";
    await page.locator("textarea").fill(permissionPrompt);
    await page.getByTitle("Send").click();
    await page.getByText("Permission Required").waitFor({ timeout: 60000 });
    await shot(page, "04_waiting_permission.png");

    console.log("[step] refresh to verify waiting recovery");
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("textarea", { timeout: 30000 });

    const recoveryBanner = page.getByText(
      "This session is waiting for permission approval.",
    );
    const permissionDialog = page.getByText("Permission Required");

    if (await recoveryBanner.isVisible().catch(() => false)) {
      console.log("[step] waiting recovery banner visible; click recover");
      await shot(page, "05_waiting_recovery_banner.png");
      await page.getByRole("button", { name: /Recover/i }).click();
      await permissionDialog.waitFor({ timeout: 30000 });
    } else {
      console.log("[step] permission dialog survived refresh directly");
    }

    await shot(page, "06_waiting_recovered.png");

    console.log("[step] approve once");
    await page.getByRole("button", { name: "Approve Once" }).click();
    await waitForStatus(page, "idle", 60000);
    await shot(page, "07_after_approve.png");

    console.log("[step] upload markdown file");
    await page.getByRole("button", { name: /Files/i }).click();
    const uploadInput = page.locator('input[type="file"]');
    await uploadInput.setInputFiles(SAMPLE_MD);
    await page.getByText("phase_d_sample.md", { exact: true }).waitFor({
      timeout: 30000,
    });
    await shot(page, "08_file_uploaded.png");

    console.log("[step] open markdown preview");
    await page.getByText("phase_d_sample.md", { exact: true }).click();
    await page.getByText("This file is uploaded by the Playwright smoke script.").waitFor({
      timeout: 30000,
    });
    await shot(page, "09_markdown_preview.png");

    console.log("[done] Phase D smoke flow completed");
  } catch (err) {
    try {
      await shot(page, "99_failure.png");
    } catch {
      // ignore screenshot failure
    }
    throw err;
  } finally {
    await context.close();
    await browser.close();
  }
}

main().catch((err) => {
  console.error("[fatal]", err);
  process.exit(1);
});
