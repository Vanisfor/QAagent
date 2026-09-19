import { expect, test, type Page } from "@playwright/test";

const token = `test.${Buffer.from(JSON.stringify({ sub: "demo@example.com" })).toString("base64url")}.test`;
const llm = { configured: true, provider: "deepseek", model: "demo", base_url: "https://api.deepseek.com", temperature: 0.2, max_tokens: 2000, thinking_enabled: false, api_key_masked: null };
const makeSession = (id: string) => ({ session_id: id, name: `会话 ${id}`, token: { access_token: id } });

async function setup(page: Page, authenticated = true, empty = false) {
  const state = { refreshes: 0, loggedIn: authenticated, appearance: { theme: "light", sidebar_collapsed: false, background: { preset: "none", brightness: 1, blur: 0, opacity: 1, imageDataUrl: undefined as string | undefined } }, personalization: { personality: "professional", custom_instructions: "", verbosity: "balanced", response_style: { markdown: true, emoji: false, technical_depth: "medium" }, memory_enabled: true }, profile: { display_name: "Demo User", avatar_url: null, bio: "", language: "auto", timezone: "Asia/Shanghai" }, sessions: empty ? [] : [makeSession("alpha"), makeSession("beta")], history: [] as string[], creates: 0, spaces: [{ slug: "u1-notes-demo", name: "我的笔记", role: "owner", is_public: false, document_count: 0 }], documents: [] as Array<{ id: number; source: string; title: string; status: string; updated_at: string }> };
  if (authenticated) await page.addInitScript((value) => {
    if (!sessionStorage.getItem("test-initialized")) {
      sessionStorage.setItem("qa-user-token", value);
      sessionStorage.setItem("qa-session-id", "beta");
      sessionStorage.setItem("test-initialized", "true");
    }
  }, token);
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/refresh")) { state.refreshes++; return route.fulfill(state.loggedIn ? { json: { access_token: token } } : { status: 401 }); }
    if (path.endsWith("/auth/logout")) { state.loggedIn = false; return route.fulfill({ status: 204 }); }
    if (path.endsWith("/users/me")) return route.fulfill({ json: { id: 1, email: "demo@example.com", status: "active", created_at: "2026-01-01T00:00:00Z", last_login_at: null, profile: state.profile } });
    if (path.endsWith("/users/me/settings")) {
      if (route.request().method() === "PATCH") state.appearance = route.request().postDataJSON();
      return route.fulfill({ json: state.appearance });
    }
    if (path.endsWith("/personalization")) {
      if (route.request().method() === "PATCH") state.personalization = route.request().postDataJSON();
      return route.fulfill({ json: state.personalization });
    }
    if (path.endsWith("/avatar")) {
      if (route.request().method() === "POST") { state.profile.avatar_url = "/api/v1/users/me/avatar?v=demo"; return route.fulfill({ json: state.profile }); }
      if (route.request().method() === "DELETE") { state.profile.avatar_url = null; return route.fulfill({ status: 204 }); }
      return state.profile.avatar_url ? route.fulfill({ contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6iT8AAAAASUVORK5CYII=", "base64") }) : route.fulfill({ status: 404 });
    }
    if (path.endsWith("/profile")) { state.profile = { ...state.profile, ...route.request().postDataJSON() }; return route.fulfill({ json: state.profile }); }
    if (path.endsWith("/settings/llm")) return route.fulfill({ json: llm });
    if (path.endsWith("/knowledge-spaces")) {
      if (route.request().method() === "POST") {
        const name = String(route.request().postDataJSON().name);
        const space = { slug: `u1-${state.spaces.length + 1}`, name, role: "owner", is_public: false, document_count: 0 };
        state.spaces.push(space);
        return route.fulfill({ status: 201, json: space });
      }
      return route.fulfill({ json: state.spaces });
    }
    const documentMatch = path.match(/\/knowledge-spaces\/([^/]+)\/documents(?:\/(\d+))?$/);
    if (documentMatch) {
      if (route.request().method() === "POST") {
        const document = { id: 11, source: "guide.md", title: "guide.md", status: "available", updated_at: "2026-09-18T00:00:00Z" };
        state.documents = [document]; state.spaces[0].document_count = 1;
        return route.fulfill({ status: 202, json: { job: { id: 5, space_slug: decodeURIComponent(documentMatch[1]), file_name: "guide.md", status: "completed", attempts: 1, document_id: 11, error: null, created_at: "2026-09-18T00:00:00Z" } } });
      }
      if (route.request().method() === "DELETE") { state.documents = []; state.spaces[0].document_count = 0; return route.fulfill({ status: 204 }); }
      return route.fulfill({ json: state.documents });
    }
    if (path.includes("/knowledge-ingestion-jobs/")) return route.fulfill({ json: { id: 5, space_slug: "u1-notes-demo", file_name: "guide.md", status: "completed", attempts: 1, document_id: 11, error: null, created_at: "2026-09-18T00:00:00Z" } });
    if (path.endsWith("/auth/login")) { state.loggedIn = true; return route.fulfill({ json: { access_token: token } }); }
    if (path.endsWith("/auth/sessions")) return route.fulfill({ json: state.sessions });
    if (path.endsWith("/auth/session")) {
      const session = makeSession(`new-${++state.creates}`);
      state.sessions.push(session);
      return route.fulfill({ json: session });
    }
    if (route.request().method() === "DELETE") {
      state.sessions = state.sessions.filter((session) => !path.endsWith(`/${session.session_id}`));
      return route.fulfill({ status: 204 });
    }
    if (path.endsWith("/chatbot/messages")) {
      const id = route.request().headers().authorization.replace("Bearer ", "");
      state.history.push(id);
      return route.fulfill({ json: { messages: [{ role: "assistant", content: `历史记录 ${id}` }] } });
    }
    return route.fulfill({ status: 404, json: { detail: "Unexpected test request" } });
  });
  return state;
}

async function openMenu(page: Page) {
  await page.getByRole("button", { name: "账户菜单：demo@example.com" }).click();
}

async function installStream(page: Page) {
  await page.addInitScript(() => {
    const originalFetch = window.fetch;
    const state = window as typeof window & { finishStream?: () => void; streamAborted?: boolean };
    window.fetch = async (input, init) => {
      if (String(input).endsWith("/chatbot/chat/stream")) {
        return new Response(new ReadableStream({
          start(controller) {
            const encoder = new TextEncoder();
            controller.enqueue(encoder.encode('data: {"type":"answer_delta","content":"正在回答"}\n\n'));
            state.finishStream = () => {
              controller.enqueue(encoder.encode('data: {"type":"answer_delta","content":"，完成"}\n\ndata: {"type":"done"}\n\n'));
              controller.close();
            };
            init?.signal?.addEventListener("abort", () => {
              state.streamAborted = true;
              controller.error(new DOMException("Aborted", "AbortError"));
            });
          },
        }), { headers: { "Content-Type": "text/event-stream" } });
      }
      return originalFetch(input, init);
    };
  });
}

test("direct session URL wins over saved session and survives refresh", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/chat/alpha");
  await expect(page.getByText("历史记录 alpha", { exact: true })).toBeVisible();
  await expect(page.locator('.session-item[aria-current="page"]')).toContainText("alpha");
  expect(state.history).toEqual(["alpha"]);
  await page.reload();
  await expect(page.getByText("历史记录 alpha", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/chat\/alpha$/);
});

test("session navigation follows browser back and forward", async ({ page }) => {
  await setup(page);
  await page.goto("/chat/alpha");
  await expect(page.getByText("历史记录 alpha", { exact: true })).toBeVisible();
  await page.getByText("会话 beta", { exact: true }).click();
  await expect(page).toHaveURL(/\/chat\/beta$/);
  await expect(page.getByText("历史记录 beta", { exact: true })).toBeVisible();
  await page.goBack();
  await expect(page.getByText("历史记录 alpha", { exact: true })).toBeVisible();
  await page.goForward();
  await expect(page.getByText("历史记录 beta", { exact: true })).toBeVisible();
});

test("login resumes protected deep link and logout protects browser history", async ({ page }) => {
  await setup(page, false);
  await page.goto("/chat/alpha");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("邮箱", { exact: true }).fill("demo@example.com");
  await page.getByLabel("密码", { exact: true }).fill("Test-password-1!");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/\/chat\/alpha$/);
  await expect(page.getByText("历史记录 alpha", { exact: true })).toBeVisible();
  await page.getByText("会话 beta", { exact: true }).click();
  await expect(page.getByText("历史记录 beta", { exact: true })).toBeVisible();
  await openMenu(page);
  await page.getByRole("menuitem", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goBack();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByLabel("邮箱", { exact: true })).toBeVisible();
  await expect(page.getByText("历史记录 alpha", { exact: true })).toHaveCount(0);
});

test("unavailable session and unknown page have explicit recovery", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/chat/not-owned");
  await expect(page.getByRole("heading", { name: "会话不可用" })).toBeVisible();
  expect(state.history).toEqual([]);
  await page.getByRole("link", { name: "返回聊天" }).click();
  await expect(page).toHaveURL(/\/chat\/beta$/);
  await page.goto("/unknown");
  await expect(page.getByRole("heading", { name: "页面不存在" })).toBeVisible();
});

test("settings remain modals and keep URL and draft unchanged", async ({ page }) => {
  await setup(page);
  await page.goto("/chat/alpha");
  await page.getByLabel("聊天输入").fill("未发送的草稿");
  for (const item of ["个性化", "设置", "账户"]) {
    await openMenu(page);
    await page.getByRole("menuitem", { name: item, exact: true }).click();
    await expect(page.getByRole("dialog", { name: "设置", exact: true })).toBeVisible();
    await expect(page).toHaveURL(/\/chat\/alpha$/);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByLabel("聊天输入")).toHaveValue("未发送的草稿");
  }
  await openMenu(page);
  await page.getByRole("menuitem", { name: "设置", exact: true }).click();
  await page.getByRole("navigation", { name: "设置分类" }).getByRole("button", { name: "外观", exact: true }).click();
  await expect(page.getByRole("heading", { name: "背景样式" })).toBeVisible();
  await expect(page).toHaveURL(/\/chat\/alpha$/);
});

test("opening settings preserves active answer stream", async ({ page }) => {
  await setup(page);
  await installStream(page);
  await page.goto("/chat/alpha");
  await page.getByLabel("聊天输入").fill("测试回答");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.getByText("正在回答", { exact: true })).toBeVisible();
  await openMenu(page);
  await page.getByRole("menuitem", { name: "设置", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.evaluate(() => (window as typeof window & { finishStream: () => void }).finishStream());
  await page.keyboard.press("Escape");
  await expect(page.getByText("正在回答，完成", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "停止生成" })).toHaveCount(0);
  await expect(page).toHaveURL(/\/chat\/alpha$/);
});

test("switching session aborts old stream and keeps new history", async ({ page }) => {
  await setup(page);
  await installStream(page);
  await page.goto("/chat/alpha");
  await page.getByLabel("聊天输入").fill("测试回答");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.getByText("正在回答", { exact: true })).toBeVisible();
  await page.getByText("会话 beta", { exact: true }).click();
  await expect(page.getByText("历史记录 beta", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => (window as typeof window & { streamAborted: boolean }).streamAborted)).toBe(true);
  await expect(page.getByText("正在回答", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "停止生成" })).toHaveCount(0);
});

test("delayed history cannot replace newly selected session", async ({ page }) => {
  await setup(page);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/chatbot/messages", async (route) => {
    if (route.request().headers().authorization !== "Bearer alpha") return route.fallback();
    await pending;
    await route.fulfill({ json: { messages: [{ role: "assistant", content: "过期的 alpha 历史" }] } });
  });
  await page.goto("/chat/alpha");
  await page.getByText("会话 beta", { exact: true }).click();
  await expect(page.getByText("历史记录 beta", { exact: true })).toBeVisible();
  const lateResponse = page.waitForResponse((response) => response.url().endsWith("/chatbot/messages") && response.request().headers().authorization === "Bearer alpha");
  release();
  await lateResponse;
  await expect(page.getByText("历史记录 beta", { exact: true })).toBeVisible();
  await expect(page.getByText("过期的 alpha 历史", { exact: true })).toHaveCount(0);
});

test("new and deleted sessions update the URL", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/chat/alpha");
  await page.getByRole("button", { name: "新建对话" }).click();
  await expect(page).toHaveURL(/\/chat\/new-1$/);
  await expect(page.getByText("历史记录 new-1", { exact: true })).toBeVisible();
  expect(state.creates).toBe(1);
  page.on("dialog", (dialog) => dialog.accept());
  await page.locator('.session-item[aria-current="page"]').getByRole("button", { name: "删除", exact: true }).click();
  await expect(page).not.toHaveURL(/\/chat\/new-1$/);
  await expect(page.getByText("历史记录 new-1", { exact: true })).toHaveCount(0);
});

test("empty account initializes exactly one session", async ({ page }) => {
  const state = await setup(page, true, true);
  await page.goto("/");
  await expect(page).toHaveURL(/\/chat\/new-1$/);
  await expect(page.getByText("历史记录 new-1", { exact: true })).toBeVisible();
  expect(state.creates).toBe(1);
});

test("mobile navigation closes sidebar and profile stays inside viewport", async ({ page }) => {
  await setup(page);
  await page.setViewportSize({ width: 320, height: 568 });
  await page.goto("/chat/alpha");
  await page.getByRole("button", { name: "切换侧边栏" }).click();
  await page.getByText("会话 beta", { exact: true }).click();
  await expect(page).toHaveURL(/\/chat\/beta$/);
  await expect(page.getByRole("complementary", { name: "会话侧边栏" })).toBeHidden();
  await page.getByRole("button", { name: "切换侧边栏" }).click();
  await openMenu(page);
  const bounds = await page.getByRole("menu").boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(320);
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(568);
});

test("logout is available only in the profile menu, including load failure", async ({ page }) => {
  await setup(page);
  await page.goto("/chat/alpha");
  await expect(page.getByLabel("聊天输入")).toBeVisible();
  await expect(page.getByRole("button", { name: "退出登录", exact: true })).toHaveCount(0);
  await openMenu(page);
  await expect(page.getByRole("menuitem", { name: "退出登录" })).toHaveCount(1);
  await page.keyboard.press("Escape");
  await page.route("**/auth/sessions", (route) => route.fulfill({ status: 503, json: { detail: "Temporarily unavailable" } }));
  await page.reload();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.getByRole("button", { name: "退出登录", exact: true })).toHaveCount(0);
  await openMenu(page);
  await page.getByRole("menuitem", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
});

test("background image scales once to fill chat on desktop and mobile", async ({ page }) => {
  const state = await setup(page);
  state.appearance.background = { preset: "custom", imageDataUrl: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6iT8AAAAASUVORK5CYII=", brightness: 1, blur: 0, opacity: 1 };
  await page.goto("/chat/alpha");
  await expect(page.getByLabel("聊天输入")).toBeVisible();
  for (const viewport of [{ width: 1440, height: 900 }, { width: 320, height: 568 }]) {
    await page.setViewportSize(viewport);
    const background = page.locator(".chat-backdrop-image");
    await expect(background).toHaveCount(1);
    await expect(background).toHaveCSS("background-size", "cover");
    await expect(background).toHaveCSS("background-repeat", "no-repeat");
    await expect(background).toHaveCSS("background-position", "50% 50%");
    expect(await background.boundingBox()).toEqual(await page.locator(".main-area").boundingBox());
  }
});

test("account profile and avatar save to server and survive reload", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/chat/alpha");
  await openMenu(page);
  await page.getByRole("menuitem", { name: "账户", exact: true }).click();
  await page.getByLabel("显示名称").fill("Alice");
  await page.getByRole("button", { name: "保存账户资料" }).click();
  await expect(page.getByText("已保存到你的账户。", { exact: true })).toBeVisible();
  expect(state.profile.display_name).toBe("Alice");
  await page.getByRole("dialog").locator('input[type="file"]').setInputFiles({ name: "bad.txt", mimeType: "text/plain", buffer: Buffer.from("bad") });
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText("JPG");
  await page.getByRole("dialog").locator('input[type="file"]').setInputFiles({ name: "avatar.png", mimeType: "image/png", buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6iT8AAAAASUVORK5CYII=", "base64") });
  await expect(page.getByRole("dialog").getByAltText("当前头像")).toBeVisible();
  await page.keyboard.press("Escape");
  await page.reload();
  await expect(page.locator(".profile-trigger")).toContainText("Alice");
  await expect(page.locator(".profile-trigger img")).toBeVisible();
  await openMenu(page);
  await page.getByRole("menuitem", { name: "账户", exact: true }).click();
  await page.getByRole("button", { name: "恢复默认", exact: true }).click();
  await expect(page.getByRole("dialog").getByAltText("当前头像")).toHaveCount(0);
});

test("appearance and personalization use durable account settings", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/chat/alpha");
  await openMenu(page);
  await page.getByRole("menuitem", { name: "设置", exact: true }).click();
  await page.getByRole("navigation", { name: "设置分类" }).getByRole("button", { name: "外观", exact: true }).click();
  await page.getByRole("combobox", { name: "主题", exact: true }).selectOption("dark");
  await page.getByRole("button", { name: "海蓝", exact: true }).click();
  await page.getByRole("button", { name: "保存外观设置" }).click();
  await expect(page.getByText("已保存到你的账户。", { exact: true })).toBeVisible();
  expect(state.appearance.theme).toBe("dark");
  expect(state.appearance.background.preset).toBe("ocean");
  await page.getByRole("navigation", { name: "设置分类" }).getByRole("button", { name: "个性化", exact: true }).click();
  await page.getByLabel("自定义指令").fill("回答时给出一个例子");
  await page.getByLabel("回答长度").selectOption("concise");
  await page.getByLabel("使用长期记忆", { exact: true }).uncheck();
  await page.getByRole("button", { name: "保存个性化", exact: true }).click();
  await expect(page.getByText("已保存到你的账户。", { exact: true })).toBeVisible();
  expect(state.personalization.memory_enabled).toBe(false);
  await page.keyboard.press("Escape");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.locator(".chat-backdrop-image")).toHaveCSS("background-repeat", "no-repeat");
  await openMenu(page);
  await page.getByRole("menuitem", { name: "个性化", exact: true }).click();
  await expect(page.getByLabel("自定义指令")).toHaveValue("回答时给出一个例子");
  await expect(page.getByLabel("使用长期记忆", { exact: true })).not.toBeChecked();
});

test("failed server logout keeps login and offers a retry", async ({ page }) => {
  await setup(page);
  await page.goto("/chat/alpha");
  await page.route("**/auth/logout", (route) => route.fulfill({ status: 503 }));
  await openMenu(page);
  await page.getByRole("menuitem", { name: "退出登录" }).click();
  await expect(page.getByRole("alert")).toContainText("退出失败");
  await expect(page).toHaveURL(/\/chat\/alpha$/);
  await page.unroute("**/auth/logout");
  await openMenu(page);
  await page.getByRole("menuitem", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
});

test("expired user access refreshes once and preserves draft", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/chat/alpha");
  await page.getByLabel("聊天输入").fill("保持草稿");
  const initialRefreshes = state.refreshes;
  let calls = 0;
  await page.route("**/users/me/settings", (route) => {
    if (route.request().method() === "PATCH" && ++calls === 1) return route.fulfill({ status: 401 });
    return route.fallback();
  });
  await page.getByRole("button", { name: "切换到深色主题" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(state.refreshes).toBe(initialRefreshes + 1);
  await expect(page.getByLabel("聊天输入")).toHaveValue("保持草稿");
});

test("personal knowledge panel creates, selects and uploads a private document", async ({ page }) => {
  await setup(page);
  await page.goto("/chat/alpha");
  await page.getByRole("button", { name: "我的知识库" }).click();
  await expect(page.getByRole("heading", { name: "我的知识库" })).toBeVisible();
  await page.getByPlaceholder("新私有空间名称").fill("课程资料");
  await page.getByRole("button", { name: "创建" }).click();
  await expect(page.getByRole("button", { name: /课程资料 0 个文档/ })).toBeVisible();
  await page.getByText("我的笔记", { exact: true }).click();
  await page.getByLabel("用于聊天").first().check();
  await page.getByRole("button", { name: "上传" }).click();
  await page.locator('input[type="file"][accept*=".md"]').setInputFiles({
    name: "guide.md",
    mimeType: "text/markdown",
    buffer: Buffer.from("# Guide\nPrivate fact"),
  });
  await expect(page.getByText("guide.md", { exact: true })).toBeVisible();
  await expect(page.getByText("已可以检索")).toBeVisible();
});
