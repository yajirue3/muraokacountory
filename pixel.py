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

from db import get_supabase

router = APIRouter()
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

BOARD_WIDTH = 200
BOARD_HEIGHT = 200
ROCK_ID = 65535
EXPECTED_BYTE_SIZE = BOARD_WIDTH * BOARD_HEIGHT * 3

board = bytearray(EXPECTED_BYTE_SIZE)
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

async def load_board_from_supabase():
    """Supabaseから盤面バイナリデータを読み込み"""
    global board
    client = await get_supabase()
    res = await client.table("pixel_board").select("data").eq("id", 1).execute()
    
    loaded = False
    if res.data and len(res.data) > 0:
        raw_data = res.data[0].get("data")
        if raw_data:
            # Supabase Python SDK が bytes/bytearray で返すか hex 文字列 (\x...) で返す場合に対応
            if isinstance(raw_data, (bytes, bytearray)):
                data_bytes = bytes(raw_data)
            elif isinstance(raw_data, str) and raw_data.startswith("\\x"):
                data_bytes = bytes.fromhex(raw_data[2:])
            else:
                data_bytes = b""
            
            if len(data_bytes) == EXPECTED_BYTE_SIZE:
                board[:] = data_bytes
                loaded = True

    # DBに有効なデータがない場合は岩盤付きの初期状態を生成してDBへ保存
    if not loaded:
        board[:] = bytearray(EXPECTED_BYTE_SIZE)
        for x in range(BOARD_WIDTH):
            if 3 <= x < BOARD_WIDTH - 3:
                set_tile(x, 100, ROCK_ID, 255)
        for y in range(BOARD_HEIGHT):
            if 3 <= y < BOARD_HEIGHT - 3:
                set_tile(100, y, ROCK_ID, 255)
        
        await save_board_to_supabase()

    # マスカウントの集計
    player_mass_counts.clear()
    for y in range(BOARD_HEIGHT):
        for x in range(BOARD_WIDTH):
            owner, _ = get_tile(x, y)
            if owner != 0 and owner != ROCK_ID:
                player_mass_counts[owner] = player_mass_counts.get(owner, 0) + 1

async def save_board_to_supabase():
    """現在の盤面バイナリデータをSupabaseへ保存"""
    client = await get_supabase()
    hex_str = "\\x" + board.hex()
    await client.table("pixel_board").upsert({
        "id": 1,
        "data": hex_str,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }).execute()

async def save_board_task():
    """5分ごとに自動的にSupabaseへ盤面を保存"""
    while True:
        await asyncio.sleep(300)
        async with board_lock:
            try:
                await save_board_to_supabase()
            except Exception as e:
                print(f"[Pixel Board Auto Save Error]: {e}")

@router.on_event("startup")
async def start_pixel_tasks():
    await load_board_from_supabase()
    asyncio.create_task(save_board_task())

@router.on_event("shutdown")
async def shutdown_pixel_tasks():
    async with board_lock:
        try:
            await save_board_to_supabase()
        except Exception as e:
            print(f"[Pixel Board Shutdown Save Error]: {e}")

async def get_user_from_token(authorization: str):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証トークンがありません")
    token = authorization.split(" ")[1]
    try:
        supabase = await get_supabase()
        user_res = await supabase.auth.get_user(token)
        return user_res.user
    except Exception:
        raise HTTPException(status_code=401, detail="無効なトークン")

async def get_alliances(player_id: int) -> set:
    client = await get_supabase()
    res = await client.table("pixel_alliances").select("*").or_(f"player1_id.eq.{player_id},player2_id.eq.{player_id}").execute()
    return {row["player1_id"] if row["player2_id"] == player_id else row["player2_id"] for row in res.data}

def is_adjacent_to_owned(target_x: int, target_y: int, owner_id: int, alliances: set) -> bool:
    for dx, dy in [(-1,0), (1,0), (0,-1), (0,1)]:
        nx, ny = target_x + dx, target_y + dy
        if 0 <= nx < BOARD_WIDTH and 0 <= ny < BOARD_HEIGHT:
            neighbor_owner, _ = get_tile(nx, ny)
            if neighbor_owner == owner_id or neighbor_owner in alliances:
                return True
    return False

def get_valid_spawn():
    for _ in range(500):
        cx, cy = random.randint(5, BOARD_WIDTH-6), random.randint(5, BOARD_HEIGHT-6)
        if all(get_tile(cx+dx, cy+dy)[0] == 0 for dx in range(-2, 3) for dy in range(-2, 3)):
            return cx, cy
    for _ in range(500):
        cx, cy = random.randint(1, BOARD_WIDTH-2), random.randint(1, BOARD_HEIGHT-2)
        if get_tile(cx, cy)[0] == 0:
            return cx, cy
    return 10, 10

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
            if 0 <= nx < BOARD_WIDTH and 0 <= ny < BOARD_HEIGHT and (nx, ny) not in visited:
                t_owner, _ = get_tile(nx, ny)
                if t_owner == owner_id or t_owner in alliances:
                    visited.add((nx, ny))
                    queue.append((nx, ny))
    return set() if is_connected else visited

def apply_flood_fill(start_x: int, start_y: int, owner_id: int, diffs: list):
    for dx, dy in [(-1,0), (1,0), (0,-1), (0,1)]:
        nx, ny = start_x + dx, start_y + dy
        if not (0 <= nx < BOARD_WIDTH and 0 <= ny < BOARD_HEIGHT): continue
        t_owner, _ = get_tile(nx, ny)
        if t_owner == owner_id or t_owner == ROCK_ID: continue
            
        queue = deque([(nx, ny)])
        visited = set([(nx, ny)])
        is_closed = True
        while queue:
            cx, cy = queue.popleft()
            if cx == 0 or cx == BOARD_WIDTH - 1 or cy == 0 or cy == BOARD_HEIGHT - 1:
                is_closed = False
                if len(visited) > 400: break
            for ddx, ddy in [(-1,0), (1,0), (0,-1), (0,1)]:
                nnx, nny = cx + ddx, cy + ddy
                if 0 <= nnx < BOARD_WIDTH and 0 <= nny < BOARD_HEIGHT and (nnx, nny) not in visited:
                    no, _ = get_tile(nnx, nny)
                    if no != owner_id and no != ROCK_ID:
                        visited.add((nnx, nny))
                        queue.append((nnx, nny))
                        
        if is_closed and len(visited) <= 400:
            for vx, vy in visited:
                set_tile(vx, vy, owner_id, 1)
                diffs.append(struct.pack(">BBHB", vx, vy, owner_id, 1))

async def broadcast_event(message: dict):
    for c in connected_clients:
        try: await c.send_json(message)
        except: pass

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
            
            # 1. バリデーション
            mass_count = player_mass_counts.get(player_id, 0)
            rpc_res = await client.rpc("pixel_lazy_update", {
                "p_player_id": player_id, "p_mass_count": mass_count, "p_now": datetime.now(timezone.utc).isoformat()
            }).execute()
            
            state = rpc_res.data
            if not state or state.get("status") != "ok":
                await ws.send_json({"type": "error", "msg": "死亡または未登録です。"})
                continue
            if state.get("barrier", False):
                await ws.send_json({"type": "error", "msg": "バリア中は攻撃できません。"})
                continue
            if state.get("ink", 0) < 1:
                await ws.send_json({"type": "error", "msg": "インクが不足しています。"})
                continue

            alliances = await get_alliances(player_id)
            target_owner, target_hp = get_tile(x, y)
            
            if target_owner == ROCK_ID:
                await ws.send_json({"type": "error", "msg": "岩盤は操作できません。"})
                continue

            if action == 1:
                if target_owner == player_id or target_owner in alliances:
                    await ws.send_json({"type": "error", "msg": "防壁強化モードに切り替えてください。"})
                    continue
                if not is_adjacent_to_owned(x, y, player_id, alliances):
                    await ws.send_json({"type": "error", "msg": "自陣に隣接していません。"})
                    continue
                target_mass = player_mass_counts.get(target_owner, 0) if target_owner != 0 else 0
                if (mass_count <= 10 and target_owner != 0) or (target_owner != 0 and target_mass <= 10):
                    await ws.send_json({"type": "error", "msg": "初心者保護（10マス以下）対象です。"})
                    continue
            elif action == 2:
                if target_owner != player_id and target_owner not in alliances:
                    await ws.send_json({"type": "error", "msg": "自陣のみ強化可能です。"})
                    continue
                if target_hp >= 255:
                    await ws.send_json({"type": "error", "msg": "防壁が最大です。"})
                    continue

            # 2. アトミック消費
            consume_res = await client.rpc("pixel_consume_ink", {"p_player_id": player_id}).execute()
            if not consume_res.data:
                await ws.send_json({"type": "error", "msg": "インク不足"})
                continue

            # 3. 盤面書き換えと判定
            diffs = []
            assassinated = False
            bounty_msg = None

            async with board_lock:
                if action == 1:
                    if target_owner != 0 and target_hp > 1:
                        set_tile(x, y, target_owner, target_hp - 1)
                        diffs.append(struct.pack(">BBHB", x, y, target_owner, target_hp - 1))
                    else:
                        set_tile(x, y, player_id, 1)
                        diffs.append(struct.pack(">BBHB", x, y, player_id, 1))
                        
                        if target_owner != 0:
                            enemy_res = await client.table("pixel_players").select("core_x, core_y").eq("player_id", target_owner).execute()
                            if enemy_res.data:
                                ep = enemy_res.data[0]
                                if x == ep["core_x"] and y == ep["core_y"]:
                                    assassinated = True
                                else:
                                    enemy_alliances = await get_alliances(target_owner)
                                    for dx, dy in [(-1,0), (1,0), (0,-1), (0,1)]:
                                        nx, ny = x + dx, y + dy
                                        if get_tile(nx, ny)[0] == target_owner:
                                            disconnected = check_connectivity(nx, ny, target_owner, ep["core_x"], ep["core_y"], enemy_alliances)
                                            for dx_disc, dy_disc in disconnected:
                                                set_tile(dx_disc, dy_disc, 0, 0)
                                                diffs.append(struct.pack(">BBHB", dx_disc, dy_disc, 0, 0))

                        apply_flood_fill(x, y, player_id, diffs)
                elif action == 2:
                    set_tile(x, y, target_owner, target_hp + 1)
                    diffs.append(struct.pack(">BBHB", x, y, target_owner, target_hp + 1))

            # ロック解除後の重いDB処理 (コア破壊)
            if assassinated:
                kill_res = await client.rpc("pixel_assassinate_core", {"p_killer_id": player_id, "p_victim_id": target_owner}).execute()
                if kill_res.data and kill_res.data.get("success"):
                    bounty = kill_res.data["bounty"]
                    bounty_msg = f"PLAYER #{player_id} が #{target_owner} を討伐！ 懸賞金 {bounty:,} G 強奪！"
                    await client.table("pixel_logs").insert({"event_type": "ASSASSINATE", "message": bounty_msg}).execute()
                    
                    # 敵マス全消去
                    async with board_lock:
                        for cy in range(BOARD_HEIGHT):
                            for cx in range(BOARD_WIDTH):
                                if get_tile(cx, cy)[0] == target_owner:
                                    set_tile(cx, cy, 0, 0)
                                    diffs.append(struct.pack(">BBHB", cx, cy, 0, 0))

            # ブロードキャスト
            if diffs:
                payload = b"".join(diffs)
                for c in connected_clients:
                    try: await c.send_bytes(payload)
                    except: pass
                await ws.send_json({"type": "status", "ink": state["ink"] - 1})
            
            if bounty_msg:
                await broadcast_event({"type": "broadcast", "message": bounty_msg, "color": "#e74c3c"})

    except WebSocketDisconnect:
        connected_clients.remove(ws)

class BasePixelRequest(BaseModel):
    wallet_id: str

@router.get("/api/pixel/me")
async def get_my_pixel_status(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    res = await client.table("pixel_players").select("*").eq("user_id", user.id).execute()
    if not res.data: return {"exists": False, "is_dead": True}
    p = res.data[0]
    return { "exists": True, "is_dead": p["is_dead"], "player_id": p["player_id"], "core_x": p["core_x"], "core_y": p["core_y"], "ink": p["ink"], "max_ink": p["max_ink"], "regen": p["regen"], "barrier_active": p["barrier_active"], "wallet_id": p["wallet_id"] }

@router.post("/api/pixel/spawn")
async def pixel_spawn(data: BasePixelRequest, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    res = await client.table("pixel_players").select("*").eq("user_id", user.id).execute()
    
    async with board_lock:
        if res.data:
            p = res.data[0]
            if not p["is_dead"]: return p
            core_x, core_y = get_valid_spawn()
            await client.table("pixel_players").update({
                "is_dead": False, "core_x": core_x, "core_y": core_y, 
                "ink": 1, "max_ink": 10, "regen": 1, "bounty_gold": 0, "barrier_active": False, "last_tick": datetime.now(timezone.utc).isoformat()
            }).eq("id", p["id"]).execute()
            player_id = p["player_id"]
        else:
            core_x, core_y = get_valid_spawn()
            player_id = random.randint(1, 65534)
            await client.table("pixel_players").insert({
                "user_id": str(user.id), "wallet_id": data.wallet_id, "player_id": player_id, "core_x": core_x, "core_y": core_y, "ink": 1
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
    p_res = await client.table("pixel_players").select("player_id").eq("user_id", user.id).execute()
    if not p_res.data: raise HTTPException(status_code=400, detail="未登録")
    rpc_res = await client.rpc("pixel_upgrade_stat", { "p_player_id": p_res.data[0]["player_id"], "p_wallet_id": data.wallet_id, "p_type": data.upgrade_type }).execute()
    result = rpc_res.data
    if not result.get("success"): raise HTTPException(status_code=400, detail=result.get("detail", "エラー"))
    return result

@router.post("/api/pixel/barrier/toggle")
async def toggle_barrier(data: BasePixelRequest, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    p_res = await client.table("pixel_players").select("*").eq("user_id", user.id).execute()
    new_state = not p_res.data[0]["barrier_active"]
    await client.table("pixel_players").update({"barrier_active": new_state, "last_tick": datetime.now(timezone.utc).isoformat()}).eq("id", p_res.data[0]["id"]).execute()
    return {"barrier_active": new_state}

@router.get("/api/pixel/leaderboard")
async def get_leaderboard():
    client = await get_supabase()
    res = await client.table("pixel_players").select("player_id, bounty_gold").eq("is_dead", False).order("bounty_gold", desc=True).limit(5).execute()
    return res.data

class AllianceRequest(BaseModel):
    target_player_id: int

@router.post("/api/pixel/alliance/add")
async def add_alliance(data: AllianceRequest, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    p = await client.table("pixel_players").select("player_id").eq("user_id", user.id).execute()
    if p.data[0]["player_id"] == data.target_player_id: raise HTTPException(status_code=400, detail="自己指定不可")
    try: await client.table("pixel_alliances").insert({"player1_id": min(p.data[0]["player_id"], data.target_player_id), "player2_id": max(p.data[0]["player_id"], data.target_player_id)}).execute()
    except: pass
    return {"success": True, "message": "同盟締結完了"}

@router.post("/api/pixel/alliance/break")
async def break_alliance(data: AllianceRequest, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    my_id = (await client.table("pixel_players").select("player_id").eq("user_id", user.id).execute()).data[0]["player_id"]
    target_id = data.target_player_id
    await client.table("pixel_alliances").delete().or_(f"and(player1_id.eq.{min(my_id, target_id)},player2_id.eq.{max(my_id, target_id)})").execute()
    
    msg = f"PLAYER #{my_id} が #{target_id} との同盟を破棄！"
    await client.table("pixel_logs").insert({"event_type": "BETRAYAL", "message": msg}).execute()
    await broadcast_event({"type": "broadcast", "message": msg, "color": "#f39c12"})

    my_alliances, target_alliances = await get_alliances(my_id), await get_alliances(target_id)
    t_res = await client.table("pixel_players").select("core_x, core_y").eq("player_id", target_id).execute()
    p_res = await client.table("pixel_players").select("core_x, core_y").eq("player_id", my_id).execute()

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
    return {"success": True, "message": "同盟破棄・孤立領地消滅"}

@router.get("/pixel", response_class=HTMLResponse)
async def get_pixel(request: Request):
    return templates.TemplateResponse(request=request, name="pixel.html")
