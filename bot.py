import asyncio
import copy
import logging
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("CardBot")

# BOT基本設定
BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "クソザコBOT"

# カードの基本Tier（標準価値）
BOT_CARD_TIER = {
    "s_05": 100, "u_06": 98, "s_02": 95, "s_01": 90,
    "u_02": 88,  "s_09": 85, "u_05": 80, "s_04": 75,
    "u_04": 70,  "u_01": 65, "s_07": 60, "s_08": 55,
    "u_03": 50,  "u_07": 30, "s_03": 25, "s_06": 10,
}

# 部屋ごとの相手ドラフト記憶
HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}


class SuperBotEngine:
    def __init__(self, session: Any, card_database: Dict[str, dict]):
        self.session = session
        self.db = card_database
        self.bot_id = BOT_USER_ID

    # ----------------------------------------------------
    # 動的カード評価（状況に応じたTier変動）
    # ----------------------------------------------------
    def get_dynamic_tier(self, card_id: str, my_hp: int, my_hand_len: int, opp_board_len: int) -> int:
        base_score = BOT_CARD_TIER.get(card_id, 50)
        
        # 1. 自身のHPがピンチ（10以下）なら防衛・回復カードを超爆上げ
        if my_hp <= 10:
            if card_id in ["s_03", "u_02"]:
                base_score += 50
            elif card_id == "s_09":
                base_score += 30

        # 2. 相手の盤面が展開されているなら全体攻撃（嵐）を最優先
        if opp_board_len >= 3 and card_id == "s_02":
            base_score += 60

        # 3. 手札が枯渇気味（2枚以下）ならドロー呪文を最優先
        if my_hand_len <= 2 and card_id == "s_04":
            base_score += 45

        return base_score

    # ----------------------------------------------------
    # 相手の次ターン最大攻撃力＋直火ダメージ予測
    # ----------------------------------------------------
    def predict_opponent_max_damage(self, opp_id: str) -> Tuple[int, int]:
        opp_board = self.session.boards.get(opp_id, [])
        opp_hand = self.session.hands.get(opp_id, [])
        next_opp_mp = min(10, self.session.max_mp.get(opp_id, 1) + 1)

        # 盤面からの直接打点
        board_dmg = sum(
            u.get("atk", 0) * u.get("max_attacks", 1)
            for u in opp_board
            if u.get("frozen_turns", 0) <= 1
        )

        # 手札からの確定直火魔法ダメージ
        spell_dmg = sum(
            c.get("val", 0) for c in opp_hand
            if c.get("type") == "spell" and c.get("effect") == "damage" and c.get("cost", 99) <= next_opp_mp
        )

        # 未知カードの安全バッファー（手札1枚につき3点警戒）
        human_memories = HUMAN_DRAFT_MEMORIES.get(self.session.room_id, [])
        unknown_count = max(0, len(opp_hand) - len(human_memories))
        safety_buffer = unknown_count * 3

        return board_dmg + spell_dmg + safety_buffer, board_dmg

    # ----------------------------------------------------
    # メイン思考・行動決定ロジック
    # ----------------------------------------------------
    def decide_best_action(self) -> Optional[dict]:
        try:
            opp_id = next((uid for uid in self.session.player_order if uid != self.bot_id), None)
            if not opp_id:
                return None

            bot_hp = self.session.hp.get(self.bot_id, 0)
            opp_hp = self.session.hp.get(opp_id, 0)
            bot_mp = self.session.mp.get(self.bot_id, 0)

            bot_board = self.session.boards.get(self.bot_id, [])
            opp_board = self.session.boards.get(opp_id, [])
            bot_hand = self.session.hands.get(self.bot_id, [])
            opp_hand = self.session.hands.get(opp_id, [])

            opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]
            next_potential_dmg, _ = self.predict_opponent_max_damage(opp_id)
            is_in_desperate_danger = (bot_hp <= next_potential_dmg)

            # ====================================================
            # 1. 確定リーサル（即勝ち）のミリ秒判定
            # ====================================================
            board_atk_sum = sum(
                u.get("atk", 0) * u.get("attacks_left", 1)
                for u in bot_board
                if u.get("can_attack") and u.get("frozen_turns", 0) == 0 and u.get("attacks_left", 0) > 0
            )
            playable_spells = [c for c in bot_hand if c.get("type") == "spell" and c.get("cost", 99) <= bot_mp]
            direct_dmg_spells = [c for c in playable_spells if c.get("effect") == "damage"]
            hand_dmg_sum = sum(c.get("val", 0) for c in direct_dmg_spells)

            # 直火で削り切れる
            for spell in direct_dmg_spells:
                if spell.get("val", 0) >= opp_hp:
                    return {"action": "PLAY_HAND", "card_instance_id": spell["instance_id"], "target": {"type": "hero", "id": opp_id}}

            # 暗殺で挑発を消せば打点が届く
            if opp_taunts and (board_atk_sum + hand_dmg_sum >= opp_hp):
                assassinate = next((c for c in playable_spells if c.get("id") == "s_05"), None)
                if assassinate:
                    return {"action": "PLAY_HAND", "card_instance_id": assassinate["instance_id"], "target": {"type": "unit", "id": opp_taunts[0]["instance_id"]}}

            # 挑発無視/不使用での総攻撃リーサル
            if not
