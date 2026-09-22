import asyncio
import copy
import logging
import os
import random
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Any

from fastapi import APIRouter, HTTPException, Header, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from supabase import AsyncClient

# db.py の get_supabase をインポート
from db import get_supabase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CardEngine")

router = APIRouter()
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

async def get_user_from_token_async(authorization: str):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証トークンがありません")
    token = authorization.split(" ")[1]
    try:
        supabase = await get_supabase()
        user_res = await supabase.auth.get_user(token)
        return user_res.user
    except Exception:
        raise HTTPException(status_code=401, detail="無効なトークンです")

# マスターデータ
CARD_DATABASE = {
    "u_01": {"id": "u_01", "name": "先鋒兵", "type": "unit", "cost": 1, "atk": 2, "hp": 1, "haste": True, "desc": "速攻"},
    "u_02": {"id": "u_02", "name": "重装兵", "type": "unit", "cost": 3, "atk": 2, "hp": 5, "taunt": True, "desc": "挑発"},
    "u_03": {"id": "u_03", "name": "魔導士", "type": "unit", "cost": 2, "atk": 3, "hp": 2, "desc": "標準アタッカー"},
    "u_04": {"id": "u_04", "name": "巨兵", "type": "unit", "cost": 6, "atk": 7, "hp": 6, "desc": "大型ユニット"},
    "u_05": {"id": "u_05", "name": "吸血鬼", "type": "unit", "cost": 4, "atk": 3, "hp": 4, "lifesteal": True, "desc": "吸血"},
    "s_01": {"id": "s_01", "name": "雷撃", "type": "spell", "cost": 2, "effect": "damage", "val": 3, "need_target": True, "desc": "単体3点"},
    "s_02": {"id": "s_02", "name": "嵐", "type": "spell", "cost": 4, "effect": "aoe_damage", "val": 2, "need_target": False, "desc": "全体2点"},
    "s_03": {"id": "s_03", "name": "治癒", "type": "spell", "cost": 2, "effect": "heal", "val": 5, "need_target": False, "desc": "自分回復5点"},
    "s_04": {"id": "s_04", "name": "補充", "type": "spell", "cost": 3, "effect": "draw", "val": 2, "need_target": False, "desc": "2枚引く"},
}

class CreateRoomRequest(BaseModel):
    wallet_id: str
    amount: int = Field(..., gt=0)
    max_players: int = Field(2, ge=2, le=3) # 2 or 3

class CardGameSession:
    def __init__(self, room_id: str, host_id: str, host_name: str, host_wallet_id: str, bet_amount: int, max_players: int):
        self.room_id = room_id
        self.host_id = host_id
        self.bet_amount = bet_amount
        self.max_players = max_players
        
        # プレイヤー情報管理 {user_id: {"name": str, "wallet_id": str}}
        self.players: Dict[str, dict] = {
            host_id: {"name": host_name, "wallet_id": host_wallet_id}
        }
        self.player_order: List[str] = [host_id]
        
        self.status = "WAITING"
        self.lock = asyncio.Lock()
        self.timer_task: Optional[asyncio.Task] = None
        self.time_limit = 60
        
        self.turn_user_id: Optional[str] = None
        self.turn_idx: int = 0
        
        self.draft_pool: List[str] = []
        self.draft_options: Dict[str, List[str]] = {}
        self.decks: Dict[str, List[dict]] = {}
        self.hands: Dict[str, List[dict]] = {}
        self.boards: Dict[str, List[dict]] = {}
        self.hp: Dict[str, int] = {}
        self.mp: Dict[str, int] = {}
        self.max_mp: Dict[str, int] = {}
        self.winner_id: Optional[str] = None
        self.message: str = "待機中..."

CARD_SESSIONS: Dict[str, CardGameSession] = {}
CLIENT_CONNECTIONS: Dict[str, Dict[str, WebSocket]] = {}

@router.get("/card", response_class=HTMLResponse)
async def get_card(request: Request):
    return templates.TemplateResponse(request=request, name="card.html")

@router.get("/api/card/rooms")
async def get_rooms():
    return {"rooms": [{"room_id": s.room_id, "host_name": s.players[s.host_id]["name"], "bet_amount": s.bet_amount, "max_players": s.max_players, "current_players": len(s.players)} 
                      for s in CARD_SESSIONS.values() if s.status == "WAITING"]}

@router.post("/api/card/create")
async def create_room(data: CreateRoomRequest, authorization: str = Header(None)):
    user = await get_user_from_token_async(authorization)
    supabase = await get_supabase()

    if supabase:
        w_res = await supabase.table("wallets").select("*").eq("wallet_id", data.wallet_id).eq("user_id", user.id).execute()
        if not w_res.data or w_res.data[0]["balance"] < data.amount:
            raise HTTPException(status_code=400, detail="残高が不足しています")
        
        new_bal = w_res.data[0]["balance"] - data.amount
        await supabase.table("wallets").update({"balance": new_bal}).eq("wallet_id", data.wallet_id).execute()

        p_res = await supabase.table("profiles").select("nickname").eq("id", user.id).execute()
        name = p_res.data[0]["nickname"] if p_res and p_res.data and "nickname" in p_res.data[0] else f"Player-{str(user.id)[:4]}"
    else:
        name = f"Player-{str(user.id)[:4]}"

    room_id = str(uuid.uuid4())[:8]
    session = CardGameSession(room_id, str(user.id), name, data.wallet_id, data.amount, data.max_players)
    CARD_SESSIONS[room_id] = session
    
    set_timer(session, seconds=60)
    return {"room_id": room_id}

@router.websocket("/ws/card/{room_id}")
async def card_websocket(websocket: WebSocket, room_id: str, token: str):
    await websocket.accept()
    try:
        user = await get_user_from_token_async(f"Bearer {token}")
    except Exception as e:
        logger.error(f"Token validation failed: {e}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    user_id = str(user.id)
    session = CARD_SESSIONS.get(room_id)
    if not session:
        await websocket.send_json({"type": "ERROR", "message": "部屋が存在しません"})
        await websocket.close()
        return

    CLIENT_CONNECTIONS.setdefault(room_id, {})[user_id] = websocket

    try:
        async with session.lock:
            if session.status == "WAITING" and user_id not in session.players:
                if len(session.players) >= session.max_players:
                    await websocket.send_json({"type": "ERROR", "message": "部屋が満員です"})
                    await websocket.close()
                    return

                supabase = await get_supabase()
                if supabase:
                    w_res = await supabase.table("wallets").select("*").eq("user_id", user.id).gte("balance", session.bet_amount).execute()
                    if not w_res or not w_res.data:
                        await websocket.send_json({"type": "ERROR", "message": "参加資金が不足しています"})
                        await websocket.close()
                        return
                    
                    guest_w = w_res.data[0]
                    await supabase.table("wallets").update({"balance": guest_w["balance"] - session.bet_amount}).eq("wallet_id", guest_w["wallet_id"]).execute()

                    p_res = await supabase.table("profiles").select("nickname").eq("id", user.id).execute()
                    guest_name = p_res.data[0]["nickname"] if p_res and p_res.data and "nickname" in p_res.data[0] else f"Player-{user_id[:4]}"
                    guest_wallet_id = guest_w["wallet_id"]
                else:
                    guest_name = f"Player-{user_id[:4]}"
                    guest_wallet_id = f"w_{user_id[:4]}"

                session.players[user_id] = {"name": guest_name, "wallet_id": guest_wallet_id}
                session.player_order.append(user_id)

                if len(session.players) == session.max_players:
                    start_draft_phase(session)
                else:
                    session.message = f"参加者を待っています... ({len(session.players)}/{session.max_players})"

        await broadcast_state(room_id)

        while True:
            try:
                payload = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.warning(f"Invalid payload received: {e}")
                continue
            
            try:
                if payload.get("action") == "PING":
                    await websocket.send_json({"type": "PONG"})
                    continue

                async with session.lock:
                    await process_action(session, user_id, payload)
                await broadcast_state(room_id)
            except Exception as e:
                logger.error(f"Error processing action for user {user_id}: {e}")
                await websocket.send_json({"type": "ERROR", "message": "無効な操作、または処理エラーが発生しました"})

    except WebSocketDisconnect:
        logger.info(f"User {user_id} disconnected normally.")
    except Exception as e:
        logger.error(f"Unexpected connection error for user {user_id}: {e}")
    finally:
        await handle_disconnect(room_id, user_id)

async def handle_disconnect(room_id: str, user_id: str):
    if room_id in CLIENT_CONNECTIONS and user_id in CLIENT_CONNECTIONS[room_id]:
        del CLIENT_CONNECTIONS[room_id][user_id]

    session = CARD_SESSIONS.get(room_id)
    if not session: return

    async with session.lock:
        if session.status == "WAITING":
            if user_id == session.host_id:
                session.status = "ENDED"
                session.message = "ホストが退室しました。解散します。"
                for pid, pinfo in session.players.items():
                    await refund_wallet(pinfo["wallet_id"], session.bet_amount)
                await cleanup_room(room_id)
            else:
                pinfo = session.players.pop(user_id, None)
                if pinfo:
                    session.player_order.remove(user_id)
                    await refund_wallet(pinfo["wallet_id"], session.bet_amount)
                    session.message = f"参加者を待っています... ({len(session.players)}/{session.max_players})"
        elif session.status in ["DRAFT", "BATTLE"]:
            session.message = f"{session.players.get(user_id, {}).get('name', 'プレイヤー')} が通信を切断しました。"
            if user_id in session.hp:
                session.hp[user_id] = 0 # 切断者はHP0（敗北）扱い
            await check_battle_state(session)
    
    await broadcast_state(room_id)
    if session and session.status == "ENDED":
        await cleanup_room(room_id)

async def refund_wallet(wallet_id: str, amount: int):
    supabase = await get_supabase()
    if not supabase or not wallet_id: return
    w_res = await supabase.table("wallets").select("balance").eq("wallet_id", wallet_id).execute()
    if w_res and w_res.data:
        await supabase.table("wallets").update({"balance": w_res.data[0]["balance"] + amount}).eq("wallet_id", wallet_id).execute()

async def cleanup_room(room_id: str):
    if room_id in CARD_SESSIONS: del CARD_SESSIONS[room_id]
    if room_id in CLIENT_CONNECTIONS:
        for ws in CLIENT_CONNECTIONS[room_id].values():
            try: await ws.close()
            except: pass
        del CLIENT_CONNECTIONS[room_id]

def set_timer(session: CardGameSession, seconds: int = 30):
    if session.timer_task and not session.timer_task.done():
        session.timer_task.cancel()
    session.time_limit = seconds
    session.timer_task = asyncio.create_task(run_timer(session.room_id))

async def run_timer(room_id: str):
    try:
        while True:
            await asyncio.sleep(1)
            session = CARD_SESSIONS.get(room_id)
            if not session: break
            
            async with session.lock:
                if session.status == "ENDED": break
                session.time_limit -= 1
                
                if session.time_limit <= 0:
                    if session.status == "WAITING":
                        session.status = "ENDED"
                        session.message = "対戦相手が集まりませんでした。"
                        for pid, pinfo in session.players.items():
                            await refund_wallet(pinfo["wallet_id"], session.bet_amount)
                        await broadcast_state(room_id)
                        await cleanup_room(room_id)
                        break
                    elif session.status == "DRAFT":
                        auto_draft(session)
                    elif session.status == "BATTLE":
                        switch_turn(session)

            if session.time_limit % 5 == 0 or session.time_limit <= 5:
                await broadcast_state(room_id)
    except asyncio.CancelledError:
        pass

def start_draft_phase(session: CardGameSession):
    session.status = "DRAFT"
    session.message = "ドラフトフェーズ：カードを選択してください"
    pool_size = 6 * session.max_players
    pool = list(CARD_DATABASE.keys()) * pool_size
    random.shuffle(pool)
    session.draft_pool = pool
    
    for uid in session.player_order:
        session.decks[uid] = []
    
    session.turn_idx = 0
    session.turn_user_id = session.player_order[session.turn_idx]
    generate_draft_candidates(session)
    set_timer(session, 15)

def generate_draft_candidates(session: CardGameSession):
    if len(session.draft_pool) >= 3:
        session.draft_options[session.turn_user_id] = [session.draft_pool.pop() for _ in range(3)]

def auto_draft(session: CardGameSession):
    uid = session.turn_user_id
    opts = session.draft_options.get(uid, [])
    if opts:
        session.decks[uid].append(copy.deepcopy(CARD_DATABASE[opts[0]]))
    advance_draft(session)

def advance_draft(session: CardGameSession):
    if all(len(session.decks[uid]) == 6 for uid in session.player_order):
        start_battle_phase(session)
    else:
        session.turn_idx = (session.turn_idx + 1) % session.max_players
        session.turn_user_id = session.player_order[session.turn_idx]
        generate_draft_candidates(session)
        set_timer(session, 15)

def start_battle_phase(session: CardGameSession):
    session.status = "BATTLE"
    session.message = "バトル開始！"
    
    for uid in session.player_order:
        random.shuffle(session.decks[uid])
        session.hp[uid] = 20
        session.max_mp[uid] = 1
        session.mp[uid] = 1
        session.boards[uid] = []
        session.hands[uid] = []
        
        for _ in range(min(3, len(session.decks[uid]))):
            c = session.decks[uid].pop()
            c["instance_id"] = str(uuid.uuid4())[:8]
            session.hands[uid].append(c)

    session.turn_idx = 0
    session.turn_user_id = session.player_order[session.turn_idx]
    set_timer(session, 45)

def get_unit_owner(session: CardGameSession, target_id: str):
    for uid in session.player_order:
        for u in session.boards[uid]:
            if u["instance_id"] == target_id:
                return uid, u
    return None, None

async def process_action(session: CardGameSession, user_id: str, action: dict):
    if session.status == "ENDED" or session.turn_user_id != user_id: return
    act = action.get("action")

    if session.status == "DRAFT" and act == "PICK_CARD":
        cid = action.get("card_id")
        opts = session.draft_options.get(user_id, [])
        if cid in opts:
            session.decks[user_id].append(copy.deepcopy(CARD_DATABASE[cid]))
            advance_draft(session)

    elif session.status == "BATTLE":
        if act == "PLAY_HAND":
            instance_id = action.get("card_instance_id")
            target = action.get("target") 

            hand = session.hands[user_id]
            idx = next((i for i, c in enumerate(hand) if c.get("instance_id") == instance_id), None)
            if idx is None: return

            card = hand[idx]
            if session.mp[user_id] < card["cost"]: return

            session.mp[user_id] -= card["cost"]
            played = hand.pop(idx)
            session.message = f"{session.players[user_id]['name']} が {played['name']} を使用！"

            if played["type"] == "unit":
                session.boards[user_id].append({
                    "instance_id": str(uuid.uuid4())[:8],
                    "card_id": played["id"],
                    "name": played["name"],
                    "atk": played["atk"],
                    "max_hp": played["hp"],
                    "curr_hp": played["hp"],
                    "can_attack": played.get("haste", False),
                    "taunt": played.get("taunt", False),
                    "lifesteal": played.get("lifesteal", False)
                })
            elif played["type"] == "spell":
                resolve_spell(session, user_id, played, target)

            await check_battle_state(session)

        elif act == "DECLARE_ATTACK":
            atk_id = action.get("attacker_id")
            target = action.get("target")

            attacker = next((u for u in session.boards[user_id] if u["instance_id"] == atk_id), None)
            if not attacker or not attacker["can_attack"] or not target: return

            target_id = target.get("id")
            if target.get("type") == "hero":
                opp_id = target_id
                defender = None
            elif target.get("type") == "unit":
                opp_id, defender = get_unit_owner(session, target_id)
            else:
                return

            if opp_id == user_id or opp_id not in session.player_order or session.hp.get(opp_id, 0) <= 0:
                return

            taunts = [u for u in session.boards[opp_id] if u.get("taunt")]
            if taunts:
                if target.get("type") != "unit" or target_id not in [u["instance_id"] for u in taunts]:
                    return

            session.message = f"{attacker['name']} の攻撃！"
            if target.get("type") == "hero":
                session.hp[opp_id] -= attacker["atk"]
                if attacker["lifesteal"]: session.hp[user_id] = min(20, session.hp[user_id] + attacker["atk"])
                attacker["can_attack"] = False
            elif target.get("type") == "unit" and defender:
                defender["curr_hp"] -= attacker["atk"]
                attacker["curr_hp"] -= defender["atk"]
                if attacker["lifesteal"]: session.hp[user_id] = min(20, session.hp[user_id] + attacker["atk"])
                attacker["can_attack"] = False

            await check_battle_state(session)

        elif act == "END_TURN":
            switch_turn(session)

def resolve_spell(session: CardGameSession, user_id: str, card: dict, target: Optional[dict]):
    eff, val = card.get("effect"), card.get("val", 0)

    if eff == "damage" and target:
        if target.get("type") == "hero" and target.get("id") in session.player_order:
            session.hp[target.get("id")] -= val
        elif target.get("type") == "unit":
            opp_id, unit = get_unit_owner(session, target.get("id"))
            if unit: unit["curr_hp"] -= val
    elif eff == "aoe_damage":
        for pid in session.player_order:
            if pid != user_id and session.hp.get(pid, 0) > 0:
                for u in session.boards[pid]: u["curr_hp"] -= val
    elif eff == "heal":
        session.hp[user_id] = min(20, session.hp[user_id] + val)
    elif eff == "draw":
        for _ in range(val):
            if session.decks[user_id] and len(session.hands[user_id]) < 7:
                c = session.decks[user_id].pop()
                c["instance_id"] = str(uuid.uuid4())[:8]
                session.hands[user_id].append(c)

def switch_turn(session: CardGameSession):
    for _ in range(session.max_players):
        session.turn_idx = (session.turn_idx + 1) % session.max_players
        nxt = session.player_order[session.turn_idx]
        if session.hp.get(nxt, 0) > 0:
            break

    session.turn_user_id = nxt
    session.message = f"{session.players[nxt]['name']} のターン"
    
    session.max_mp[nxt] = min(10, session.max_mp[nxt] + 1)
    session.mp[nxt] = session.max_mp[nxt]
    for u in session.boards[nxt]: u["can_attack"] = True

    if session.decks[nxt] and len(session.hands[nxt]) < 7:
        c = session.decks[nxt].pop()
        c["instance_id"] = str(uuid.uuid4())[:8]
        session.hands[nxt].append(c)

    if len(session.hands[nxt]) == 0:
        rand_id = random.choice(list(CARD_DATABASE.keys()))
        c = copy.deepcopy(CARD_DATABASE[rand_id])
        c["instance_id"] = str(uuid.uuid4())[:8]
        session.hands[nxt].append(c)
        session.message = f"【奇跡のドロー】{session.players[nxt]['name']} は手札がないためランダムなカードを生成した！"

    set_timer(session, 45)

async def check_battle_state(session: CardGameSession):
    alive_players = []
    
    for uid in session.player_order:
        session.boards[uid] = [u for u in session.boards[uid] if u["curr_hp"] > 0]
        if session.hp.get(uid, 0) > 0:
            alive_players.append(uid)

    if len(alive_players) == 1:
        winner = alive_players[0]
        session.status = "ENDED"
        session.winner_id = winner
        session.message = f"{session.players[winner]['name']} の勝利！"
        if session.timer_task: session.timer_task.cancel()
        await settle_payout(session, winner=winner)
    elif len(alive_players) == 0:
        session.status = "ENDED"
        session.message = "引き分け！全員に資金を返却します。"
        if session.timer_task: session.timer_task.cancel()
        await settle_payout(session, winner=None)
    else:
        if session.hp.get(session.turn_user_id, 0) <= 0:
            switch_turn(session)

async def settle_payout(session: CardGameSession, winner: Optional[str]):
    supabase = await get_supabase()
    if not supabase: return
    if winner is None:
        for uid in session.player_order:
            await refund_wallet(session.players[uid]["wallet_id"], session.bet_amount)
    else:
        win_wid = session.players[winner]["wallet_id"]
        reward = session.bet_amount * session.max_players
        w_res = await supabase.table("wallets").select("balance").eq("wallet_id", win_wid).execute()
        if w_res and w_res.data:
            await supabase.table("wallets").update({"balance": w_res.data[0]["balance"] + reward}).eq("wallet_id", win_wid).execute()

async def broadcast_state(room_id: str):
    session = CARD_SESSIONS.get(room_id)
    if not session or room_id not in CLIENT_CONNECTIONS: return

    disconnected_users = []

    for uid, ws in list(CLIENT_CONNECTIONS[room_id].items()):
        try:
            state = mask_session_for_client(session, uid)
            await ws.send_json({"type": "SYNC_STATE", "payload": state})
        except WebSocketDisconnect:
            disconnected_users.append(uid)
        except Exception as e:
            logger.error(f"Broadcast error for {uid}: {e}")
            disconnected_users.append(uid)
            
    for uid in disconnected_users:
        await handle_disconnect(room_id, uid)

def mask_session_for_client(session: CardGameSession, target_uid: str) -> dict:
    masked_hands = {}
    for uid in session.player_order:
        if uid == target_uid:
            masked_hands[uid] = session.hands.get(uid, [])
        else:
            masked_hands[uid] = [{"instance_id": f"masked_{i}"} for i in range(len(session.hands.get(uid, [])))]

    return {
        "room_id": session.room_id,
        "status": session.status,
        "message": session.message,
        "time_limit": session.time_limit,
        "turn_user_id": session.turn_user_id,
        "players": {uid: {"id": uid, "name": p["name"]} for uid, p in session.players.items()},
        "max_players": session.max_players,
        "bet_amount": session.bet_amount,
        "draft_options": [CARD_DATABASE[cid] for cid in session.draft_options.get(target_uid, [])] if session.status == "DRAFT" else [],
        "hands": masked_hands,
        "deck_counts": {uid: len(deck) for uid, deck in session.decks.items()},
        "my_deck": session.decks.get(target_uid, []),
        "boards": session.boards,
        "hp": session.hp,
        "mp": session.mp,
        "max_mp": session.max_mp,
        "winner_id": session.winner_id,
        "your_user_id": target_uid
    }
