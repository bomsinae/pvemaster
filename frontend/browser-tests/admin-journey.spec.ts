import { expect, test } from "@playwright/test";

import { ids, installApiMock, loginAs } from "./support/api-mock";

test("SUPER_ADMIN registers, tests, imports, and assigns cluster inventory", async ({ page }) => {
  const state = await installApiMock(page, { initialClusters: false });
  state.imported = false;
  state.assigned = false;

  await loginAs(page, "admin");
  await expect(page.getByRole("heading", { name: "운영 개요" })).toBeVisible();

  await page.getByRole("button", { name: "클러스터", exact: true }).click();
  await page.getByRole("button", { name: "클러스터 등록" }).click();

  const drawer = page.getByRole("dialog", { name: "관리 작업" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByLabel("관리 작업 닫기")).toBeFocused();
  await drawer.getByLabel("표시 이름").fill("staging-pve");
  await drawer.getByLabel("API endpoint").fill("https://pve.example.test:8006");
  await drawer.getByLabel("Token identifier").fill("svc@pve!portal");
  await drawer.getByLabel("Token secret").fill("write-only-browser-secret");
  await drawer.getByRole("button", { name: "검증 후 등록" }).click();

  await expect(page.getByText("클러스터를 등록하고 최소 권한 연결 시험을 완료했습니다.")).toBeVisible();
  await expect(page.getByText("staging-pve", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "연결 시험" }).click();
  await expect(page.getByText(/연결 확인 완료 · PVE 9\.0/)).toBeVisible();

  await page.getByRole("button", { name: "VM/CT 가져오기" }).click();
  await expect(page.getByText(/VM\/CT 1개 확인 · 1개 가져옴/)).toBeVisible();

  await page.getByRole("button", { name: "사용자와 조직", exact: true }).click();
  await page.getByRole("button", { name: /리소스 할당/ }).click();
  await expect(page.getByText("customer-web-01", { exact: true })).toBeVisible();
  await page.getByRole("combobox", { name: "할당 대상 조직" }).click();
  await page.getByRole("option", { name: /Acme Korea/ }).click();
  await page.getByRole("button", { name: "조직에 할당" }).click();
  await expect(page.getByText("워크로드를 조직에 할당했습니다.")).toBeVisible();

  expect(state.requests).toContainEqual({
    method: "POST",
    path: `/api/v1/admin/clusters/${ids.cluster}/workloads/import`,
  });
  expect(state.requests).toContainEqual({
    method: "POST",
    path: `/api/v1/admin/workloads/${ids.workload}/assign`,
  });
});

test("admin drawer traps focus, closes with Escape, and restores trigger focus", async ({ page }) => {
  await installApiMock(page);
  await loginAs(page, "admin");
  await page.getByRole("button", { name: "클러스터", exact: true }).click();

  const trigger = page.getByRole("button", { name: "클러스터 등록" });
  await trigger.focus();
  await trigger.press("Enter");
  const drawer = page.getByRole("dialog", { name: "관리 작업" });
  await expect(drawer.getByLabel("관리 작업 닫기")).toBeFocused();

  await page.keyboard.press("Shift+Tab");
  await expect(drawer.getByRole("button", { name: "검증 후 등록" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("admin reviews inventory freshness, requests sync, and acknowledges drift", async ({ page }) => {
  const state = await installApiMock(page);
  await loginAs(page, "admin");
  await page.getByRole("button", { name: "라이트 테마로 전환" }).click();

  await page.getByRole("button", { name: "동기화와 재조정", exact: true }).click();
  await expect(page.getByRole("heading", { name: "클러스터 동기화 상태" })).toBeVisible();
  await expect(page.getByText("customer-web-01의 메모리 사양이 변경되었습니다.")).toBeVisible();
  await expect(page.getByText("FULL · #7")).toBeVisible();
  const navigationBox = await page.locator(".admin-nav").boundingBox();
  const contentHeadingBox = await page
    .getByRole("heading", { name: "클러스터 동기화 상태" })
    .boundingBox();
  expect(navigationBox).not.toBeNull();
  expect(contentHeadingBox).not.toBeNull();
  expect(contentHeadingBox!.x).toBeGreaterThan(navigationBox!.x + navigationBox!.width + 30);
  await expect(page.locator(".inventory-freshness-grid article")).toHaveCSS(
    "background-color",
    "rgb(255, 255, 255)",
  );

  await page.getByRole("button", { name: "전체 동기화" }).click();
  await expect(page.getByText(/인벤토리 동기화를 요청했습니다/)).toBeVisible();
  await expect(page.getByText("RUNNING", { exact: true })).toBeVisible();
  await expect(page.getByText("SUCCEEDED", { exact: true })).toBeVisible();

  await page.getByLabel(/담당자/).selectOption("admin-id");
  await page.getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.getByText("재조정 항목을 확인 처리했습니다.")).toBeVisible();
  await expect(page.getByText("ACKNOWLEDGED", { exact: true })).toBeVisible();
  await expect(page.getByText("담당자 Admin", { exact: true })).toBeVisible();

  expect(state.requests).toContainEqual({
    method: "POST",
    path: `/api/v1/admin/clusters/${ids.cluster}/sync`,
  });
  expect(state.requests).toContainEqual({
    method: "POST",
    path: `/api/v1/admin/inventory/reconciliation/findings/${ids.finding}/acknowledge`,
  });
});

test("admin triages a failed operation from the persisted recovery center", async ({ page }) => {
  const state = await installApiMock(page);
  await loginAs(page, "admin");

  await page.getByRole("button", { name: "Operation 센터", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Operation 센터" })).toBeVisible();
  await expect(page.getByText("customer-web-01", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("CLUSTER_UNREACHABLE", { exact: false })).toBeVisible();
  await expect(page.getByText("STATUS_CHANGED", { exact: true })).toBeVisible();
  await expect(page.getByText(/UPID:/)).toHaveCount(0);

  await page.getByRole("button", { name: "확인", exact: true }).click();
  await expect.poll(() =>
    state.requests.some((request) =>
      request.method === "POST"
      && request.path === `/api/v1/admin/operations/${ids.operation}/acknowledge`
    ),
  ).toBe(true);
});
