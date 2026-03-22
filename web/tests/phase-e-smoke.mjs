import fs from "node:fs";
import path from "node:path";
import { chromium } from "@playwright/test";

const ROOT = path.resolve(process.cwd(), "..");
const OUT_DIR = path.join(ROOT, "debug", "phase_e_playwright");

const BASE_URL = process.env.PHASE_E_WEB_URL || "http://127.0.0.1:3000";
const USERNAME = process.env.PHASE_E_USER || "admin";
const PASSWORD = process.env.PHASE_E_PASS || "admin123";
const USE_FSD = (process.env.PHASE_E_USE_FSD || "1") !== "0";

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

async function shot(page, name) {
  const file = path.join(OUT_DIR, name);
  await page.screenshot({ path: file, fullPage: true });
  console.log(`[shot] ${file}`);
}

async function waitForStatus(page, expected, timeout = 60000) {
  await page.waitForFunction(
    (value) => {
      const spans = Array.from(document.querySelectorAll("span"));
      return spans.some((n) => n.textContent?.trim() === value);
    },
    expected,
    { timeout },
  );
}

async function waitForTaskPlan(page, titleText, timeout = 60000) {
  await page.waitForFunction(
    (title) => {
      const spans = Array.from(document.querySelectorAll("span"));
      return spans.some((n) => (n.textContent || "").includes(title));
    },
    titleText,
    { timeout },
  );
}

async function waitForStepText(page, text, timeout = 60000) {
  await page.getByText(text, { exact: false }).waitFor({ timeout });
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

async function resolvePermissionsUntilIdle(page, maxApprovals = 4) {
  for (let i = 0; i < maxApprovals; i += 1) {
    const state = await page
      .waitForFunction(
        () => {
          const status = Array.from(document.querySelectorAll("span")).find((n) =>
            ["idle", "queued", "running", "waiting", "error"].includes(
              (n.textContent || "").trim(),
            ),
          )?.textContent?.trim();
          const waiting = !!Array.from(document.querySelectorAll("button")).find(
            (n) => /Approve Once/i.test(n.textContent || ""),
          );
          return { status, waiting };
        },
        {},
        { timeout: 90000 },
      )
      .then((h) => h.jsonValue());

    if (state.status === "idle") return;
    if (state.status === "error") {
      throw new Error("Workflow entered error status");
    }
    if (state.waiting) {
      console.log(`[step] approve once (#${i + 1})`);
      await shot(page, `07_waiting_${i + 1}.png`);
      await page.getByRole("button", { name: "Approve Once" }).click();
      await shot(page, `07_after_approve_${i + 1}.png`);
      continue;
    }
  }

  await waitForStatus(page, "idle", 120000);
}

async function main() {
  ensureDir(OUT_DIR);

  const browser = await chromium.launch({
    channel: "chrome",
    headless: false,
    slowMo: 150,
  });

  const context = await browser.newContext({
    viewport: { width: 1600, height: 1100 },
  });
  const page = await context.newPage();

  page.on("console", (msg) => {
    console.log(`[browser:${msg.type()}] ${msg.text()}`);
  });

  const prompt = [
    "请严格按下面要求执行，并且必须使用对应工具：",
    "1. 先调用 planning 建立任务计划，标题写“Phase E 工具验收”，至少包含 5 个步骤。",
    "2. 在执行过程中，每完成一个步骤都调用 todo_update 更新状态。",
    "3. 必须使用 list_dir 查看当前工作区文件。",
    "4. 必须使用 file_write 创建 phase_e_acceptance.txt，内容为三行：Alpha、Beta、Gamma。",
    "5. 必须使用 file_edit 把第二行 Beta 改成 Beta-updated。",
    "6. 必须使用 glob 查找 phase_e_acceptance.txt。",
    "7. 必须使用 grep 搜索字符串 Beta-updated。",
    "8. 最后用中文简短汇报结果。",
  ].join("\n");

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

    console.log("[step] create a fresh session");
    await page.getByRole("button", { name: /New Session/i }).click();
    await page.waitForTimeout(1200);
    await shot(page, "03_new_session.png");

    if (USE_FSD) {
      console.log("[step] switch policy to FSD");
      await switchToFsd(page);
      await shot(page, "04_policy_fsd.png");
    }

    console.log("[step] send Phase E acceptance prompt");
    await page.locator("textarea").fill(prompt);
    await page.getByTitle("Send").click();
    await page.getByText("请严格按下面要求执行", { exact: false }).waitFor({
      timeout: 20000,
    });
    await shot(page, "05_prompt_sent.png");

    console.log("[step] wait for planning panel");
    await waitForTaskPlan(page, "Phase E 工具验收", 60000);
    await waitForStepText(page, "查看当前工作区文件", 60000).catch(() => {});
    await shot(page, "06_task_plan_created.png");

    console.log("[step] wait for workflow to complete");
    if (USE_FSD) {
      await waitForStatus(page, "idle", 120000);
    } else {
      console.log("[step] wait for first permission request");
      await page.getByText("Permission Required").waitFor({ timeout: 90000 });
      await shot(page, "07_waiting_permission.png");
      await resolvePermissionsUntilIdle(page);
    }
    await page.getByText("phase_e_acceptance.txt", { exact: true }).waitFor({
      timeout: 60000,
    });
    await page.getByText("Beta-updated", { exact: false }).waitFor({
      timeout: 60000,
    });
    await shot(page, "08_workflow_completed.png");

    console.log("[step] open output file preview");
    await page.getByText("phase_e_acceptance.txt", { exact: true }).click();
    await page.getByText("Alpha", { exact: true }).waitFor({ timeout: 30000 });
    await page.getByText("Beta-updated", { exact: false }).waitFor({
      timeout: 30000,
    });
    await shot(page, "09_output_file_preview.png");

    console.log("[step] capture final task plan state");
    await shot(page, "10_final_task_plan.png");

    console.log("[done] Phase E smoke completed");
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
