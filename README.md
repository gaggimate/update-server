# update-server

Simple FastAPI service for hosting firmware releases and OTA manifests.

It supports:
- release channels (`stable`, `nightly`)
- multiple targets (`controller`, `display`, `headless`)
- manifest generation for ESP-style multi-part firmware
- authenticated upload endpoint for CI/CD automation

## API overview

- `GET /` templated firmware flashing page
- `GET /api` API health endpoint
- `GET /api/channels` list available channels
- `GET /api/channels/{channel}/releases` list uploaded release versions
- `GET /api/channels/{channel}/check` compare currently running versions with latest uploaded target versions
- `POST /api/admin/releases/upload` upload firmware artifacts for a target
- `GET /api/channels/{channel}/releases/{version}/{target}/manifest` fetch manifest for a specific release
- `GET /api/channels/{channel}/releases/latest/{target}/manifest` fetch manifest for latest release of a target
- `GET /api/channels/{channel}/releases/{version}/{target}/{filename}` download firmware binary

## Configuration

Environment variables:

- `UPDATE_STORAGE_DIR` (default: `storage`): where releases are stored
- `ADMIN_API_KEY`: upload endpoint auth key; required unless `ALLOW_UNAUTHENTICATED_UPLOADS=true`
- `ALLOW_UNAUTHENTICATED_UPLOADS` (default: `false`): development-only bypass for upload auth
- `MAX_UPLOAD_FILE_SIZE_BYTES` (default: `33554432`): per-file upload size limit

## Local development

### 1) Install dependencies

```bash
poetry install
```

### 2) Run server

```bash
poetry run uvicorn update_server.main:app --reload --host 0.0.0.0 --port 8000
```

### 3) Run tests

```bash
poetry run python -m unittest discover -s tests -v
```

## Docker

### Build and run locally

```bash
docker build -t update-server:local .
docker run --rm -p 8000:8000 -e UPDATE_STORAGE_DIR=/data/storage update-server:local
```

### Docker Compose smoke test

```bash
docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from smoke-test
docker compose -f docker-compose.test.yml down -v --remove-orphans
```

## Uploading firmware locally

You can upload with plain `curl`:

```bash
curl -X POST http://localhost:8000/api/admin/releases/upload \
  -H "x-api-key: ${ADMIN_API_KEY}" \
  -F channel=stable \
  -F version=1.2.3 \
  -F target=controller \
  -F files=@bootloader.bin \
  -F files=@partitions.bin \
  -F files=@boot_app0.bin \
  -F files=@firmware.bin
```

If your filenames are not one of the default ESP offsets, pass `parts_json` (or `offsets_json`) as documented in `app/routers/updates.py`.

## CI-friendly upload script

Use `scripts/upload_release.sh` for local and CI uploads.

### Required arguments

```bash
scripts/upload_release.sh \
  --server-url https://updates.example.com \
  --channel stable \
  --version 1.2.3 \
  --target controller \
  --file bootloader.bin \
  --file partitions.bin \
  --file boot_app0.bin \
  --file firmware.bin
```

### Optional arguments

- `--api-key <key>` (or set env `ADMIN_API_KEY`)
- `--component-version <version>`
- `--chip-family <chip>`
- `--label <manifest label>`
- `--parts-json-file <path>` (for explicit per-file offsets)
- `--offsets-json-file <path>`

## GitHub Actions firmware upload (for firmware repos)

The workflow in `.github/workflows/upload-firmware.yml` is designed to run in the **firmware-producing repository**, not only in this server repo.

It uploads firmware directly with `curl` and does **not** depend on `scripts/upload_release.sh`, so it is safe to copy as-is into another repository.

### Option A: Copy workflow into your firmware repo

Copy `.github/workflows/upload-firmware.yml` to your firmware project and run it with `workflow_dispatch`.

### Option B: Reuse this workflow from another repo

You can also call it as a reusable workflow:

```yaml
name: Publish firmware

on:
  workflow_dispatch:

jobs:
  publish:
    uses: <owner>/<update-server-repo>/.github/workflows/upload-firmware.yml@main
    with:
      channel: stable
      version: 1.2.3
      target: controller
      firmware_dir: firmware
      firmware_files: bootloader.bin,partitions.bin,boot_app0.bin,firmware.bin
    secrets:
      UPDATE_SERVER_URL: ${{ secrets.UPDATE_SERVER_URL }}
      UPDATE_SERVER_API_KEY: ${{ secrets.UPDATE_SERVER_API_KEY }}
```

### Required secrets in the firmware repo

- `UPDATE_SERVER_URL` (e.g. `https://updates.example.com`)
- `UPDATE_SERVER_API_KEY` (only needed when server enforces auth)

### Workflow inputs

- `channel`, `version`, `target`
- `firmware_dir` + `firmware_files` (comma-separated)
- optional metadata: `component_version`, `chip_family`, `label`
- optional mapping file: `parts_json_file` **or** `offsets_json_file`
