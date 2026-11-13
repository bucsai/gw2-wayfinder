#!/usr/bin/env bash
# PreToolUse hook: before Claude runs a command containing `git push`, run the same checks as CI.
# Exit 2 blocks the command and shows the failing output to Claude.

grep -q 'git push' || exit 0

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
for check in "ruff check" "ruff format --check" "pyright" "pytest -q"; do
  if ! output=$(uv run $check 2>&1); then
    echo "Push blocked: 'uv run $check' failed." >&2
    echo "$output" | tail -40 >&2
    exit 2
  fi
done
