import asyncio
import websockets
import uuid
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel


async def connect():
    uri = "ws://localhost:8765"
    async with websockets.connect(uri) as websocket:
        channel_id = str(uuid.uuid4())
        print(f"Channel ID: {channel_id}")
        print("Share this channel ID with the user who wants to connect.")
        print("Users can also discover this channel automatically when they run the user script.")

# Run the client
if __name__ == "__main__":
    asyncio.run(connect())
