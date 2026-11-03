#!/usr/bin/env bash
# Fetch the current Local vLLM installer and forward all arguments and environment.
set -euo pipefail

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
curl -fsSL https://raw.githubusercontent.com/hossbit/local-ai-server-vllm/main/install.sh -o "$tmp_dir/install.sh"
bash "$tmp_dir/install.sh" "$@"
