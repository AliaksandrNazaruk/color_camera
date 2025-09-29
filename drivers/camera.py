# app/drivers/realsense_camera.py
import pyrealsense2 as rs
import cv2
import numpy as np
import threading
import time
import logging
import psutil
import os
import signal
from typing import Optional, Tuple

logger = logging.getLogger("camera")
logging.basicConfig(level=logging.INFO)


class CameraError(Exception):
    """Custom camera exception for unified error handling."""


class CameraBackend:
    """Абстрактный backend для получения кадров."""

    def start(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def get_frame(self) -> Tuple[Optional[np.ndarray], float]:
        """Возвращает (color, timestamp)."""
        raise NotImplementedError


class RealSenseBackend(CameraBackend):
    def __init__(self, serial: Optional[str] = None,
                 width: int = 640, height: int = 480, fps: int = 30,
                 rotation: int = 0):
        self.serial = serial
        self.width = width
        self.height = height
        self.fps = fps
        self.rotation = rotation  # Rotation angle: 0, 90, 180, 270

        self.pipeline: Optional[rs.pipeline] = None
        self.align: Optional[rs.align] = None
        self.temp_filter = rs.temporal_filter()
        self.colorizer = rs.colorizer()
        self.running = False
        self.max_retries = 3
        self.retry_delay = 2.0
        
        # Connection state management
        self.connection_state = "disconnected"  # disconnected, connecting, connected, failed
        self.last_connection_attempt = 0.0
        self.connection_retry_count = 0
        self.max_connection_retries = 5
        self.connection_retry_delay = 5.0  # Base delay in seconds
        self.max_connection_retry_delay = 300.0  # Max delay: 5 minutes
        self.last_successful_frame = 0.0
        self.connection_timeout = 30.0  # Consider disconnected if no frames for 30 seconds

    def _check_device_availability(self) -> bool:
        """Check if the RealSense device is available and not in use."""
        try:
            ctx = rs.context()
            devices = ctx.query_devices()
            
            if not devices:
                logger.warning("No RealSense devices found")
                return False
                
            # If specific serial requested, check for it
            if self.serial:
                target_devices = [d for d in devices 
                           if d.get_info(rs.camera_info.serial_number) == self.serial]
                if not target_devices:
                    logger.warning(f"RealSense device with serial {self.serial} not found")
                    return False
                devices = target_devices
            
            # Check if device is in use by checking for existing pipelines
            for device in devices:
                try:
                    # Try to get device info to see if it's accessible
                    device.get_info(rs.camera_info.name)
                    device.get_info(rs.camera_info.serial_number)
                    logger.debug(f"Device {device.get_info(rs.camera_info.serial_number)} is available")
                except Exception as e:
                    logger.warning(f"Device {device.get_info(rs.camera_info.serial_number)} may be in use: {e}")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error checking device availability: {e}")
            return False

    def _find_conflicting_processes(self) -> list:
        """Find processes that might be using the RealSense camera."""
        conflicting_processes = []
        
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    proc_info = proc.info
                    cmdline = ' '.join(proc_info['cmdline'] or [])
                    
                    # Look for processes that might be using RealSense
                    realsense_keywords = [
                        'realsense', 'rs', 'rs-sensor-control', 'rs-enumerate-devices',
                        'rs-data-collect', 'rs-convert', 'python', 'camera'
                    ]
                    
                    if any(keyword in cmdline.lower() for keyword in realsense_keywords):
                        # Skip our own process
                        if proc_info['pid'] != os.getpid():
                            conflicting_processes.append({
                                'pid': proc_info['pid'],
                                'name': proc_info['name'],
                                'cmdline': cmdline
                            })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                    
        except Exception as e:
            logger.warning(f"Error scanning for conflicting processes: {e}")
            
        return conflicting_processes

    def _force_release_device(self):
        """Attempt to force release the camera device."""
        try:
            logger.info("Attempting to force release camera device...")
            
            # Try to stop any existing pipeline
            if self.pipeline:
                try:
                    self.pipeline.stop()
                except:
                    pass
                self.pipeline = None
            
            # Wait a bit for the device to be released
            time.sleep(1.0)
            
            # Try to reset USB devices (Linux only)
            if os.name == 'posix':
                try:
                    # This is a more aggressive approach - reset USB device
                    ctx = rs.context()
                    devices = ctx.query_devices()
                    for device in devices:
                        if not self.serial or device.get_info(rs.camera_info.serial_number) == self.serial:
                            device.hardware_reset()
                            logger.info("Hardware reset performed on RealSense device")
                            time.sleep(2.0)  # Give more time after hardware reset
                            break
                except Exception as e:
                    logger.warning(f"Hardware reset failed: {e}")
            
        except Exception as e:
            logger.warning(f"Error during force release: {e}")

    def _rotate_image(self, image: np.ndarray) -> np.ndarray:
        """Rotate image by the specified angle."""
        if self.rotation == 0:
            return image
        elif self.rotation == 90:
            return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
        elif self.rotation == 180:
            return cv2.rotate(image, cv2.ROTATE_180)
        elif self.rotation == 270:
            return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
        else:
            logger.warning(f"Invalid rotation angle {self.rotation}, using 0")
            return image

    def _should_attempt_reconnection(self) -> bool:
        """Check if we should attempt to reconnect to the camera."""
        current_time = time.time()
        
        # If we're already connecting, don't start another attempt
        if self.connection_state == "connecting":
            return False
            
        # If we're connected and recently got frames, no need to reconnect
        if (self.connection_state == "connected" and 
            current_time - self.last_successful_frame < self.connection_timeout):
            return False
            
        # If we've exceeded max retries, use exponential backoff
        if self.connection_retry_count >= self.max_connection_retries:
            time_since_last_attempt = current_time - self.last_connection_attempt
            backoff_delay = min(
                self.connection_retry_delay * (2 ** (self.connection_retry_count - self.max_connection_retries)),
                self.max_connection_retry_delay
            )
            if time_since_last_attempt < backoff_delay:
                return False
                
        # If we're in failed state and haven't waited long enough
        if (self.connection_state == "failed" and 
            current_time - self.last_connection_attempt < self.connection_retry_delay):
            return False
            
        return True

    def _attempt_reconnection(self) -> bool:
        """Attempt to reconnect to the camera."""
        current_time = time.time()
        
        if not self._should_attempt_reconnection():
            return False
            
        logger.info(f"Attempting camera reconnection (attempt {self.connection_retry_count + 1})")
        self.connection_state = "connecting"
        self.last_connection_attempt = current_time
        self.connection_retry_count += 1
        
        try:
            # Clean up any existing pipeline
            if self.pipeline:
                try:
                    self.pipeline.stop()
                except:
                    pass
                self.pipeline = None
            
            # Wait a bit before attempting reconnection
            time.sleep(1.0)
            
            # Check if device is available
            if not self._check_device_availability():
                logger.warning("Camera device not available for reconnection")
                self.connection_state = "failed"
                return False
            
            # Try to start the pipeline
            self.pipeline = rs.pipeline()
            config = rs.config()
            
            if self.serial:
                config.enable_device(self.serial)

            config.enable_stream(rs.stream.color, self.width, self.height,
                                 rs.format.bgr8, self.fps)

            self.pipeline.start(config)
            self.align = rs.align(rs.stream.color)
            self.running = True
            self.connection_state = "connected"
            self.connection_retry_count = 0  # Reset retry count on successful connection
            self.last_successful_frame = current_time
            
            logger.info("Camera reconnection successful")
            return True
            
        except Exception as e:
            logger.warning(f"Camera reconnection failed: {e}")
            self.connection_state = "failed"
            return False

    def _update_connection_state(self):
        """Update connection state based on current conditions."""
        current_time = time.time()
        
        # If we're connected but haven't received frames recently, mark as disconnected
        if (self.connection_state == "connected" and 
            current_time - self.last_successful_frame > self.connection_timeout):
            logger.warning("Camera connection timeout - no frames received recently")
            self.connection_state = "disconnected"
            self.running = False

    def start(self):
        """Start the RealSense pipeline with retry mechanism and device management."""
        
        # Check for conflicting processes first
        conflicting_processes = self._find_conflicting_processes()
        if conflicting_processes:
            logger.warning("Found potentially conflicting processes:")
            for proc in conflicting_processes:
                logger.warning(f"  PID {proc['pid']}: {proc['name']} - {proc['cmdline'][:100]}...")
        
        # Try to start with retry mechanism
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                logger.info(f"Starting RealSense pipeline (attempt {attempt + 1}/{self.max_retries})")
                
                # Check device availability before attempting to start
                if not self._check_device_availability():
                    raise CameraError("RealSense device is not available or in use")
                
                # Create new pipeline and config
                self.pipeline = rs.pipeline()
                config = rs.config()
                
                if self.serial:
                    config.enable_device(self.serial)

                config.enable_stream(rs.stream.color, self.width, self.height,
                                     rs.format.bgr8, self.fps)

                # Attempt to start the pipeline
                self.pipeline.start(config)
                self.align = rs.align(rs.stream.color)
                self.running = True
                self.connection_state = "connected"
                self.last_successful_frame = time.time()
                self.connection_retry_count = 0
                
                logger.info("RealSense pipeline started successfully")
                return  # Success, exit retry loop
                
            except Exception as e:
                last_exception = e
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
                
                # Clean up failed pipeline
                if self.pipeline:
                    try:
                        self.pipeline.stop()
                    except:
                        pass
                    self.pipeline = None
                
                # If this is a "device busy" error and we have retries left, try to force release
                if "device or resource busy" in str(e).lower() and attempt < self.max_retries - 1:
                    logger.info("Device busy detected, attempting to force release...")
                    self._force_release_device()
                    time.sleep(self.retry_delay * (attempt + 1))  # Exponential backoff
                elif attempt < self.max_retries - 1:
                    # For other errors, wait before retrying
                    time.sleep(self.retry_delay)
        
        # All retries failed - set state to failed
        self.connection_state = "failed"
        self.last_connection_attempt = time.time()
        
        error_msg = f"Failed to start RealSense after {self.max_retries} attempts"
        if conflicting_processes:
            error_msg += f". Conflicting processes found: {[p['pid'] for p in conflicting_processes]}"
        error_msg += f". Last error: {last_exception}"
        
        raise CameraError(error_msg)

    def stop(self):
        """Stop the RealSense pipeline and ensure proper cleanup."""
        self.running = False
        self.connection_state = "disconnected"
        
        if self.pipeline:
            try:
                logger.info("Stopping RealSense pipeline...")
                self.pipeline.stop()
                logger.info("RealSense pipeline stopped successfully")
            except Exception as e:
                logger.warning(f"Error stopping pipeline: {e}")
            finally:
                self.pipeline = None
        
        # Additional cleanup
        self.align = None
        
        # Reset connection tracking
        self.connection_retry_count = 0
        self.last_connection_attempt = 0.0
        self.last_successful_frame = 0.0
        
        # Wait a moment to ensure the device is fully released
        time.sleep(0.5)
        
        logger.info("RealSense stopped and cleaned up")

    def get_frame(self):
        current_time = time.time()
        
        # Update connection state
        self._update_connection_state()
        
        # If we're not connected, try to reconnect
        if self.connection_state != "connected":
            if self._attempt_reconnection():
                # Reconnection successful, continue to get frame
                pass
            else:
                # Reconnection failed, return None without logging errors
                return None, current_time
        
        # If we don't have a valid pipeline, return None
        if not self.running or not self.pipeline:
            return None, current_time

        try:
            # Use shorter timeout to avoid blocking for too long
            frames = self.pipeline.wait_for_frames(timeout_ms=1000)
            frames = self.align.process(frames)
            color_frame = frames.get_color_frame()

            if not color_frame:
                logger.debug("No color frame received")
                return None, current_time
                
            color_image = np.asanyarray(color_frame.get_data())
            ts = frames.get_timestamp() / 1000.0  # ms → s
            
            # Apply rotation if needed
            if self.rotation != 0:
                color_image = self._rotate_image(color_image)
            
            # Update successful frame timestamp
            self.last_successful_frame = current_time
            
            return color_image, ts
            
        except Exception as e:
            # Check if this is a "pipeline not started" error
            if "cannot be called before start" in str(e).lower():
                logger.warning("Pipeline not properly started, marking as disconnected")
                self.connection_state = "disconnected"
                self.running = False
                return None, current_time
            
            # Only log as error if it's not a timeout (which is normal)
            if "timeout" in str(e).lower() or "didn't arrive" in str(e).lower():
                logger.debug(f"RealSense frame timeout (normal): {e}")
            else:
                logger.warning(f"RealSense frame grab failed: {e}")
                # Mark as disconnected if we get persistent errors
                self.connection_state = "disconnected"
                self.running = False
                
            return None, current_time

class CameraService:
    """Высокоуровневый сервис, управляющий backend’ом и восстановлением."""

    def __init__(self, backend: CameraBackend, restart_interval: int = 3600):
        self.backend = backend
        self.restart_interval = restart_interval
        self.last_restart = 0.0
        self.running = False
        self.lock = threading.Lock()
        self.worker: Optional[threading.Thread] = None
        self.frame: Tuple[Optional[np.ndarray], float] = (None, 0.0)

    def start(self):
        """Start the camera service with better error handling."""
        try:
            # Always start the worker thread, even if backend fails
            self.running = True
            self.last_restart = time.time()
            self.worker = threading.Thread(target=self._loop, daemon=True)
            self.worker.start()
            
            # Try to start the backend, but don't fail if it doesn't work
            try:
                self.backend.start()
                logger.info("CameraService started successfully with camera")
            except Exception as backend_error:
                logger.warning(f"CameraService started without camera: {backend_error}")
                # Set backend to disconnected state so it will try to reconnect
                if hasattr(self.backend, 'connection_state'):
                    self.backend.connection_state = "disconnected"
                    self.backend.running = False
                logger.info("CameraService will attempt to reconnect when camera becomes available")
                
        except Exception as e:
            logger.error(f"Failed to start CameraService: {e}")
            # Ensure we don't leave the service in a partially started state
            self.running = False
            if self.worker and self.worker.is_alive():
                self.worker.join(timeout=1)
            self.worker = None
            raise

    def stop(self):
        self.running = False
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=2)
        self.backend.stop()
        logger.info("CameraService stopped")

    def _loop(self):
        consecutive_errors = 0
        max_consecutive_errors = 10
        consecutive_timeouts = 0
        max_consecutive_timeouts = 50  # Allow more timeouts before considering it an error
        last_connection_log = 0.0
        connection_log_interval = 30.0  # Log connection status every 30 seconds
        last_camera_check = 0.0
        camera_check_interval = 10.0  # Check for camera availability every 10 seconds
        
        while self.running:
            try:
                # Check if we need to attempt reconnection (for stub services)
                current_time = time.time()
                if (not self.backend.running and 
                    hasattr(self.backend, 'connection_state') and 
                    self.backend.connection_state == "disconnected" and
                    current_time - last_camera_check > camera_check_interval):
                    
                    logger.info("Checking for camera availability...")
                    last_camera_check = current_time
                    
                    if self.backend._check_device_availability():
                        logger.info("Camera detected, attempting to start service")
                        try:
                            self.backend.start()
                            logger.info("Camera service started successfully")
                        except Exception as e:
                            logger.warning(f"Failed to start camera service: {e}")
                
                color, ts = self.backend.get_frame()
                with self.lock:
                    self.frame = (color, ts)
                
                # Reset counters on successful frame
                if color is not None:
                    consecutive_errors = 0
                    consecutive_timeouts = 0
                else:
                    # Count timeouts separately from other errors
                    consecutive_timeouts += 1

                    # Log connection status periodically when no frames
                    if (current_time - last_connection_log > connection_log_interval):
                        if hasattr(self.backend, 'connection_state'):
                            logger.info(f"Camera connection state: {self.backend.connection_state}")
                        last_connection_log = current_time

                # Periodic restart (only if we have a working connection)
                if (time.time() - self.last_restart > self.restart_interval and 
                    hasattr(self.backend, 'connection_state') and 
                    self.backend.connection_state == "connected"):
                    logger.info("Restart interval reached, restarting backend")
                    self.backend.stop()
                    time.sleep(1)
                    self.backend.start()
                    self.last_restart = time.time()
                    consecutive_errors = 0
                    consecutive_timeouts = 0
                    
            except Exception as e:
                consecutive_errors += 1
                
                # Only log as error if it's not a timeout
                if "timeout" in str(e).lower() or "didn't arrive" in str(e).lower():
                    if consecutive_timeouts >= max_consecutive_timeouts:
                        logger.warning(f"Too many consecutive timeouts ({consecutive_timeouts}), checking camera health")
                        consecutive_timeouts = 0
                else:
                    logger.error(f"Camera loop error ({consecutive_errors}/{max_consecutive_errors}): {e}")
                
                # If too many consecutive errors (not timeouts), try to restart the backend
                if consecutive_errors >= max_consecutive_errors:
                    logger.warning("Too many consecutive errors, attempting backend restart")
                    try:
                        self.backend.stop()
                        time.sleep(2)
                        self.backend.start()
                        consecutive_errors = 0
                        consecutive_timeouts = 0
                        logger.info("Backend restarted successfully")
                    except Exception as restart_error:
                        logger.error(f"Failed to restart backend: {restart_error}")
                
                time.sleep(0.1)  # Shorter sleep for more responsive handling

    def get_latest(self) -> Tuple[Optional[np.ndarray], float]:
        with self.lock:
            return self.frame
    
    def get_connection_status(self) -> dict:
        """Get detailed connection status information."""
        if hasattr(self.backend, 'connection_state'):
            return {
                "state": self.backend.connection_state,
                "retry_count": getattr(self.backend, 'connection_retry_count', 0),
                "last_attempt": getattr(self.backend, 'last_connection_attempt', 0),
                "last_successful_frame": getattr(self.backend, 'last_successful_frame', 0),
                "running": self.backend.running
            }
        return {"state": "unknown", "running": False}


if __name__ == "__main__":
    backend = RealSenseBackend(width=640, height=480, fps=30)
    service = CameraService(backend)
    service.start()

    try:
        while True:
            color, ts = service.get_latest()
            if color is not None:
                cv2.imshow("Color", color)
            if cv2.waitKey(1) == 27:
                break
    finally:
        service.stop()
        cv2.destroyAllWindows()
