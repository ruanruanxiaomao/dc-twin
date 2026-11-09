# Security policy

## Scope

`dc-twin` is a **local research / visualisation tool**. Its HTTP console is a
single-user development helper, not a production service.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | ✅ |

## Defaults and safeguards

- The console binds `127.0.0.1` by default.
- `dc serve --host 0.0.0.0` is refused unless `DC_CONSOLE_TOKEN` is set
  (error `DC-E4001`). There is no authentication for `/api/run` or
  `/api/config`; **do not expose the console to an untrusted network**.
- No credentials, no telemetry, no outbound network calls in the pipeline.
  The only external input is an optional local CSV/WMS adapter.

## Reporting a vulnerability

Please open an issue with `dc doctor --json` output (redact your local path if
you prefer) and a minimal reproduction. Do not include real customer data:
the repository contains synthetic orders only.

## Third-party components

cargoTwin assets (MIT), Three.js (MIT), Pillow, imageio-ffmpeg and jsonschema
— see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Blender 5.2 is used
as an external renderer; it is never bundled.
