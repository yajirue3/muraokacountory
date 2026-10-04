from fastapi import APIRouter, HTTPException, Header, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from pathlib import Path
from datetime import datetime, timezone
import random

from db import get_supabase

router = APIRouter()
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

BOARD_WIDTH = 200
BOARD_HEIGHT = 200

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

def get_valid_spawn():
    return random.randint(5, BOARD_WIDTH - 6), random.randint(5, BOARD_HEIGHT - 6)

class BasePixelRequest(BaseModel):
    wallet_id: str

@router.get("/api/pixel/me")
async def get_my_pixel_status(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    res = await client.table("pixel_players").select("*").eq("user_id", user.id).execute()
    if not res.data:
        return {"exists": False, "is_dead": True}
    p = res.data[0]
    return {
        "exists": True,
        "is_dead": p["is_dead"],
        "player_id": p["player_id"],
        "core_x": p["core_x"],
        "core_y": p["core_y"],
        "ink": p["ink"],
        "max_ink": p["max_ink"],
        "regen": p["regen"],
        "barrier_active": p["barrier_active"],
        "wallet_id": p["wallet_id"]
    }

@router.post("/api/pixel/spawn")
async def pixel_spawn(data: BasePixelRequest, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    res = await client.table("pixel_players").select("*").eq("user_id", user.id).execute()
    
    if res.data:
        p = res.data[0]
        if not p["is_dead"]:
            return p
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

    return {"player_id": player_id, "core_x": core_x, "core_y": core_y}

class UpgradeRequest(BaseModel):
    wallet_id: str
    upgrade_type: str

@router.post("/api/pixel/upgrade")
async def pixel_upgrade(data: UpgradeRequest, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    p_res = await client.table("pixel_players").select("player_id").eq("user_id", user.id).execute()
    if not p_res.data:
        raise HTTPException(status_code=400, detail="未登録")
    rpc_res = await client.rpc("pixel_upgrade_stat", {
        "p_player_id": p_res.data[0]["player_id"],
        "p_wallet_id": data.wallet_id,
        "p_type": data.upgrade_type
    }).execute()
    result = rpc_res.data
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("detail", "エラー"))
    return result

@router.post("/api/pixel/barrier/toggle")
async def toggle_barrier(data: BasePixelRequest, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    p_res = await client.table("pixel_players").select("*").eq("user_id", user.id).execute()
    new_state = not p_res.data[0]["barrier_active"]
    await client.table("pixel_players").update({
        "barrier_active": new_state,
        "last_tick": datetime.now(timezone.utc).isoformat()
    }).eq("id", p_res.data[0]["id"]).execute()
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
    if p.data[0]["player_id"] == data.target_player_id:
        raise HTTPException(status_code=400, detail="自己指定不可")
    try:
        await client.table("pixel_alliances").insert({
            "player1_id": min(p.data[0]["player_id"], data.target_player_id),
            "player2_id": max(p.data[0]["player_id"], data.target_player_id)
        }).execute()
    except Exception:
        pass
    return {"success": True, "message": "同盟締結完了"}

@router.post("/api/pixel/alliance/break")
async def break_alliance(data: AllianceRequest, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    my_id = (await client.table("pixel_players").select("player_id").eq("user_id", user.id).execute()).data[0]["player_id"]
    target_id = data.target_player_id
    await client.table("pixel_alliances").delete().or_(
        f"and(player1_id.eq.{min(my_id, target_id)},player2_id.eq.{max(my_id, target_id)})"
    ).execute()
    return {"success": True, "message": "同盟破棄完了"}

@router.get("/api/pixel/cores")
async def get_active_cores():
    client = await get_supabase()
    res = await client.table("pixel_players").select("player_id, core_x, core_y").eq("is_dead", False).execute()
    return res.data or []

@router.get("/pixel", response_class=HTMLResponse)
async def get_pixel(request: Request):
    return templates.TemplateResponse(request=request, name="pixel.html")
