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
- `GET /api/channels/{channel}/releases/{version}` list detailed metadata for all targets/files in a release
- `GET /api/channels/{channel}/check` compare currently running versions with latest uploaded target versions
- `POST /api/admin/releases/upload` upload firmware artifacts for a target (manifest offsets are inferred from default filenames)
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

You can optionally include a clearsigned SHA256SUMS payload when uploading:

```bash
curl -X POST http://localhost:8000/api/admin/releases/upload \
  -H "x-api-key: ${ADMIN_API_KEY}" \
  -F channel=stable \
  -F version=1.2.3 \
  -F target=controller \
  -F signed_sha256="$(cat SHA256SUMS.asc)" \
  -F files=@firmware.bin
```

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
- `--signed-sha256-file <path>` (ASCII armored clearsigned sha256 sums, optional)

## Creating and verifying signed SHA256 manifests

The `signed_sha256` field is intended to carry a GPG-clearsigned `sha256sum` file (similar to Apt package metadata workflows).

### 1) Create checksums

From the directory containing firmware files:

```bash
sha256sum bootloader.bin partitions.bin boot_app0.bin firmware.bin > SHA256SUMS
```

### 2) Clearsign with GPG

```bash
gpg --clearsign --output SHA256SUMS.asc SHA256SUMS
```

`SHA256SUMS.asc` is what you submit as `signed_sha256` (or `--signed-sha256-file` in the helper script).

### 3) Upload with signed checksums

```bash
scripts/upload_release.sh \
  --server-url https://updates.example.com \
  --channel stable \
  --version 1.2.3 \
  --target controller \
  --file bootloader.bin \
  --file partitions.bin \
  --file boot_app0.bin \
  --file firmware.bin \
  --signed-sha256-file SHA256SUMS.asc
```

### 4) Client-side verification

After downloading `signedSha256` from `GET /api/channels/{channel}/releases/{version}` and fetching binaries:

```bash
# Verify signature and extract signed content
gpg --verify SHA256SUMS.asc

# Verify hashes against downloaded files
sha256sum -c SHA256SUMS
```

If you only have the clearsigned file, you can extract the signed body first:

```bash
gpg --decrypt SHA256SUMS.asc > SHA256SUMS
sha256sum -c SHA256SUMS
```
