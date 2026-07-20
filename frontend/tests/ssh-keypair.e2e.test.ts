import assert from "node:assert/strict";
import test from "node:test";

import { validateSshPublicKeys } from "../lib/ssh-public-key.ts";
import { generateSshRsaKeyPair, sshPrivateKeyFilename } from "../lib/ssh-keypair.ts";

test("a browser-generated RSA key is accepted by the provisioning key validator", async () => {
  const keyPair = await generateSshRsaKeyPair("customer-web");

  assert.equal(validateSshPublicKeys(keyPair.publicKey).error, null);
  assert.match(keyPair.publicKey, /^ssh-rsa [A-Za-z0-9+/]+=* customer-web$/);
  assert.match(keyPair.privateKeyPem, /^-----BEGIN PRIVATE KEY-----\n/);
  assert.match(keyPair.privateKeyPem, /\n-----END PRIVATE KEY-----\n$/);
  assert.match(keyPair.fingerprint, /^SHA256:[A-Za-z0-9+/]+$/);
  assert.equal(keyPair.filename, "customer-web.pem");
});

test("private key download filenames are normalized", () => {
  assert.equal(sshPrivateKeyFilename(" customer web / prod "), "customer-web-prod.pem");
  assert.equal(sshPrivateKeyFilename("***"), "pvemaster-key.pem");
});
