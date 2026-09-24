import asyncio
import copy
import logging
import os
import random
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Any
from bot import BOT_USER_ID, BOT_USER_NAME, HUMAN_DRAFT_MEMORIES, process_super_ai_turn

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

# 通常マスターデータ（ドラフト・通常ドロー用プール）
BASE_CARD_DATABASE = {
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

# BOT戦限定の配布専用カード（ドラフトや通常生成には出現しない）
BOT_EXCLUSIVE_CARDS = {
    "u_08": {
        "id": "u_08",
        "name": "重装備巨兵",
        "type": "unit",
        "cost": 6,
        "atk": 7,
        "hp": 6,
        "taunt": True,
        "cannot_assassinate": True,
        "desc": "【BOT戦・プレイヤー専用】挑発・常時城壁(被ダメ-1)・暗殺無効"
    },
    "s_10": {
        "id": "s_10",
        "name": "王家の矛",
        "type": "spell",
        "cost": 8,
        "effect": "royal_spear",
        "val": 1,
        "need_target": False,
        "desc": "【BOT専用】味方ユニット全体の攻撃力+1(小人を除く)"
    }
}

# 全カード辞書（実行時の参照・図鑑用）
CARD_DATABASE = {**BASE_CARD_DATABASE, **BOT_EXCLUSIVE_CARDS}
BASE_CARD_IDS = list(BASE_CARD_DATABASE.keys())

class CreateRoomRequest(BaseModel):
    wallet_id: str
    amount: int = Field(..., ge=0)
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
        sel
