#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-codex-console-production}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-codex-console}"
WEBUI_URL="${WEBUI_URL:-http://127.0.0.1:16670/}"
NOVNC_URL="${NOVNC_URL:-http://127.0.0.1:6080/vnc.html}"

export COMPOSE_PROJECT_NAME

find_python() {
  if command -v python3 >/dev/null 2>&1; then
    printf '%s\n' "python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    printf '%s\n' "python"
    return 0
  fi
  return 1
}

resolve_realpath() {
  local input_path="$1"
  local python_cmd=""

  python_cmd="$(find_python || true)"
  if [[ -n "${python_cmd}" ]]; then
    "${python_cmd}" -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "${input_path}"
    return 0
  fi

  (
    cd -- "${input_path}"
    if command -v /bin/pwd >/dev/null 2>&1; then
      /bin/pwd -P
    else
      pwd -P
    fi
  )
}

to_compose_path() {
  local input_path="$1"
  local uname_out=""

  uname_out="$(uname -s 2>/dev/null || true)"
  case "${uname_out}" in
    CYGWIN*|MINGW*|MSYS*)
      if command -v cygpath >/dev/null 2>&1; then
        cygpath -m "${input_path}"
        return 0
      fi
      ;;
  esac

  printf '%s\n' "${input_path}"
}

SCRIPT_DIR="$(resolve_realpath "$(dirname -- "${BASH_SOURCE[0]}")")"
PROJECT_ROOT="$(resolve_realpath "${SCRIPT_DIR}/../..")"
COMPOSE_PROJECT_ROOT="$(to_compose_path "${PROJECT_ROOT}")"

reuse_existing_password() {
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "${CONTAINER_NAME}" 2>/dev/null \
    | awk -F= '$1 == "WEBUI_ACCESS_PASSWORD" {print substr($0, index($0, "=") + 1); exit}'
}

if [[ -z "${WEBUI_ACCESS_PASSWORD:-}" ]]; then
  existing_password="$(reuse_existing_password)"
  if [[ -n "${existing_password}" ]]; then
    export WEBUI_ACCESS_PASSWORD="${existing_password}"
    echo "[deploy] reused WEBUI_ACCESS_PASSWORD from ${CONTAINER_NAME}"
  else
    echo "[deploy] WEBUI_ACCESS_PASSWORD is required for first deployment" >&2
    echo "[deploy] example: WEBUI_ACCESS_PASSWORD='your-password' $0" >&2
    exit 1
  fi
fi

mkdir -p "${PROJECT_ROOT}/data" "${PROJECT_ROOT}/logs"

if docker inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
  echo "[deploy] removing existing container ${CONTAINER_NAME}"
  docker rm -f "${CONTAINER_NAME}" >/dev/null
fi

echo "[deploy] project root: ${PROJECT_ROOT}"
docker compose \
  --project-directory "${COMPOSE_PROJECT_ROOT}" \
  -f "${PROJECT_ROOT}/docker-compose.yml" \
  up -d --build "$@"

wait_for_url() {
  local label="$1"
  local url="$2"
  local code=""

  local attempts=0

  while [[ "${attempts}" -lt 40 ]]; do
    attempts=$((attempts + 1))
    code="$(curl -s -o /dev/null -w '%{http_code}' "${url}" || true)"
    if [[ "${code}" == "200" || "${code}" == "302" || "${code}" == "303" ]]; then
      echo "[deploy] ${label} ready: ${code} ${url}"
      return 0
    fi
    sleep 1
  done

  echo "[deploy] ${label} not ready after timeout: last_status=${code:-none} ${url}" >&2
  docker logs --tail 120 "${CONTAINER_NAME}" >&2 || true
  return 1
}

wait_for_url "webui" "${WEBUI_URL}"
wait_for_url "novnc" "${NOVNC_URL}"
wait_for_url "favicon" "http://127.0.0.1:16670/favicon.ico"

docker ps --filter "name=${CONTAINER_NAME}" --format '[deploy] {{.Names}} {{.Status}} {{.Ports}}'
