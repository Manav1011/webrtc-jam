import asyncio
import json
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription
import time
import sys
import os

async def discover_channels():
    """Connect to the discovery endpoint and retrieve available channels"""
    channels = []
    try:
        async with websockets.connect("ws://localhost:8001/ws/discover") as ws:
            # Request available channels
            await ws.send(json.dumps({"type": "get_channels"}))
            
            # Wait for response
            response = await ws.recv()
            data = json.loads(response)
            
            if data["type"] == "channels":
                channels = data["channels"]
    except Exception as e:
        print(f"Error discovering channels: {e}")
    
    return channels

async def select_channel():
    """Continuously update channel list and prompt user for selection"""
    while True:
        # Discover available channels
        print("\nDiscovering available channels...")
        channels = await discover_channels()
        
        if not channels:
            print("No active channels found. Retrying in 5 seconds...")
            await asyncio.sleep(5)
            continue
        
        print("\nAvailable channels:")
        for i, channel in enumerate(channels, 1):
            print(f"{i}. {channel}")
        
        # Use asyncio.gather with a timeout to handle both user input and refreshing the list
        try:
            selection_task = asyncio.create_task(prompt_for_selection(channels))
            refresh_task = asyncio.create_task(asyncio.sleep(10))  # Refresh list every 10 seconds
            
            done, pending = await asyncio.wait(
                [selection_task, refresh_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # Cancel pending tasks
            for task in pending:
                task.cancel()
            
            # If selection was made, return the channel
            if selection_task in done:
                result = selection_task.result()
                if result:
                    return result
            
            # If timeout occurred, loop continues to refresh the channel list
            
        except asyncio.CancelledError:
            continue

async def prompt_for_selection(channels):
    """Prompt user to select a channel"""
    # This uses a separate thread to not block the event loop
    loop = asyncio.get_event_loop()
    selection = await loop.run_in_executor(
        None, 
        lambda: input("\nSelect a channel (number) or 'q' to quit [press Enter to refresh list]: ")
    )
    
    if selection.lower() == 'q':
        print("Exiting...")
        sys.exit(0)
    
    if not selection:  # Empty input, user wants to refresh
        return None
    
    try:
        index = int(selection) - 1
        if 0 <= index < len(channels):
            channel_id = channels[index]
            print(f"Selected channel: {channel_id}")
            return channel_id
        else:
            print("Invalid selection. Please try again.")
            return None
    except ValueError:
        print("Please enter a valid number or 'q' to quit.")
        return None

async def run_rtc_connection(channel_id):
    """Establish and maintain RTC connection with the host"""
    # Create peer connection
    pc = RTCPeerConnection()
    data_channel = None
    connection_established = False
    connection_closed = False
    
    # Set up connection state change monitoring
    @pc.on("connectionstatechange")
    def on_connectionstatechange():
        print(f"Connection state changed to: {pc.connectionState}")
        if pc.connectionState in ["failed", "closed"]:
            nonlocal connection_closed
            print(f"Connection {pc.connectionState} according to RTCPeerConnection state")
            connection_closed = True
    
    @pc.on("iceconnectionstatechange")
    def on_iceconnectionstatechange():
        print(f"ICE connection state changed to: {pc.iceConnectionState}")
        if pc.iceConnectionState in ["failed", "closed", "disconnected"]:
            print(f"ICE connection {pc.iceConnectionState}")
    
    # Handle data channel
    @pc.on("datachannel")
    def on_datachannel(channel):
        nonlocal data_channel
        data_channel = channel
        
        @channel.on("message")
        def on_message(message):
            print(f"Received from host: {message}")
            if message.startswith("PING"):
                print(f"[{time.strftime('%H:%M:%S')}] Received {message} from host")
                channel.send("PONG")
                print(f"[{time.strftime('%H:%M:%S')}] Sent PONG response to host")

        @channel.on("open")
        def on_open():
            print("Data channel is open")
            channel.send("Hello from user!")

        @channel.on("close")
        def on_close():
            nonlocal connection_closed
            print("Data channel closed by host")
            connection_closed = True

    # Connect to signaling server and establish WebRTC connection
    websocket = None
    try:
        websocket = await websockets.connect(f"ws://localhost:8001/ws/{channel_id}/user")
        
        # Request the offer
        await websocket.send(json.dumps({"type": "get_offer"}))
        
        print("Waiting for host's offer...")
        
        async for message in websocket:
            data = json.loads(message)
            if data["type"] == "offer":
                print("Received offer, creating answer...")
                offer = RTCSessionDescription(
                    sdp=data["offer"]["sdp"],
                    type=data["offer"]["type"]
                )
                await pc.setRemoteDescription(offer)
                
                # Create answer
                answer = await pc.createAnswer()
                await pc.setLocalDescription(answer)
                
                # Send answer
                await websocket.send(json.dumps({
                    "type": "answer",
                    "answer": {
                        "sdp": pc.localDescription.sdp,
                        "type": pc.localDescription.type
                    }
                }))
                
                print("Answer sent, connection established!")
                connection_established = True
                break
    finally:
        # Close WebSocket connection after establishing WebRTC connection
        if websocket and connection_established:
            print("Closing WebSocket connection to signaling server...")
            await websocket.close()
            print("WebSocket connection closed")

    # Keep the P2P connection alive (outside WebSocket context)
    print("P2P connection active, sending/receiving through data channel...")
    print(f"Initial connection state: {pc.connectionState}")
    print(f"Initial ICE connection state: {pc.iceConnectionState}")
    
    # Monitor connection status
    ping_missed_count = 0
    last_ping_time = time.time()
    
    while True:
        try:
            await asyncio.sleep(1)
            
            # Check RTCPeerConnection state
            if pc.connectionState in ["failed", "closed"]:
                print(f"Connection {pc.connectionState} - reconnecting")
                return False
                
            # Check ICE connection state
            if pc.iceConnectionState in ["failed", "disconnected"]:
                print(f"ICE connection {pc.iceConnectionState} - reconnecting")
                return False
            
            # Check for explicit connection closure
            if connection_closed:
                print("Connection explicitly closed by host or data channel")
                return False  # Signal to restart
            
            # Check data channel state
            if data_channel and data_channel.readyState != "open":
                print(f"Data channel state: {data_channel.readyState}")
                return False  # Signal to restart
            
            # Check for missed pings (if we haven't received a ping for 15 seconds)
            current_time = time.time()
            if data_channel and data_channel.readyState == "open" and current_time - last_ping_time > 15:
                ping_missed_count += 1
                print(f"No ping received for {int(current_time - last_ping_time)} seconds (warning {ping_missed_count}/3)")
                print(f"Connection state: {pc.connectionState}, ICE state: {pc.iceConnectionState}, Channel state: {data_channel.readyState}")
                
                # After 3 missed ping cycles (45 seconds total), consider connection lost
                if ping_missed_count >= 3:
                    print("Connection lost - no pings received for too long")
                    return False  # Signal to restart
                
                # Reset timer to not spam warnings
                last_ping_time = current_time
            
            # Reset ping counter if we see any activity
            if data_channel and data_channel.readyState == "open" and not connection_closed:
                if hasattr(data_channel, '_lastActivity') and data_channel._lastActivity > last_ping_time:
                    last_ping_time = data_channel._lastActivity
                    ping_missed_count = 0
                    
        except Exception as e:
            print(f"Error in connection monitor: {e}")
            await asyncio.sleep(5)
            return False  # Signal to restart on error

async def run_user(channel_id: str = None):
    while True:  # Main application loop
        try:
            # If no channel ID is provided, let user select one
            if not channel_id:
                channel_id = await select_channel()
                if not channel_id:  # User quit
                    return
            
            # Establish and maintain RTC connection
            connection_ok = await run_rtc_connection(channel_id)
            
            if not connection_ok:
                print("\n--- Connection lost, restarting ---\n")
                # Reset channel_id to force rediscovery
                channel_id = None
                # Small delay before restarting
                await asyncio.sleep(2)
            else:
                # If connection ended normally, exit loop
                break
                
        except Exception as e:
            print(f"Error in main loop: {e}")
            # Reset channel_id to force rediscovery
            channel_id = None
            await asyncio.sleep(2)

if __name__ == "__main__":
    import sys
    
    channel_id = None
    if len(sys.argv) == 2:
        channel_id = sys.argv[1]
    
    try:
        asyncio.run(run_user(channel_id))
    except KeyboardInterrupt:
        print("\nExiting...")
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)