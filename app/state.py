import asyncio
import os
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import logging

from drivers.camera import CameraService, RealSenseBackend
from app.config import get_initial_ice_config

logger = logging.getLogger("state")

camera_service: Optional[CameraService] = None
ice_config: Dict[str, Any] = {}
ice_lock = asyncio.Lock()

def get_camera_config():
    """Get camera configuration from environment variables."""
    return {
        "width": int(os.getenv("CAMERA_WIDTH", "640")),
        "height": int(os.getenv("CAMERA_HEIGHT", "480")),
        "fps": int(os.getenv("CAMERA_FPS", "30")),
        "rotation": int(os.getenv("CAMERA_ROTATION", "180")),
        "serial": os.getenv("CAMERA_SERIAL", None)
    }

# Single connection management: only one active WebRTC connection at a time
current_client_id: Optional[str] = None
current_connection: Optional[Dict[str, Any]] = None
connections_lock = asyncio.Lock()

async def init_state():
    global camera_service, ice_config
    try:
        if camera_service is None:
            # Initialize camera backend with retry mechanism
            max_init_retries = 3
            init_retry_delay = 5.0
            
            # Get camera configuration
            camera_config = get_camera_config()
            logger.info(f"Camera config: {camera_config}")
            
            for attempt in range(max_init_retries):
                try:
                    logger.info(f"Initializing camera service (attempt {attempt + 1}/{max_init_retries})")
                    backend = RealSenseBackend(
                        serial=camera_config["serial"],
                        width=camera_config["width"],
                        height=camera_config["height"],
                        fps=camera_config["fps"],
                        rotation=camera_config["rotation"]
                    )
                    camera_service = CameraService(backend)
                    camera_service.start()
                    logger.info("Camera service initialized successfully")
                    break
                except Exception as e:
                    logger.warning(f"Camera initialization attempt {attempt + 1} failed: {e}")
                    if attempt < max_init_retries - 1:
                        logger.info(f"Retrying camera initialization in {init_retry_delay} seconds...")
                        await asyncio.sleep(init_retry_delay)
                        # Clean up failed service
                        if camera_service:
                            try:
                                camera_service.stop()
                            except:
                                pass
                            camera_service = None
                    else:
                        # Final attempt failed - create a stub service that will try to reconnect
                        logger.warning("Failed to initialize camera service, creating stub for later reconnection")
                        backend = RealSenseBackend(
                            serial=camera_config["serial"],
                            width=camera_config["width"],
                            height=camera_config["height"],
                            fps=camera_config["fps"],
                            rotation=camera_config["rotation"]
                        )
                        camera_service = CameraService(backend)
                        # Don't start the service yet, it will be started when camera becomes available
                        camera_service.running = False
                        camera_service.backend.connection_state = "disconnected"
                        logger.info("Camera stub service created - will attempt reconnection when camera becomes available")

        if not ice_config:
            ice_config = get_initial_ice_config()
            logger.info("ICE config initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize state: {e}")
        # Don't re-raise the exception to allow the service to start without camera
        # The camera endpoints will handle the case where camera_service is None
        logger.warning("Service will start without camera functionality")

async def shutdown_state():
    global camera_service
    if camera_service is not None:
        try:
            camera_service.stop()
            logger.info("Camera service stopped successfully")
        except Exception as e:
            logger.error(f"Error stopping camera service: {e}")
        finally:
            camera_service = None

async def get_ice_config_state() -> Dict[str, Any]:
    async with ice_lock:
        return dict(ice_config)

async def update_ice_config_state(new_config: Dict[str, Any]) -> Dict[str, Any]:
    async with ice_lock:
        try:
            if "use_turn" in new_config:
                ice_config["use_turn"] = bool(new_config["use_turn"])
            if "urls" in new_config and isinstance(new_config["urls"], list):
                # Валидация URL-ов
                valid_urls = []
                for url in new_config["urls"]:
                    if isinstance(url, str) and url.strip():
                        valid_urls.append(url.strip())
                ice_config["urls"] = valid_urls
            if "username" in new_config and new_config["username"] is not None:
                ice_config["username"] = str(new_config["username"])
            if "credential" in new_config and new_config["credential"] is not None:
                ice_config["credential"] = str(new_config["credential"])
            if "relay_only" in new_config:
                ice_config["relay_only"] = bool(new_config["relay_only"])
            return dict(ice_config)
        except Exception as e:
            logger.error(f"Error updating ICE config: {e}")
            raise

async def create_connection(client_id: str, pc, track, extra_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Creates a new WebRTC connection, disconnecting the previous client if exists."""
    async with connections_lock:
        global current_client_id, current_connection
        
        # Disconnect existing client if any
        if current_client_id is not None and current_connection is not None:
            logger.info(f"Disconnecting previous client {current_client_id}")
            try:
                await current_connection["pc"].close()
                logger.info(f"Previous client {current_client_id} disconnected successfully")
            except Exception as e:
                logger.warning(f"Error disconnecting previous client {current_client_id}: {e}")
        
        # Create new connection data
        connection_data = {
            "pc": pc,
            "track": track,
            "created_at": datetime.now(timezone.utc),
            "connection_state": "new",
            "ice_connection_state": "new",
            "ice_gathering_state": "new",
        }
        if extra_data:
            connection_data.update(extra_data)

        # Set new client as current
        current_client_id = client_id
        current_connection = connection_data
        
        logger.info(f"New client {client_id} connected successfully")
        return current_connection

async def get_connection(client_id: str) -> Optional[Dict[str, Any]]:
    """Gets connection data only if the client is currently active."""
    async with connections_lock:
        if current_client_id == client_id and current_connection is not None:
            return current_connection
        return None

async def update_connection_state(client_id: str, state_type: str, state_value: str):
    """Updates connection state only for the currently active client."""
    async with connections_lock:
        if current_client_id == client_id and current_connection is not None:
            current_connection[f"{state_type}_state"] = state_value
            logger.debug(f"Updated {state_type} state for client {client_id}: {state_value}")

async def remove_connection(client_id: str):
    """Removes connection for the specified client (only if it's currently active)."""
    async with connections_lock:
        global current_client_id, current_connection
        
        if current_client_id == client_id and current_connection is not None:
            try:
                await current_connection["pc"].close()
                logger.info(f"Client {client_id} disconnected successfully")
            except Exception as e:
                logger.warning(f"Error disconnecting client {client_id}: {e}")
            finally:
                current_client_id = None
                current_connection = None
        else:
            logger.info(f"Client {client_id} is not currently connected or doesn't exist")

async def get_all_connections() -> Dict[str, Dict[str, Any]]:
    """Returns information about the currently active client (if any)."""
    async with connections_lock:
        if current_client_id is not None and current_connection is not None:
            return {current_client_id: current_connection}
        return {}

async def cleanup_old_connections():
    """Disconnects client if connection is older than 1 hour."""
    async with connections_lock:
        global current_client_id, current_connection
        
        if current_client_id is not None and current_connection is not None:
            now = datetime.now(timezone.utc)
            connection_age = (now - current_connection["created_at"]).total_seconds()
            
            if connection_age > 3600:  # 1 hour
                logger.info(f"Client {current_client_id} connected for {connection_age:.0f} seconds - disconnecting due to timeout")
                try:
                    await current_connection["pc"].close()
                    logger.info(f"Old client {current_client_id} disconnected due to timeout")
                except Exception as e:
                    logger.warning(f"Error disconnecting old client {current_client_id}: {e}")
                finally:
                    current_client_id = None
                    current_connection = None
            else:
                logger.debug(f"Client {current_client_id} connected for {connection_age:.0f} seconds - still within timeout")
        else:
            logger.debug("No active connections to cleanup")

async def force_release_camera():
    """Forcefully disconnects the current client."""
    async with connections_lock:
        global current_client_id, current_connection
        
        if current_client_id is not None and current_connection is not None:
            logger.info(f"Force disconnecting client {current_client_id}")
            try:
                await current_connection["pc"].close()
                logger.info(f"Client {current_client_id} force disconnected successfully")
            except Exception as e:
                logger.warning(f"Error force disconnecting client {current_client_id}: {e}")
            finally:
                current_client_id = None
                current_connection = None
        else:
            logger.info("No active connections to force disconnect")

async def get_current_client_info() -> Optional[Dict[str, Any]]:
    """Returns information about the currently connected client."""
    async with connections_lock:
        if current_client_id is not None and current_connection is not None:
            return {
                "client_id": current_client_id,
                "connection": current_connection,
                "time_in_cell": (datetime.now(timezone.utc) - current_connection["created_at"]).total_seconds()
            }
        return None

async def get_camera_service():
    """Returns the initialized camera service."""
    global camera_service
    if camera_service is None:
        logger.warning("Camera service not initialized, initializing now")
        await init_state()
    elif not camera_service.running and hasattr(camera_service.backend, 'connection_state'):
        # Camera service exists but is not running - try to reconnect
        logger.info("Camera service exists but not running, attempting reconnection")
        try:
            camera_service.start()
            logger.info("Camera service reconnected successfully")
        except Exception as e:
            logger.warning(f"Failed to reconnect camera service: {e}")
    return camera_service
