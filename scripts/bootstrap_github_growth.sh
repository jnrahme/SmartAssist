#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
REPO=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --repo)
      REPO="${2:-}"
      shift 2
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$REPO" ]]; then
  REPO="$(gh repo view --json nameWithOwner --jq '.nameWithOwner')"
fi

DESCRIPTION="AI memory that learns from developer feedback for Claude Code and other coding agents"
HOMEPAGE="https://smartassist-memory.com"

TOPICS=(
  "claude-code"
  "mcp"
  "developer-tools"
  "coding-agents"
  "rag"
  "ai-memory"
  "codex"
  "source-available"
)

LABELS=(
  "setup|1D76DB|Install, onboarding, or first-use friction"
  "feedback|A371F7|Product feedback and workflow observations"
  "community|0E8A16|Contributor interest or community operations"
  "needs-info|FBCA04|Waiting on follow-up before triage can continue"
  "stale-candidate|BFD4F2|Safe to include in manual stale triage"
  "stale|C5D0DB|Inactive triage item pending follow-up"
  "area: docs|0075CA|Documentation and written guidance"
  "area: website|C2E0C6|Website or landing-page changes"
  "area: tests|5319E7|Automated tests and QA coverage"
  "area: workflows|6F42C1|GitHub Actions and repo automation"
  "area: hooks|D93F0B|Claude hook lifecycle and hook tooling"
  "area: cli|0E8A16|CLI surfaces and local commands"
  "area: mcp|1D76DB|MCP server or tool behavior"
  "area: packaging|E99695|Packaging, release, or install paths"
)

run_cmd() {
  if [[ "$DRY_RUN" == true ]]; then
    printf '[dry-run]'
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
    return 0
  fi

  "$@"
}

printf 'Configuring GitHub growth surfaces for %s\n' "$REPO"

run_cmd gh repo edit "$REPO" --description "$DESCRIPTION"
run_cmd gh repo edit "$REPO" --homepage "$HOMEPAGE"
run_cmd gh repo edit "$REPO" --enable-discussions

existing_topics="$(gh api "repos/$REPO/topics" --jq '.names[]' 2>/dev/null || true)"
for topic in "${TOPICS[@]}"; do
  if grep -Fxq "$topic" <<< "$existing_topics"; then
    printf 'Topic already present: %s\n' "$topic"
  else
    run_cmd gh repo edit "$REPO" --add-topic "$topic"
  fi
done

for entry in "${LABELS[@]}"; do
  IFS='|' read -r name color description <<< "$entry"
  run_cmd gh label create "$name" --repo "$REPO" --color "$color" \
    --description "$description" --force
done

printf 'Done.\n'
