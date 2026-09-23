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

def safe_get_val(container: Any, key: str, default: Any = 0) -> Any:
    if isinstance(container, dict):
        return container.get(key, default)
    return getattr(container, key, default)

async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================================
        # 1. ドラフトフェーズ（再帰を排除して即時1手ピックで抜ける）
        # ==========================================================
        if session.status == "DRAFT":
            opts = safe_get_val(session.draft_options, BOT_USER_ID, [])
            if opts:
                my_deck = safe_get_val(session.decks, BOT_USER_ID, [])
                my_card_ids = [c.get("id") if isinstance(c, dict) else c for c in my_deck]
                heavy_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("cost", 0) >= 6)[span_8](start_span)[span_8](end_span)[span_9](start_span)[span_9](end_span)

                weights = {
                    "u_01": 110,
                    "u_07": 105,
                    "s_05": 102 if heavy_count < 2 else 25,
                    "s_04": 98,
                    "u_02": 95,
                    "s_01": 92,
                    "u_03": 90,
                    "u_06": 85,
                    "s_02": 82,
                    "s_07": 80,
                    "s_09": 75,
                    "u_04": 70 if heavy_count < 2 else 5,
                    "u_05": 65,
                    "s_03": 40,
                }

                best_card = max(opts, key=lambda cid: weights.get(cid, 20))[span_10](start_span)[span_10](end_span)[span_11](start_span)[span_11](end_span)
                await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})[span_12](start_span)[span_12](end_span)[span_13](start_span)[span_13](end_span)
            return

        # ==========================================================
        # 2. バトルフェーズ
        # ==========================================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)[span_14](start_span)[span_14](end_span)[span_15](start_span)[span_15](end_span)
            if not opp_id:
                return

            # 最適配牌（T1先鋒兵、T2魔導士/雷撃、T3重装兵、T4奇術師、T5〜暗殺者/補充）
            if getattr(session, "_deck_stacked", False) is False:
                b_hands = safe_get_val(session.hands, BOT_USER_ID, [])
                b_decks = safe_get_val(session.decks, BOT_USER_ID, [])
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
                        c["instance_id"] = str(uuid.uuid4())[:8][span_16](start_span)[span_16](end_span)[span_17](start_span)[span_17](end_span)

                hand_count = max(3, len(b_hands))
                session.hands[BOT_USER_ID] = all_cards[:hand_count]
                session.decks[BOT_USER_ID] = all_cards[hand_count:]
                session._deck_stacked = True

            # ------------------------------------------------------
            # PHASE 1: 手札カードプレイ（MP完全消化＋貪欲法）
            # ------------------------------------------------------
            play_loop_count = 0
            while play_loop_count < 10: # 無駄な空回りを防ぐため回数を制限
                play_loop_count += 1
                my_mp = safe_get_val(session.mp, BOT_USER_ID, 1)
                opp_hp = safe_get_val(session.hp, opp_id, 20)
                my_hand = safe_get_val(session.hands, BOT_USER_ID, [])
                my_board = safe_get_val(session.boards, BOT_USER_ID, [])
                opp_board = safe_get_val(session.boards, opp_id, [])

                for u in my_board:
                    if u.get("card_id") == "u_07" and not u.get("_buffed"):
                        u["atk"] = random.randint(3, 5)[span_18](start_span)[span_18](end_span)[span_19](start_span)[span_19](end_span)
                        u["curr_hp"] = random.randint(4, 6)[span_20](start_span)[span_20](end_span)[span_21](start_span)[span_21](end_span)
                        u["max_hp"] = u["curr_hp"][span_22](start_span)[span_22](end_span)[span_23](start_span)[span_23](end_span)
                        u["name"] = f"奇術師({u['atk']}/{u['curr_hp']})[span_24](start_span)[span_25](start_span)"[span_24](end_span)[span_25](end_span)
                        u["_buffed"] = True[span_26](start_span)[span_26](end_span)[span_27](start_span)[span_27](end_span)

                playable_cards = [c for c in my_hand if c.get("cost", 99) <= my_mp]
                if not playable_cards:
                    break

                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

                # 雷撃による即時スペルリーサル
                s01_card = next((c for c in playable_cards if c.get("id") == "s_01"), None)
                if s01_card and opp_hp <= 3 and not opp_taunts:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_card["instance_id"], "target": {"type": "hero", "id": opp_id}})[span_28](start_span)[span_28](end_span)[span_29](start_span)[span_29](end_span)
                    continue

                best_play = None
                highest_score = -1

                for c in playable_cards:
                    cid = c.get("id")
                    cost = c.get("cost", 0)
                    c_type = c.get("type")

                    if cid == "s_05" and opp_board:
                        for t in opp_board:
                            score = 15000 + (t.get("atk", 0) * 500) + (6000 if t.get("card_id") == "u_04" else 0) + (3000 if t.get("taunt") else 0)
                            if score > highest_score:
                                highest_score = score
                                best_play = {"card": c, "target": {"type": "unit", "id": t["instance_id"]}}

                    elif cid == "s_07" and opp_board:
                        freezable = [t for t in opp_board if t.get("frozen_turns", 0) <= 0]
                        if freezable:
                            target = max(freezable, key=lambda x: (x.get("atk", 0) * 300) + (5000 if x.get("card_id") == "u_04" else 0))
                            score = 9000 + target.get("atk", 0) * 400
                            if score > highest_score:
                                highest_score = score
                                best_play = {"card": c, "target": {"type": "unit", "id": target["instance_id"]}}

                    elif cid == "s_01" and opp_board:
                        for t in opp_board:
                            score = 4000 + (t.get("atk", 0) * 200)
                            if t.get("curr_hp", 0) <= 3:
                                score += 8000 + (4000 if t.get("card_id") == "u_06" else 0) + (3000 if t.get("taunt") else 0)
                            if score > highest_score:
                                highest_score = score
                                best_play = {"card": c, "target": {"type": "unit", "id": t["instance_id"]}}

                    elif cid == "s_02" and opp_board:
                        wipes = sum(1 for t in opp_board if t.get("curr_hp", 0) <= 2)
                        score = (len(opp_board) * 2500) + (wipes * 5000)
                        if score > highest_score and (len(opp_board) >= 2 or wipes >= 1):
                            highest_score = score
                            best_play = {"card": c, "target": None}

                    elif cid == "s_04":
                        score = 8000 if len(my_hand) <= 4 else (4500 if len(my_hand) <= 6 else 1000)
                        if score > highest_score:
                            highest_score = score
                            best_play = {"card": c, "target": None}

                    elif cid == "s_09" and my_board:
                        unwalled = [u for u in my_board if u.get("wall_turns", 0) == 0]
                        if unwalled:
                            target = max(unwalled, key=lambda x: (3000 if x.get("card_id") == "u_06" else 0) + (2000 if x.get("taunt") else 0) + x.get("atk", 0) * 50)
                            score = 5000 + target.get("curr_hp", 0) * 100
                            if score > highest_score:
                                highest_score = score
                                best_play = {"card": c, "target": {"type": "unit", "id": target["instance_id"]}}

                    elif c_type == "unit" and len(my_board) < 7:
                        score = cost * 1500
                        if cid == "u_07": score += 7000
                        elif cid == "u_01" and my_mp == 1: score += 6000
                        elif cid == "u_02": score += (6000 if opp_board else 3500)
                        elif cid == "u_04": score += 5500
                        elif cid == "u_06": score += 5000

                        if score > highest_score:
                            highest_score = score
                            best_play = {"card": c, "target": None}

                if best_play:
                    try:
                        await process_action_func(session, BOT_USER_ID, {
                            "action": "PLAY_HAND",
                            "card_instance_id": best_play["card"]["instance_id"],
                            "target": best_play["target"]
                        })
                        continue
                    except Exception:
                        pass

                break

            # ------------------------------------------------------
            # PHASE 2: 盤面総攻撃（挑発粉砕・脳死リーサル）
            # ------------------------------------------------------
            attack_loop_count = 0
            while attack_loop_count < 10:
                attack_loop_count += 1
                my_board = safe_get_val(session.boards, BOT_USER_ID, [])
                opp_board = safe_get_val(session.boards, opp_id, [])
                opp_hp = safe_get_val(session.hp, opp_id, 20)

                active_units = [u for u in my_board if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0]
                if not active_units:
                    break

                attacker = active_units[0]
                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

                # 挑発不在時の絶対リーサル
                if not opp_taunts:
                    total_board_dmg = sum(u.get("atk", 0) * u.get("attacks_left", 1) for u in active_units)
                    if total_board_dmg >= opp_hp or attacker.get("atk", 0) >= opp_hp:
                        await process_action_func(session, BOT_USER_ID, {
                            "action": "DECLARE_ATTACK",
                            "attacker_id": attacker["instance_id"],
                            "target": {"type": "hero", "id": opp_id}
                        })
                        continue

                # 挑発がいる場合は最優先集中攻撃
                if opp_taunts:
                    target_taunt = min(opp_taunts, key=lambda x: x.get("curr_hp", 0))
                    await process_action_func(session, BOT_USER_ID, {
                        "action": "DECLARE_ATTACK",
                        "attacker_id": attacker["instance_id"],
                        "target": {"type": "unit", "id": target_taunt["instance_id"]}
                    })
                    continue

                best_target = None
                best_val = -9999

                for t in opp_board:
                    u_atk = max(0, attacker.get("atk", 0) - 1) if t.get("wall_turns", 0) > 0 else attacker.get("atk", 0)
                    if u_atk <= 0 and not attacker.get("ranged"):
                        continue

                    t_atk = 0 if attacker.get("ranged") else (max(0, t.get("atk", 0) - 1) if attacker.get("wall_turns", 0) > 0 else t.get("atk", 0))
                    kills = u_atk >= t.get("curr_hp", 0)
                    survives = t_atk < attacker.get("curr_hp", 0)

                    threat = t.get("atk", 0) * 400
                    if t.get("card_id") == "u_04": threat += 5000
                    if t.get("card_id") == "u_06": threat += 4000

                    if kills and survives:
                        val = 20000 + threat
                    elif kills and not survives:
                        val = 14000 + threat
                    elif not kills and survives:
                        val = 6000 + (u_atk * 100)
                    else:
                        val = threat - 2000

                    if val > best_val:
                        best_val = val
                        best_target = {"type": "unit", "id": t["instance_id"]}

                has_danger = any(t.get("card_id") in ["u_04", "u_06"] or t.get("atk", 0) >= 4 for t in opp_board)
                face_val = 8000 + (attacker.get("atk", 0) * 300) if not has_danger else 1000

                if not opp_board or face_val > best_val:
                    best_target = {"type": "hero", "id": opp_id}

                await process_action_func(session, BOT_USER_ID, {
                    "action": "DECLARE_ATTACK",
                    "attacker_id": attacker["instance_id"],
                    "target": best_target
                })

            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})[span_30](start_span)[span_30](end_span)[span_31](start_span)[span_31](end_span)

    except Exception as e:
        logger.error(f"AI Turn Error: {e}", exc_info=True)[span_32](start_span)[span_32](end_span)[span_33](start_span)[span_33](end_span)
        try:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})[span_34](start_span)[span_34](end_span)[span_35](start_span)[span_35](end_span)
        except Exception:
            pass
