import asyncio
import websockets
import uuid
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceServer, RTCConfiguration
import subprocess
import json

ice_servers = [
    RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
    RTCIceServer(
        urls=["turn:your.turn.server:3478"],
        username="your_username",
        credential="your_password"
    )
]
rtc_config = RTCConfiguration(iceServers=ice_servers)

class AudioPlayer:
    def __init__(self, sample_rate=48000, channels=2):
        self.sample_rate = sample_rate
        self.channels = channels
        self.process = None
        self.start_process()

    def start_process(self):
        if self.process:
            self.stop()
        try:
            self.process = subprocess.Popen(
                [
                    "paplay",
                    "--rate", str(self.sample_rate),
                    "--channels", str(self.channels),
                    "--format=s16le",
                    "--raw",
                    "--latency-msec=1",
                    "--process-time-msec=1"
                ],
                stdin=subprocess.PIPE,
                bufsize=0,
            )
        except Exception as e:
            print(f"Error starting audio process: {e}")
            self.process = None

    def play(self, audio_data):
        try:
            print(audio_data)
            if self.process and self.process.poll() is None:
                self.process.stdin.write(audio_data)
                self.process.stdin.flush()
                del audio_data
            else:
                self.start_process()
        except BrokenPipeError:
            self.stop()
            self.start_process()
        except Exception as e:
            print(f"Error playing audio: {e}")
            self.stop()
                
    def stop(self):
        if self.process:
            try:
                if self.process.poll() is None:
                    try:
                        self.process.stdin.close()
                    except:
                        pass
                    try:
                        self.process.terminate()
                        self.process.wait(timeout=1)
                    except:
                        self.process.kill()
            except Exception as e:
                print(f"Error stopping audio process: {e}")
            self.process = None

async def gather_complete(pc):
    await asyncio.sleep(0.1)
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.1)

async def cleanup_connection(client_pc, audio_player, in_progress=False):
    """Helper function to clean up resources with protection against double cleanup"""
    if getattr(client_pc, '_cleanup_in_progress', False) and in_progress:
        print("Cleanup already in progress")
        return

    if in_progress:
        setattr(client_pc, '_cleanup_in_progress', True)
    
    try:
        if audio_player:
            audio_player.stop()
            
        if client_pc:
            # Clear any pending operations on transceivers
            for transceiver in client_pc.getTransceivers():
                if transceiver.sender:
                    try:
                        await transceiver.sender.replaceTrack(None)
                    except:
                        pass

            if client_pc.connectionState != "closed":
                try:
                    await client_pc.close()
                    await asyncio.sleep(0.2)  # Give time for cleanup
                except Exception as e:
                    print(f"Error closing peer connection: {e}")
    finally:
        if in_progress:
            setattr(client_pc, '_cleanup_in_progress', False)

async def connect():
    audio_player = AudioPlayer()
    uri = "wss://jam-ws-server.onrender.com/ws"
    client_pc = None
    
    try:
        async with websockets.connect(uri) as websocket:
            client_pc = RTCPeerConnection(rtc_config)
            
            @client_pc.on("iceconnectionstatechange")
            def on_iceconnectionstatechange():
                print(f"ICE connection state: {client_pc.iceConnectionState}")
                
            @client_pc.on("connectionstatechange")
            async def on_connectionstatechange():
                print(f"Connection state changed to: {client_pc.connectionState}")
                if client_pc.connectionState in ["failed", "closed", "disconnected"]:
                    await cleanup_connection(client_pc, audio_player, True)
            
            @client_pc.on("datachannel")
            def on_datachannel(channel):
                print(f"Data channel {channel.label} received")

                @channel.on("message")
                def on_message(message):
                    try:
                        if channel.readyState == "open":
                            audio_player.play(message)
                            del message  # Help with garbage collection
                    except Exception as e:
                        if "not connected" not in str(e):  # Ignore expected disconnection errors
                            print(f"Error handling audio message: {e}")

                @channel.on("close")
                def on_close():
                    print("Data channel closed")
                    audio_player.stop()
            
            channel_id = input("Enter Channel ID to join: ")
            participant_id = str(uuid.uuid4())
            
            try:
                message = {
                    "client":"participant",
                    "type": "connection",
                    "channel_id":channel_id,
                    "participant_id":participant_id
                }
                
                await websocket.send(json.dumps(message))
                
                async for message in websocket:
                    try:
                        data = json.loads(message)
                        if data['type'] == 'set_offer':
                            offer = RTCSessionDescription(
                                sdp=data["sdp"],
                                type='offer'
                            )
                            
                            await client_pc.setRemoteDescription(offer)
                            answer = await client_pc.createAnswer()
                            await client_pc.setLocalDescription(answer)
                            await gather_complete(client_pc)
                            
                            message = {
                                'client':'participant',
                                'type':'set_answer',
                                'channel_id':channel_id,
                                'participant_id':participant_id,
                                "sdp":client_pc.localDescription.sdp
                            }
                            await websocket.send(json.dumps(message))
                            
                        elif data['type'] == 'not_found':
                            print(f"Channel ID {channel_id} not found. Please enter a valid Channel ID.")
                            channel_id = input("Enter Channel ID to join: ")
                            message = {
                                "client":"participant",
                                "type": "connection",
                                "channel_id":channel_id,
                                "participant_id":participant_id
                            }
                            await websocket.send(json.dumps(message))
                    except Exception as e:
                        print(f"Error processing message: {e}")
                        await cleanup_connection(client_pc, audio_player, True)
                        break
                
                # Keep connection alive
                while True:
                    await asyncio.sleep(1)
                    
            except Exception as e:
                print(f"Connection error: {e}")
                await cleanup_connection(client_pc, audio_player, True)
                
    except Exception as e:
        print(f"Fatal error: {e}")
    finally:
        await cleanup_connection(client_pc, audio_player, True)