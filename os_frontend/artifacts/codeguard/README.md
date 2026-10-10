# CodeGuard local demo workspace

The browser opens at the compiler. Local workspaces, C/C++ source, custom input, and deterministic simulation history are stored in `localStorage`; workspace labels are demonstration labels and are not real identities.

The optional FastAPI base URL is configured with `VITE_API_BASE_URL`. Its default is `http://localhost:8000/api/v1`. CodeGuard probes the configured base and its origin for an OpenAPI document, then enables execution or report reads only for operations discoverable from that contract. No template `/api/healthz` hook, assumed endpoint, example server response, or local code execution is used.

Simulation scenarios are safe, deterministic UI-only event streams. They never launch programs or represent real OS telemetry.
