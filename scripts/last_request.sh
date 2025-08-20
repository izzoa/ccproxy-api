#!/usr/bin/env bash
# Use a function - it won't visually expand
fdcat() {
  fd -t f "$@" -x sh -c 'printf "\n\033[1;34m=== %s ===\033[0m\n" "$1" && cat "$1"' _ {}
}
PATH_LOG="/tmp/ccproxy"
PATH_REQ="${PATH_LOG}/raw/"
# LAST_UUID=$(eza -la --sort=modified "${PATH_REQ}" | grep -E '[a-f0-9-]{36}' | tail -1 | sed -E 's/.*_([a-f0-9-]{36})_.*/\1/')
LAST_UUID=$(eza -la --sort=modified "${PATH_REQ}" | grep -E '[a-f0-9-]{36}' | tail -1 | sed -E 's/.*([a-f0-9-]{36})_.*/\1/')

printf "\n\033[1;34m=== Log ===\033[0m\n"
grep "${LAST_UUID}" "${PATH_LOG}/ccproxy.log" | jq .
printf "\n\033[1;34m=== Requests ===\033[0m\n"
bat "${PATH_REQ}"*"${LAST_UUID}"*.http
