from fastapi import APIRouter, HTTPException, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pathlib import Path
from datetime import datetime, timezone
import random
import struct
import asyncio
from collections import deque
import os

from db import get_supabase

router = APIRouter()
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

BOARD_WIDTH = 200
BOARD_HEIGHT = 200
ROCK_ID = 65535

board = bytearray(BOARD_WIDTH * BOARD_HEIGHT * 3)
BOARD_FILE = BASE_DIR / "board_data.bin"
board_lock = asyncio.Lock()

player_mass_counts = {}
connected_clients: list[WebSocket] = []


def get_idx(x: int, y: int) -> int:
    return (y * BOARD_WIDTH + x) * 3

def get_tile(x: int, y: int):
    if not (0 <= x < BOARD_WIDTH and 0 <= y < BOARD_HEIGHT):
        return None, None
    return struct.unpack_from(">HB", board, get_idx(x, y))

def set_tile(x: int, y: int, owner: int, hp: int):
    idx = get_idx(x, y)
    old_owner, _ = struct.unpack_from(">HB", board, idx)
    struct.pack_into(">HB", board, idx, owner, hp)
    
    if old_owner != owner:
        if old_owner != 0 and old_owner != ROCK_ID:
            player_mass_counts[old_owner] = max(0, player_mass_counts.get(old_owner, 0) - 1)
        if owner != 0 and owner != ROCK_ID:
            player_mass_counts[owner] = player_mass_counts.get(owner, 0) + 1

def init_board():
    if os.path.exists(BOARD_FILE):
        with open(BOARD_FILE, "rb") as f:
            board[:] = f.read()
    else:
        for x in range(BOARD_WIDTH):
            if 3 <= x < BOARD_WIDTH - 3:
                set_tile(x, 100, ROCK_ID, 255)
        for y in range(BOARD_HEIGHT):
            if 3 <= y < BOARD_HEIGHT - 3:
                set_tile(100, y, ROCK_ID, 255)
                
    for y in range(BOARD_HEIGHT):
        for x in range(BOARD_WIDTH):
            owner, _ = get_tile(x, y)
            if owner != 0 and owner != ROCK_ID:
                player_mass_counts[owner] = player_mass_counts.get(owner, 0) + 1

async def save_board_task():
    while True:
        await asyncio.sleep(300)
        async with board_lock:
            with open(BOARD_FILE, "wb") as f:
                f.write(board)

@router.on_event("startup")
async def start_pixel_tasks():
    init_board()
    asyncio.create_task(save_board_task())

async def get_user_from_token(authorization: str):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証トークンがありません")
    token = authorization.split(" ")[1]
    try:
        supabase = await get_supabase()
        user_res = await supabase.auth.get_user(token)
        return user_res.user
    except Exception:
        raise HTTPException(status_code=401, detail="無効なトークンです")

async def get_alliances(player_id: int) -> set:
    client = await get_supabase()
    res = await client.table("pixel_alliances").select("*").or_(f"player1_id.eq.{player_id},player2_id.eq.{player_id}").execute()
    return {row["player1_id"] if row["player2_id"] == player_id else row["player2_id"] for row in res.data}

def is_adjacent_to_owned(target_x: int, target_y: int, owner_id: int, alliances: set) -> bool:
    """飛び地防止：塗ろうとしているマスが自陣または同盟領地に隣接しているか判定"""
    for dx, dy in [(-1,0), (1,0), (0,-1), (0,1)]:
        nx, ny = target_x + dx, target_y + dy
        if 0 <= nx < BOARD_WIDTH and 0 <= ny < BOARD_HEIGHT:
            neighbor_owner, _ = get_tile(nx, ny)
            if neighbor_owner == owner_id or neighbor_owner in alliances:
                return True
    return False

def check_connectivity(start_x: int, start_y: int, owner_id: int, core_x: int, core_y: int, alliances: set) -> set:
    queue = deque([(start_x, start_y)])
    visited = set([(start_x, start_y)])
    is_connected = False

    while queue:
        cx, cy = queue.popleft()
        if cx == core_x and cy == core_y:
            is_connected = True
            break
            
        for dx, dy in [(-1,0), (1,0), (0,-1), (0,1)]:
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < BOARD_WIDTH and 0 <= ny < BOARD_HEIGHT:
                if (nx, ny) not in visited:
                    t_owner, _ = get_tile(nx, ny)
                    if t_owner == owner_id or t_owner in alliances:
                        visited.add((nx, ny))
                        queue.append((nx, ny))
                        
    return set() if is_connected else visited

def apply_flood_fill(start_x: int, start_y: int, owner_id: int, diffs: list):
    for dx, dy in [(-1,0), (1,0), (0,-1), (0,1)]:
        nx, ny = start_x + dx, start_y + dy
        if not (0 <= nx < BOARD_WIDTH and 0 <= ny < BOARD_HEIGHT):
            continue
            
        t_owner, _ = get_tile(nx, ny)
        if t_owner == owner_id or t_owner == ROCK_ID:
            continue
            
        queue = deque([(nx, ny)])
        visited = set([(nx, ny)])
        is_closed = True
        
        while queue:
            cx, cy = queue.popleft()
            if cx == 0 or cx == BOARD_WIDTH - 1 or cy == 0 or cy == BOARD_HEIGHT - 1:
                is_closed = False
                if len(visited) > 500: break

            for ddx, ddy in [(-1,0), (1,0), (0,-1), (0,1)]:
                nnx, nny = cx + ddx, cy + ddy
                if 0 <= nnx < BOARD_WIDTH and 0 <= nny < BOARD_HEIGHT and (nnx, nny) not in visited:
                    no, _ = get_tile(nnx, nny)
                    if no != owner_id and no != ROCK_ID:
                        visited.add((nnx, nny))
                        queue.append((nnx, nny))
                        
        if is_closed and len(visited) <= 500:
            for vx, vy in visited:
                set_tile(vx, vy, owner_id, 1)
                diffs.append(struct.pack(">BBHB", vx, vy, owner_id, 1))

@router.websocket("/api/pixel/ws")
async def websocket_pixel(ws: WebSocket):
    await ws.accept()
    connected_clients.append(ws)
    try:
        await ws.send_bytes(board)

        while True:
            data = await ws.receive_bytes()
            if len(data) != 5: continue
            x, y, action, player_id = struct.unpack(">BBB H", data)
            
            client = await get_supabase()
            
            async with board_lock:
                mass_count = player_mass_counts.get(player_id, 0)
                rpc_res = await client.rpc("pixel_lazy_update", {
                    "p_player_id": player_id, "p_mass_count": mass_count, "p_now": datetime.now(timezone.utc).isoformat()
                }).execute()
                
                state = rpc_res.data
                if state.get("status") == "dead_or_not_found" or state.get("ink", 0) < 1:
                    continue
                if state.get("barrier", False) and action == 1:
                    continue

                alliances = await get_alliances(player_id)
                target_owner, target_hp = get_tile(x, y)
                diffs = []

                # ACTION 1: 塗る / 攻撃
                if action == 1:
                    if target_owner == ROCK_ID or target_owner == player_id or target_owner in alliances:
                        continue
                    
                    # 飛び地防止: 自陣または同盟領地に隣接していなければ配置不能
                    if not is_adjacent_to_owned(x, y, player_id, alliances):
                        continue
                        
                    # 初心者保護 (10マス以下不可侵)
                    target_mass = player_mass_counts.get(target_owner, 0) if target_owner != 0 else 0
                    if (mass_count <= 10 and target_owner != 0) or (target_owner != 0 and target_mass <= 10):
                        continue

                    # 耐久度削り or 上書き
                    if target_owner != 0 and target_hp > 1:
                        set_tile(x, y, target_owner, target_hp - 1)
                        diffs.append(struct.pack(">BBHB", x, y, target_owner, target_hp - 1))
                    else:
                        set_tile(x, y, player_id, 1)
                        diffs.append(struct.pack(">BBHB", x, y, player_id, 1))
                        
                        if target_owner != 0:
                            enemy_res = await client.table("pixel_players").select("*").eq("player_id", target_owner).execute()
                            if enemy_res.data:
                                ep = enemy_res.data[0]
                                if x == ep["core_x"] and y == ep["core_y"]:
                                    # 暗殺成功: 懸賞金強奪
                                    bounty = ep["bounty_gold"]
                                    if bounty > 0:
                                        p_res = await client.table("pixel_players").select("wallet_id").eq("player_id", player_id).execute()
                                        if p_res.data:
                                            await client.rpc("pixel_claim_bounty", {
                                                "p_wallet_id": p_res.data[0]["wallet_id"],
                                                "p_amount": bounty
                                            }).execute()
                                    
                                    # 敵マス全消滅
                                    for cy in range(BOARD_HEIGHT):
                                        for cx in range(BOARD_WIDTH):
                                            if get_tile(cx, cy)[0] == target_owner:
                                                set_tile(cx, cy, 0, 0)
                                                diffs.append(struct.pack(">BBHB", cx, cy, 0, 0))
                                                
                                    await client.table("pixel_players").update({"is_dead": True, "ink": 0}).eq("player_id", target_owner).execute()
                                    await client.table("pixel_logs").insert({
                                        "event_type": "ASSASSINATE",
                                        "message": f"プレイヤー {player_id} が {target_owner} のコアを破壊！ 賞金 {bounty}G 強奪！"
                                    }).execute()
                                else:
                                    # 導線切断BFS
                                    enemy_alliances = await get_alliances(target_owner)
                                    for dx, dy in [(-1,0), (1,0), (0,-1), (0,1)]:
                                        nx, ny = x + dx, y + dy
                                        if get_tile(nx, ny)[0] == target_owner:
                                            disconnected = check_connectivity(nx, ny, target_owner, ep["core_x"], ep["core_y"], enemy_alliances)
                                            for dx, dy in disconnected:
                                                set_tile(dx, dy, 0, 0)
                                                diffs.append(struct.pack(">BBHB", dx, dy, 0, 0))

                        apply_flood_fill(x, y, player_id, diffs)

                # ACTION 2: 防壁強化
                elif action == 2:
                    if target_owner == player_id or target_owner in alliances:
                        if target_hp < 255:
                            set_tile(x, y, target_owner, target_hp + 1)
                            diffs.append(struct.pack(">BBHB", x, y, target_owner, target_hp + 1))

                if diffs:
                    await client.table("pixel_players").update({"ink": state["ink"] - 1}).eq("player_id", player_id).execute()
                    payload = b"".join(diffs)
                    for c in connected_clients:
                        try:
                            await c.send_bytes(payload)
                        except:
                            pass

    except WebSocketDisconnect:
        connected_clients.remove(ws)

class BasePixelRequest(BaseModel):
    wallet_id: str

@router.post("/api/pixel/spawn")
async def pixel_spawn(data: BasePixelRequest, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    res = await client.table("pixel_players").select("*").eq("user_id", user.id).execute()
    
    async with board_lock:
        core_x, core_y = 0, 0
        while True:
            core_x, core_y = random.randint(0, BOARD_WIDTH-1), random.randint(0, BOARD_HEIGHT-1)
            if get_tile(core_x, core_y)[0] == 0: break
            
        if res.data:
            p = res.data[0]
            if not p["is_dead"]:
                raise HTTPException(status_code=400, detail="既に生存しています。")
            await client.table("pixel_players").update({
                "is_dead": False, "core_x": core_x, "core_y": core_y, 
                "ink": 1, "max_ink": 10, "regen": 1, "bounty_gold": 0,
                "barrier_active": False, "last_tick": datetime.now(timezone.utc).isoformat()
            }).eq("id", p["id"]).execute()
            player_id = p["player_id"]
        else:
            player_id = random.randint(1, 65534)
            await client.table("pixel_players").insert({
                "user_id": str(user.id), "wallet_id": data.wallet_id, "player_id": player_id,
                "core_x": core_x, "core_y": core_y, "ink": 1
            }).execute()
            
        set_tile(core_x, core_y, player_id, 1)
        
    return {"player_id": player_id, "core_x": core_x, "core_y": core_y}

class UpgradeRequest(BaseModel):
    wallet_id: str
    upgrade_type: str

@router.post("/api/pixel/upgrade")
async def pixel_upgrade(data: UpgradeRequest, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    p_res = await client.table("pixel_players").select("*").eq("user_id", user.id).execute()
    if not p_res.data or p_res.data[0]["is_dead"]:
        raise HTTPException(status_code=400, detail="プレイヤーが見つからないか死亡しています。")
    p = p_res.data[0]
    
    if data.upgrade_type == "MAX_INK":
        cost = p["max_ink"] * 100
    elif data.upgrade_type == "REGEN":
        cost = p["regen"] * 5000
    else:
        raise HTTPException(status_code=400, detail="無効な強化タイプです。")
        
    w_res = await client.table("wallets").select("*").eq("wallet_id", data.wallet_id).execute()
    if not w_res.data or w_res.data[0]["balance"] < cost:
        raise HTTPException(status_code=400, detail="残高不足です。")
        
    await client.table("wallets").update({"balance": w_res.data[0]["balance"] - cost}).eq("id", w_res.data[0]["id"]).execute()
    
    update_data = {"bounty_gold": p["bounty_gold"] + cost}
    if data.upgrade_type == "MAX_INK":
        update_data["max_ink"] = p["max_ink"] + 10
    else:
        update_data["regen"] = p["regen"] + 1
        
    await client.table("pixel_players").update(update_data).eq("id", p["id"]).execute()
    return {"success": True, "new_stats": update_data}

@router.post("/api/pixel/barrier/toggle")
async def toggle_barrier(data: BasePixelRequest, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    p_res = await client.table("pixel_players").select("*").eq("user_id", user.id).execute()
    p = p_res.data[0]
    new_state = not p["barrier_active"]
    await client.table("pixel_players").update({"barrier_active": new_state, "last_tick": datetime.now(timezone.utc).isoformat()}).eq("id", p["id"]).execute()
    return {"barrier_active": new_state}

class BreakAllianceRequest(BaseModel):
    target_player_id: int

@router.post("/api/pixel/alliance/break")
async def break_alliance(data: BreakAllianceRequest, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    
    p_res = await client.table("pixel_players").select("*").eq("user_id", user.id).execute()
    my_id = p_res.data[0]["player_id"]
    target_id = data.target_player_id
    
    await client.table("pixel_alliances").delete().or_(f"and(player1_id.eq.{my_id},player2_id.eq.{target_id}),and(player1_id.eq.{target_id},player2_id.eq.{my_id})").execute()
    await client.table("pixel_logs").insert({"event_type": "BETRAYAL", "message": f"プレイヤー {my_id} が {target_id} との同盟を破棄！"}).execute()

    my_alliances = await get_alliances(my_id)
    target_alliances = await get_alliances(target_id)
    t_res = await client.table("pixel_players").select("*").eq("player_id", target_id).execute()
    
    async with board_lock:
        diffs = []
        for p_id, alliances, p_data in [(my_id, my_alliances, p_res.data[0]), (target_id, target_alliances, t_res.data[0])]:
            connected = set()
            queue = deque([(p_data["core_x"], p_data["core_y"])])
            visited = set([(p_data["core_x"], p_data["core_y"])])
            
            while queue:
                cx, cy = queue.popleft()
                connected.add((cx, cy))
                for dx, dy in [(-1,0), (1,0), (0,-1), (0,1)]:
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < BOARD_WIDTH and 0 <= ny < BOARD_HEIGHT and (nx, ny) not in visited:
                        no, _ = get_tile(nx, ny)
                        if no == p_id or no in alliances:
                            visited.add((nx, ny))
                            queue.append((nx, ny))
            
            for cy in range(BOARD_HEIGHT):
                for cx in range(BOARD_WIDTH):
                    if get_tile(cx, cy)[0] == p_id and (cx, cy) not in connected:
                        set_tile(cx, cy, 0, 0)
                        diffs.append(struct.pack(">BBHB", cx, cy, 0, 0))
                        
        if diffs:
            payload = b"".join(diffs)
            for c in connected_clients:
                try: await c.send_bytes(payload)
                except: pass

    return {"success": True, "message": "同盟を破棄し、孤立領地を消滅させました。"}

@router.get("/pixel", response_class=HTMLResponse)
async def get_pixel(request: Request):
    return templates.TemplateResponse(request=request, name="pixel.html")
