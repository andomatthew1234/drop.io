import socket
import json
import random
import os
import shutil
import uuid
from datetime import datetime
from typing import Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException, Form, Response
from fastapi.responses import FileResponse
import uvicorn

app = FastAPI()

# --- Directories & Config ---
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
if not os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"time_machine_password": "admin"}, f, indent=4)

def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

LOCAL_IP = get_local_ip()
PORT = 8000

def log_transfer(action: str, details: str):
    log_path = os.path.join(UPLOAD_DIR, "_transfer_log.txt")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {action} | {details}\n")

class ConnectionManager:
    def __init__(self):
        self.active_peers: Dict[str, dict] = {}

    def generate_unique_code(self) -> str:
        while True:
            code = f"{random.randint(1000, 9999)}"
            if not any(peer["code"] == code for peer in self.active_peers.values()):
                return code

    async def connect(self, websocket: WebSocket, peer_id: str, name: str, client_ip: str, visible: bool) -> str:
        code = self.generate_unique_code()
        self.active_peers[peer_id] = {
            "ws": websocket,
            "name": name,
            "code": code,
            "ip": client_ip,
            "visible": visible
        }
        await self.broadcast_peer_list()
        return code

    async def disconnect(self, peer_id: str):
        if peer_id in self.active_peers:
            del self.active_peers[peer_id]
            await self.broadcast_peer_list()

    async def broadcast_peer_list(self):
        for target_pid, target_data in self.active_peers.items():
            visible_peers = [
                {"id": pid, "name": pdata["name"], "code": pdata["code"]}
                for pid, pdata in self.active_peers.items()
                if pid != target_pid and pdata["visible"]
            ]
            try:
                await target_data["ws"].send_text(json.dumps({"type": "peer_list", "peers": visible_peers}))
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

# --- HTTP Endpoints ---

@app.get("/")
async def get_index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    file_id = str(uuid.uuid4())
    file_path = os.path.join(UPLOAD_DIR, file_id)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    size = os.path.getsize(file_path)
    log_transfer("MEDIA_UPLOAD", f"Filename: {file.filename} | Size: {size} bytes | Saved as: {file_id}")
    return {"file_id": file_id, "filename": file.filename, "size": size}

@app.get("/download/{file_id}")
async def download_file(file_id: str, filename: str = None):
    target_name = filename if filename else file_id
    file_path = os.path.join(UPLOAD_DIR, file_id)
    if not os.path.exists(file_path):
        return {"error": "File not found."}
    
    log_transfer("DOWNLOAD", f"File: {target_name} downloaded via HTTP.")
    return FileResponse(file_path, filename=target_name)

@app.get("/api/timemachine")
async def time_machine(password: str):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        raise HTTPException(status_code=500, detail="Config error on server")

    if password != config.get("time_machine_password"):
        raise HTTPException(status_code=403, detail="Invalid password")
    
    files = []
    for f_name in os.listdir(UPLOAD_DIR):
        if f_name == "_transfer_log.txt" or os.path.isdir(os.path.join(UPLOAD_DIR, f_name)): 
            continue
        path = os.path.join(UPLOAD_DIR, f_name)
        if os.path.isfile(path):
            files.append({
                "name": f_name,
                "size": os.path.getsize(path),
                "time": os.path.getmtime(path)
            })
    
    files.sort(key=lambda x: x["time"], reverse=True)
    return {"files": files}

# --- File History Backup Endpoints ---

@app.get("/api/get-backup-client")
async def get_backup_client():
    """Generates an HTML GUI Backup Tool requiring zero installation or prerequisites."""
    
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LocalDrop - File History Backup</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/lucide@latest"></script>
    <style>
        body {{ background-color: #0b0f19; color: #f3f4f6; font-family: system-ui, -apple-system, sans-serif; }}
        .loader {{ border: 3px solid #1f2937; border-top: 3px solid #3b82f6; border-radius: 50%; width: 20px; height: 20px; animation: spin 1s linear infinite; }}
        @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
    </style>
</head>
<body class="min-h-screen flex flex-col items-center justify-center p-6">
    <div class="w-full max-w-xl bg-gray-900 border border-gray-800 rounded-2xl shadow-2xl overflow-hidden">
        
        <div class="bg-gray-950 p-6 border-b border-gray-800 flex items-center justify-between">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 rounded-xl bg-emerald-600/20 text-emerald-400 flex items-center justify-center">
                    <i data-lucide="hard-drive"></i>
                </div>
                <div>
                    <h1 class="text-lg font-black text-white">File History Backup</h1>
                    <p class="text-xs text-gray-500 font-mono">Target: {LOCAL_IP}:{PORT}</p>
                </div>
            </div>
        </div>

        <div class="p-6 flex flex-col gap-6">
            <div>
                <label class="text-xs font-bold uppercase tracking-wider text-gray-400 mb-2 block">1. Device Name</label>
                <input type="text" id="device-name" placeholder="e.g. My-Laptop" class="w-full bg-gray-950 border border-gray-700 rounded-xl px-4 py-3 text-white focus:outline-none focus:border-emerald-500 transition" />
            </div>

            <div>
                <label class="text-xs font-bold uppercase tracking-wider text-gray-400 mb-2 block">2. Select Folder</label>
                <div class="relative border-2 border-dashed border-gray-700 rounded-xl bg-gray-950 hover:border-emerald-500 transition-colors p-6 text-center cursor-pointer flex flex-col items-center justify-center">
                    <input type="file" webkitdirectory directory multiple id="folder-input" class="absolute inset-0 w-full h-full opacity-0 cursor-pointer" onchange="handleFolderSelect()" />
                    <i data-lucide="folder-open" class="w-8 h-8 text-gray-500 mb-2" id="folder-icon"></i>
                    <p class="text-sm font-bold text-gray-300" id="folder-label">Click to browse folders</p>
                    <p class="text-xs text-gray-600 mt-1" id="folder-sub">All sub-folders and files will be included</p>
                </div>
            </div>

            <div id="progress-area" class="hidden flex-col gap-3 pt-4 border-t border-gray-800">
                <div class="flex justify-between items-center text-sm font-bold text-gray-300">
                    <span id="status-text" class="flex items-center gap-2"><div class="loader"></div> Backing up...</span>
                    <span id="counter-text" class="font-mono text-emerald-400">0 / 0</span>
                </div>
                <div class="w-full bg-gray-800 rounded-full h-3 overflow-hidden">
                    <div id="progress-bar" class="bg-emerald-500 h-3 rounded-full transition-all duration-200" style="width: 0%"></div>
                </div>
                <div id="current-file" class="text-xs text-gray-500 font-mono truncate">Preparing...</div>
            </div>

            <button id="start-btn" onclick="startBackup()" class="w-full bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-4 rounded-xl transition shadow-lg flex justify-center items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed">
                <i data-lucide="cloud-upload" class="w-5 h-5"></i> Start Backup
            </button>
        </div>
    </div>

    <script>
        const SERVER_URL = "http://{LOCAL_IP}:{PORT}/api/backup";
        let selectedFiles = [];

        function handleFolderSelect() {{
            const input = document.getElementById('folder-input');
            selectedFiles = Array.from(input.files);
            
            if (selectedFiles.length > 0) {{
                const folderName = selectedFiles[0].webkitRelativePath.split('/')[0];
                document.getElementById('folder-icon').classList.replace('text-gray-500', 'text-emerald-400');
                document.getElementById('folder-label').innerText = folderName;
                document.getElementById('folder-label').classList.replace('text-gray-300', 'text-emerald-400');
                document.getElementById('folder-sub').innerText = `${{selectedFiles.length}} files detected inside.`;
            }}
        }}

        async function startBackup() {{
            const deviceName = document.getElementById('device-name').value.trim();
            if (!deviceName) return alert("Please enter a device name first.");
            if (selectedFiles.length === 0) return alert("Please select a folder to backup.");

            document.getElementById('start-btn').disabled = true;
            document.getElementById('progress-area').classList.remove('hidden');
            document.getElementById('progress-area').classList.add('flex');
            
            let successCount = 0;
            const total = selectedFiles.length;

            for (let i = 0; i < total; i++) {{
                const file = selectedFiles[i];
                // file.webkitRelativePath contains the full folder structure (e.g. "MyFolder/subfolder/file.txt")
                const relPath = file.webkitRelativePath;
                
                document.getElementById('counter-text').innerText = `${{i + 1}} / ${{total}}`;
                document.getElementById('current-file').innerText = relPath;
                document.getElementById('progress-bar').style.width = `${{Math.round(((i + 1) / total) * 100)}}%`;

                const formData = new FormData();
                formData.append('device_name', deviceName);
                formData.append('relative_path', relPath);
                formData.append('file', file);

                try {{
                    const res = await fetch(SERVER_URL, {{ method: 'POST', body: formData }});
                    if (res.ok) successCount++;
                }} catch (e) {{
                    console.error("Failed to upload", relPath, e);
                }}
            }}

            document.getElementById('status-text').innerHTML = '<i data-lucide="check-circle" class="w-5 h-5 text-emerald-400"></i> Backup Complete';
            document.getElementById('current-file').innerText = `Successfully backed up ${{successCount}} of ${{total}} files.`;
            document.getElementById('start-btn').disabled = false;
            document.getElementById('start-btn').innerText = "Run Another Backup";
            lucide.createIcons();
        }}
        lucide.createIcons();
    </script>
</body>
</html>
"""
    # Send it down as a standard downloadable HTML file
    return Response(content=html_content, media_type="text/html", headers={"Content-Disposition": "attachment; filename=LocalDrop_Backup.html"})

@app.post("/api/backup")
async def handle_backup(
    device_name: str = Form(...),
    relative_path: str = Form(...),
    file: UploadFile = File(...)
):
    """
    Handles saving incoming backup files exactly to:
    /uploads/file_history/[devicename]/[uploaddate]/[relative_path_folders]/filename.ext
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    # Sanitize inputs to prevent directory traversal
    safe_device = "".join(c for c in device_name if c.isalnum() or c in ("-", "_")).strip()
    if not safe_device: safe_device = "UnknownDevice"
    safe_rel = relative_path.replace("..", "").lstrip("\\/")
    
    # Build exact path string
    save_dir = os.path.join(UPLOAD_DIR, "file_history", safe_device, date_str, os.path.dirname(safe_rel))
    os.makedirs(save_dir, exist_ok=True)
    
    file_path = os.path.join(save_dir, os.path.basename(safe_rel))
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    log_transfer("BACKUP", f"Backed up {safe_rel} for {safe_device}")
    return {"status": "success"}

# --- WebSockets ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    client_ip = websocket.client.host if websocket.client else "unknown"
    peer_id = None

    try:
        init_raw = await websocket.receive_text()
        init_data = json.loads(init_raw)

        if init_data.get("type") != "register":
            await websocket.close()
            return

        peer_id = init_data.get("id")
        device_name = init_data.get("name", "Unknown Device")
        is_visible = init_data.get("visible", True)

        assigned_code = await manager.connect(websocket, peer_id, device_name, client_ip, is_visible)

        await websocket.send_text(json.dumps({
            "type": "registered",
            "id": peer_id,
            "code": assigned_code,
            "local_url": f"http://{LOCAL_IP}:{PORT}"
        }))

        while True:
            data_raw = await websocket.receive_text()
            data = json.loads(data_raw)
            msg_type = data.get("type")

            if msg_type in ["offer_transfer", "offer_transfer_by_code"]:
                target_id = data.get("target_id")
                if "code" in msg_type:
                    target_id = manager.find_peer_by_code(data.get("target_code", "").strip())
                
                if not target_id or target_id == peer_id:
                    await websocket.send_text(json.dumps({"type": "offer_error", "error": "Invalid recipient or PIN."}))
                    continue

                await manager.send_to_peer(target_id, {
                    "type": "incoming_offer",
                    "sender_id": peer_id,
                    "sender_name": device_name,
                    "payload_type": data.get("payload_type"),
                    "meta": data.get("meta") 
                })

            elif msg_type == "offer_response":
                original_sender_id = data.get("target_id")
                accepted = data.get("accepted")
                
                await manager.send_to_peer(original_sender_id, {
                    "type": "transfer_response",
                    "accepted": accepted,
                    "responder_name": device_name,
                    "target_id": peer_id
                })

            elif msg_type == "send_actual_data":
                target_id = data.get("target_id")
                payload = data.get("payload")
                p_type = payload.get("type", "unknown")
                content = payload.get("content", "")

                file_id = str(uuid.uuid4())
                ext = "txt"
                if p_type == "code": ext = "py" 
                if p_type == "url": ext = "url"
                
                save_filename = f"{p_type}_{file_id}.{ext}"
                save_path = os.path.join(UPLOAD_DIR, save_filename)
                
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write(content)

                log_transfer("TEXT_TRANSFER", f"{device_name} -> target | Type: {p_type.upper()} | Saved payload as: {save_filename}")

                success = await manager.send_to_peer(target_id, {
                    "type": "incoming_data",
                    "sender_id": peer_id,
                    "sender_name": device_name,
                    "payload": payload
                })
                await websocket.send_text(json.dumps({"type": "send_ack", "success": success}))

            elif msg_type == "file_uploaded":
                target_id = data.get("target_id")
                await manager.send_to_peer(target_id, {
                    "type": "file_ready",
                    "sender_id": peer_id,
                    "sender_name": device_name,
                    "file_id": data.get("file_id"),
                    "filename": data.get("filename"),
                    "size": data.get("size")
                })

            elif msg_type == "file_downloaded":
                target_id = data.get("target_id")
                await manager.send_to_peer(target_id, {
                    "type": "file_transfer_complete",
                    "receiver_name": device_name
                })

            elif msg_type == "update_state":
                device_name = data.get("name", device_name).strip()
                if peer_id in manager.active_peers:
                    manager.active_peers[peer_id]["name"] = device_name
                    manager.active_peers[peer_id]["visible"] = data.get("visible", True)
                    await manager.broadcast_peer_list()

    except WebSocketDisconnect:
        if peer_id: await manager.disconnect(peer_id)
    except Exception:
        if peer_id: await manager.disconnect(peer_id)

if __name__ == "__main__":
    print("=" * 60)
    print(f"🚀 LocalDrop Hub running on LAN!")
    print(f"📡 Access URL: http://{LOCAL_IP}:{PORT}")
    print(f"📁 Transfers logged to: {UPLOAD_DIR}")
    print(f"⏳ Time Machine Password is in: {CONFIG_FILE}")
    print("=" * 60)
    uvicorn.run(app, host=LOCAL_IP, port=PORT, log_level="warning")