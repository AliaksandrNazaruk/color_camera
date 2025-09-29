# app/models/offer.py
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class Offer(BaseModel):
    sdp: str
    type: str
    color_index: int = 0
    stereo_index: int = 0
    client_id: Optional[str] = None


class IceCandidate(BaseModel):
    candidate: str
    sdp_mid: Optional[str] = None
    sdp_mline_index: Optional[int] = None
    client_id: str


class IceConfig(BaseModel):
    use_turn: bool = False
    urls: List[str] = []
    username: Optional[str] = None
    credential: Optional[str] = None
    relay_only: bool = False


class ConnectionInfo(BaseModel):
    client_id: str
    connection_state: str
    ice_connection_state: str
    ice_gathering_state: str