#!/usr/bin/env python3
"""
Camera Status Monitor
Simple script to monitor camera connection status and health
"""

import requests
import time
import json
from datetime import datetime

def get_camera_status(base_url="http://localhost:8104"):
    """Get camera status from the service."""
    try:
        response = requests.get(f"{base_url}/camera/status", timeout=5)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        return {"error": str(e), "status": "connection_failed"}

def format_timestamp(timestamp):
    """Format timestamp for display."""
    if timestamp == 0:
        return "Never"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

def print_status(status):
    """Print formatted status information."""
    print(f"\n{'='*60}")
    print(f"Camera Status - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    
    if "error" in status:
        print(f"❌ Error: {status['error']}")
        return
    
    if status.get("status") == "error":
        print(f"❌ Service Error: {status.get('message', 'Unknown error')}")
        return
    
    # Connection state
    state = status.get("connection_state", "unknown")
    state_emoji = {
        "connected": "✅",
        "connecting": "🔄", 
        "disconnected": "❌",
        "failed": "💥",
        "unknown": "❓"
    }.get(state, "❓")
    
    print(f"{state_emoji} Connection State: {state.upper()}")
    print(f"🔧 Running: {'Yes' if status.get('running', False) else 'No'}")
    print(f"📊 Has Frame: {'Yes' if status.get('has_frame', False) else 'No'}")
    print(f"🔄 Retry Count: {status.get('retry_count', 0)}")
    print(f"⏰ Last Attempt: {format_timestamp(status.get('last_attempt', 0))}")
    print(f"✅ Last Success: {format_timestamp(status.get('last_successful_frame', 0))}")
    
    if status.get('frame_timestamp'):
        print(f"📷 Frame Time: {format_timestamp(status.get('frame_timestamp', 0))}")

def main():
    """Main monitoring loop."""
    print("🎥 Camera Status Monitor")
    print("Press Ctrl+C to stop")
    
    try:
        while True:
            status = get_camera_status()
            print_status(status)
            
            # Wait before next check
            time.sleep(10)
            
    except KeyboardInterrupt:
        print("\n👋 Monitor stopped")

if __name__ == "__main__":
    main()
