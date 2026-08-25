import socket
import json
import random
import os
from typing import Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
import uvicorn

app = FastAPI()

def get_local_ip() -> str:
    """Detects the primary LAN IP address of this machine."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Dummy connect to determine routing interface (does not send data)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

LOCAL_IP = get_local_ip()
PORT = 8000

class ConnectionManager:
    def __init__(self):
        # Maps peer_id -> {"ws": WebSocket, "name": str, "code": str, "ip": str}
        self.active_peers: Dict[str, dict] = {}

    def generate_unique_code(self) -> str:
        while True:
            code = f"{random.randint(1000, 9999)}"
            if not any(peer["code"] == code for peer in self.active_peers.values()):
                return code

    async def connect(self, websocket: WebSocket, peer_id: str, name: str, client_ip: str) -> str:
        await websocket.accept()
        code = self.generate_unique_code()
        self.active_peers[peer_id] = {
            "ws": websocket,
            "name": name,
            "code": code,
            "ip": client_ip
        }
        await self.broadcast_peer_list()
        return code

    async def disconnect(self, peer_id: str):
        if peer_id in self.active_peers:
            del self.active_peers[peer_id]
            await self.broadcast_peer_list()

    async def broadcast_peer_list(self):
        peer_list = [
            {
                "id": pid,
                "name": data["name"],
                "code": data["code"]
            }
            for pid, data in self.active_peers.items()
        ]
        
        payload = json.dumps({
            "type": "peer_list",
            "peers": peer_list
        })

        for pid, data in list(self.active_peers.items()):
            try:
                await data["ws"].send_text(payload)
            except Exception:
                pass

    async def send_to_peer(self, target_id: str, message: dict) -> bool:
        if target_id in self.active_peers:
            try:
                await self.active_peers[target_id]["ws"].send_text(json.dumps(message))
                return True
            except Exception:
                return False
        return False

    def find_peer_by_code(self, code: str) -> str | None:
        for pid, data in self.active_peers.items():
            if data["code"] == code:
                return pid
        return None

manager = ConnectionManager()

@app.get("/")
async def get_index():
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    return FileResponse(index_path)

@app.get("/api/info")
async def get_info():
    return {
        "local_ip": LOCAL_IP,
        "port": PORT,
        "url": f"http://{LOCAL_IP}:{PORT}"
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    client_ip = websocket.client.host if websocket.client else "unknown"
    peer_id = None

    try:
        # Handshake: Expect client hello with name and id
        init_raw = await websocket.receive_text()
        init_data = json.loads(init_raw)

        if init_data.get("type") != "register":
            await websocket.close()
            return

        peer_id = init_data.get("id")
        device_name = init_data.get("name", "Unknown Device")

        assigned_code = await manager.connect(websocket, peer_id, device_name, client_ip)

        # Notify the client of their assigned code & network URL
        await websocket.send_text(json.dumps({
            "type": "registered",
            "id": peer_id,
            "code": assigned_code,
            "local_url": f"http://{LOCAL_IP}:{PORT}"
        }))

        # Message routing loop
        while True:
            data_raw = await websocket.receive_text()
            data = json.loads(data_raw)
            msg_type = data.get("type")

            if msg_type == "send_direct":
                target_id = data.get("target_id")
                payload = data.get("payload")
                
                success = await manager.send_to_peer(target_id, {
                    "type": "incoming_data",
                    "sender_id": peer_id,
                    "sender_name": device_name,
                    "payload": payload
                })

                await websocket.send_text(json.dumps({
                    "type": "send_ack",
                    "success": success
                }))

            elif msg_type == "send_by_code":
                target_code = data.get("target_code", "").strip()
                payload = data.get("payload")
                target_id = manager.find_peer_by_code(target_code)

                if target_id and target_id != peer_id:
                    success = await manager.send_to_peer(target_id, {
                        "type": "incoming_data",
                        "sender_id": peer_id,
                        "sender_name": device_name,
                        "payload": payload
                    })
                else:
                    success = False

                await websocket.send_text(json.dumps({
                    "type": "send_ack",
                    "success": success,
                    "error": "Device not found with that 4-digit code" if not success else None
                }))

            elif msg_type == "update_name":
                new_name = data.get("name", device_name).strip()
                if new_name and peer_id in manager.active_peers:
                    device_name = new_name
                    manager.active_peers[peer_id]["name"] = new_name
                    await manager.broadcast_peer_list()

    except WebSocketDisconnect:
        if peer_id:
            await manager.disconnect(peer_id)
    except Exception:
        if peer_id:
            await manager.disconnect(peer_id)

if __name__ == "__main__":
    print("=" * 60)
    print(f"🚀 LocalDrop Hub running on LAN!")
    print(f"📡 Access URL: http://{LOCAL_IP}:{PORT}")
    print(f"📲 Open that address in any browser on the same Wi-Fi.")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")