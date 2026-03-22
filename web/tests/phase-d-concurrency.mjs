import fs from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test";

const ROOT = path.resolve(process.cwd(), "..");
const OUT_DIR = path.join(ROOT, "debug", "phase_d_concurrency");
const FIXTURE_DIR = path.join(OUT_DIR, "fixtures");

const BASE_URL = process.env.PHASE_D_WEB_URL || "http://127.0.0.1:3000";
const USERNAME = process.env.PHASE_D_USER || "admin";
const PASSWORD = process.env.PHASE_D_PASS || "admin123";

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function sampleFile(name, lines) {
  const file = path.join(FIXTURE_DIR, name);
  fs.writeFileSync(file, lines.join("\n"), "utf8");
  return file;
}

async function shot(page, actor, name) {
  const dir = path.join(OUT_DIR, actor);
  ensureDir(dir);
  const file = path.join(dir, name);
  await page.screenshot({ path: file, fullPage: true });
  console.log(`[${actor}] [shot] ${file}`);
}

async function waitForStatus(page, expected, timeout = 60000) {
  await page.waitForFunction(
    (value) => {
      const badges = Array.from(document.querySelectorAll("span"));
      return badges.some((n) => n.textContent?.trim() === value);
    },
    expected,
    { timeout },
  );
}

async function waitForOneOfStatuses(page, expectedList, timeout = 60000) {
  const handle = await page.waitForFunction(
    (values) => {
      const badges = Array.from(document.querySelectorAll("span"));
      const hit = badges.find((n) => values.includes(n.textContent?.trim() || ""));
      return hit ? hit.textContent?.trim() : null;
    },
    expectedList,
    { timeout },
  );
  return handle.jsonValue();
}

async function currentHeader(page) {
  try {
    return (await page.locator("h2").first().textContent())?.trim() || "";
  } catch {
    return "";
  }
}

async function login(page, actor) {
  await page.goto(`${BASE_URL}/login`, { waitUntil: "networkidle" });
  await page.locator('input[type="text"]').first().fill(USERNAME);
  await page.locator('input[type="password"]').first().fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/chat/, { timeout: 30000 });
  await page.waitForSelector("textarea", { timeout: 30000 });
  console.log(`[${actor}] login ok url=${page.url()}`);
}

async function forceNewSession(page, actor) {
  await page.getByRole("button", { name: "New Session" }).first().click();
  await page.waitForFunction(
    () => {
      const spans = Array.from(document.querySelectorAll("span"));
      return spans.some((n) => n.textContent?.trim() === "queued") ||
        spans.some((n) => n.textContent?.trim() === "idle");
    },
    { timeout: 30000 },
  );
  console.log(`[${actor}] forced new session url=${page.url()} header=${await currentHeader(page)}`);
}

async function sendGreeting(page, actor) {
  await page.locator("textarea").fill(`你好，我是并发测试用户 ${actor}`);
  await page.getByTitle("Send").click();
  await page.getByText(`你好，我是并发测试用户 ${actor}`, { exact: true }).waitFor({
    timeout: 15000,
  });
  const status = await waitForOneOfStatuses(page, ["idle", "error"], 60000);
  console.log(`[${actor}] greeting status=${status} header=${await currentHeader(page)} url=${page.url()}`);
  if (status === "error") {
    throw new Error(`${actor} greeting entered error`);
  }
  await page.locator("div").filter({ hasText: /^Agent$/ }).first().waitFor({
    timeout: 60000,
  });
}

async function actorA(browser) {
  const actor = "actorA";
  const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
  const page = await context.newPage();
  page.on("console", (msg) => console.log(`[${actor}] [browser:${msg.type()}] ${msg.text()}`));
  try {
    await login(page, actor);
    await forceNewSession(page, actor);
    await sendGreeting(page, actor);
    await shot(page, actor, "01_after_greeting.png");

    const prompt = "请务必使用 bash 命令 ls -la 检查当前目录里有什么文件，然后告诉我。";
    await page.locator("textarea").fill(prompt);
    await page.getByTitle("Send").click();
    await page.getByText("Permission Required").waitFor({ timeout: 60000 });
    console.log(`[${actor}] before refresh header=${await currentHeader(page)} url=${page.url()}`);
    await shot(page, actor, "02_waiting.png");

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("textarea", { timeout: 30000 });
    const recoveryBanner = page.getByText("This session is waiting for permission approval.");
    const permissionDialog = page.getByText("Permission Required");
    if (await recoveryBanner.isVisible().catch(() => false)) {
      await page.getByRole("button", { name: /Recover/i }).click();
      await permissionDialog.waitFor({ timeout: 30000 });
    }
    console.log(`[${actor}] after refresh header=${await currentHeader(page)} url=${page.url()}`);
    await shot(page, actor, "03_after_refresh.png");

    await page.getByRole("button", { name: "Approve Once" }).click();
    await waitForStatus(page, "idle", 60000);
    const permissionStillVisible = await permissionDialog.isVisible().catch(() => false);
    console.log(`[${actor}] after approve header=${await currentHeader(page)} permissionVisible=${permissionStillVisible}`);
    await shot(page, actor, "04_after_approve.png");
    return {
      actor,
      ok: true,
      permissionStillVisible,
      header: await currentHeader(page),
      url: page.url(),
    };
  } catch (error) {
    await shot(page, actor, "99_failure.png").catch(() => {});
    return { actor, ok: false, error: String(error) };
  } finally {
    await context.close();
  }
}

async function actorB(browser) {
  const actor = "actorB";
  const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
  const page = await context.newPage();
  const mdFile = sampleFile("actor_b_preview.md", [
    "# Actor B Preview",
    "",
    "This preview belongs to actor B.",
  ]);
  page.on("console", (msg) => console.log(`[${actor}] [browser:${msg.type()}] ${msg.text()}`));
  try {
    await login(page, actor);
    await forceNewSession(page, actor);
    await sendGreeting(page, actor);
    await shot(page, actor, "01_after_greeting.png");

    await page.getByRole("button", { name: /Files/i }).click();
    await page.locator('input[type="file"]').setInputFiles(mdFile);
    await page.getByText("actor_b_preview.md", { exact: true }).waitFor({ timeout: 30000 });
    await shot(page, actor, "02_uploaded.png");

    await page.getByText("actor_b_preview.md", { exact: true }).click();
    await page.getByText("This preview belongs to actor B.").waitFor({ timeout: 30000 });
    await shot(page, actor, "03_preview.png");
    return { actor, ok: true, header: await currentHeader(page), url: page.url() };
  } catch (error) {
    await shot(page, actor, "99_failure.png").catch(() => {});
    return { actor, ok: false, error: String(error) };
  } finally {
    await context.close();
  }
}

async function actorC(browser) {
  const actor = "actorC";
  const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
  const page = await context.newPage();
  page.on("console", (msg) => console.log(`[${actor}] [browser:${msg.type()}] ${msg.text()}`));
  try {
    await login(page, actor);
    await forceNewSession(page, actor);
    await sendGreeting(page, actor);
    await shot(page, actor, "01_after_greeting.png");

    const prompt = "请务必使用 bash 命令 ls -la 检查当前目录里有什么文件，然后告诉我。";
    await page.locator("textarea").fill(prompt);
    await page.getByTitle("Send").click();
    await page.getByText("Permission Required").waitFor({ timeout: 60000 });
    await shot(page, actor, "02_waiting.png");

    await page.getByRole("button", { name: "Approve Always" }).click();
    await waitForStatus(page, "idle", 60000);
    await shot(page, actor, "03_after_approve_always.png");

    await page.locator("textarea").fill(prompt);
    await page.getByTitle("Send").click();

    let autoPilotWorked = false;
    try {
      await page.getByText("Permission Required").waitFor({ timeout: 5000 });
      autoPilotWorked = false;
    } catch {
      autoPilotWorked = true;
    }

    const finalStatus = await waitForOneOfStatuses(page, ["idle", "error", "waiting"], 60000);
    console.log(`[${actor}] autopilot=${autoPilotWorked} status=${finalStatus} header=${await currentHeader(page)}`);
    await shot(page, actor, "04_second_run.png");
    return {
      actor,
      ok: finalStatus !== "error",
      autoPilotWorked,
      status: finalStatus,
      header: await currentHeader(page),
      url: page.url(),
    };
  } catch (error) {
    await shot(page, actor, "99_failure.png").catch(() => {});
    return { actor, ok: false, error: String(error) };
  } finally {
    await context.close();
  }
}

async function main() {
  ensureDir(OUT_DIR);
  ensureDir(FIXTURE_DIR);

  const browser = await chromium.launch({
    channel: "chrome",
    headless: true,
  });

  try {
    const results = await Promise.all([actorA(browser), actorB(browser), actorC(browser)]);
    console.log("[summary]", JSON.stringify(results, null, 2));
    const failed = results.filter((r) => !r.ok);
    if (failed.length > 0) {
      throw new Error(`Concurrency smoke had ${failed.length} failing actor(s)`);
    }
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error("[fatal]", err);
  process.exit(1);
});
