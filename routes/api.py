# app/api/webrtc.py
import json
import uuid
import logging
import time
from fastapi import APIRouter, HTTPException
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer, RTCIceCandidate

from service.video_service import CameraStreamTrack
from models.webrtc import WebRTCSession
from models.offer import Offer, IceConfig, IceCandidate
from app.state import (get_ice_config_state, update_ice_config_state,
    create_connection, get_connection, remove_connection,
    update_connection_state, get_all_connections, cleanup_old_connections,
    force_release_camera, get_current_client_info, get_camera_service
)

logger = logging.getLogger("webrtc")
router = APIRouter()

@router.get("/ice_config")
async def get_ice_config():
    """Returns current ICE configuration."""
    config = await get_ice_config_state()
    logger.debug("ICE config requested")
    return config

@router.post("/ice_config")
async def update_ice_config(config: IceConfig):
    """Updates ICE configuration."""
    updated_config = await update_ice_config_state(config.dict())
    logger.info("ICE config updated")
    return updated_config

@router.post("/offer")
async def handle_offer(params: Offer, mode: str = "color"):
    """Handles WebRTC offer - new client will replace existing connection."""
    try:
        client_id = params.client_id or str(uuid.uuid4())
        logger.info(f"Handling offer for client {client_id} in mode {mode}")
        
        # Check if there's already an active client
        current_info = await get_current_client_info()
        if current_info:
            logger.info(f"New client {client_id} will replace existing client {current_info['client_id']}")
        
        # Get camera service from state manager
        logger.info(f"Getting camera service for client {client_id}")
        camera_service = await get_camera_service()
        if camera_service is None:
            logger.error("Failed to initialize camera service")
            raise HTTPException(status_code=500, detail="Failed to initialize camera service")
        
        logger.info(f"Creating WebRTC session for client {client_id}")
        session = WebRTCSession(client_id, camera_service, mode)
        
        logger.info(f"Creating offer for client {client_id}")
        desc = await session.create(params)

        # State manager will automatically disconnect previous client
        logger.info(f"Creating connection for client {client_id}")
        await create_connection(client_id, session.pc, session.track, {"session": session})
        
        logger.info(f"Client {client_id} connected successfully, camera allocated")
        return {"sdp": desc.sdp, "type": desc.type, "client_id": client_id}
        
    except Exception as e:
        logger.error(f"Error handling offer: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.post("/ice")
async def add_ice(candidate: IceCandidate):
    """Adds ICE candidate - only for currently active client."""
    conn = await get_connection(candidate.client_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Client not found or already disconnected")
    
    session: WebRTCSession = conn["session"]
    await session.add_ice_candidate(candidate)
    logger.debug(f"ICE candidate added for client {candidate.client_id}")
    return {"status": "ok"}

@router.delete("/connections/{client_id}")
async def close_connection(client_id: str):
    """Closes connection for the specified client."""
    conn = await get_connection(client_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Client not found or already disconnected")
    
    session: WebRTCSession = conn["session"]
    await session.close()
    await remove_connection(client_id)  # Notify state manager
    
    logger.info(f"Connection closed for client {client_id}")
    return {"status": "closed"}

@router.get("/connections")
async def list_connections():
    """Returns information about current connections."""
    connections = await get_all_connections()
    current_info = await get_current_client_info()
    
    response = {
        "clients": list(connections.keys()),
        "current_client": current_info["client_id"] if current_info else None,
        "connection_duration": current_info["time_in_cell"] if current_info else 0
    }
    
    logger.debug(f"Connection status requested")
    return response

@router.post("/cleanup")
async def cleanup():
    """Cleans up old connections (disconnects clients older than 1 hour)."""
    current_info = await get_current_client_info()
    if current_info:
        logger.info(f"Cleanup requested - will disconnect client {current_info['client_id']} if timeout exceeded")
    
    await cleanup_old_connections()
    return {"status": "ok"}

@router.post("/force-release")
async def force_release():
    """Forcefully disconnects the current client."""
    current_info = await get_current_client_info()
    if current_info:
        logger.warning(f"Force release requested for client {current_info['client_id']}")
        await force_release_camera()
        return {"status": "force_released", "released_client": current_info['client_id']}
    else:
        logger.info("No active connections to force release")
        return {"status": "already_empty"}

@router.get("/camera/status")
async def get_camera_status():
    """Returns detailed camera connection status."""
    try:
        camera_service = await get_camera_service()
        if camera_service is None:
            return {
                "status": "error",
                "message": "Camera service not initialized",
                "connection_state": "unavailable"
            }
        
        # Get connection status from camera service
        connection_status = camera_service.get_connection_status()
        
        # Get latest frame info
        frame, timestamp = camera_service.get_latest()
        has_frame = frame is not None
        
        return {
            "status": "ok",
            "connection_state": connection_status.get("state", "unknown"),
            "running": connection_status.get("running", False),
            "retry_count": connection_status.get("retry_count", 0),
            "last_attempt": connection_status.get("last_attempt", 0),
            "last_successful_frame": connection_status.get("last_successful_frame", 0),
            "has_frame": has_frame,
            "frame_timestamp": timestamp if has_frame else None
        }
        
    except Exception as e:
        logger.error(f"Error getting camera status: {e}")
        return {
            "status": "error",
            "message": str(e),
            "connection_state": "error"
        }

@router.post("/camera/reconnect")
async def force_camera_reconnect():
    """Forces camera reconnection attempt."""
    try:
        camera_service = await get_camera_service()
        if camera_service is None:
            raise HTTPException(status_code=500, detail="Camera service not initialized")
        
        # Force reconnection by stopping and starting the backend
        logger.info("Force camera reconnection requested")
        camera_service.backend.stop()
        time.sleep(1)
        camera_service.backend.start()
        
        return {
            "status": "ok",
            "message": "Camera reconnection initiated"
        }
        
    except Exception as e:
        logger.error(f"Error forcing camera reconnection: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to reconnect camera: {str(e)}")

@router.get("/camera/config")
async def get_camera_config():
    """Returns current camera configuration."""
    try:
        camera_service = await get_camera_service()
        if camera_service is None:
            return {
                "status": "error",
                "message": "Camera service not initialized"
            }
        
        backend = camera_service.backend
        return {
            "status": "ok",
            "config": {
                "width": backend.width,
                "height": backend.height,
                "fps": backend.fps,
                "rotation": backend.rotation,
                "serial": backend.serial
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting camera config: {e}")
        return {
            "status": "error",
            "message": str(e)
        }
