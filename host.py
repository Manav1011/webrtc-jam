import asyncio
import json
import uuid
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCDataChannel
import time
import signal
import sys

async def run_host():
    # Generate a unique channel ID
    channel_id = str(uuid.uuid4())
    print(f"Channel ID: {channel_id}")
    print("Share this channel ID with the user who wants to connect.")
    print("Users can also discover this channel automatically when they run the user script.")

    # Create peer connection
    pc = RTCPeerConnection()
    
    # Create a data channel
    dc = pc.createDataChannel("audio")
    
    # WebSocket connection
    ws = None
    
    # Setup graceful shutdown to properly close connections
    def signal_handler(sig, frame):
        print("\nShutting down gracefully...")
        if ws and not ws.closed:
            asyncio.create_task(ws.close())
        sys.exit(0)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    @dc.on("open")
    def on_open():
        print("Data channel is open")

    @dc.on("message")
    def on_message(message):
        print(f"Received message: {message}")
        if message == "PONG":
            print(f"[{time.strftime('%H:%M:%S')}] Received PONG response from user")

    # Create offer
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    # Connect to signaling server
    try:
        ws = await websockets.connect(f"ws://localhost:8001/ws/{channel_id}/host")
        
        # Send offer
        await ws.send(json.dumps({
            "type": "offer",
            "offer": {
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type
            }
        }))

        print("Offer sent, waiting for answer...")

        # Wait for answer
        async for message in ws:
            data = json.loads(message)
            if data["type"] == "answer":
                print("Received answer")
                answer = RTCSessionDescription(
                    sdp=data["answer"]["sdp"],
                    type=data["answer"]["type"]
                )
                await pc.setRemoteDescription(answer)
                print("Connection established!")
                break

        # Keep the connection alive with regular pings
        ping_count = 0
        while True:
            try:
                if dc.readyState == "open":
                    await asyncio.sleep(5)  # Send ping every 5 seconds
                    ping_count += 1
                    print(f"[{time.strftime('%H:%M:%S')}] Sending PING #{ping_count} to user")
                    dc.send(f"PING #{ping_count}")
                else:
                    print("Data channel closed, attempting to reconnect...")
                    await asyncio.sleep(5)
            except Exception as e:
                print(f"Error in ping loop: {e}")
                await asyncio.sleep(5)
    finally:
        # Ensure WebSocket is closed when exiting
        if ws and not ws.closed:
            await ws.close()
            print("WebSocket connection closed")

if __name__ == "__main__":
    asyncio.run(run_host())