import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("CardBot")

# ====================================================
# BOT基本設定
# ====================================================
BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "クソザコBOT"

# カードの基本Tier（標準価値）
BOT_CARD_TIER = {
    "s_05": 100, "u_06": 98, "s_02": 95, "s_01": 90,
    "u_02": 88,  "s_09": 85, "u_05": 80, "s_04": 75,
    "u_04": 70,  "u_01": 65, "s_07": 60, "s_08": 55,
    "u_03": 50,  "u_07": 30, "s_03": 25, "s_06": 10,
}

# card.py 側からのインポートエラー回避用
HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}

class SuperBotEngine:
    def __init__(self, session: Any, card_database: Dict[str, dict]):
        self.session = session
        self.db = card_database
        self.bot_id = BOT_USER_ID

    # ----------------------------------------------------
    # 動的カード評価（状況とマナカーブに応じたTier変動）
    # ----------------------------------------------------
    def get_dynamic_tier(self, card_id: str, card_cost: int, my_hp: int, my_hand_len: int, opp_board_len: int, current_turn: int) -> int:
        base_score = BOT_CARD_TIER.get(card_id, 50)
        
        if current_turn <= 3:
            if card_cost <= current_turn:
                base_score += 40
            else:
                base_score -= 50

        if my_hp <= 10:
            if card_id in ["s_03", "u_02"]:
                base_score += 50
            elif card_id == "s_09":
                base_score += 30

        if opp_board_len >= 3 and card_id == "s_02":
            base_score += 60

        if my_hand_len <= 2 and card_id == "s_04":
            base_score += 45

        return base_score

    
    # ----------------------------------------------------
    # 未来予測：相手の次ターン確定最大攻撃力＋直火ダメージ
    # ----------------------------------------------------
    def predict_opponent_max_damage(self, opp_id: str) -> Tuple[int, int]:
        opp_board = self.session.boards.get(opp_id, [])
        opp_hand = self.session.hands.get(opp_id, [])
        next_opp_mp = min(10, self.session.max_mp.get(opp_id, 1) + 1)

        board_dmg = sum(
            u.get("atk", 0) * u.get("max_attacks", 1)
            for u in opp_board if u.get("frozen_turns", 0) <= 1
        )

        spell_dmg = sum(
            c.get("val", 0) for c in opp_hand
            if c.get("type") == "spell" and c.get("effect") == "damage" and c.get("cost", 99) <= next_opp_mp
        )

        return board_dmg + spell_dmg, board_dmg

    # ----------------------------------------------------
    # メイン思考・行動決定ロジック
    # ----------------------------------------------------
    def decide_best_action(self) -> Optional[dict]:
        try:
            opp_id = next((uid for uid in self.session.player_order if uid != self.bot_id), None)
            if not opp_id: return None

            bot_hp = self.session.hp.get(self.bot_id, 0)
            opp_hp = self.session.hp.get(opp_id, 0)
            bot_mp = self.session.mp.get(self.bot_id, 0)
            current_turn = self.session.max_mp.get(self.bot_id, 1)
            next_opp_mp = min(10, self.session.max_mp.get(opp_id, 1) + 1)

            bot_board = self.session.boards.get(self.bot_id, [])
            opp_board = self.session.boards.get(opp_id, [])
            bot_hand = self.session.hands.get(self.bot_id, [])
            opp_hand = self.session.hands.get(opp_id, [])

            opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]
            
            next_potential_dmg, _ = self.predict_opponent_max_damage(opp_id)
            is_in_desperate_danger = (bot_hp <= next_potential_dmg)

            # === 1. 確定リーサル（即勝ち）判定 ===
            board_atk_sum = sum(
                u.get("atk", 0) * u.get("attacks_left", 1)
                for u in bot_board if u.get("can_attack") and u.get("frozen_turns", 0) == 0 and u.get("attacks_left", 0) > 0
            )
            playable_spells = [c for c in bot_hand if c.get("type") == "spell" and c.get("cost", 99) <= bot_mp]
            direct_dmg_spells = [c for c in playable_spells if c.get("effect") == "damage"]
            hand_dmg_sum = sum(c.get("val", 0) for c in direct_dmg_spells)

            for spell in direct_dmg_spells:
                if spell.get("val", 0) >= opp_hp:
                    return {"action": "PLAY_HAND", "card_instance_id": spell["instance_id"], "target": {"type": "hero", "id": opp_id}}

            if opp_taunts and (board_atk_sum + hand_dmg_sum >= opp_hp):
                assassinate = next((c for c in playable_spells if c.get("id") == "s_05"), None)
                if assassinate:
                    return {"action": "PLAY_HAND", "card_instance_id": assassinate["instance_id"], "target": {"type": "unit", "id": opp_taunts[0]["instance_id"]}}

            if not opp_taunts and (board_atk_sum >= opp_hp or board_atk_sum + hand_dmg_sum >= opp_hp):
                for u in bot_board:
                    if u.get("can_attack") and u.get("frozen_turns", 0) == 0 and u.get("attacks_left", 0) > 0:
                        return {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "hero", "id": opp_id}}

            # === 2. 高危険度目標の優先処理 ===
            priority_targets = [u for u in opp_board if u.get("card_id") == "u_03" or u.get("atk", 0) >= 4]
            if priority_targets:
                target = max(priority_targets, key=lambda x: x.get("atk", 0))
                for spell in playable_spells:
                    if spell.get("effect") == "damage" and spell.get("val", 0) >= target.get("curr_hp", 0):
                        return {"action": "PLAY_HAND", "card_instance_id": spell["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}}

            # === 3. 相手の確定リーサルに対するエマージェンシー防衛 ===
            if is_in_desperate_danger:
                assassinate = next((c for c in playable_spells if c.get("id") == "s_05"), None)
                if assassinate and opp_board:
                    best_target = max(opp_board, key=lambda x: x.get("atk", 0))
                    return {"action": "PLAY_HAND", "card_instance_id": assassinate["instance_id"], "target": {"type": "unit", "id": best_target["instance_id"]}}
                heal_spell = next((c for c in playable_spells if c.get("effect") == "heal"), None)
                if heal_spell:
                    return {"action": "PLAY_HAND", "card_instance_id": heal_spell["instance_id"], "target": None}
                
                taunt_unit = next((c for c in bot_hand if c.get("taunt") and c.get("cost", 99) <= bot_mp), None)
                if taunt_unit:
                    return {"action": "PLAY_HAND", "card_instance_id": taunt_unit["instance_id"], "target": None}

            # === 4. 最適有利トレード ===
            for attacker in bot_board:
                if attacker.get("can_attack") and attacker.get("frozen_turns", 0) == 0 and attacker.get("attacks_left", 0) > 0:
                    atk_val = attacker.get("atk", 0)
                    
                    if attacker.get("card_id") == "u_06":
                        if opp_taunts:
                            target_t = next((u for u in opp_taunts if u.get("wall_turns", 0) == 0), opp_taunts[0])
                            return {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": {"type": "unit", "id": target_t["instance_id"]}}
                        elif opp_board:
                            target_e = max(opp_board, key=lambda x: x.get("atk", 0))
                            return {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": {"type": "unit", "id": target_e["instance_id"]}}
                        else:
                            return {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": {"type": "hero", "id": opp_id}}

                    if opp_taunts:
                        for taunt in opp_taunts:
                            dmg = max(0, atk_val - 1) if taunt.get("wall_turns", 0) > 0 else atk_val
                            if dmg > 0:
                                return {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": {"type": "unit", "id": taunt["instance_id"]}}
                    else:
                        for defender in opp_board:
                            dmg_to_def = max(0, atk_val - 1) if defender.get("wall_turns", 0) > 0 else atk_val
                            dmg_to_atk = max(0, defender.get("atk", 0) - 1) if attacker.get("wall_turns", 0) > 0 else defender.get("atk", 0)
                            if (dmg_to_def >= defender.get("curr_hp", 0) and dmg_to_atk < attacker.get("curr_hp", 0)) or (is_in_desperate_danger and dmg_to_def >= defender.get("curr_hp", 0)):
                                return {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": {"type": "unit", "id": defender["instance_id"]}}

            # === 5. 手札プレイ（相手の手札カンニング対応） ===
            playable = [c for c in bot_hand if c.get("cost", 99) <= bot_mp]
            opp_hand_ids = [c.get("id") for c in opp_hand]
            
            if "s_02" in opp_hand_ids and next_opp_mp >= 4:
                playable = [c for c in playable if not (c.get("type") == "unit" and c.get("hp", 0) <= 2 and not c.get("haste"))]
            if "s_05" in opp_hand_ids and next_opp_mp >= 6:
                playable = [c for c in playable if not (c.get("id") == "u_04")]

            if playable:
                has_board_unit = len(bot_board) > 0
                for c in playable:
                    score = self.get_dynamic_tier(c.get("id"), c.get("cost", 0), bot_hp, len(bot_hand), len(opp_board), current_turn)
                    if not has_board_unit and c.get("type") == "unit":
                        score += 50
                    c["_temp_score"] = score

                playable.sort(key=lambda c: c["_temp_score"], reverse=True)

                for card in playable:
                    c_type = card.get("type")
                    eff = card.get("effect")

                    if c_type == "unit":
                        if len(bot_board) < 7:
                            return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": None}

                    elif c_type == "spell":
                        if card.get("id") == "s_09":
                            if not bot_board:
                                continue
                            taunt_units = [u for u in bot_board if u.get("taunt") and u.get("wall_turns", 0) == 0]
                            target_unit = taunt_units[0] if taunt_units else max(bot_board, key=lambda x: x.get("curr_hp", 0))
                            return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": target_unit["instance_id"]}}

                        if eff == "assassinate":
                            targets = [u for u in opp_board if u.get("curr_hp", 0) >= 4 or u.get("atk", 0) >= 3]
                            if targets:
                                best_t = max(targets, key=lambda x: x.get("atk", 0) + x.get("curr_hp", 0))
                                return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": best_t["instance_id"]}}
                            continue

                        elif eff == "aoe_damage":
                            if len(opp_board) >= 2 or any(u.get("curr_hp", 0) <= 2 for u in opp_board):
                                return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": None}
                            continue

                        elif eff == "draw":
                            if len(bot_hand) <= 5:
                                return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": None}
                            continue

                        elif eff == "heal":
                            if bot_hp <= 15:
                                return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": None}
                            continue

                        elif eff == "damage":
                            targets = [u for u in opp_board if u.get("curr_hp", 0) <= 3]
                            if targets:
                                best_t = max(targets, key=lambda x: x.get("atk", 0))
                                return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": best_t["instance_id"]}}
                            return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "hero", "id": opp_id}}

                        elif eff == "freeze":
                            targets = [u for u in opp_board if u.get("frozen_turns", 0) == 0 and u.get("atk", 0) >= 2]
                            if targets:
                                best_t = max(targets, key=lambda x: x.get("atk", 0))
                                return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": best_t["instance_id"]}}
                            continue

                        elif eff == "wall":
                            targets = [u for u in bot_board if u.get("wall_turns", 0) == 0]
                            if targets:
                                best_t = max(targets, key=lambda x: x.get("curr_hp", 0))
                                return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": best_t["instance_id"]}}
                            continue

            # === 6. 残存ユニットでの顔面攻撃 ===
            for attacker in bot_board:
                if attacker.get("can_attack") and attacker.get("frozen_turns", 0) == 0 and attacker.get("attacks_left", 0) > 0:
                    if not opp_taunts:
                        return {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": {"type": "hero", "id": opp_id}}

        except Exception as e:
            logger.error(f"[SuperBotEngine Guard] Error processing decision: {e}")

        return None

# ====================================================
# 外側から非同期で安全呼び出しするメインエントリー
# ====================================================
async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
        return

    bot_engine = SuperBotEngine(session, card_database)
    opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)

    if session.status == "DRAFT":
        opts = session.draft_options.get(BOT_USER_ID, [])
        if opts:
            my_deck_ids = [c.get("id") for c in session.decks.get(BOT_USER_ID, [])]
            best_card, best_score = None, -1

            for cid in opts:
                score = BOT_CARD_TIER.get(cid, 0)
                if cid == "s_09" and any(card_database.get(c, {}).get("taunt") for c in my_deck_ids):
                    score += 25
                if cid == "u_06" and "u_02" in my_deck_ids:
                    score += 15
                if score > best_score:
                    best_score, best_card = score, cid

            await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card or opts[0]})
            if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                await process_super_ai_turn(session, card_database, process_action_func)
        return

    if session.status == "BATTLE":
        max_steps = 10
        step_count = 0

        while session.turn_user_id == BOT_USER_ID and session.status == "BATTLE" and step_count < max_steps:
            step_count += 1
            
            # スレッド化の無駄なオーバーヘッドを完全削除し、同期的に即時実行
            action = bot_engine.decide_best_action()

            if not action:
                break

            await process_action_func(session, BOT_USER_ID, action)

        if session.turn_user_id == BOT_USER_ID and session.status == "BATTLE":
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
