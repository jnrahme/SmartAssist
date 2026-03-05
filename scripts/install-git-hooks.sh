#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK_DIR="$REPO_ROOT/.git/hooks"
PRE_PUSH_HOOK="$HOOK_DIR/pre-push"

if [[ ! -d "$HOOK_DIR" ]]; then
  echo "Not a git repository: $REPO_ROOT"
  exit 1
fi

mkdir -p "$HOOK_DIR"

cat > "$PRE_PUSH_HOOK" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
bash "$REPO_ROOT/scripts/pre-push-main.sh"
HOOK

chmod +x "$PRE_PUSH_HOOK"
chmod +x "$REPO_ROOT/scripts/pre-push-main.sh"

echo "Installed pre-push hook: $PRE_PUSH_HOOK"
echo "It runs: scripts/pre-push-main.sh"
