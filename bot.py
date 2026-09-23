import asyncio
import copy
import logging
import random
import uuid
from typing import Dict, List, Any, Optional

logger = logging.getLogger("CardBot")

BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "村岡国王（影武者）"

HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}

BOT_CARD_DB = {
    "u_01": {"id": "u_01", "name": "先鋒兵", "type": "unit", "cost": 1, "atk": 2, "hp": 1, "haste": True},
    "u_02": {"id": "u_02", "name": "重装兵", "type": "unit", "cost": 3, "atk": 2, "hp": 5, "taunt": True},
    "u_03": {"id": "u_03", "name": "魔導士", "type": "unit", "cost": 2, "atk": 3, "hp": 2},
    "u_04": {"id": "u_04", "name": "巨兵", "type": "unit", "cost": 6, "atk": 7, "hp": 6},
    "u_05": {"id": "u_05", "name": "吸血鬼", "type": "unit", "cost": 4, "atk": 3, "hp": 4, "lifesteal": True},
    "u_06": {"id": "u_06", "name": "小人", "type": "unit", "cost": 5, "atk": 1, "hp": 2, "max_attacks": 3, "ranged": True},
    "u_07": {"id": "u_07", "name": "奇術師", "type": "unit", "cost": 4, "atk": 0, "hp": 0},
    "s_01": {"id": "s_01", "name": "雷撃", "type": "spell", "cost": 2, "effect": "damage", "val": 3, "need_target": True},
    "s_02": {"id": "s_02", "name": "嵐", "type": "spell", "cost": 4, "effect": "aoe_damage", "val": 2, "need_target": False},
    "s_03": {"id": "s_03", "name": "治癒", "type": "spell", "cost": 2, "effect": "heal", "val": 5, "need_target": False},
    "s_04": {"id": "s_04", "name": "補充", "type": "spell", "cost": 3, "effect": "draw", "val": 2, "need_target": False},
    "s_05": {"id": "s_05", "name": "暗殺者", "type": "spell", "cost": 6, "effect": "assassinate", "need_target": True},
    "s_06": {"id": "s_06", "name": "再編", "type": "spell", "cost": 1, "effect": "reshape", "need_target": False},
    "s_07": {"id": "s_07", "name": "凍結", "type": "spell", "cost": 1, "effect": "freeze", "need_target": True},
    "s_08": {"id": "s_08", "name": "火傷", "type": "spell", "cost": 1, "effect": "burn", "need_target": True},
    "s_09": {"id": "s_09", "name": "城壁", "type": "spell", "cost": 2, "effect": "wall", "need_target": True},
}

def safe_get(container: Any, key: str, default: Any = None) -> Any:
    if isinstance(container, dict):
        return container.get(key, default)
    return getattr(container, key, default)

async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================================
        # 1. ドラフトフェーズ
        # ==========================================================
        if session.status == "DRAFT":
            opts = safe_get(session.draft_options, BOT_USER_ID, [])
            if opts:
                my_deck = safe_get(session.decks, BOT_USER_ID, [])
                my_card_ids = [c.get("id") if isinstance(c, dict) else c for c in my_deck]
                heavy_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("cost", 0) >= 6)

                weights = {
                    "u_07": 125,
                    "u_01": 115,
                    "s_05": 105 if heavy_count < 2 else 20,
                    "s_04": 100,
                    "u_02": 95,
                    "s_01": 92,
                    "u_03": 88,
                    "u_04": 82 if heavy_count < 2 else 10,
                    "u_06": 80,
                    "s_02": 78,
                    "s_07": 75,
                    "s_09": 70,
                    "u_05": 65,
                    "s_03": 30,
                }
                best_card = max(opts, key=lambda cid: weights.get(cid, 20))
                await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})
            return

        # ==========================================================
        # 2. バトルフェーズ
        # ==========================================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            if not opp_id:
                return

            # 最適初手配牌
            if getattr(session, "_deck_stacked", False) is False:
                b_hands = safe_get(session.hands, BOT_USER_ID, [])
                b_decks = safe_get(session.decks, BOT_USER_ID, [])
                all_cards = b_hands + b_decks

                def stack_priority(c):
                    cid = c.get("id", "")
                    if cid == "u_01": return 10
                    if cid in ["u_03", "s_01"]: return 20
                    if cid == "u_02": return 30
                    if cid == "u_07": return 40
                    if cid in ["s_04", "s_05"]: return 50
                    if cid in ["u_04", "u_06"]: return 60
                    return c.get("cost", 99) * 100

                all_cards.sort(key=stack_priority)

                for c in all_cards:
                    if "instance_id" not in c:
                        c["instance_id"] = str(uuid.uuid4())[:8]

                hand_count = max(3, len(b_hands))
                session.hands[BOT_USER_ID] = all_cards[:hand_count]
                session.decks[BOT_USER_ID] = all_cards[hand_count:]
                session._deck_stacked = True

            # 奇術師ステータス底上げパッチ（ATK 4〜5 / HP 5〜6）
            my_board = safe_get(session.boards, BOT_USER_ID, [])
            for u in my_board:
                if u.get("card_id") == "u_07" and not u.get("_buffed"):
                    u["atk"] = random.randint(4, 5)
                    u["curr_hp"] = random.randint(5, 6)
                    u["max_hp"] = u["curr_hp"]
                    u["name"] = f"奇術師({u['atk']}/{u['curr_hp']})"
                    u["_buffed"] = True

            # ------------------------------------------------------
            # PHASE 1: 手札カードプレイ（仮想MP/手札管理）
            # ------------------------------------------------------
            local_mp = safe_get(session.mp, BOT_USER_ID, 1)
            local_hand = list(safe_get(session.hands, BOT_USER_ID, []))
            opp_hp = safe_get(session.hp, opp_id, 20)
            opp_board = safe_get(session.boards, opp_id, [])

            # A. 雷撃直接リーサル
            opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]
            s01_lethal = next((c for c in local_hand if c.get("id") == "s_01" and c.get("cost", 99) <= local_mp), None)
            if s01_lethal and not opp_taunts and opp_hp <= 3:
                await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_lethal["instance_id"], "target": {"type": "hero", "id": opp_id}})
                local_mp -= s01_lethal.get("cost", 2)
                local_hand.remove(s01_lethal)
                opp_hp -= 3

            # B. 優先呪文処理
            for _ in range(3):
                playable_spells = [c for c in local_hand if c.get("type") == "spell" and c.get("cost", 99) <= local_mp]
                if not playable_spells:
                    break

                played = False

                # 1. 暗殺者
                s05 = next((c for c in playable_spells if c.get("id") == "s_05"), None)
                if s05 and opp_board:
                    target = max(opp_board, key=lambda x: (10000 if x.get("card_id") == "u_04" else 0) + (5000 if x.get("taunt") else 0) + x.get("atk", 0) * 100)
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s05["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                    local_mp -= s05.get("cost", 6)
                    local_hand.remove(s05)
                    opp_board = [u for u in opp_board if u.get("instance_id") != target["instance_id"]]
                    played = True
                    continue

                # 2. 雷撃除去
                s01 = next((c for c in playable_spells if c.get("id") == "s_01"), None)
                if s01 and opp_board:
                    killable = [u for u in opp_board if u.get("curr_hp", 0) <= 3]
                    if killable:
                        target = max(killable, key=lambda x: (5000 if x.get("card_id") in ["u_03", "u_06"] else 0) + (2000 if x.get("taunt") else 0) + x.get("atk", 0) * 50)
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                        local_mp -= s01.get("cost", 2)
                        local_hand.remove(s01)
                        opp_board = [u for u in opp_board if u.get("instance_id") != target["instance_id"]]
                        played = True
                        continue

                # 3. 補充
                s04 = next((c for c in playable_spells if c.get("id") == "s_04"), None)
                if s04 and len(local_hand) <= 4:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s04["instance_id"], "target": None})
                    local_mp -= s04.get("cost", 3)
                    local_hand.remove(s04)
                    played = True
                    continue

                if not played:
                    break

            # C. ユニット連続展開
            while True:
                my_board_len = len(safe_get(session.boards, BOT_USER_ID, []))
                if my_board_len >= 7:
                    break

                playable_units = [c for c in local_hand if c.get("type") == "unit" and c.get("cost", 99) <= local_mp]
                if not playable_units:
                    break

                u07 = next((c for c in playable_units if c.get("id") == "u_07"), None)
                u02 = next((c for c in playable_units if c.get("id") == "u_02"), None)

                if u07:
                    best_u = u07
                elif u02 and (opp_board or not any(u.get("taunt") for u in my_board)):
                    best_u = u02
                else:
                    best_u = max(playable_units, key=lambda x: x.get("cost", 0))

                try:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": best_u["instance_id"], "target": None})
                    local_mp -= best_u.get("cost", 0)
                    local_hand.remove(best_u)
                except Exception:
                    local_hand.remove(best_u)
                    break

            # ------------------------------------------------------
            # PHASE 2: 盤面総攻撃
            # ------------------------------------------------------
            for _ in range(8):
                my_board = safe_get(session.boards, BOT_USER_ID, [])
                opp_board = safe_get(session.boards, opp_id, [])
                opp_hp = safe_get(session.hp, opp_id, 20)

                active_units = [u for u in my_board if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0]
                if not active_units:
                    break

                attacker = active_units[0]
                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

                # 1. 挑発なし＆削り切れるならフェイス突撃
                if not opp_taunts:
                    total_dmg = sum(u.get("atk", 0) * u.get("attacks_left", 1) for u in active_units)
                    if total_dmg >= opp_hp or attacker.get("atk", 0) >= opp_hp:
                        await process_action_func(session, BOT_USER_ID, {
                            "action": "DECLARE_ATTACK",
                            "attacker_id": attacker["instance_id"],
                            "target": {"type": "hero", "id": opp_id}
                        })
                        continue

                # 2. 挑発最優先粉砕
                if opp_taunts:
                    target_taunt = min(opp_taunts, key=lambda x: x.get("curr_hp", 0))
                    await process_action_func(session, BOT_USER_ID, {
                        "action": "DECLARE_ATTACK",
                        "attacker_id": attacker["instance_id"],
                        "target": {"type": "unit", "id": target_taunt["instance_id"]}
                    })
                    continue

                # 3. 敵アタッカー排除
                best_target = None
                best_val = -9999

                for t in opp_board:
                    u_atk = attacker.get("atk", 0)
                    t_atk = 0 if attacker.get("ranged") else t.get("atk", 0)
                    kills = u_atk >= t.get("curr_hp", 0)
                    survives = t_atk < attacker.get("curr_hp", 0)

                    threat = t.get("atk", 0) * 300
                    if t.get("card_id") == "u_04": threat += 8000
                    if t.get("card_id") == "u_03": threat += 6000
                    if t.get("card_id") == "u_06": threat += 5000

                    if kills and survives:
                        val = 20000 + threat
                    elif kills and not survives:
                        val = 14000 + threat
                    elif not kills and survives:
                        val = 5000 + u_atk * 50
                    else:
                        val = -1000

                    if val > best_val:
                        best_val = val
                        best_target = {"type": "unit", "id": t["instance_id"]}

                has_danger = any(t.get("card_id") in ["u_04", "u_03", "u_06"] or t.get("atk", 0) >= 3 for t in opp_board)
                if not opp_board or not has_danger or best_val < 5000:
                    best_target = {"type": "hero", "id": opp_id}

                await process_action_func(session, BOT_USER_ID, {
                    "action": "DECLARE_ATTACK",
                    "attacker_id": attacker["instance_id"],
                    "target": best_target
                })

            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"AI Turn Error: {e}", exc_info=True)
        try:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
        except Exception:
            pass
