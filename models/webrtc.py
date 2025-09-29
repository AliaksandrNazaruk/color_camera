from drivers.camera import CameraService
from service.video_service import CameraStreamTrack
from models.offer import Offer
from app.state import get_ice_config_state, update_connection_state, remove_connection
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer, RTCIceCandidate
import json
from models.offer import Offer, IceConfig, IceCandidate
import logging
logger = logging.getLogger("webrtc")

class WebRTCSession:
    def __init__(self, client_id: str, service: CameraService, mode: str = "color"):
        self.client_id = client_id
        self.service = service
        self.pc = None
        self.track = CameraStreamTrack(service, mode=mode)

    async def create(self, offer: Offer):
        # ICE configuration
        current_ice = await get_ice_config_state()
        ice_servers = []
        urls = current_ice.get("urls") or []
        if urls:
            ice_servers.append(RTCIceServer(
                urls=urls,
                username=current_ice.get("username"),
                credential=current_ice.get("credential")
            ))

        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
        self.pc.addTrack(self.track)

        @self.pc.on("connectionstatechange")
        async def on_connectionstatechange():
            state = self.pc.connectionState
            logger.info(f"Connection {self.client_id} state={state}")
            await update_connection_state(self.client_id, "connection", state)
            if state in ("failed", "closed", "disconnected"):
                # Don't call self.close() here as it might cause double cleanup
                # The state manager will handle cleanup automatically
                pass

        @self.pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange():
            state = self.pc.iceConnectionState
            logger.info(f"ICE {self.client_id} state={state}")
            await update_connection_state(self.client_id, "ice_connection", state)

        @self.pc.on("datachannel")
        def on_datachannel(channel):
            @channel.on("message")
            def on_message(message):
                try:
                    data = json.loads(message)
                    if data.get("type") == "ping":
                        channel.send(json.dumps({"type": "pong"}))
                except Exception as e:
                    logger.error(f"Datachannel error: {e}")

        # WebRTC SDP handshake
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=offer.sdp, type=offer.type))
        answer = await self.pc.createAnswer()
        await self.pc.setLocalDescription(answer)
        return self.pc.localDescription

    async def add_ice_candidate(self, candidate: IceCandidate):
        if not self.pc:
            raise RuntimeError("PeerConnection not initialized")
        ice_candidate = RTCIceCandidate(
            candidate=candidate.candidate,
            sdpMid=candidate.sdp_mid,
            sdpMLineIndex=candidate.sdp_mline_index
        )
        await self.pc.addIceCandidate(ice_candidate)

    async def close(self):
        if self.pc:
            try:
                await self.pc.close()
            except Exception:
                pass
        await remove_connection(self.client_id)
        logger.info(f"Closed WebRTC session {self.client_id}")
