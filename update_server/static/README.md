# Vendored front-end assets

The flasher page writes firmware to devices over Web Serial, so everything it executes is served from this
origin and pinned in git instead of being loaded from a CDN at view time. The page's Content-Security-Policy
(`update_server/middleware.py`) only allows same-origin scripts, fonts and requests.

| Directory | Source | Version | License |
| --- | --- | --- | --- |
| `esp-web-tools/` | npm `esp-web-tools`, `dist/web/` (unmodified) | 9.4.3 | Apache-2.0 |
| `fonts/inter-*` | npm `@fontsource/inter`, `files/` | 5.3.0 | OFL-1.1 |
| `fonts/montserrat-*` | npm `@fontsource/montserrat`, `files/` | 5.3.0 | OFL-1.1 |

## Updating esp-web-tools

```bash
npm pack esp-web-tools@<version>
tar -xzf esp-web-tools-<version>.tgz
rm update_server/static/esp-web-tools/*.js
cp package/dist/web/*.js update_server/static/esp-web-tools/
```

The chunk file names contain content hashes, so stale files must be removed. Review the diff before committing.
