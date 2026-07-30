import assert from "node:assert/strict";
import test from "node:test";

import { fetchWithAccessToken, resetAccessTokenState } from "../lib/authenticated-fetch.ts";
import { registerStepUpHandler } from "../lib/step-up.ts";

const expired = () => Response.json(
  { error: { code: "INVALID_ACCESS_TOKEN", message: "expired" } },
  { status: 401 },
);

test("expired access token is refreshed and the request is retried once", async () => {
  resetAccessTokenState();
  const authorizations: string[] = [];
  let refreshes = 0;
  const fetcher: typeof fetch = async (input, init) => {
    if (String(input) === "/api/auth/session") {
      refreshes += 1;
      return Response.json({ access_token: "fresh-access" });
    }
    const authorization = new Headers(init?.headers).get("Authorization") ?? "";
    authorizations.push(authorization);
    return authorization === "Bearer fresh-access"
      ? Response.json({ ok: true })
      : expired();
  };

  const response = await fetchWithAccessToken("http://api.test/resource", "expired-access", {}, fetcher);

  assert.equal(response.status, 200);
  assert.equal(refreshes, 1);
  assert.deepEqual(authorizations, ["Bearer expired-access", "Bearer fresh-access"]);
});

test("parallel 401 responses share one rotating refresh request", async () => {
  resetAccessTokenState();
  let refreshes = 0;
  const fetcher: typeof fetch = async (input, init) => {
    if (String(input) === "/api/auth/session") {
      refreshes += 1;
      await new Promise((resolve) => setTimeout(resolve, 5));
      return Response.json({ access_token: "shared-access" });
    }
    return new Headers(init?.headers).get("Authorization") === "Bearer shared-access"
      ? Response.json({ ok: true })
      : expired();
  };

  const responses = await Promise.all([
    fetchWithAccessToken("http://api.test/one", "expired-access", {}, fetcher),
    fetchWithAccessToken("http://api.test/two", "expired-access", {}, fetcher),
    fetchWithAccessToken("http://api.test/three", "expired-access", {}, fetcher),
  ]);

  assert.equal(refreshes, 1);
  assert.deepEqual(responses.map((response) => response.status), [200, 200, 200]);
});

test("a failed refresh preserves the original API error response", async () => {
  resetAccessTokenState();
  let apiRequests = 0;
  const fetcher: typeof fetch = async (input) => {
    if (String(input) === "/api/auth/session") {
      return Response.json({ error: { code: "SESSION_EXPIRED" } }, { status: 401 });
    }
    apiRequests += 1;
    return expired();
  };

  const response = await fetchWithAccessToken("http://api.test/resource", "expired-access", {}, fetcher);

  assert.equal(response.status, 401);
  assert.equal(apiRequests, 1);
  assert.equal((await response.json()).error.code, "INVALID_ACCESS_TOKEN");
});

test("a protected action is retried once with an action-bound step-up token", async () => {
  resetAccessTokenState();
  const requestedActions: string[] = [];
  const unregister = registerStepUpHandler(async (action) => {
    requestedActions.push(action);
    return "step-up-proof";
  });
  const headers: string[] = [];
  const fetcher: typeof fetch = async (_input, init) => {
    const proof = new Headers(init?.headers).get("X-Step-Up-Token") ?? "";
    headers.push(proof);
    return proof === "step-up-proof"
      ? Response.json({ ok: true })
      : Response.json(
          {
            error: {
              code: "STEP_UP_REQUIRED",
              details: { action: "BACKUP_RESTORE" },
            },
          },
          { status: 403 },
        );
  };

  try {
    const response = await fetchWithAccessToken(
      "http://api.test/protected",
      "access",
      { method: "POST" },
      fetcher,
    );
    assert.equal(response.status, 200);
    assert.deepEqual(requestedActions, ["BACKUP_RESTORE"]);
    assert.deepEqual(headers, ["", "step-up-proof"]);
  } finally {
    unregister();
  }
});
