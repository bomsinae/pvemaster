import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { installApiMock, loginAs } from "./support/api-mock";

async function expectNoSeriousAccessibilityViolations(page: Page) {
  await page.locator(".login-form, .enter-admin, .customer-inventory")
    .first()
    .evaluate(async (element) => {
      await Promise.all(element.getAnimations().map((animation) => animation.finished));
    })
    .catch(() => undefined);
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  const blocking = result.violations.filter((violation) =>
    violation.impact === "critical" || violation.impact === "serious"
  );
  expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
}

test("login and admin primary view pass automated WCAG checks", async ({ page }) => {
  await installApiMock(page);
  await page.goto("/");
  await page.locator(".login-form").evaluate(async (element) => {
    await Promise.all(element.getAnimations().map((animation) => animation.finished));
  });
  await expectNoSeriousAccessibilityViolations(page);
  await page.getByLabel("이메일").fill("admin@example.test");
  await page.getByLabel("비밀번호").fill("browser-test-password");
  await page.getByRole("button", { name: "로그인" }).click();
  await expect(page.getByRole("heading", { name: "운영 개요" })).toBeVisible();
  await expectNoSeriousAccessibilityViolations(page);
});

test("customer inventory and confirmation pass automated WCAG checks", async ({ page }) => {
  await installApiMock(page);
  await loginAs(page, "customer");
  await expect(page.getByRole("heading", { name: "가상 머신" })).toBeVisible();
  await expectNoSeriousAccessibilityViolations(page);
  await page.getByRole("button", { name: "시작", exact: true }).click();
  await expectNoSeriousAccessibilityViolations(page);
});
