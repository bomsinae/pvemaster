import { expect, test } from "@playwright/test";

import { installApiMock, loginAs } from "./support/api-mock";

for (const viewport of [
  { name: "mobile", width: 390, height: 844 },
  { name: "tablet", width: 820, height: 1_180 },
  { name: "desktop", width: 1_440, height: 1_000 },
]) {
  test(`customer inventory reflows at ${viewport.name} width`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await installApiMock(page);
    await loginAs(page, "customer");
    const inventory = page.getByRole("heading", { name: "가상 머신" });
    await expect(inventory).toBeVisible();
    const bodyWidth = await page.locator("body").evaluate((element) => element.scrollWidth);
    expect(bodyWidth).toBeLessThanOrEqual(viewport.width);
    await expect(page.getByRole("button", { name: "시작", exact: true })).toBeVisible();
    await page.getByRole("button", { name: /customer-web-01.*상세 보기/ }).click();
    await expect(page.getByRole("heading", { name: "customer-web-01" })).toBeVisible();
    const detailBodyWidth = await page.locator("body").evaluate((element) => element.scrollWidth);
    expect(detailBodyWidth).toBeLessThanOrEqual(viewport.width);
    await expect(page.getByRole("img", { name: "CPU 성능 그래프" })).toBeVisible();
  });
}

test("slow API remains recoverable and exposes loading state", async ({ page }) => {
  await installApiMock(page, { delayCustomerListMs: 700 });
  await loginAs(page, "customer");
  await expect(page.locator(".customer-table-empty[role='status']")).toContainText("불러오는 중");
  await expect(page.getByText("customer-web-01", { exact: true })).toBeVisible();
});

test("API failure is announced and a refresh recovers the session", async ({ page }) => {
  await installApiMock(page, { failCustomerListOnce: true });
  await loginAs(page, "customer");
  await expect(page.locator(".error-banner[role='alert']")).toContainText("가상 머신 목록 조회 시간이 초과되었습니다.");

  await page.reload();
  await expect(page.getByText("customer-web-01", { exact: true })).toBeVisible();
});

test("reduced motion preference removes non-essential animation", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await installApiMock(page);
  await loginAs(page, "customer");
  const durations = await page.locator("main").evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      animation: Number.parseFloat(style.animationDuration) || 0,
      transition: Number.parseFloat(style.transitionDuration) || 0,
    };
  });
  expect(durations.animation).toBeLessThanOrEqual(0.001);
  expect(durations.transition).toBeLessThanOrEqual(0.001);
});
