# Color Camera WebRTC Microservice

This repository contains a container-friendly FastAPI microservice that exposes an Intel® RealSense™ color camera stream over WebRTC. The service is designed for unattended operation inside a Docker container and guarantees that only the most recently connected viewer can access the live stream.

## Features

- **Autonomous RealSense management** – The `CameraService` continuously monitors the device, handles reconnection attempts, and restarts the pipeline if frames stop arriving.
- **Single-viewer guarantee** – Whenever a new WebRTC client connects, the previous peer connection is closed and the camera is switched to the newcomer.
- **STUN/TURN ready** – ICE configuration is loaded from `ice_config.json`, an optional JSON file path (`ICE_CONFIG_PATH`), or environment variables (`TURN_URLS`, `USE_TURN`, etc.). Configuration can also be updated at runtime via the REST API.
- **Container-first** – Production Dockerfile builds librealsense, installs all dependencies (including `curl` for the health probe), and exposes the FastAPI application on port `8104`.
- **Operational insight** – Built-in `/camera/status` endpoint and optional `monitor_camera.py` CLI provide visibility into camera health and connection state.

## Getting started

### 1. Prepare environment variables

Copy the sample file and adjust parameters if necessary:

```bash
cp camera.env.example camera.env
```

Key variables:

| Variable | Purpose |
|----------|---------|
| `CAMERA_WIDTH`, `CAMERA_HEIGHT`, `CAMERA_FPS`, `CAMERA_ROTATION` | Basic RealSense stream parameters. |
| `CAMERA_SERIAL` | Optional RealSense serial number to bind to a specific device. |
| `USE_TURN` | Enable TURN usage (set to `true`/`1`). |
| `TURN_URLS` | Comma-separated list of STUN/TURN URLs, e.g. `stun:stun.l.google.com:19302,turn:turn.example.com:3478`. |
| `TURN_USERNAME`, `TURN_CREDENTIAL` | TURN credentials (optional). |
| `ICE_RELAY_ONLY` | Force relay-only ICE candidates when TURN is required. |

### 2. Build the container image

```bash
docker build -t color-camera-service .
```

The build installs librealsense from source, aiortc, OpenCV, and all Python dependencies that are required for WebRTC streaming.

### 3. Run with Docker Compose

A production-ready compose file is provided. It pins the container to the host network (recommended for low-latency WebRTC) and grants access to the USB bus for the RealSense device.

```bash
docker compose up -d
```

The compose service reads configuration from `camera.env`, attaches the container to the host `video` group, and exposes the health endpoint via `curl`.

### 4. Connect a WebRTC viewer

1. Open the web interface served at `http://<host>:8104/` (or the proxied path `/api/v1/color_camera/`).
2. The frontend fetches ICE configuration from `/ice_config`, performs offer/answer exchange via `/offer`, and starts the WebRTC stream.
3. If a second viewer connects, the server automatically closes the previous peer connection, ensuring that only the newest client receives frames.

### 5. Observability

- `GET /camera/status` – Returns camera connection state, retry counts, and timestamps of the last successful frame.
- `GET /connections` – Shows which client currently owns the camera and for how long.
- `POST /force-release` – Forcefully closes the active WebRTC session (useful for operational tooling).
- `python monitor_camera.py` – CLI helper that polls `/camera/status` and prints the result.

## Updating ICE / TURN configuration at runtime

You can change STUN/TURN settings without restarting the container by sending a request to the REST endpoint:

```bash
curl -X POST http://localhost:8104/ice_config \
     -H "Content-Type: application/json" \
     -d '{
           "use_turn": true,
           "urls": ["stun:stun.l.google.com:19302", "turn:turn.example.com:3478"],
           "username": "demo",
           "credential": "demo-password",
           "relay_only": false
         }'
```

The configuration is stored in memory by the state manager and will be used for subsequent WebRTC negotiations.

## API overview

| Method & Path | Description |
|---------------|-------------|
| `POST /offer` | Accepts a WebRTC SDP offer, tears down any existing client, and returns an SDP answer + generated `client_id`. |
| `POST /ice` | Adds ICE candidates for the active client. |
| `DELETE /connections/{client_id}` | Closes a peer connection explicitly. |
| `POST /cleanup` | Disconnects the active client if the session is older than one hour. |
| `POST /force-release` | Immediately releases the camera. |
| `GET /camera/status` | Provides camera and backend diagnostics. |
| `GET /camera/config` | Reports current RealSense stream parameters. |

All endpoints are available both directly (e.g. `/offer`) and through the proxy prefix `/api/v1/color_camera/`.

## Health check

The container exposes a `HEALTHCHECK` that hits `http://127.0.0.1:8104/camera/status`. Docker marks the container as unhealthy if the endpoint is unreachable or returns an error.

## Development tips

- Install Python dependencies locally with `pip install -r requirements.txt`.
- Run `python -m compileall .` to ensure there are no syntax errors.
- Use `monitor_camera.py` while developing to verify that camera reconnection logic works as expected.

## License

See the repository for licensing information of bundled dependencies. Intel® RealSense™ SDK is licensed under Apache 2.0.
