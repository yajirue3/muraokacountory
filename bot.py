import asyncio
import copy
import logging
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

async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================================
        # 1. ドラフトフェーズ：6マナ2枚許容、小人とアグロ優先
        # ==========================================================
        if session.status == "DRAFT":
            opts = session.draft_options.get(BOT_USER_ID, [])
            if opts:
                my_deck = session.decks.get(BOT_USER_ID, [])
                my_card_ids = [c.get("id") if isinstance(c, dict) else c for c in my_deck]

                picked_units = [cid for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("type") == "unit"]
                picked_spells = [cid for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("type") == "spell"]
                heavy_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("cost", 0) >= 6)
                remaining_picks = 6 - len(my_card_ids)

                u01_cnt = picked_units.count("u_01")
                u02_cnt = picked_units.count("u_02")
                u03_cnt = picked_units.count("u_03")
                s03_cnt = picked_spells.count("s_03")

                best_card, max_score = opts[0], -9999999

                for cid in opts:
                    c = BOT_CARD_DB.get(cid, {})
                    c_type = c.get("type")
                    c_cost = c.get("cost", 0)

                    if cid == "u_07":
                        score = -9999999
                    elif c_type == "spell" and (len(picked_units) + remaining_picks <= 3):
                        score = -9999999
                    elif c_type == "spell" and len(picked_spells) >= 2:
                        score = -9999999
                    elif c_cost >= 6 and heavy_count >= 2:
                        score = -9999999
                    else:
                        score = (c.get("atk", 0) * 200) + (c.get("hp", 0) * 150) - (c_cost * 100)
                        
                        if cid == "u_01" and u01_cnt < 2: score += 50000
                        elif cid == "u_03" and u03_cnt < 2: score += 45000
                        elif cid == "s_01": score += 40000
                        elif cid == "u_02" and u02_cnt < 2: score += 35000
                        elif cid == "u_06": score += 30000 # 小人超絶強化
                        elif cid == "s_05": score += 25000 # 暗殺者
                        elif cid == "u_05": score += 20000 # 吸血鬼
                        elif cid == "u_04": score += 15000 # 巨兵
                        elif cid == "s_09": score += 10000
                        elif cid == "s_04": score += 8000
                        elif cid == "s_03" and s03_cnt == 0: score += 5000

                    if score > max_score:
                        max_score = score
                        best_card = cid

                await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})
                await asyncio.sleep(0.01)

                if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                    await process_super_ai_turn(session, card_database, process_action_func)
            return

        # ==========================================================
        # 2. バトルフェーズ
        # ==========================================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            if not opp_id: return

            # ------------------------------------------------------
            # 【イカサマ完全版】手札事故ゼロ化＆KeyError撲滅
            # ------------------------------------------------------
            if getattr(session, "_deck_stacked", False) is False:
                all_cards = session.hands.get(BOT_USER_ID, []) + session.decks.get(BOT_USER_ID, [])
                
                # 理想のコスト順（1→2→3→その他重い順）
                def get_priority(c):
                    cid = c.get("id", "")
                    if cid == "u_01": return 10
                    if cid in ["u_03", "s_01"]: return 20
                    if cid == "u_02": return 30
                    return c.get("cost", 99) * 100
                
                all_cards.sort(key=get_priority)
                
                # 山札に戻す際、欠落している instance_id を強制付与してエラーを消滅させる
                for c in all_cards:
                    if "instance_id" not in c:
                        c["instance_id"] = str(uuid.uuid4())[:8]
                
                hand_count = len(session.hands.get(BOT_USER_ID, []))
                session.hands[BOT_USER_ID] = all_cards[:hand_count]
                # 引かれる順序が適切になるよう山札を反転配置
                session.decks[BOT_USER_ID] = all_cards[hand_count:][::-1]
                
                session._deck_stacked = True
            # ------------------------------------------------------

            action_loop_count = 0
            max_loop_limit = 25

            while session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID and action_loop_count < max_loop_limit:
                action_loop_count += 1

                my_mp = session.mp.get(BOT_USER_ID)
                if my_mp is None:
                    my_mp = session.max_mp.get(BOT_USER_ID, 1)
                    session.mp[BOT_USER_ID] = my_mp

                my_board = session.boards.get(BOT_USER_ID, [])
                opp_board = session.boards.get(opp_id, [])
                my_hand = session.hands.get(BOT_USER_ID, [])
                opp_hp = session.hp.get(opp_id, 20)

                playable_cards = [c for c in my_hand if c.get("cost", 99) <= my_mp]
                active_units = [u for u in my_board if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0]
                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

                # --- 1. ドロー先行 ---
                s04_cards = [c for c in playable_cards if c.get("id") == "s_04"]
                if s04_cards and len(my_hand) < 7:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s04_cards[0].get("instance_id"), "target": None})
                    await asyncio.sleep(0.01); continue
                
                # --- 2. 除去スペル（挑発や吸血鬼・小人の絶対排除） ---
                s05 = next((c for c in playable_cards if c.get("id") == "s_05"), None)
                if s05 and opp_board:
                    target = max(opp_board, key=lambda x: (10000 if x.get("taunt") else 0) + (5000 if x.get("card_id") in ["u_05", "u_06"] else 0) + x.get("atk", 0) * 10 + x.get("curr_hp", 0))
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s05.get("instance_id"), "target": {"type": "unit", "id": target.get("instance_id")}})
                    await asyncio.sleep(0.01); continue

                s01 = next((c for c in playable_cards if c.get("id") == "s_01"), None)
                if s01 and opp_board:
                    killable = [t for t in opp_board if t.get("curr_hp", 0) <= (2 if t.get("wall_turns", 0) > 0 else 3)]
                    if killable:
                        target = max(killable, key=lambda x: (10000 if x.get("taunt") else 0) + (5000 if x.get("card_id") in ["u_05", "u_06"] else 0) + x.get("atk", 0))
                        if target.get("taunt") or target.get("card_id") in ["u_05", "u_06"] or (opp_hp > 3):
                            await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01.get("instance_id"), "target": {"type": "unit", "id": target.get("instance_id")}})
                            await asyncio.sleep(0.01); continue

                # --- 3. 城壁付与（小人や重装兵の要塞化） ---
                s09 = next((c for c in playable_cards if c.get("id") == "s_09"), None)
                if s09 and my_board:
                    unwalled = [u for u in my_board if u.get("wall_turns", 0) == 0]
                    if unwalled:
                        target = max(unwalled, key=lambda x: (10000 if x.get("card_id") == "u_06" else 0) + (5000 if x.get("taunt") else 0) + x.get("atk", 0) * 10 + x.get("curr_hp", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s09.get("instance_id"), "target": {"type": "unit", "id": target.get("instance_id")}})
                        await asyncio.sleep(0.01); continue

                # --- 4. ユニット展開（マナ完全燃焼：ナップサック） ---
                playable_units = [c for c in playable_cards if c.get("type") == "unit" and c.get("id") != "u_07"]
                if playable_units and len(my_board) < 7:
                    best_combo = []
                    best_cost = -1
                    
                    def find_combo(idx, current_combo, current_cost):
                        nonlocal best_combo, best_cost
                        if current_cost > my_mp: return
                        if current_cost > best_cost:
                            best_cost = current_cost
                            best_combo = list(current_combo)
                        for i in range(idx, len(playable_units)):
                            current_combo.append(playable_units[i])
                            find_combo(i + 1, current_combo, current_cost + playable_units[i].get("cost", 0))
                            current_combo.pop()
                            
                    find_combo(0, [], 0)
                    if best_combo:
                        target_unit = max(best_combo, key=lambda x: x.get("cost", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": target_unit.get("instance_id"), "target": None})
                        await asyncio.sleep(0.01); continue

                # --- 5. 盤面攻撃（一方的トレード＆顔面集中） ---
                if active_units:
                    u = active_units[0]
                    targets = opp_taunts if opp_taunts else opp_board
                    
                    best_target = None
                    best_val = -9999
                    
                    if not opp_taunts:
                        face_val = 2500 + u.get("atk", 0) * 10
                        if face_val > best_val:
                            best_val = face_val
                            best_target = {"type": "hero", "id": opp_id}
                    
                    for t in targets:
                        if u.get("card_id") == "u_06" and t.get("wall_turns", 0) > 0:
                            continue
                            
                        u_atk = max(0, u.get("atk", 0) - 1) if t.get("wall_turns", 0) > 0 else u.get("atk", 0)
                        if u_atk <= 0 and not u.get("ranged"):
                            continue
                            
                        kills = u_atk >= t.get("curr_hp", 0)
                        t_atk = 0 if u.get("ranged") else (max(0, t.get("atk", 0) - 1) if u.get("wall_turns", 0) > 0 else t.get("atk", 0))
                        survives = t_atk < u.get("curr_hp", 0)
                        
                        val = 0
                        if kills and survives:
                            val = 3000 + t.get("atk", 0) * 100
                        elif kills and not survives:
                            val = 2000 if t.get("taunt") else 0
                        elif not kills and survives:
                            val = 500
                        else:
                            val = -10000
                            
                        if kills and t.get("card_id") in ["u_05", "u_06"]:
                            val += 2000
                            
                        if val > best_val:
                            best_val = val
                            best_target = {"type": "unit", "id": t.get("instance_id")}
                            
                    if best_target and best_val > 0:
                        await process_action_func(session, BOT_USER_ID, {"action": "DECLARE_ATTACK", "attacker_id": u.get("instance_id"), "target": best_target})
                        await asyncio.sleep(0.01); continue

                # --- 6. 余剰マナ雷撃（顔面） ---
                s01_face = next((c for c in playable_cards if c.get("id") == "s_01"), None)
                if s01_face and not opp_taunts:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_face.get("instance_id"), "target": {"type": "hero", "id": opp_id}})
                    await asyncio.sleep(0.01); continue

                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
                break

            if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"AI Turn Error: {e}", exc_info=True)
        if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
