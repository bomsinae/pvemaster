import assert from "node:assert/strict";
import test from "node:test";

import { validateSshPublicKeys } from "../lib/ssh-public-key.ts";

test("plain text is rejected before a VM provisioning request is sent", () => {
  const result = validateSshPublicKeys("dddd");

  assert.match(result.error ?? "", /SSH 공개키 형식/);
  assert.deepEqual(result.keys, ["dddd"]);
});

test("an empty SSH key explains that password login is not supported", () => {
  const result = validateSshPublicKeys("   \n");

  assert.match(result.error ?? "", /비밀번호 로그인은 현재 지원하지 않습니다/);
  assert.deepEqual(result.keys, []);
});

test("a supported public key is normalized and accepted", () => {
  const publicKey = "ssh-ed25519 AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= test@example.invalid";
  const result = validateSshPublicKeys(`  ${publicKey}  \n`);

  assert.equal(result.error, null);
  assert.deepEqual(result.keys, [publicKey]);
});
