# app/services/video_service.py
import cv2
import numpy as np
from av import VideoFrame
from aiortc import VideoStreamTrack
import logging
import time
from typing import Optional

from drivers.camera import CameraService, RealSenseBackend

logger = logging.getLogger("video_service")


class CameraStreamTrack(VideoStreamTrack):
    """
    WebRTC VideoStreamTrack, завязанный на CameraService.
    mode:
      - "color"   → RGB/BGR кадр
    """

    def __init__(self, service: CameraService, mode: str = "color"):
        super().__init__()
        self.service = service
        self.mode = mode
        logger.info(f"CameraStreamTrack initialized in mode={mode}")

    async def recv(self) -> VideoFrame:
        pts, time_base = await self.next_timestamp()
        try:
            color, ts = self.service.get_latest()

            bgr: Optional[np.ndarray] = None

            if self.mode == "color" and color is not None:
                bgr = color
                
            if bgr is None:
                # fallback: пустой кадр
                bgr = np.zeros((480, 640, 3), dtype=np.uint8)

            frame = VideoFrame.from_ndarray(bgr, format="bgr24")
            frame.pts = pts
            frame.time_base = time_base
            return frame

        except Exception as e:
            logger.error(f"CameraStreamTrack recv() error: {e}")
            bgr = np.zeros((480, 640, 3), dtype=np.uint8)
            frame = VideoFrame.from_ndarray(bgr, format="bgr24")
            frame.pts = pts
            frame.time_base = time_base
            return frame

def main():
    # Можно выбрать backend
    backend = RealSenseBackend(width=640, height=480, fps=30)

    service = CameraService(backend)
    service.start()

    # Для WebRTC:
    track_color = CameraStreamTrack(service, mode="color")

    import cv2

    try:
        while True:
            color, _ = service.get_latest()
            if color is not None:
                cv2.imshow("Color", color)
            if cv2.waitKey(1) == 27:
                break
    finally:
        service.stop()
        cv2.destroyAllWindows()