import fs from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test";

const ROOT = path.resolve(process.cwd(), "..");
const OUT_DIR = path.join(ROOT, "debug", "phase_d_multiuser");
const FIXTURE_DIR = path.join(OUT_DIR, "fixtures");
const BASE_URL = process.env.PHASE_D_WEB_URL || "http://127.0.0.1:3000";
const SKIP_NEW_SESSION = process.env.PHASE_D_SKIP_NEW_SESSION === "1";

const USERS = [
  { actor: "actorA", username: "fd_actor_a", password: "fdpass123" },
  { actor: "actorB", username: "fd_actor_b", password: "fdpass123" },
  { actor: "actorC", username: "fd_actor_c", password: "fdpass123" },
];

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

async function login(page, actor, username, password) {
  await page.goto(`${BASE_URL}/login`, { waitUntil: "networkidle" });
  await page.locator('input[type="text"]').first().fill(username);
  await page.locator('input[type="password"]').first().fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/chat/, { timeout: 30000 });
  await page.waitForSelector("textarea", { timeout: 30000 });
  console.log(`[${actor}] login ok url=${page.url()}`);
}

async function forceNewSession(page, actor) {
  if (SKIP_NEW_SESSION) {
    console.log(`[${actor}] skip new session url=${page.url()} header=${await currentHeader(page)}`);
    return;
  }
  await page.getByRole("button", { name: "New Session" }).first().click();
  await page.waitForFunction(
    () => {
      const spans = Array.from(document.querySelectorAll("span"));
      return spans.some((n) => ["queued", "idle", "running"].includes(n.textContent?.trim() || ""));
    },
    { timeout: 30000 },
  );
  console.log(`[${actor}] new session url=${page.url()} header=${await currentHeader(page)}`);
}

async function sendGreeting(page, actor) {
  await page.locator("textarea").fill(`你好，我是多用户并发测试用户 ${actor}`);
  await page.getByTitle("Send").click();
  await page.getByText(`你好，我是多用户并发测试用户 ${actor}`, { exact: true }).waitFor({
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

async function recoverWaitingIfNeeded(page) {
  const recoveryBanner = page.getByText("This session is waiting for permission approval.");
  const permissionDialog = page.getByText("Permission Required");
  if (await recoveryBanner.isVisible().catch(() => false)) {
    const recoverButton = page.getByRole("button", { name: /Recover/i });
    if (await recoverButton.isVisible().catch(() => false)) {
      await recoverButton.click();
      await permissionDialog.waitFor({ timeout: 30000 });
    }
  }
}

async function actorA(browser, user) {
  const { actor, username, password } = user;
  const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
  const page = await context.newPage();
  page.on("console", (msg) => console.log(`[${actor}] [browser:${msg.type()}] ${msg.text()}`));
  try {
    await login(page, actor, username, password);
    await forceNewSession(page, actor);
    await sendGreeting(page, actor);
    await shot(page, actor, "01_after_greeting.png");

    const prompt = "请务必使用 bash 命令 ls -la 检查当前目录里有什么文件，然后告诉我。";
    await page.locator("textarea").fill(prompt);
    await page.getByTitle("Send").click();
    await page.getByText("Permission Required").waitFor({ timeout: 60000 });
    await shot(page, actor, "02_waiting.png");

    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector("textarea", { timeout: 30000 });
    await recoverWaitingIfNeeded(page);
    await shot(page, actor, "03_after_refresh.png");

    await page.getByRole("button", { name: "Approve Once" }).click();
    await waitForStatus(page, "idle", 60000);
    await page.locator("div").filter({ hasText: /^Agent$/ }).nth(1).waitFor({ timeout: 60000 });
    await shot(page, actor, "04_after_approve.png");
    return { actor, ok: true, header: await currentHeader(page), url: page.url() };
  } catch (error) {
    await shot(page, actor, "99_failure.png").catch(() => {});
    return { actor, ok: false, error: String(error) };
  } finally {
    await context.close();
  }
}

async function actorB(browser, user) {
  const { actor, username, password } = user;
  const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
  const page = await context.newPage();
  const mdFile = sampleFile("multi_user_preview.md", [
    "# Multi-user Preview",
    "",
    "This preview belongs to actor B in multi-user mode.",
  ]);
  page.on("console", (msg) => console.log(`[${actor}] [browser:${msg.type()}] ${msg.text()}`));
  try {
    await login(page, actor, username, password);
    await forceNewSession(page, actor);
    await sendGreeting(page, actor);
    await shot(page, actor, "01_after_greeting.png");

    await page.getByRole("button", { name: /Files/i }).click();
    await page.locator('input[type="file"]').setInputFiles(mdFile);
    await page.getByText("multi_user_preview.md", { exact: true }).waitFor({ timeout: 30000 });
    await shot(page, actor, "02_uploaded.png");

    await page.getByText("multi_user_preview.md", { exact: true }).click();
    await page.getByText("This preview belongs to actor B in multi-user mode.").waitFor({ timeout: 30000 });
    await shot(page, actor, "03_preview.png");
    return { actor, ok: true, header: await currentHeader(page), url: page.url() };
  } catch (error) {
    await shot(page, actor, "99_failure.png").catch(() => {});
    return { actor, ok: false, error: String(error) };
  } finally {
    await context.close();
  }
}

async function actorC(browser, user) {
  const { actor, username, password } = user;
  const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
  const page = await context.newPage();
  page.on("console", (msg) => console.log(`[${actor}] [browser:${msg.type()}] ${msg.text()}`));
  try {
    await login(page, actor, username, password);
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
    } catch {
      autoPilotWorked = true;
    }
    const finalStatus = await waitForOneOfStatuses(page, ["idle", "error", "waiting"], 60000);
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
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  try {
    const results = await Promise.all([
      actorA(browser, USERS[0]),
      actorB(browser, USERS[1]),
      actorC(browser, USERS[2]),
    ]);
    console.log("[summary]", JSON.stringify(results, null, 2));
    const failed = results.filter((r) => !r.ok);
    if (failed.length > 0) {
      throw new Error(`Multi-user concurrency smoke had ${failed.length} failing actor(s)`);
    }
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error("[fatal]", err);
  process.exit(1);
});
