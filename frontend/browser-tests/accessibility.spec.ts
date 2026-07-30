import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { installApiMock, loginAs } from "./support/api-mock";

async function expectNoSeriousAccessibilityViolations(page: Page) {
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

test("customer VM detail exposes keyboard and chart semantics", async ({ page }) => {
  await installApiMock(page);
  await loginAs(page, "customer");
  await page.getByRole("button", { name: /customer-web-01.*상세 보기/ }).click();
  await expect(page.getByRole("heading", { name: "customer-web-01" })).toBeVisible();
  await page.getByRole("button", { name: "30일" }).focus();
  await expect(page.getByRole("button", { name: "30일" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "30일" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("navigation", { name: "성능 지표 조회 기간" })).toBeVisible();
  await expect(page.getByRole("img", { name: "CPU 성능 그래프" })).toBeVisible();
});
