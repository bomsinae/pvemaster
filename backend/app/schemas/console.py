from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ConsoleSessionResponse(BaseModel):
    session_id: UUID
    websocket_path: str
    protocol_token: str = Field(min_length=32, max_length=255)
    console_type: Literal["NOVNC", "TERMINAL"]
    rfb_password: str | None = Field(default=None, min_length=1, max_length=4096)
    expires_in: int
