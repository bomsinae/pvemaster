import assert from "node:assert/strict";
import test from "node:test";

import nextConfig from "../next.config.ts";

test("frontend routes apply the release security header baseline", async () => {
  assert.equal(typeof nextConfig.headers, "function");

  const routes = await nextConfig.headers!();
  const baseline = Object.fromEntries(
    routes
      .find((route) => route.source === "/(.*)")!
      .headers.map((header) => [header.key, header.value]),
  );

  assert.equal(baseline["X-Content-Type-Options"], "nosniff");
  assert.equal(baseline["X-Frame-Options"], "DENY");
  assert.equal(baseline["Referrer-Policy"], "no-referrer");
  assert.equal(baseline["Cross-Origin-Opener-Policy"], "same-origin");
  assert.match(baseline["Permissions-Policy"], /microphone=\(\)/);
});
