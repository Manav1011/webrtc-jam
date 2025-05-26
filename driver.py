import asyncio
import websockets
import uuid
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceServer, RTCConfiguration
import subprocess
import numpy as np
import json
from asyncio import Lock
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
from pathlib import Path
from pydantic import BaseModel

# Create necessary directories
Path("templates").mkdir(exist_ok=True)
Path("static").mkdir(exist_ok=True)

# Create FastAPI app
app = FastAPI()

# Create templates directory and mount static files
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Common configurations
ice_servers = [
    RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
    RTCIceServer(
        urls=["turn:your.turn.server:3478"],
        username="your_username",
        credential="your_password"
    )
]
rtc_config = RTCConfiguration(iceServers=ice_servers)

# Host-specific variables
participants = {}
data_channels = {}
cleanup_locks = {}
DEVICE = None
current_server_mode = None
current_channel_id = None
server_lock = Lock()
active_connection = None

# Request models
class ConnectionRequest(BaseModel):
    mode: str
    channel_id: str

class DisconnectRequest(BaseModel):
    mode: str
    channel_id: str

# FastAPI routes
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/check-mode")
async def check_mode():
    global active_connection
    # Clean up completed or cancelled connections
    if active_connection and (active_connection.done() or active_connection.cancelled()):
        await clear_server_mode()
    
    return {
        "mode": current_server_mode,
        "channel_id": current_channel_id,
        "active": bool(active_connection and not active_connection.done())
    }

class ServerModeError(Exception):
    pass

async def check_and_set_server_mode(mode: str, channel_id: str) -> bool:
    global current_server_mode, current_channel_id, active_connection
    
    async with server_lock:
        if current_server_mode:
            if current_server_mode == mode and current_channel_id == channel_id and active_connection and not active_connection.done():
                # Already connected with same mode and channel
                raise ServerModeError(f"Already connected in {current_server_mode} mode")
            elif current_server_mode != mode:
                # Different mode attempting to connect
                raise ServerModeError(f"Server is already running in {current_server_mode} mode")
            elif active_connection and not active_connection.done():
                # Connection exists and is still running
                raise ServerModeError(f"A connection is already active")
            else:
                # Previous connection is done, allow new connection
                current_server_mode = mode
                current_channel_id = channel_id
                return True
        else:
            # No active mode - allow connection
            current_server_mode = mode
            current_channel_id = channel_id
            return True

async def clear_server_mode():
    global current_server_mode, current_channel_id, active_connection
    async with server_lock:
        current_server_mode = None
        current_channel_id = None
        if active_connection:
            active_connection.cancel()
            active_connection = None

@app.post("/connect")
async def connect(request: ConnectionRequest):
    global active_connection
    
    try:
        # Check if mode can be activated
        await check_and_set_server_mode(request.mode, request.channel_id)
        
        # Cancel any existing connection
        if active_connection and not active_connection.done():
            active_connection.cancel()
            await asyncio.sleep(0.1)  # Give it a moment to clean up
        
        # Start new connection
        if request.mode == "host":
            active_connection = asyncio.create_task(host_connect(request.channel_id))
        else:
            active_connection = asyncio.create_task(user_connect(request.channel_id))
        
        return JSONResponse({
            "status": "success", 
            "message": f"Connected as {request.mode}",
            "mode": current_server_mode,
            "channel_id": current_channel_id
        })
    except ServerModeError as e:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": str(e)}
        )
    except Exception as e:
        await clear_server_mode()  # Clear mode if connection fails
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": str(e)}
        )

@app.post("/disconnect")
async def disconnect(request: DisconnectRequest):
    try:
        if request.mode == "host":
            # Close all participant connections
            for participant_id in list(participants.keys()):
                pc = participants[participant_id]
                await delete_participant(pc)
        else:
            # Close participant connection
            if request.channel_id in participants:
                pc = participants[request.channel_id]
                await delete_participant(pc)
        
        await clear_server_mode()
        return JSONResponse({
            "status": "success", 
            "message": "Disconnected successfully"
        })
    except Exception as e:
        return JSONResponse({
            "status": "error", 
            "message": str(e)
        })

# Host-specific functions
async def delete_participant(pc):
    try:
        participant_key = next((k for k, v in participants.items() if v == pc), None)
        if not participant_key:
            print("Participant already deleted")
            return

        if participant_key in cleanup_locks:
            print(f"Cleanup already in progress for {participant_key}")
            return
            
        cleanup_locks[participant_key] = True
        print(f"Starting cleanup for participant {participant_key}")
        
        try:
            if participant_key in data_channels:
                channel = data_channels[participant_key]
                if channel and channel.readyState != "closed":
                    try:
                        channel._data_channel._send_queued = []
                        channel.close()
                    except Exception as e:
                        print(f"Error closing data channel: {e}")
                    await asyncio.sleep(0.2)
                data_channels.pop(participant_key, None)

            if pc.connectionState != "closed":
                for transceiver in pc.getTransceivers():
                    if transceiver.sender:
                        try:
                            await transceiver.sender.replaceTrack(None)
                        except:
                            pass

                try:
                    await pc.close()
                    await asyncio.sleep(0.2)
                except Exception as e:
                    print(f"Error closing peer connection: {e}")

            participants.pop(participant_key, None)
            print(f"Participant {participant_key} deleted successfully")

        finally:
            cleanup_locks.pop(participant_key, None)
            
    except Exception as e:
        print(f"Error during participant deletion: {e}")
        if participant_key in cleanup_locks:
            cleanup_locks.pop(participant_key, None)

def is_silence(pcm_data, threshold=500):
    try:
        audio_array = np.frombuffer(pcm_data, dtype=np.int16)
        rms = np.sqrt(np.mean(np.square(audio_array.astype(np.float32))))
        return rms < threshold
    except Exception as e:
        print(f"Error checking for silence: {e}")
        return False

async def stream_audio(participant_id, data_channel):
    if participant_id not in data_channels:
        print(f"Data channel for {participant_id} no longer exists")
        return

    process = None
    try:
        process = subprocess.Popen(
            [
                "parec",
                "--device", DEVICE,
                "--format=s16le",
                "--rate", "48000",
                "--channels", "2",
                "--latency-msec=1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=3840,
        )
        
        while True:        
            if (participant_id not in data_channels or 
                data_channels[participant_id] != data_channel or 
                data_channel.readyState != "open"):
                print(f"Data channel for {participant_id} no longer available or not open")
                break

            try:
                pcm_data = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: process.stdout.read(3840)
                )
                if not pcm_data:
                    break
                if is_silence(pcm_data):
                    print("silence")
                    continue
                else:
                    print("No silence")

                if data_channel.readyState == "open":
                    try:
                        data_channel.send(pcm_data)
                        del pcm_data
                    except Exception as e:
                        if "not connected" not in str(e):
                            print(f"Error sending audio data: {e}")
                        break
                else:
                    break
                    
                await asyncio.sleep(0)
            except Exception as e:
                if "not connected" not in str(e):
                    print(f"Error streaming audio: {e}")
                break
    finally:
        if process and process.poll() is None:
            try:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
            except Exception as e:
                print(f"Error cleaning up audio process: {e}")
                
        if participant_id in data_channels:
            data_channels.pop(participant_id, None)

async def host_connect(channel_id: str):
    global DEVICE
    try:
        DEVICE = get_default_monitor()    
        print(f"\nSelected DEVICE: {DEVICE}")
        
        uri = "wss://jam-ws-server.onrender.com/ws"
        async with websockets.connect(uri) as ws:
            message = {
                "client": "host",
                "channel_id": channel_id,
                "type": "connection"
            }
            await ws.send(json.dumps(message))
            
            try:
                async for message in ws:
                    # Check if connection is still active
                    if not current_server_mode or current_channel_id != channel_id:
                        print("Connection no longer active, closing...")
                        break
                        
                    data = json.loads(message)
                    if data['type'] == 'send_offer':
                        participant_id = data['participant_id']
                        pc = RTCPeerConnection(rtc_config)
                        
                        participants[participant_id] = pc
                        
                        try:
                            data_channel = pc.createDataChannel("audio")
                            data_channels[participant_id] = data_channel

                            @data_channel.on("open")
                            def on_datachannel_open():
                                print(f"Data channel opened for participant {participant_id}")
                                asyncio.create_task(stream_audio(participant_id, data_channel))

                            @data_channel.on("close")
                            def on_datachannel_close():
                                print(f"Data channel closed for participant {participant_id}")

                            @pc.on("connectionstatechange")
                            async def on_connectionstatechange():
                                print(f"PeerConnection state changed to: {pc.connectionState}")
                                if pc.connectionState in ["failed", "disconnected", "closed"]:
                                    print(f"Connection {pc.connectionState}")
                                    await delete_participant(pc)
                                    if not participants:
                                        await clear_server_mode()
                            
                            offer = await pc.createOffer()
                            await pc.setLocalDescription(offer)
                            await gather_complete(pc)
                            
                            message = {
                                "client": "host",
                                "type": "set_offer",
                                "participant_id": participant_id,
                                "sdp": pc.localDescription.sdp
                            }
                            await ws.send(json.dumps(message))
                            
                        except Exception as e:
                            print(f"Error setting up connection: {e}")
                            await delete_participant(pc)
                            continue
                    
                    elif data['type'] == 'set_answer':
                        try:
                            answer = RTCSessionDescription(
                                sdp=data["sdp"],
                                type='answer'
                            )
                            participant_id = data['participant_id']
                            if participant_id in participants:
                                client_pc = participants[participant_id]
                                await client_pc.setRemoteDescription(answer)
                                print("Answer set successfully")
                            else:
                                print(f"Participant {participant_id} not found for answer")
                        except Exception as e:
                            print(f"Error setting remote description: {e}")
                            
            except Exception as e:
                print(f"WebSocket error: {e}")
                await clear_server_mode()
                
    except Exception as e:
        print(f"Host connect error: {e}")
        await clear_server_mode()
    finally:
        # Ensure cleanup happens
        await clear_server_mode()

# User-specific functions
async def cleanup_connection(client_pc, audio_player, in_progress=False):
    if getattr(client_pc, '_cleanup_in_progress', False) and in_progress:
        print("Cleanup already in progress")
        return

    if in_progress:
        setattr(client_pc, '_cleanup_in_progress', True)
    
    try:
        if audio_player:
            audio_player.stop()
            
        if client_pc:
            for transceiver in client_pc.getTransceivers():
                if transceiver.sender:
                    try:
                        await transceiver.sender.replaceTrack(None)
                    except:
                        pass

            if client_pc.connectionState != "closed":
                try:
                    await client_pc.close()
                    await asyncio.sleep(0.2)
                except Exception as e:
                    print(f"Error closing peer connection: {e}")
    finally:
        if in_progress:
            setattr(client_pc, '_cleanup_in_progress', False)

async def user_connect(channel_id: str):
    audio_player = AudioPlayer()
    uri = "wss://jam-ws-server.onrender.com/ws"
    client_pc = None
    participant_id = str(uuid.uuid4())
    
    try:
        async with websockets.connect(uri) as ws:
            client_pc = RTCPeerConnection(rtc_config)
            
            @client_pc.on("connectionstatechange")
            async def on_connectionstatechange():
                print(f"Connection state changed to: {client_pc.connectionState}")
                if client_pc.connectionState in ["failed", "closed", "disconnected"]:
                    await cleanup_connection(client_pc, audio_player, True)
                    await clear_server_mode()
            
            try:
                message = {
                    "client": "participant",
                    "type": "connection",
                    "channel_id": channel_id,
                    "participant_id": participant_id
                }
                
                await ws.send(json.dumps(message))
                
                async for message in ws:
                    # Check if connection is still active
                    if not current_server_mode or current_channel_id != channel_id:
                        print("Connection no longer active, closing...")
                        break
                        
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
                                'client': 'participant',
                                'type': 'set_answer',
                                'channel_id': channel_id,
                                'participant_id': participant_id,
                                "sdp": client_pc.localDescription.sdp
                            }
                            await ws.send(json.dumps(message))
                            
                        elif data['type'] == 'not_found':
                            print(f"Channel ID {channel_id} not found")
                            
                    except Exception as e:
                        print(f"Error processing message: {e}")
                        await cleanup_connection(client_pc, audio_player, True)
                        break
                
            except Exception as e:
                print(f"WebSocket error: {e}")
                await clear_server_mode()
                
    except Exception as e:
        print(f"User connect error: {e}")
        await clear_server_mode()
    finally:
        await cleanup_connection(client_pc, audio_player, True)
        await clear_server_mode()

# Utility functions
async def gather_complete(pc):
    await asyncio.sleep(0.1)
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.1)

def get_default_monitor() -> str:
    output = subprocess.check_output(["pactl", "info"]).decode()
    for line in output.splitlines():
        if "Default Sink:" in line:
            default_sink = line.split(":")[1].strip()
            return f"{default_sink}.monitor"
    raise RuntimeError("Default sink not found")

# Audio player class
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

def main():
    # Run the FastAPI server
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    main() 