import os
import sys
import logging
from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from app.state import init_state, shutdown_state
from routes import api

logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация и корректное завершение приложения."""
    await init_state()
    try:
        yield
    finally:
        await shutdown_state()


app = FastAPI(
    title="Camera WebRTC Microservice",
    version="1.0.0",
    lifespan=lifespan
)

# --- Routers ---
# Основной роутер для прямого доступа
app.include_router(api.router, tags=["Color Camera WebRTC"])

# Прокси-роутер для работы через прокси
app.include_router(api.router, prefix="/api/v1/color_camera", tags=["Color Camera WebRTC Proxy"])

# --- Static files ---
app.mount("/static", StaticFiles(directory="static"), name="static")

# Прокси-роут для статических файлов
app.mount("/api/v1/color_camera/static", StaticFiles(directory="static"), name="static_proxy")


@app.get("/", include_in_schema=False)
async def root():
    """Отдаём index.html или JSON с ошибкой."""
    filename = os.path.join(CURRENT_DIR, "index.html")
    if not os.path.exists(filename):
        return JSONResponse(content={"error": "index.html not found"}, status_code=404)
    return FileResponse(filename)

@app.get("/api/v1/color_camera/", include_in_schema=False)
async def proxy_root():
    """Прокси-роут для главной страницы через /api/v1/color_camera/."""
    filename = os.path.join(CURRENT_DIR, "index.html")
    if not os.path.exists(filename):
        return JSONResponse(content={"error": "index.html not found"}, status_code=404)
    return FileResponse(filename)


# --- Run with uvicorn ---
if __name__ == "__main__":
    import uvicorn

    host = os.getenv("SERVICE_HOST", "0.0.0.0")
    port = int(os.getenv("SERVICE_PORT", "8104"))

    uvicorn.run("main:app", host=host, port=port, reload=False)
