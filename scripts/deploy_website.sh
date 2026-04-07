#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: deploy_website.sh [--dry-run]

Required environment:
  SMARTASSIST_WEBSITE_HOST          SSH host or IP for the website server
  SMARTASSIST_WEBSITE_PATH          Remote directory that serves website files

Optional environment:
  SMARTASSIST_WEBSITE_USER          SSH user (default: root)
  SMARTASSIST_WEBSITE_PORT          SSH port (default: 22)
  SMARTASSIST_WEBSITE_SOURCE_DIR    Local website source dir (default: <repo>/website)
  SMARTASSIST_WEBSITE_SSH_KEY_PATH  Private key path for ssh/rsync
  SMARTASSIST_WEBSITE_SMOKE_URL     URL to check after deploy
EOF
}

dry_run=0
if [[ "${1:-}" == "--dry-run" ]]; then
  dry_run=1
elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
elif [[ $# -gt 0 ]]; then
  echo "Unknown argument: $1" >&2
  usage >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
source_dir="${SMARTASSIST_WEBSITE_SOURCE_DIR:-${repo_root}/website}"
host="${SMARTASSIST_WEBSITE_HOST:?SMARTASSIST_WEBSITE_HOST is required}"
remote_path="${SMARTASSIST_WEBSITE_PATH:?SMARTASSIST_WEBSITE_PATH is required}"
user="${SMARTASSIST_WEBSITE_USER:-root}"
port="${SMARTASSIST_WEBSITE_PORT:-22}"
smoke_url="${SMARTASSIST_WEBSITE_SMOKE_URL:-}"
key_path="${SMARTASSIST_WEBSITE_SSH_KEY_PATH:-}"

if [[ ! -d "${source_dir}" ]]; then
  echo "Website source directory not found: ${source_dir}" >&2
  exit 1
fi

if [[ ! -f "${source_dir}/index.html" ]]; then
  echo "Website source is missing index.html: ${source_dir}/index.html" >&2
  exit 1
fi

if [[ -n "${key_path}" && ! -f "${key_path}" ]]; then
  echo "SSH key not found: ${key_path}" >&2
  exit 1
fi

ssh_opts=(
  -p "${port}"
  -o BatchMode=yes
  -o StrictHostKeyChecking=yes
)

if [[ -n "${key_path}" ]]; then
  ssh_opts+=(-i "${key_path}")
fi

ssh_cmd=(ssh "${ssh_opts[@]}")
rsync_rsh=("ssh" "${ssh_opts[@]}")

echo "Deploying website from ${source_dir} to ${user}@${host}:${remote_path}"

"${ssh_cmd[@]}" "${user}@${host}" "mkdir -p '${remote_path}'"

rsync_args=(
  -az
  --delete
  --omit-dir-times
  --exclude
  ".DS_Store"
  -e
  "${rsync_rsh[*]}"
)

if [[ ${dry_run} -eq 1 ]]; then
  rsync_args+=(--dry-run --itemize-changes)
fi

rsync "${rsync_args[@]}" "${source_dir}/" "${user}@${host}:${remote_path}/"

if [[ ${dry_run} -eq 1 ]]; then
  echo "Dry run complete."
  exit 0
fi

if [[ -n "${smoke_url}" ]]; then
  echo "Smoke checking ${smoke_url}"
  curl --fail --silent --show-error --location --max-time 20 "${smoke_url}" >/dev/null
fi

echo "Website deploy complete."
