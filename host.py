import asyncio
import websockets
import uuid
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceServer, RTCConfiguration
import subprocess
import numpy as np
import json
from threading import Lock

participants = {}
data_channels = {}
cleanup_locks = {}  # Lock for each participant's cleanup
DEVICE :str | None = None
async def gather_complete(pc):
    await asyncio.sleep(0.1)
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.1)

async def delete_participant(pc):
    try:
        participant_key = next((k for k, v in participants.items() if v == pc), None)
        if not participant_key:
            print("Participant already deleted")
            return

        # Check if cleanup is already in progress
        if participant_key in cleanup_locks:
            print(f"Cleanup already in progress for {participant_key}")
            return
            
        cleanup_locks[participant_key] = True
        print(f"Starting cleanup for participant {participant_key}")
        
        try:
            # Stop audio streaming first
            if participant_key in data_channels:
                channel = data_channels[participant_key]
                if channel and channel.readyState != "closed":
                    try:
                        # Stop any pending send operations
                        channel._data_channel._send_queued = []
                        channel.close()
                    except Exception as e:
                        print(f"Error closing data channel: {e}")
                    await asyncio.sleep(0.2)  # Give more time for cleanup
                data_channels.pop(participant_key, None)

            # Close peer connection gracefully
            if pc.connectionState != "closed":
                # Clear any pending operations
                for transceiver in pc.getTransceivers():
                    if transceiver.sender:
                        try:
                            await transceiver.sender.replaceTrack(None)
                        except:
                            pass

                try:
                    await pc.close()
                    await asyncio.sleep(0.2)  # Give more time for cleanup
                except Exception as e:
                    print(f"Error closing peer connection: {e}")

            # Final cleanup
            participants.pop(participant_key, None)
            print(f"Participant {participant_key} deleted successfully")

        finally:
            cleanup_locks.pop(participant_key, None)
            
    except Exception as e:
        print(f"Error during participant deletion: {e}")
        if participant_key in cleanup_locks:
            cleanup_locks.pop(participant_key, None)

ice_servers = [
    RTCIceServer(urls=["stun:stun.l.google.com:19302"]),
    RTCIceServer(
        urls=["turn:your.turn.server:3478"],
        username="your_username",
        credential="your_password"
    )
]
rtc_config = RTCConfiguration(iceServers=ice_servers)

def is_silence(pcm_data, threshold=500):
    """
    Check if the audio data is silence by checking if the amplitude 
    is below a threshold. Returns True if the audio is considered silence.
    """
    try:
        # Convert bytes to numpy array of 16-bit integers
        audio_array = np.frombuffer(pcm_data, dtype=np.int16)
        
        # Calculate RMS (Root Mean Square) amplitude
        rms = np.sqrt(np.mean(np.square(audio_array.astype(np.float32))))
        
        # If RMS is below threshold, consider it silence
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
        
        silence_count = 0  # Counter for consecutive silence chunks
        while True:
            # Check data channel state before reading audio
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
                else:
                    print("No silence")
                # # Check if the audio data is silence
                # if is_silence(pcm_data):
                #     silence_count += 1
                #     if silence_count >= 5:  # Skip if we've seen 5 consecutive silence chunks
                #         print("silence")
                #         continue
                # else:
                #     silence_count = 0  # Reset silence counter when we get non-silence

                # Double check state before sending
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
                
        # Help with cleanup
        if participant_id in data_channels:
            data_channels.pop(participant_id, None)

async def connect():
    global DEVICE
    sources = list_pulse_sources()
    DEVICE = select_source(sources)
    print(f"\nSelected DEVICE: {DEVICE}")
    uri = "ws://localhost:8765"
    async with websockets.connect(uri) as websocket:
        channel_id = input("Add Channel ID: ")
        message = {
            "client":"host",
            "channel_id": channel_id,
            "type":"connection"
        }
        await websocket.send(json.dumps(message))
        
        async for message in websocket:
            data = json.loads(message)
            if data['type'] == 'send_offer':
                participant_id = data['participant_id']
                pc = RTCPeerConnection(rtc_config)
                
                # Store participant first so we can clean up if data channel creation fails
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
                        if pc.connectionState == "failed":
                            print("Connection failed")
                            await delete_participant(pc)
                        elif pc.connectionState == "disconnected":
                            print("Peer disconnected")
                            await delete_participant(pc)
                        elif pc.connectionState == "closed":
                            print("Connection closed")
                            await delete_participant(pc)
                            
                    offer = await pc.createOffer()
                    await pc.setLocalDescription(offer)
                    await gather_complete(pc)
                    
                    message = {
                        "client":"host",
                        "type":"set_offer",
                        "participant_id":participant_id,
                        "sdp":pc.localDescription.sdp
                    }
                    await websocket.send(json.dumps(message))
                    
                except Exception as e:
                    print(f"Error setting up connection: {e}")
                    await delete_participant(pc)
                    continue
                
            if data['type'] == 'set_answer':
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
        
        while True:
            await asyncio.sleep(1)

def list_pulse_sources() -> list[tuple[int, str]]:
    """Return a list of PulseAudio source index and names."""
    output = subprocess.check_output(["pactl", "list", "short", "sources"]).decode()
    lines = output.strip().splitlines()
    sources = []
    for line in lines:
        parts = line.split("\t")
        index = int(parts[0])
        name = parts[1]
        sources.append((index, name))
    return sources

def select_source(sources: list[tuple[int, str]]) -> str:
    """Prompt user to select a source."""
    print("\nAvailable PulseAudio Sources:\n")
    for i, (index, name) in enumerate(sources):
        print(f"[{i}] {name}")
    while True:
        try:
            choice = int(input("\nSelect source index: "))
            if 0 <= choice < len(sources):
                return sources[choice][1]
        except ValueError:
            pass
        print("Invalid choice. Try again.")

if __name__ == "__main__":
    asyncio.run(connect())
