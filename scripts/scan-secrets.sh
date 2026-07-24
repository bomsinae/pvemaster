#!/usr/bin/env sh
set -eu

pattern='-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}'

matches="$(
  git grep -I -l -E -- "$pattern" -- . \
    ':(exclude)scripts/scan-secrets.sh' \
    ':(exclude).env.example' \
    ':(exclude)frontend/tests/ssh-keypair.e2e.test.ts' \
    || true
)"

if [ -n "$matches" ]; then
  echo "Potential committed secrets detected in:"
  echo "$matches"
  exit 1
fi

echo "No high-confidence committed secret patterns detected."
