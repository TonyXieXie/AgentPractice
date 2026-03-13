import { expect, test } from "@playwright/test";

const taskPrompt = "Write a hello world function in TypeScript.";
const sessionId = "session-smoke-001";
const runId = "run-smoke-001";
const controllerAgentId = "controller-agent-001";
const userAgentId = "user-agent-001";
const assistantAgentId = "assistant-agent-001";
const createdAt = "2026-03-13T12:00:00.000Z";

test("creates a run from the task form and hydrates the run view", async ({ page }) => {
  let submittedPayload: Record<string, unknown> | null = null;

  await page.route("**/runs", async (route) => {
    submittedPayload = (await route.request().postDataJSON()) as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        run_id: runId,
        session_id: sessionId,
        user_agent_id: userAgentId,
        assistant_agent_id: assistantAgentId,
        status: "accepted",
      }),
    });
  });

  await page.route(`**/sessions/${sessionId}/facts/shared**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        session_id: sessionId,
        next_after_seq: 2,
        shared_facts: [
          {
            fact_id: "fact-submit-001",
            session_id: sessionId,
            run_id: runId,
            fact_seq: 1,
            message_id: "message-submit-001",
            sender_id: "external:http",
            target_agent_id: controllerAgentId,
            target_profile_id: "controller",
            topic: "run.submit",
            fact_type: "message",
            payload_json: {
              content: taskPrompt,
              strategy: "react",
              controller_agent_id: controllerAgentId,
              user_agent_id: userAgentId,
              assistant_agent_id: assistantAgentId,
            },
            metadata_json: {},
            visibility: "public",
            level: "info",
            created_at: createdAt,
          },
          {
            fact_id: "fact-started-001",
            session_id: sessionId,
            run_id: runId,
            fact_seq: 2,
            message_id: "message-started-001",
            sender_id: controllerAgentId,
            target_agent_id: assistantAgentId,
            target_profile_id: "assistant",
            topic: "run.started",
            fact_type: "event",
            payload_json: {
              status: "running",
              strategy: "react",
              controller_agent_id: controllerAgentId,
              user_agent_id: userAgentId,
              assistant_agent_id: assistantAgentId,
            },
            metadata_json: {},
            visibility: "public",
            level: "info",
            created_at: createdAt,
          },
        ],
      }),
    });
  });

  await page.goto("/");

  await expect(page.getByText("Backend resolved. Ready to observe.")).toBeVisible();

  await page.getByPlaceholder("Describe the task for the assistant.").fill(taskPrompt);
  await page.getByRole("button", { name: "Create Run" }).click();

  await expect(page.locator(".task-strip-text")).toHaveText(taskPrompt);
  await expect(page.locator(".metric-grid").getByText(runId)).toBeVisible();
  await expect(page.locator(".status-grid").getByText(`session ${sessionId}`)).toBeVisible();
  await expect(page.locator(".status-grid").getByText("status running")).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop Run" })).toBeEnabled();

  expect(submittedPayload).toEqual({
    content: taskPrompt,
    strategy: "react",
    request_overrides: {},
  });
});
