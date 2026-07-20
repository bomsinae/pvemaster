const SUPPORTED_KEY_TYPES = new Set([
  "ssh-ed25519",
  "ssh-rsa",
  "ecdsa-sha2-nistp256",
]);

export type SshPublicKeyValidation = {
  keys: string[];
  error: string | null;
};

export function validateSshPublicKeys(value: string): SshPublicKeyValidation {
  const keys = value
    .split("\n")
    .map((key) => key.trim())
    .filter(Boolean);

  if (keys.length === 0) {
    return {
      keys,
      error: "SSH 공개키를 1개 이상 입력하세요. 비밀번호 로그인은 현재 지원하지 않습니다.",
    };
  }
  if (keys.length > 8) {
    return { keys, error: "SSH 공개키는 최대 8개까지 입력할 수 있습니다." };
  }

  for (const key of keys) {
    if (key.length > 4096) {
      return { keys, error: "SSH 공개키 한 줄은 4,096자를 넘을 수 없습니다." };
    }

    const parts = key.split(/\s+/);
    if (parts.length < 2 || !SUPPORTED_KEY_TYPES.has(parts[0])) {
      return {
        keys,
        error: "SSH 공개키 형식이 아닙니다. ~/.ssh/id_ed25519.pub의 'ssh-ed25519 AAAA…' 전체 줄을 입력하세요.",
      };
    }

    const encoded = parts[1];
    if (
      encoded.length % 4 !== 0 ||
      !/^[A-Za-z0-9+/]+={0,2}$/.test(encoded)
    ) {
      return { keys, error: "SSH 공개키의 Base64 데이터가 올바르지 않습니다." };
    }

    try {
      const decoded = atob(encoded);
      if (decoded.length < 16 || decoded.length > 2048) {
        return { keys, error: "SSH 공개키의 길이가 올바르지 않습니다." };
      }
    } catch {
      return { keys, error: "SSH 공개키의 Base64 데이터가 올바르지 않습니다." };
    }
  }

  return { keys, error: null };
}
