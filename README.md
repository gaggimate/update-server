# update-server

Simple FastAPI service for hosting firmware releases and OTA manifests.

It supports:
- release channels (`stable`, `nightly`)
- multiple targets (`controller`, `display`, `headless`)
- manifest generation for ESP-style multi-part firmware
- authenticated upload endpoint for CI/CD automation
- Ed25519-signed release manifests, verified on upload and handed to clients unchanged

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
- `SIGNING_PUBLIC_KEY_FILE`: Ed25519 public key(s) that release manifests must be signed with, as PEM
  (`scripts/sign_release.py keygen`; several keys may be concatenated for rotation).
  Required for uploads unless `ALLOW_UNSIGNED_UPLOADS=true`
- `ALLOW_UNSIGNED_UPLOADS` (default: `false`): development-only bypass for the signature requirement
- `MAX_UPLOAD_FILE_SIZE_BYTES` (default: `33554432`): per-file upload size limit
- `MAX_UPLOAD_REQUEST_SIZE_BYTES` (default: twice the per-file limit): limit for a whole upload request
- `MAX_UPLOAD_FILES` (default: `16`): maximum number of files per upload

Uploads fail closed: without `ADMIN_API_KEY` the upload endpoint answers 404, and without
`SIGNING_PUBLIC_KEY_FILE` it refuses every upload. Requests to `/api/admin/` are authenticated before any
of the request body is read.

## Local development

### 1) Install dependencies

```bash
poetry install
```

### 2) Run server

```bash
ALLOW_UNAUTHENTICATED_UPLOADS=true ALLOW_UNSIGNED_UPLOADS=true \
  poetry run uvicorn update_server.main:app --reload --host 127.0.0.1 --port 8000
```

The two `ALLOW_*` switches open the upload endpoint to anyone who can reach the port; never set them on a
reachable host.

### 3) Run tests

```bash
poetry run python -m unittest discover -s tests -v
```

## Docker

### Build and run locally

```bash
docker build -t update-server:local .
docker run --rm -p 8000:8000 update-server:local
```

The image installs dependencies from `poetry.lock` and runs as an unprivileged user (uid 1001).

### Production

`docker-compose.prod.yml` expects two things next to it:

- `release-signing-keys.pub.pem`: the public halves of the release signing keys (see [Signed releases](#signed-releases))
- `ADMIN_API_KEY` in the environment or an `.env` file, e.g. `openssl rand -hex 32`

Deployment is automated: every push to `main` is tested, built and started on the OTA host with a health
check and automatic rollback. See [deploy/README.md](deploy/README.md).

### Docker Compose smoke test

```bash
docker compose -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from smoke-test
docker compose -f docker-compose.test.yml down -v --remove-orphans
```

## Uploading firmware locally

You can upload with plain `curl`. `manifest` and `signature` are the signed release manifest for exactly
the files of this upload (see [Signed releases](#signed-releases)); `<` makes curl send the file content
byte for byte:

```bash
curl -X POST http://localhost:8000/api/admin/releases/upload \
  -H "x-api-key: ${ADMIN_API_KEY}" \
  -F channel=stable \
  -F version=1.2.3 \
  -F target=controller \
  -F "manifest=<manifest.json" \
  -F "signature=<manifest.sig" \
  -F files=@bootloader.bin \
  -F files=@partitions.bin \
  -F files=@boot_app0.bin \
  -F files=@firmware.bin
```

An upload is all-or-nothing: files only become downloadable once every file is stored and the signature
checks out. A published `channel/version/target` can not be overwritten.

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
  --file firmware.bin \
  --manifest-file manifest.json \
  --signature-file manifest.sig
```

### Optional arguments

- `--api-key <key>` (or set env `ADMIN_API_KEY`)
- `--component-version <version>`: version reported for this target if it differs from the release version
  (for signed uploads it must match the manifest, see `sign_release.py sign --component-version`)
- `--chip-family <chip>`
- `--label <manifest label>`
- `--manifest-file <path>` and `--signature-file <path>`: the signed release manifest; only optional on
  servers running with `ALLOW_UNSIGNED_UPLOADS=true`

## Signed releases

The server is not the root of trust for firmware: the release signing key is. Its private half lives with
whoever builds releases and never touches this server. Devices ship the public half and must only install
what it signed, so a compromised server or API key can withhold updates but can not get firmware onto
devices. The server checks the same signature at upload time so a bad release is rejected before any device
sees it.

### The signed manifest

One manifest describes one `channel/version/target` and every file that belongs to it:

```json
{"channel":"stable","files":[{"name":"firmware.bin","sha256":"<64 hex>","size":1234567}],"schema":1,"target":"controller","version":"1.2.3"}
```

- `schema` is `1`; `channel`, `version` and `target` say what the files are, so a signed build can not be
  republished as a different target or as a newer version than it is.
- `files` lists name, size and lowercase hex SHA-256 of every file.
- `componentVersion` is optional and overrides the version reported for this target.
- Further fields are allowed and are covered by the signature like everything else.

The signature is a detached Ed25519 signature (RFC 8032) over the exact bytes of the manifest, base64
encoded. Nothing is canonicalized: the manifest is uploaded, stored and served byte for byte.

### 1) Create the key pair (once)

```bash
pip install cryptography
scripts/sign_release.py keygen --private-key release-signing-key.pem --public-key release-signing-key.pub.pem
```

- `release-signing-key.pem` is the private key. Keep it offline or in your release pipeline's secret store,
  never on the update server.
- `release-signing-key.pub.pem` goes to the server as `SIGNING_PUBLIC_KEY_FILE`.
- The printed `RELEASE_SIGNING_KEY[32]` constant is what the firmware embeds
  (`scripts/sign_release.py info --public-key ...` prints it again).

### 2) Sign a release target

```bash
scripts/sign_release.py sign --private-key release-signing-key.pem \
  --channel stable --version 1.2.3 --target controller \
  bootloader.bin partitions.bin boot_app0.bin firmware.bin
```

This writes `manifest.json` and `manifest.sig`.

### 3) Upload

Pass both files along with the firmware files (`--manifest-file`, `--signature-file`). The upload is
rejected unless

- the signature is a valid Ed25519 signature by a key in `SIGNING_PUBLIC_KEY_FILE` over the submitted bytes,
- channel, version and target in the manifest are the ones being uploaded to,
- the manifest and the uploaded files match one to one, with identical size and SHA-256.

### 4) Client-side verification

Every manifest endpoint (`.../{target}/manifest`) and the release details carry

```json
"signed": {"manifest": "<the signed JSON text>", "signature": "<base64>", "keyId": "<16 hex>"}
```

`keyId` is the first 8 bytes of the SHA-256 of the raw public key and tells a client with several built-in
keys which one to use. Everything else in the response, including the `sha256` fields next to the download
paths, is computed by the server and must not be trusted by a device. A device

1. verifies `signature` over the UTF-8 bytes of the `manifest` string with its built-in key, and only then
   parses that string as JSON,
2. checks that `target` is its own target and that the version is newer than the one it runs,
3. downloads each file and compares size and SHA-256 with the signed manifest before activating it.

On ESP32 with the Arduino core no extra library is needed: libsodium is part of the SDK (`#include <sodium.h>`).

```cpp
// signedManifest and signatureBase64 are the two strings from the "signed" object.
uint8_t signature[crypto_sign_BYTES];
size_t signatureLength = 0;
bool ok = sodium_base642bin(signature, sizeof(signature), signatureBase64.c_str(), signatureBase64.length(),
                            nullptr, &signatureLength, nullptr, sodium_base64_VARIANT_ORIGINAL) == 0 &&
          signatureLength == crypto_sign_BYTES &&
          crypto_sign_verify_detached(signature, (const uint8_t *)signedManifest.c_str(), signedManifest.length(),
                                      RELEASE_SIGNING_KEY) == 0;
```

### Key rotation

Add the new public key to `SIGNING_PUBLIC_KEY_FILE` (both PEM blocks in one file) and ship firmware that
trusts both keys. Once devices have had time to update, sign with the new key only and remove the old one
from the server and from the firmware.
