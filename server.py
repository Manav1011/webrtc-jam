import asyncio
import json
import websockets


# hosts = {}
active_channels = {}
clients = {}

async def remove_from_dict(websocket):
    host_key = next((k for k, v in active_channels.items() if v == websocket), None)
    if host_key:
        del active_channels[host_key]
        
    client_key = next((k for k, v in clients.items() if v == websocket), None)
    if client_key:
        del clients[client_key]
    if websocket in clients:
        del clients[websocket]
    print(f"Removed {websocket} from all dictionaries.")

# Handler for each client connection
async def echo(websocket, path):
    try:
        async for message in websocket:
            message = json.loads(message)
            if message["client"] == "host":
                if message['type'] == 'connection':
                    active_channels[message['channel_id']] = websocket
                if message["type"] == 'set_offer':
                    participant_id = message['participant_id']
                    client_socket = clients[participant_id]
                    message = {
                        "type":"set_offer",
                        "sdp":message['sdp']
                    }
                    await client_socket.send(json.dumps(message))
                    
                    
            elif message["client"] == "participant":
                if message['type'] == 'connection':
                    channel_id = message['channel_id']
                    if channel_id in active_channels:
                        host = active_channels[channel_id]
                        participant_id = message['participant_id']
                        # set client config
                        clients[participant_id] = websocket
                        # Clients wants to connect
                        message = {
                            'type':'send_offer',
                            "participant_id":participant_id
                        }
                        # Send to the host to generate RTC object
                        await host.send(json.dumps(message))
                    else:
                        await websocket.send(json.dumps({
                            "type": "not_found",
                            "message": f"Channel ID {channel_id} not found."
                        }))
                if message['type'] == 'set_answer':
                    host = active_channels[message['channel_id']]
                    message = {
                        "type":"set_answer",
                        "sdp": message["sdp"],
                        "participant_id":message["participant_id"]
                    }
                    await host.send(json.dumps(message))
                    
    except websockets.exceptions.ConnectionClosedOK:
        await remove_from_dict(websocket)
        print("WebSocket closed normally.")
    except websockets.exceptions.ConnectionClosedError:
        await remove_from_dict(websocket)
        print("WebSocket closed with error.")
    finally:
        await remove_from_dict(websocket)
        print("WebSocket connection closed.")
# Main function to start the WebSocket server
async def main():
    async with websockets.serve(echo, "0.0.0.0", 8765):
        print("WebSocket server started on ws://localhost:8765")
        await asyncio.Future()  # run forever

# Run the server
if __name__ == "__main__":
    asyncio.run(main())