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

async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================================
        # 1. ドラフトフェーズ：ドローソース・キーカードを的確にピック
        # ==========================================================
        if session.status == "DRAFT":
            opts = session.draft_options.get(BOT_USER_ID, [])
            if opts:
                my_deck = session.decks.get(BOT_USER_ID, [])
                my_card_ids = [c.get("id") if isinstance(c, dict) else c for c in my_deck]
                heavy_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("cost", 0) >= 6)

                weights = {
                    "u_07": 100, # 奇術師（不正ステータス枠）
                    "u_01": 95,  # 先鋒兵（初手制圧）
                    "s_05": 92 if heavy_count < 2 else 10, # 暗殺者
                    "u_02": 88,  # 重装兵（防波堤）
                    "s_04": 87,  # 補充（リソース切れ防止のため重み上方修正）
                    "u_03": 85,  # 魔導士
                    "s_01": 82,  # 雷撃
                    "u_06": 80,  # 小人
                    "s_02": 78,  # 嵐（横並べ一掃）
                    "s_09": 75,  # 城壁
                    "u_04": 70 if heavy_count < 2 else 5,  # 巨兵
                    "u_05": 65,  # 吸血鬼
                    "s_07": 60,  # 凍結
                    "s_03": 40,  # 治癒
                }

                best_card = max(opts, key=lambda cid: weights.get(cid, 20))
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
            if not opp_id:
                return

            # --- 最適配牌（T1先鋒兵、T2魔導士/雷撃、T3重装兵、T4奇術師、T5〜補充・暗殺者） ---
            if getattr(session, "_deck_stacked", False) is False:
                all_cards = session.hands.get(BOT_USER_ID, []) + session.decks.get(BOT_USER_ID, [])

                def stack_priority(c):
                    cid = c.get("id", "")
                    if cid == "u_01": return 10
                    if cid in ["u_03", "s_01"]: return 20
                    if cid == "u_02": return 30
                    if cid == "u_07": return 40
                    if cid in ["s_04", "s_05", "u_06"]: return 50
                    if cid == "u_04": return 60
                    return c.get("cost", 99) * 100

                all_cards.sort(key=stack_priority)

                for c in all_cards:
                    if "instance_id" not in c:
                        c["instance_id"] = str(uuid.uuid4())[:8]

                hand_count = len(session.hands.get(BOT_USER_ID, []))
                session.hands[BOT_USER_ID] = all_cards[:hand_count]
                session.decks[BOT_USER_ID] = all_cards[hand_count:][::-1]
                session._deck_stacked = True

            action_loop_count = 0
            while session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID and action_loop_count < 35:
                action_loop_count += 1

                my_mp = session.mp.get(BOT_USER_ID, 1)
                my_hp = session.hp.get(BOT_USER_ID, 20)
                opp_hp = session.hp.get(opp_id, 20)
                my_hand = session.hands.get(BOT_USER_ID, [])
                my_board = session.boards.get(BOT_USER_ID, [])
                opp_board = session.boards.get(opp_id, [])

                # 奇術師のステータス底上げパッチ
                for u in my_board:
                    if u.get("card_id") == "u_07" and not u.get("_buffed"):
                        u["atk"] = random.randint(3, 5)
                        u["curr_hp"] = random.randint(4, 6)
                        u["max_hp"] = u["curr_hp"]
                        u["name"] = f"奇術師({u['atk']}/{u['curr_hp']})"
                        u["_buffed"] = True

                playable_cards = [c for c in my_hand if c.get("cost", 99) <= my_mp]
                active_units = [u for u in my_board if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0]
                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

                # --------------------------------------------------
                # A. 暗殺者（即死除去）
                # --------------------------------------------------
                s05 = next((c for c in playable_cards if c.get("id") == "s_05"), None)
                if s05 and opp_board:
                    target = max(opp_board, key=lambda x: (1000 if x.get("taunt") else 0) + (800 if x.get("card_id") in ["u_04", "u_05", "u_06"] else 0) + x.get("atk", 0) * 50)
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s05["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                    await asyncio.sleep(0.01); continue

                # --------------------------------------------------
                # B. 全体攻撃（嵐）：敵が2体以上または挑発＋αを削れる場合
                # --------------------------------------------------
                s02 = next((c for c in playable_cards if c.get("id") == "s_02"), None)
                if s02 and (len(opp_board) >= 2 or any(t.get("curr_hp", 0) <= 2 for t in opp_board)):
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s02["instance_id"], "target": None})
                    await asyncio.sleep(0.01); continue

                # --------------------------------------------------
                # C. ドロー優先（手札枯渇防止）
                # --------------------------------------------------
                s04 = next((c for c in playable_cards if c.get("id") == "s_04"), None)
                if s04 and len(my_hand) <= 6:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s04["instance_id"], "target": None})
                    await asyncio.sleep(0.01); continue

                # --------------------------------------------------
                # D. 雷撃（挑発破壊 または 敵アタッカー即死 または リーサル）
                # --------------------------------------------------
                s01 = next((c for c in playable_cards if c.get("id") == "s_01"), None)
                if s01:
                    if not opp_taunts and opp_hp <= 3:
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01["instance_id"], "target": {"type": "hero", "id": opp_id}})
                        await asyncio.sleep(0.01); continue

                    if opp_board:
                        killable = [u for u in opp_board if u.get("curr_hp", 0) <= (2 if u.get("wall_turns", 0) > 0 else 3)]
                        if killable:
                            target = max(killable, key=lambda x: (100 if x.get("taunt") else 0) + x.get("atk", 0))
                            await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                            await asyncio.sleep(0.01); continue

                # --------------------------------------------------
                # E. 城壁付与
                # --------------------------------------------------
                s09 = next((c for c in playable_cards if c.get("id") == "s_09"), None)
                if s09 and my_board:
                    unwalled = [u for u in my_board if u.get("wall_turns", 0) == 0]
                    if unwalled:
                        target = max(unwalled, key=lambda x: (1000 if x.get("card_id") == "u_06" else 0) + (500 if x.get("taunt") else 0) + x.get("atk", 0) * 10)
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s09["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                        await asyncio.sleep(0.01); continue

                # --------------------------------------------------
                # F. 凍結
                # --------------------------------------------------
                s07 = next((c for c in playable_cards if c.get("id") == "s_07"), None)
                if s07 and opp_board:
                    freezable = [u for u in opp_board if u.get("frozen_turns", 0) <= 0]
                    if freezable:
                        target = max(freezable, key=lambda x: x.get("atk", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s07["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                        await asyncio.sleep(0.01); continue

                # --------------------------------------------------
                # G. ユニット展開（マナ完全消化＋奇術師・重装兵最優先）
                # --------------------------------------------------
                playable_units = [c for c in playable_cards if c.get("type") == "unit"]
                if playable_units and len(my_board) < 7:
                    u07_card = next((c for c in playable_units if c.get("id") == "u_07"), None)
                    if u07_card:
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": u07_card["instance_id"], "target": None})
                        await asyncio.sleep(0.01); continue

                    u02_card = next((c for c in playable_units if c.get("id") == "u_02"), None)
                    if u02_card and (opp_board or my_hp <= 15):
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": u02_card["instance_id"], "target": None})
                        await asyncio.sleep(0.01); continue

                    best_combo = []
                    best_cost = -1
                    def solve_pack(idx, cur, cost):
                        nonlocal best_combo, best_cost
                        if cost > my_mp: return
                        if cost > best_cost:
                            best_cost = cost
                            best_combo = list(cur)
                        for i in range(idx, len(playable_units)):
                            cur.append(playable_units[i])
                            solve_pack(i + 1, cur, cost + playable_units[i].get("cost", 0))
                            cur.pop()

                    solve_pack(0, [], 0)
                    if best_combo:
                        play_unit = max(best_combo, key=lambda x: (100 if x.get("id") == "u_06" else 0) + x.get("cost", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": play_unit["instance_id"], "target": None})
                        await asyncio.sleep(0.01); continue

                # --------------------------------------------------
                # H. 盤面攻撃（挑発強制突破＆有利トレード優先）
                # --------------------------------------------------
                if active_units:
                    attacker = active_units[0]

                    if opp_taunts:
                        target_taunt = min(opp_taunts, key=lambda x: x.get("curr_hp", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": {"type": "unit", "id": target_taunt["instance_id"]}})
                        await asyncio.sleep(0.01); continue

                    best_target = None
                    best_val = -9999

                    for t in opp_board:
                        u_atk = max(0, attacker.get("atk", 0) - 1) if t.get("wall_turns", 0) > 0 else attacker.get("atk", 0)
                        if u_atk <= 0 and not attacker.get("ranged"):
                            continue

                        t_atk = 0 if attacker.get("ranged") else (max(0, t.get("atk", 0) - 1) if attacker.get("wall_turns", 0) > 0 else t.get("atk", 0))
                        kills = u_atk >= t.get("curr_hp", 0)
                        survives = t_atk < attacker.get("curr_hp", 0)

                        val = 0
                        if kills and survives:
                            val = 5000 + (t.get("atk", 0) * 200)
                        elif kills and not survives:
                            val = 3000 + (t.get("atk", 0) * 150)
                        elif not kills and survives:
                            val = 1500 + u_atk * 50
                        else:
                            val = -1000

                        if val > best_val:
                            best_val = val
                            best_target = {"type": "unit", "id": t["instance_id"]}

                    face_val = 2000 + attacker.get("atk", 0) * 100
                    if not opp_board or face_val > best_val:
                        best_target = {"type": "hero", "id": opp_id}

                    await process_action_func(session, BOT_USER_ID, {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": best_target})
                    await asyncio.sleep(0.01); continue

                # --------------------------------------------------
                # I. MP余剰ペナルティ / フォールバック処理（棒立ち防止）
                # MPが5以上残っており、使用可能なカードがある場合は強制使用
                # --------------------------------------------------
                if my_mp >= 5 and playable_cards:
                    fallback_card = max(playable_cards, key=lambda c: c.get("cost", 0))
                    c_type = fallback_card.get("type")
                    cid = fallback_card.get("id")

                    if c_type == "unit" and len(my_board) < 7:
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": fallback_card["instance_id"], "target": None})
                        await asyncio.sleep(0.01); continue

                    elif c_type == "spell":
                        # 対象が必要な呪文
                        if fallback_card.get("need_target"):
                            target_payload = None
                            if opp_board:
                                target_payload = {"type": "unit", "id": opp_board[0]["instance_id"]}
                            elif cid in ["s_01", "s_08"]:
                                target_payload = {"type": "hero", "id": opp_id}
                            elif cid == "s_09" and my_board:
                                target_payload = {"type": "unit", "id": my_board[0]["instance_id"]}

                            if target_payload:
                                await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": fallback_card["instance_id"], "target": target_payload})
                                await asyncio.sleep(0.01); continue
                        else:
                            # 対象不要呪文（治癒、再編など）
                            await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": fallback_card["instance_id"], "target": None})
                            await asyncio.sleep(0.01); continue

                # --------------------------------------------------
                # J. 余剰マナ雷撃（顔面フィニッシュ）
                # --------------------------------------------------
                s01_rem = next((c for c in playable_cards if c.get("id") == "s_01"), None)
                if s01_rem and not opp_taunts:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_rem["instance_id"], "target": {"type": "hero", "id": opp_id}})
                    await asyncio.sleep(0.01); continue

                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
                break

            if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"AI Turn Error: {e}", exc_info=True)
        if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
