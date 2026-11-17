#!/usr/bin/env bash
set -euo pipefail

SERVER_URL=""
CHANNEL=""
VERSION=""
TARGET=""
API_KEY="${ADMIN_API_KEY:-}"
COMPONENT_VERSION=""
CHIP_FAMILY=""
LABEL=""
MANIFEST_FILE=""
SIGNATURE_FILE=""

FILES=()

usage() {
  cat <<USAGE
Usage: $0 \
  --server-url <url> \
  --channel <stable|nightly> \
  --version <release-version> \
  --target <controller|display|headless> \
  --file <path> [--file <path> ...] \
  [--api-key <key>] \
  [--component-version <version>] \
  [--chip-family <family>] \
  [--label <label>] \
  [--manifest-file <path> --signature-file <path>]

--manifest-file and --signature-file are the signed release manifest written by scripts/sign_release.py
for exactly these files. The server rejects the upload without them unless it runs with
ALLOW_UNSIGNED_UPLOADS.
USAGE
  return
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --server-url) SERVER_URL="$2"; shift 2 ;;
    --channel) CHANNEL="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    --file) FILES+=("$2"); shift 2 ;;
    --api-key) API_KEY="$2"; shift 2 ;;
    --component-version) COMPONENT_VERSION="$2"; shift 2 ;;
    --chip-family) CHIP_FAMILY="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --manifest-file) MANIFEST_FILE="$2"; shift 2 ;;
    --signature-file) SIGNATURE_FILE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$SERVER_URL" || -z "$CHANNEL" || -z "$VERSION" || -z "$TARGET" ]]; then
  echo "Missing required arguments" >&2
  usage
  exit 1
fi

if [[ ${#FILES[@]} -eq 0 ]]; then
  echo "Provide at least one --file argument" >&2
  exit 1
fi

for file in "${FILES[@]}"; do
  if [[ ! -f "$file" ]]; then
    echo "Firmware file not found: $file" >&2
    exit 1
  fi
done

if [[ -n "$MANIFEST_FILE" || -n "$SIGNATURE_FILE" ]]; then
  if [[ ! -f "$MANIFEST_FILE" || ! -f "$SIGNATURE_FILE" ]]; then
    echo "--manifest-file and --signature-file must both point to existing files" >&2
    exit 1
  fi
fi

FORM_ARGS=(
  -F "channel=${CHANNEL}"
  -F "version=${VERSION}"
  -F "target=${TARGET}"
)

if [[ -n "$COMPONENT_VERSION" ]]; then
  FORM_ARGS+=( -F "component_version=${COMPONENT_VERSION}" )
fi
if [[ -n "$CHIP_FAMILY" ]]; then
  FORM_ARGS+=( -F "chip_family=${CHIP_FAMILY}" )
fi
if [[ -n "$LABEL" ]]; then
  FORM_ARGS+=( -F "label=${LABEL}" )
fi
if [[ -n "$MANIFEST_FILE" ]]; then
  # "<file" makes curl send the file content byte for byte; the signature is over these exact bytes.
  FORM_ARGS+=( -F "manifest=<${MANIFEST_FILE}" -F "signature=<${SIGNATURE_FILE}" )
fi

for file in "${FILES[@]}"; do
  FORM_ARGS+=( -F "files=@${file}" )
done

HEADER_ARGS=()
if [[ -n "$API_KEY" ]]; then
  HEADER_ARGS+=( -H "x-api-key: ${API_KEY}" )
fi

UPLOAD_URL="${SERVER_URL%/}/api/admin/releases/upload"

echo "Uploading release ${VERSION} (${CHANNEL}/${TARGET}) to ${UPLOAD_URL}"

curl --fail-with-body --show-error --silent \
  -X POST "${UPLOAD_URL}" \
  "${HEADER_ARGS[@]}" \
  "${FORM_ARGS[@]}"

echo
