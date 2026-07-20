export type GeneratedSshKeyPair = {
  publicKey: string;
  privateKeyPem: string;
  fingerprint: string;
  filename: string;
};

function concatBytes(...chunks: Uint8Array[]): Uint8Array {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0);
  const result = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.length;
  }
  return result;
}

function uint32(value: number): Uint8Array {
  return new Uint8Array([
    (value >>> 24) & 0xff,
    (value >>> 16) & 0xff,
    (value >>> 8) & 0xff,
    value & 0xff,
  ]);
}

function sshField(value: Uint8Array): Uint8Array {
  return concatBytes(uint32(value.length), value);
}

function sshMpint(value: Uint8Array): Uint8Array {
  const normalized = value[0] & 0x80 ? concatBytes(new Uint8Array([0]), value) : value;
  return sshField(normalized);
}

function decodeBase64Url(value: string): Uint8Array {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/").padEnd(
    Math.ceil(value.length / 4) * 4,
    "=",
  );
  return Uint8Array.from(atob(base64), (character) => character.charCodeAt(0));
}

function encodeBase64(value: Uint8Array): string {
  let binary = "";
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function pem(label: string, value: ArrayBuffer): string {
  const encoded = encodeBase64(new Uint8Array(value));
  const lines = encoded.match(/.{1,64}/g) ?? [];
  return `-----BEGIN ${label}-----\n${lines.join("\n")}\n-----END ${label}-----\n`;
}

export function sshPrivateKeyFilename(name: string): string {
  const normalized = name.trim().replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  return `${normalized || "pvemaster-key"}.pem`;
}

export async function generateSshRsaKeyPair(
  name: string,
  webCrypto: Crypto = globalThis.crypto,
): Promise<GeneratedSshKeyPair> {
  if (!webCrypto?.subtle) {
    throw new Error("이 브라우저에서는 보안 키 생성을 사용할 수 없습니다. HTTPS로 접속하거나 기존 공개키를 입력하세요.");
  }

  const keyPair = await webCrypto.subtle.generateKey(
    {
      name: "RSASSA-PKCS1-v1_5",
      modulusLength: 3072,
      publicExponent: new Uint8Array([1, 0, 1]),
      hash: "SHA-256",
    },
    true,
    ["sign", "verify"],
  );
  const [publicJwk, privateKey] = await Promise.all([
    webCrypto.subtle.exportKey("jwk", keyPair.publicKey),
    webCrypto.subtle.exportKey("pkcs8", keyPair.privateKey),
  ]);
  if (!publicJwk.e || !publicJwk.n) throw new Error("생성된 SSH 공개키를 변환하지 못했습니다.");

  const keyType = new TextEncoder().encode("ssh-rsa");
  const sshBlob = concatBytes(
    sshField(keyType),
    sshMpint(decodeBase64Url(publicJwk.e)),
    sshMpint(decodeBase64Url(publicJwk.n)),
  );
  const fingerprintInput = sshBlob.slice().buffer as ArrayBuffer;
  const fingerprintBytes = await webCrypto.subtle.digest("SHA-256", fingerprintInput);
  const fingerprint = encodeBase64(new Uint8Array(fingerprintBytes)).replace(/=+$/, "");
  const comment = name.trim().replace(/\s+/g, "-") || "pvemaster";

  return {
    publicKey: `ssh-rsa ${encodeBase64(sshBlob)} ${comment}`,
    privateKeyPem: pem("PRIVATE KEY", privateKey),
    fingerprint: `SHA256:${fingerprint}`,
    filename: sshPrivateKeyFilename(name),
  };
}
