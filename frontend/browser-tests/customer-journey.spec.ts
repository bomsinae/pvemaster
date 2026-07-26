import { expect, test } from "@playwright/test";

import { ids, installApiMock, loginAs } from "./support/api-mock";

test("customer sees only an assigned VM and completes a power operation", async ({ page }) => {
  await installApiMock(page);
  await loginAs(page, "customer");

  await expect(page.getByRole("heading", { name: "가상 머신" })).toBeVisible();
  await expect(page.getByText("customer-web-01", { exact: true })).toBeVisible();
  await expect(page.getByText("192.0.2.24", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "시작", exact: true }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "취소" })).toBeFocused();
  await dialog.getByRole("button", { name: "작업 요청" }).click();

  await expect(page.getByRole("status").filter({ hasText: "시작 · SUCCEEDED" })).toBeVisible({
    timeout: 7_500,
  });
  await expect(page.getByLabel("가상 머신 목록").getByText("실행 중", { exact: true })).toBeVisible();
});

test("customer opens a refresh-safe VM detail with sparse metrics and history", async ({ page }) => {
  await installApiMock(page);
  await loginAs(page, "customer");

  await page.getByRole("button", { name: /customer-web-01.*상세 보기/ }).click();
  await expect(page).toHaveURL(new RegExp(`/customer/vms/${ids.workload}$`));
  await expect(page.getByRole("heading", { name: "customer-web-01" })).toBeVisible();
  await expect(page.getByText("2일 1시간")).toBeVisible();
  await expect(page.getByText("일부 구간 누락")).toBeVisible();
  await expect(page.getByRole("img", { name: "CPU 성능 그래프" })).toBeVisible();
  await expect(page.getByText("수집된 값 없음")).toHaveCount(0);
  await expect(page.getByText("전원 상태가 실행 중으로 변경되었습니다.")).toBeVisible();
  await expect(page.getByText("호스트 보안 업데이트")).toBeVisible();

  await page.getByRole("button", { name: "30일" }).click();
  await expect(page.getByRole("button", { name: "30일" })).toHaveAttribute("aria-pressed", "true");
  await page.reload();
  await expect(page.getByRole("heading", { name: "customer-web-01" })).toBeVisible();
  await page.getByRole("button", { name: "VM 목록으로" }).click();
  await expect(page).toHaveURL("/");
  await expect(page.getByRole("heading", { name: "가상 머신" })).toBeVisible();
});

test("customer isolation hides foreign and former ownership and rejects inactive organizations", async ({ page }) => {
  await installApiMock(page);
  await loginAs(page, "customer");
  await expect(page.getByText("customer-web-01", { exact: true })).toBeVisible();

  const results = await page.evaluate(async (resourceIds) => {
    return Promise.all(resourceIds.map(async (id) => {
      const response = await fetch(`http://api.pvemaster.test/api/v1/customer/vms/${id}`, {
        headers: { Authorization: "Bearer customer-access" },
      });
      return { id, status: response.status, body: await response.json() };
    }));
  }, [ids.foreignWorkload, ids.formerWorkload, ids.inactiveWorkload]);

  expect(results.map((item) => item.status)).toEqual([404, 404, 403]);
  expect(results[0].body.error.code).toBe("RESOURCE_NOT_FOUND");
  expect(results[1].body.error.code).toBe("RESOURCE_NOT_FOUND");
  expect(results[2].body.error.code).toBe("ORGANIZATION_INACTIVE");
  await expect(page.getByText(ids.foreignWorkload)).toHaveCount(0);
  await expect(page.getByText(ids.formerWorkload)).toHaveCount(0);
});

test("customer action dialog traps focus and restores the table action", async ({ page }) => {
  await installApiMock(page);
  await loginAs(page, "customer");
  const trigger = page.getByRole("button", { name: "시작", exact: true });
  await trigger.click();

  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByRole("button", { name: "취소" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "작업 요청" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
});

test("customer sees stale inventory warning and cannot request power operations", async ({ page }) => {
  await installApiMock(page, { staleCustomerInventory: true });
  await loginAs(page, "customer");

  await expect(page.getByRole("status").filter({
    hasText: "일부 VM 정보가 오래되었습니다.",
  })).toBeVisible();
  await expect(page.getByText("확인 필요", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "시작", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "콘솔", exact: true })).toBeDisabled();
});

test("customer updates optional email notifications while required policy stays locked", async ({ page }) => {
  await installApiMock(page);
  await loginAs(page, "customer");

  await page.getByRole("button", { name: "알림 설정" }).click();
  const dialog = page.getByRole("dialog", { name: "이메일 알림 설정" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("c*******@example.test")).toBeVisible();
  const vmDown = dialog.getByRole("checkbox", {
    name: "Acme Korea VM 장기 비가용 이메일 알림",
  });
  const maintenance = dialog.getByRole("checkbox", {
    name: "Acme Korea 예정된 유지보수 이메일 알림",
  });
  await expect(vmDown).toBeChecked();
  await vmDown.click();
  await expect(vmDown).not.toBeChecked();
  await expect(dialog.getByText("알림 설정을 저장했습니다.")).toBeVisible();
  await expect(maintenance).toBeChecked();
  await expect(maintenance).toBeDisabled();
  await expect(dialog.getByText("조직 필수 알림")).toBeVisible();
});

test("customer reviews sessions and changes password without ending the current session", async ({ page }) => {
  await installApiMock(page);
  await loginAs(page, "customer");

  await page.getByRole("button", { name: "보안 설정" }).click();
  const security = page.getByRole("dialog", { name: "보안 설정" });
  await expect(security.getByText("Current browser · 현재")).toBeVisible();
  await expect(security.getByText("Tablet", { exact: true })).toBeVisible();
  await expect(security.getByRole("heading", { name: "최근 로그인" })).toBeVisible();
  await security.getByRole("button", { name: "다른 세션 모두 종료" }).click();
  await expect(security.getByText("Tablet", { exact: true })).toHaveCount(0);
  await security.getByRole("button", { name: "보안 설정 닫기" }).click();

  await page.getByRole("button", { name: "비밀번호 변경" }).click();
  const password = page.getByRole("dialog", { name: "비밀번호 변경" });
  await password.getByLabel("현재 비밀번호").fill("browser-test-password");
  await password.locator('input[name="new_password"]').fill("new-browser-password");
  await password.getByLabel("새 비밀번호 확인").fill("new-browser-password");
  await password.getByRole("checkbox", { name: "현재 기기를 포함한 모든 로그인 세션 종료" }).uncheck();
  await password.getByRole("button", { name: "비밀번호 변경" }).click();
  await expect(page.getByRole("status").filter({
    hasText: "현재 세션은 계속 사용할 수 있습니다.",
  })).toBeVisible();
  await expect(page.getByRole("heading", { name: "가상 머신" })).toBeVisible();
});

test("customer previews and submits a controlled metadata change", async ({ page }) => {
  await installApiMock(page);
  await loginAs(page, "customer");

  await page.getByRole("button", { name: "변경 요청" }).click();
  const dialog = page.getByRole("dialog", { name: "customer-web-01 변경 요청" });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("요청 유형").selectOption("METADATA_CHANGE");
  await dialog.getByRole("textbox", { name: "Hostname", exact: true }).fill("customer-web-02");
  await dialog.getByLabel("요청 사유").fill("서비스 이름 정리");
  await dialog.getByRole("button", { name: "영향 미리보기" }).click();
  await expect(dialog.getByText("VM identity 정보가 변경됩니다.")).toBeVisible();
  await dialog.getByRole("button", { name: "승인 요청 제출" }).click();
  await expect(dialog.getByText("승인 요청을 접수했습니다.")).toBeVisible();
  await expect(dialog.getByText("PENDING_APPROVAL")).toBeVisible();
  await dialog.getByRole("button", { name: "요청 취소" }).click();
  await expect(dialog.getByText("승인 전 요청을 취소했습니다.")).toBeVisible();
});
