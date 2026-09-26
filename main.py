import os
import json
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect, Request
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# --- Config ---
API_KEY = os.environ.get("API_KEY", "demo-key-2026")
VALID_COLORS = ["red", "amber", "green"]

# --- State ---
current_color = "red"
request_log: list[dict] = []
MAX_LOG_ENTRIES = 100


# --- WebSocket manager ---
class ConnectionManager:
    def __init__(self):
        self.connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        self.connections.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.connections.remove(ws)


manager = ConnectionManager()


# --- Auth ---
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Depends(api_key_header)):
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return api_key


# --- Request logging middleware ---
def log_entry(method: str, path: str, headers: dict, body: str | None, status: int, response_body: str):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "path": path,
        "headers": {k: ("***" if k.lower() == "x-api-key" else v) for k, v in headers.items()},
        "body": body,
        "status": status,
        "response": response_body,
    }
    request_log.append(entry)
    if len(request_log) > MAX_LOG_ENTRIES:
        request_log.pop(0)
    return entry


# --- App ---
app = FastAPI(
    title="Traffic Light API",
    description="A demo traffic light controlled entirely via API. Built for the DevRev SE panel challenge.",
    version="1.0.0",
)


# --- Models ---
class LightRequest(BaseModel):
    color: str

class LightResponse(BaseModel):
    color: str

class ColorsResponse(BaseModel):
    colors: list[str]


# --- API endpoints ---
@app.get("/api/status", response_model=LightResponse, tags=["Traffic Light"])
async def get_status(request: Request, _key: str = Depends(verify_api_key)):
    """Return the current color of the traffic light."""
    body = json.dumps({"color": current_color})
    entry = log_entry(request.method, str(request.url.path), dict(request.headers), None, 200, body)
    await manager.broadcast({"type": "log", "entry": entry})
    return LightResponse(color=current_color)


@app.get("/api/colors", response_model=ColorsResponse, tags=["Traffic Light"])
async def get_colors(request: Request, _key: str = Depends(verify_api_key)):
    """Return the list of valid colors the traffic light supports."""
    body = json.dumps({"colors": VALID_COLORS})
    entry = log_entry(request.method, str(request.url.path), dict(request.headers), None, 200, body)
    await manager.broadcast({"type": "log", "entry": entry})
    return ColorsResponse(colors=VALID_COLORS)


@app.post("/api/light", response_model=LightResponse, tags=["Traffic Light"])
async def set_light(req: LightRequest, request: Request, _key: str = Depends(verify_api_key)):
    """Change the traffic light to a specified color (red, amber, or green)."""
    global current_color
    color = req.color.lower().strip()
    if color not in VALID_COLORS:
        raise HTTPException(status_code=400, detail=f"Invalid color '{req.color}'. Must be one of: {VALID_COLORS}")
    current_color = color
    resp = json.dumps({"color": current_color})
    entry = log_entry(request.method, str(request.url.path), dict(request.headers), json.dumps(req.model_dump()), 200, resp)
    await manager.broadcast({"type": "color", "color": current_color})
    await manager.broadcast({"type": "log", "entry": entry})
    return LightResponse(color=current_color)


# --- WebSocket ---
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    # Send current state on connect
    await ws.send_json({"type": "color", "color": current_color})
    await ws.send_json({"type": "history", "entries": request_log[-20:]})
    try:
        while True:
            await ws.receive_text()  # keep-alive
    except WebSocketDisconnect:
        manager.disconnect(ws)


# --- Serve static files ---
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", include_in_schema=False)
async def root():
    return FileResponse("static/index.html")
