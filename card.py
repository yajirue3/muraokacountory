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

# マスターデータ（カードデータベース）
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
    "s_05": {"id": "s_05", "name": "暗殺者", "type": "spell", "cost": 6, "effect": "assassinate", "need_target": True, "desc": "敵ユニット1体を即死"},
    "u_06": {"id": "u_06", "name": "小人", "type": "unit", "cost": 5, "atk": 1, "hp": 2, "max_attacks": 3, "ranged": True, "desc": "1点×3回攻撃(対象自由・反撃無効)"},
    "u_07": {"id": "u_07", "name": "奇術師", "type": "unit", "cost": 4, "atk": 0, "hp": 0, "random_stat": True, "desc": "召喚時ステータスランダム決定"},
    "s_06": {"id": "s_06", "name": "再編", "type": "spell", "cost": 1, "effect": "reshape", "need_target": False, "desc": "手札をランダムに1枚捨て、1枚引く"},
    "s_07": {"id": "s_07", "name": "凍結", "type": "spell", "cost": 1, "effect": "freeze", "need_target": True, "desc": "ユニット1体の次ターン攻撃を封じる"},
    "s_08": {"id": "s_08", "name": "火傷", "type": "spell", "cost": 1, "effect": "burn", "need_target": True, "desc": "ユニット1体に3ターン毎ターン終了時1ダメージ"},
    "s_09": {"id": "s_09", "name": "城壁", "type": "spell", "cost": 2, "effect": "wall", "need_target": True, "desc": "ユニット1体の被ダメージを3ターンの間1軽減"},
}

class CreateRoomRequest(BaseModel):
    wallet_id: str
    amount: int = Field(..., gt=0)
    max_players: int = Field(2, ge=2, le=3)

class CardGameSession:
    def __init__(self, room_id: str, host_id: str, host_name: str, host_wallet_id: str, bet_amount: int, max_players: int):
        self.room_id = room_id
        self.host_id = host_id
        self.bet_amount = bet_amount
        self.max_players = max_players
        
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

# カード一覧（図鑑）取得用API
@router.get("/api/card/database")
async def get_card_database():
    return {"cards": list(CARD_DATABASE.values())}

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

                if payload.get("action") == "SYNC":
                    await broadcast_state(room_id)
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
    pool_size = 15 * session.max_players
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
    if len(session.draft_pool) >= 5:
        session.draft_options[session.turn_user_id] = [session.draft_pool.pop() for _ in range(5)]
    else:
        # 万が一プールが尽きた場合全データベースから補充
        pool = list(CARD_DATABASE.keys())
        random.shuffle(pool)
        session.draft_options[session.turn_user_id] = pool[:5]

def auto_draft(session: CardGameSession):
    uid = session.turn_user_id
    opts = session.draft_options.get(uid, [])
    if opts:
        session.decks[uid].append(copy.deepcopy(CARD_DATABASE[opts[0]]))
    advance_draft(session)

def advance_draft(session: CardGameSession):
    # ピック上限を6枚から12枚に変更してデッキ枚数を確保
    if all(len(session.decks[uid]) == 12 for uid in session.player_order):
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

# ドローヘルパー関数（山札が切れていたらランダム生成）
def draw_card_or_generate(session: CardGameSession, user_id: str):
    if len(session.hands[user_id]) >= 7:
        return
    if session.decks[user_id]:
        c = session.decks[user_id].pop()
    else:
        rand_id = random.choice(list(CARD_DATABASE.keys()))
        c = copy.deepcopy(CARD_DATABASE[rand_id])
    c["instance_id"] = str(uuid.uuid4())[:8]
    session.hands[user_id].append(c)

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
                atk_val = played.get("atk", 0)
                hp_val = played.get("hp", 1)
                name_val = played["name"]

                # 奇術師のランダムステータス生成
                if played.get("random_stat"):
                    atk_val = random.randint(1, 5)
                    hp_val = random.randint(1, 6)
                    name_val = f"奇術師({atk_val}/{hp_val})"

                session.boards[user_id].append({
                    "instance_id": str(uuid.uuid4())[:8],
                    "card_id": played["id"],
                    "name": name_val,
                    "atk": atk_val,
                    "max_hp": hp_val,
                    "curr_hp": hp_val,
                    "can_attack": played.get("haste", False),
                    "taunt": played.get("taunt", False),
                    "lifesteal": played.get("lifesteal", False),
                    "attacks_left": played.get("max_attacks", 1),
                    "max_attacks": played.get("max_attacks", 1),
                    "ranged": played.get("ranged", False),
                    "frozen_turns": 0,
                    "burn_turns": 0,
                    "wall_turns": 0
                })
            elif played["type"] == "spell":
                resolve_spell(session, user_id, played, target)

            await check_battle_state(session)

        elif act == "DECLARE_ATTACK":
            atk_id = action.get("attacker_id")
            target = action.get("target")

            attacker = next((u for u in session.boards[user_id] if u["instance_id"] == atk_id), None)
            if not attacker or not attacker["can_attack"] or not target: return
            if attacker.get("frozen_turns", 0) > 0: return

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
            
            atk_val = attacker["atk"]
            def_atk = defender["atk"] if defender else 0

            if target.get("type") == "hero":
                session.hp[opp_id] -= atk_val
                if attacker["lifesteal"]: session.hp[user_id] = min(20, session.hp[user_id] + atk_val)
            elif target.get("type") == "unit" and defender:
                # 攻撃時の城壁（ダメージ軽減）適用
                dmg_to_def = max(0, atk_val - 1) if defender.get("wall_turns", 0) > 0 else atk_val
                defender["curr_hp"] -= dmg_to_def
                
                # 遠距離（ranged）でなければ反撃を受ける
                if not attacker.get("ranged", False):
                    dmg_to_atk = max(0, def_atk - 1) if attacker.get("wall_turns", 0) > 0 else def_atk
                    attacker["curr_hp"] -= dmg_to_atk
                
                if attacker["lifesteal"]: session.hp[user_id] = min(20, session.hp[user_id] + dmg_to_def)

            # 攻撃回数の消費
            attacker["attacks_left"] = attacker.get("attacks_left", 1) - 1
            if attacker["attacks_left"] <= 0:
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
            # ダメージスペルにも城壁軽減を適用
            if unit: unit["curr_hp"] -= max(0, val - 1) if unit.get("wall_turns", 0) > 0 else val
    elif eff == "aoe_damage":
        for pid in session.player_order:
            if pid != user_id and session.hp.get(pid, 0) > 0:
                for u in session.boards[pid]:
                    u["curr_hp"] -= max(0, val - 1) if u.get("wall_turns", 0) > 0 else val
    elif eff == "heal":
        session.hp[user_id] = min(20, session.hp[user_id] + val)
    elif eff == "draw":
        # 山札が空でも確実に引ける（ランダム生成）よう改善
        for _ in range(val):
            draw_card_or_generate(session, user_id)
    elif eff == "assassinate" and target and target.get("type") == "unit":
        opp_id, unit = get_unit_owner(session, target.get("id"))
        if unit:
            unit["curr_hp"] = 0
            session.message = f"{session.players[user_id]['name']} の暗殺者が対象を仕留めた！"
    elif eff == "reshape":
        # 手札をランダムに1枚捨てる
        if session.hands[user_id]:
            discard_idx = random.randrange(len(session.hands[user_id]))
            session.hands[user_id].pop(discard_idx)
        # 確実に1枚引く（山札がなければ生成）
        draw_card_or_generate(session, user_id)
    elif eff == "freeze" and target and target.get("type") == "unit":
        opp_id, unit = get_unit_owner(session, target.get("id"))
        if unit:
            unit["frozen_turns"] = 1
            session.message = f"{unit['name']} は凍結された！"
    elif eff == "burn" and target and target.get("type") == "unit":
        opp_id, unit = get_unit_owner(session, target.get("id"))
        if unit:
            unit["burn_turns"] = 3
            session.message = f"{unit['name']} は火傷を負った！"
    elif eff == "wall" and target and target.get("type") == "unit":
        opp_id, unit = get_unit_owner(session, target.get("id"))
        if unit:
            unit["wall_turns"] = 3
            session.message = f"{unit['name']} に城壁が付与された！"

def switch_turn(session: CardGameSession):
    # ターン終了時の処理（火傷ダメージの適用など）
    if session.turn_user_id:
        for u in session.boards[session.turn_user_id]:
            if u.get("burn_turns", 0) > 0:
                u["curr_hp"] -= 1
                u["burn_turns"] -= 1
        # 死亡判定の整理
        session.boards[session.turn_user_id] = [u for u in session.boards[session.turn_user_id] if u["curr_hp"] > 0]

    for _ in range(session.max_players):
        session.turn_idx = (session.turn_idx + 1) % session.max_players
        nxt = session.player_order[session.turn_idx]
        if session.hp.get(nxt, 0) > 0:
            break

    session.turn_user_id = nxt
    session.message = f"{session.players[nxt]['name']} のターン"
    
    session.max_mp[nxt] = min(10, session.max_mp[nxt] + 1)
    session.mp[nxt] = session.max_mp[nxt]

    # 次ターンプレイヤーのユニット状態更新
    for u in session.boards[nxt]:
        if u.get("frozen_turns", 0) > 0:
            u["can_attack"] = False
            u["frozen_turns"] -= 1
        else:
            u["can_attack"] = True
        
        if u.get("wall_turns", 0) > 0:
            u["wall_turns"] -= 1

        u["attacks_left"] = u.get("max_attacks", 1)

    # 通常ドロー（山札切れ時は生成）
    draw_card_or_generate(session, nxt)

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
