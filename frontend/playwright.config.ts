import { defineConfig, devices } from "@playwright/test";

const ci = Boolean(process.env.CI);

export default defineConfig({
  testDir: "./browser-tests",
  fullyParallel: true,
  forbidOnly: ci,
  retries: ci ? 2 : 0,
  workers: ci ? 2 : undefined,
  timeout: 30_000,
  expect: { timeout: 7_500 },
  outputDir: "output/playwright/test-results",
  reporter: ci
    ? [
        ["line"],
        ["html", { outputFolder: "output/playwright/report", open: "never" }],
        ["junit", { outputFile: "output/playwright/results.xml" }],
      ]
    : [["list"], ["html", { outputFolder: "output/playwright/report", open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:3100",
    locale: "ko-KR",
    timezoneId: "Asia/Seoul",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- --hostname 127.0.0.1 --port 3100",
    url: "http://127.0.0.1:3100",
    reuseExistingServer: !ci,
    timeout: 120_000,
    env: {
      NEXT_PUBLIC_API_URL: "http://api.pvemaster.test",
    },
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
});
