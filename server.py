from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import Dict, Optional, List, Set
import json
import uuid
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Store WebSocket connections and their offers/answers
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Dict[str, WebSocket]] = {}
        self.offers: Dict[str, str] = {}
        self.active_channel_ids: Set[str] = set()  # Track active channel IDs

    async def connect(self, websocket: WebSocket, channel_id: str, client_type: str):
        await websocket.accept()
        if channel_id not in self.active_connections:
            self.active_connections[channel_id] = {}
        self.active_connections[channel_id][client_type] = websocket
        
        # Add channel to active list when host connects
        if client_type == "host":
            self.active_channel_ids.add(channel_id)
            print(f"Host connected with channel ID: {channel_id}")
            print(f"Active channels: {self.active_channel_ids}")

    async def disconnect(self, channel_id: str, client_type: str):
        if channel_id in self.active_connections:
            if client_type in self.active_connections[channel_id]:
                del self.active_connections[channel_id][client_type]
            if not self.active_connections[channel_id]:
                del self.active_connections[channel_id]
            
            # Remove channel from active list when host disconnects
            if client_type == "host":
                self.active_channel_ids.discard(channel_id)
                if channel_id in self.offers:
                    del self.offers[channel_id]
                print(f"Host disconnected from channel ID: {channel_id}")
                print(f"Active channels: {self.active_channel_ids}")

    async def send_message(self, message: str, channel_id: str, client_type: str):
        if channel_id in self.active_connections:
            if client_type in self.active_connections[channel_id]:
                await self.active_connections[channel_id][client_type].send_text(message)

    def get_active_channels(self) -> List[str]:
        return list(self.active_channel_ids)

manager = ConnectionManager()

@app.websocket("/ws/{channel_id}/{client_type}")
async def websocket_endpoint(websocket: WebSocket, channel_id: str, client_type: str):
    await manager.connect(websocket, channel_id, client_type)
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if client_type == "host":
                if message["type"] == "offer":
                    # Store the offer and send it to the user if connected
                    manager.offers[channel_id] = message["offer"]
                    await manager.send_message(
                        json.dumps({"type": "offer", "offer": message["offer"]}),
                        channel_id,
                        "user"
                    )
            
            elif client_type == "user":
                if message["type"] == "answer":
                    # Forward the answer to the host
                    await manager.send_message(
                        json.dumps({"type": "answer", "answer": message["answer"]}),
                        channel_id,
                        "host"
                    )
                elif message["type"] == "get_offer":
                    # Send stored offer to user
                    if channel_id in manager.offers:
                        await manager.send_message(
                            json.dumps({"type": "offer", "offer": manager.offers[channel_id]}),
                            channel_id,
                            "user"
                        )
                elif message["type"] == "get_channels":
                    # Send list of active channels to the user
                    active_channels = manager.get_active_channels()
                    await websocket.send_text(
                        json.dumps({"type": "channels", "channels": active_channels})
                    )
    
    except WebSocketDisconnect:
        await manager.disconnect(channel_id, client_type)

@app.websocket("/ws/discover")
async def discover_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if message["type"] == "get_channels":
                # Send list of active channels to the user
                active_channels = manager.get_active_channels()
                await websocket.send_text(
                    json.dumps({"type": "channels", "channels": active_channels})
                )
    except WebSocketDisconnect:
        pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)