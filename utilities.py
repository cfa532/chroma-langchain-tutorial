from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import sys
from typing import Union
from pydantic import BaseModel
from enum import Enum

MAX_TOKEN = {
    "gpt-4": 4096,
    "gpt-4-turbo": 8192
}

class RoleName(str, Enum):
    user = "user"
    admin = "admin"

class UserOut(BaseModel):
    username: str
    subscription: bool = False
    role: str = RoleName.user.value
    mid: Union[str, None] = None            # mimei id of this user
    token_count: Union[dict, None] = None    # how many takens left in user account
    token_usage: Union[dict, None] = None    # accumulated tokens used in user account
    email: Union[str, None] = None          # if present, useful for reset password
    family_name: Union[str, None] = None
    given_name: Union[str, None] = None
    template: Union[dict, None] = None

class UserIn(UserOut):
    password: str                           # the password is hashed in DB

class UserInDB(UserOut):
    hashed_password: str

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)