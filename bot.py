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
        # 1. ドラフトフェーズ：序盤から終盤まで隙のないピック
        # ==========================================================
        if session.status == "DRAFT":
            opts = session.draft_options.get(BOT_USER_ID, [])
            if opts:
                my_deck = session.decks.get(BOT_USER_ID, [])
                my_card_ids = [c.get("id") if isinstance(c, dict) else c for c in my_deck]
                heavy_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("cost", 0) >= 6)

                weights = {
                    "u_01": 110, # 先鋒兵（T1確実着地）
                    "u_07": 105, # 奇術師（不正ステータス）
                    "s_05": 102 if heavy_count < 2 else 25, # 暗殺者（敵巨兵絶対即殺）
                    "s_04": 98,  # 補充（終盤のリソース枯渇防止最優先）
                    "u_02": 95,  # 重装兵（防波堤）
                    "s_01": 92,  # 雷撃（小人焼き・リーサル）
                    "u_03": 90,  # 魔導士
                    "u_06": 85,  # 小人
                    "s_02": 82,  # 嵐
                    "s_07": 80,  # 凍結（大型停止）
                    "s_09": 75,  # 城壁
                    "u_04": 70 if heavy_count < 2 else 5,
                    "u_05": 65,
                    "s_03": 40,
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

            # 最適配牌（T1先鋒兵、T2魔導士/雷撃、T3重装兵、T4奇術師、T5〜補充・暗殺者）
            if getattr(session, "_deck_stacked", False) is False:
                all_cards = session.hands.get(BOT_USER_ID, []) + session.decks.get(BOT_USER_ID, [])

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

                hand_count = max(3, len(session.hands.get(BOT_USER_ID, [])))
                session.hands[BOT_USER_ID] = all_cards[:hand_count]
                session.decks[BOT_USER_ID] = all_cards[hand_count:]
                session._deck_stacked = True

            action_loop_count = 0
            while session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID and action_loop_count < 45:
                action_loop_count += 1

                my_mp = session.mp.get(BOT_USER_ID, 1)
                my_hp = session.hp.get(BOT_USER_ID, 20)
                opp_hp = session.hp.get(opp_id, 20)
                my_hand = session.hands.get(BOT_USER_ID, [])
                my_board = session.boards.get(BOT_USER_ID, [])
                opp_board = session.boards.get(opp_id, [])

                # 奇術師のステータス底上げパッチ（ATK 3〜5 / HP 4〜6）
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

                # ======================================================
                # 0. 脳死リーサル判定（挑発不在 ＆ 相手を削り切れるなら顔面全突撃）
                # ======================================================
                if not opp_taunts:
                    # 盤面攻撃力総計
                    total_board_dmg = sum(u.get("atk", 0) * u.get("attacks_left", 1) for u in active_units)
                    # 手札から飛ばせる直接ダメージ（雷撃=3）
                    total_spell_dmg = sum(3 for c in playable_cards if c.get("id") == "s_01" and c.get("cost", 0) <= my_mp)

                    # 手札の雷撃で即殺可能な場合
                    if opp_hp <= total_spell_dmg:
                        s01_kill = next((c for c in playable_cards if c.get("id") == "s_01"), None)
                        if s01_kill:
                            await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_kill["instance_id"], "target": {"type": "hero", "id": opp_id}})
                            await asyncio.sleep(0.01); continue

                    # 盤面打点だけで削り切れる、またはアタッカー単体で倒せる場合
                    if active_units and (total_board_dmg + total_spell_dmg >= opp_hp or active_units[0].get("atk", 0) >= opp_hp):
                        await process_action_func(session, BOT_USER_ID, {"action": "DECLARE_ATTACK", "attacker_id": active_units[0]["instance_id"], "target": {"type": "hero", "id": opp_id}})
                        await asyncio.sleep(0.01); continue

                # ======================================================
                # 1. 盤面攻撃ルーチン（挑発突破・巨兵相打ち・小人殲滅）
                # ======================================================
                if active_units:
                    attacker = active_units[0]

                    # 1-A. 挑発最優先突破
                    if opp_taunts:
                        target_taunt = min(opp_taunts, key=lambda x: x.get("curr_hp", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": {"type": "unit", "id": target_taunt["instance_id"]}})
                        await asyncio.sleep(0.01); continue

                    # 1-B. 挑発がいない場合の貪欲トレード判定
                    best_target = None
                    best_val = -9999

                    for t in opp_board:
                        u_atk = max(0, attacker.get("atk", 0) - 1) if t.get("wall_turns", 0) > 0 else attacker.get("atk", 0)
                        if u_atk <= 0 and not attacker.get("ranged"):
                            continue

                        t_atk = 0 if attacker.get("ranged") else (max(0, t.get("atk", 0) - 1) if attacker.get("wall_turns", 0) > 0 else t.get("atk", 0))
                        kills = u_atk >= t.get("curr_hp", 0)
                        survives = t_atk < attacker.get("curr_hp", 0)

                        threat = (t.get("atk", 0) * 300)
                        # 巨兵(u_04)と小人(u_06)は超最優先キル対象
                        if t.get("card_id") == "u_04": threat += 3000
                        if t.get("card_id") == "u_06": threat += 2500

                        val = 0
                        if kills and survives:
                            val = 15000 + threat # 一方狩り
                        elif kills and not survives:
                            val = 10000 + threat # 相打ち（自軍巨兵vs敵巨兵など）
                        elif not kills and survives:
                            val = 5000 + (u_atk * 100)
                        else:
                            # 巨兵など危険すぎる敵なら削るだけでもプラス評価
                            val = threat if t.get("atk", 0) >= 4 else -1000

                        if val > best_val:
                            best_val = val
                            best_target = {"type": "unit", "id": t["instance_id"]}

                    # 危険な敵（巨兵や小人、高ATK）が盤面にいなければ顔面を殴る
                    has_danger = any(t.get("card_id") in ["u_04", "u_06"] or t.get("atk", 0) >= 4 for t in opp_board)
                    face_val = 6000 + (attacker.get("atk", 0) * 200) if not has_danger else 500

                    if not opp_board or face_val > best_val:
                        best_target = {"type": "hero", "id": opp_id}

                    await process_action_func(session, BOT_USER_ID, {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": best_target})
                    await asyncio.sleep(0.01); continue

                # ======================================================
                # 2. 貪欲法による手札カード選定（即時除去・ドロー・展開）
                # ======================================================
                best_play = None
                highest_score = -1

                for c in playable_cards:
                    cid = c.get("id")
                    cost = c.get("cost", 0)
                    c_type = c.get("type")

                    # 2-A. 暗殺者 (s_05)：敵の巨兵・重装兵・吸血鬼を即死
                    if cid == "s_05" and opp_board:
                        for t in opp_board:
                            t_score = 12000 + (t.get("atk", 0) * 500) + (5000 if t.get("card_id") == "u_04" else 0) + (2000 if t.get("taunt") else 0)
                            if t_score > highest_score:
                                highest_score = t_score
                                best_play = {"card": c, "target": {"type": "unit", "id": t["instance_id"]}}

                    # 2-B. 凍結 (s_07)：巨兵や大型を行動停止
                    elif cid == "s_07" and opp_board:
                        freezable = [t for t in opp_board if t.get("frozen_turns", 0) <= 0]
                        if freezable:
                            target = max(freezable, key=lambda x: (x.get("atk", 0) * 300) + (4000 if x.get("card_id") == "u_04" else 0))
                            score = 8000 + target.get("atk", 0) * 400
                            if score > highest_score:
                                highest_score = score
                                best_play = {"card": c, "target": {"type": "unit", "id": target["instance_id"]}}

                    # 2-C. 雷撃 (s_01)：小人・魔導士を即死、または挑発破壊
                    elif cid == "s_01" and opp_board:
                        killable = [u for u in opp_board if u.get("curr_hp", 0) <= (2 if u.get("wall_turns", 0) > 0 else 3)]
                        if killable:
                            target = max(killable, key=lambda x: (3000 if x.get("card_id") == "u_06" else 0) + (1500 if x.get("taunt") else 0) + x.get("atk", 0) * 300)
                            score = 9000 + target.get("atk", 0) * 200
                            if score > highest_score:
                                highest_score = score
                                best_play = {"card": c, "target": {"type": "unit", "id": target["instance_id"]}}

                    # 2-D. 補充 (s_04)：中盤息切れ防止のドロー
                    elif cid == "s_04":
                        score = 8500 if len(my_hand) <= 4 else (5000 if len(my_hand) <= 6 else 1000)
                        if score > highest_score:
                            highest_score = score
                            best_play = {"card": c, "target": None}

                    # 2-E. 全体攻撃・嵐 (s_02)：敵2体以上または小人・横並べ殲滅
                    elif cid == "s_02" and opp_board:
                        wipe_count = sum(1 for t in opp_board if t.get("curr_hp", 0) <= 2)
                        score = (len(opp_board) * 2000) + (wipe_count * 4000)
                        if score > highest_score and (len(opp_board) >= 2 or wipe_count >= 1):
                            highest_score = score
                            best_play = {"card": c, "target": None}

                    # 2-F. 城壁 (s_09)
                    elif cid == "s_09" and my_board:
                        unwalled = [u for u in my_board if u.get("wall_turns", 0) == 0]
                        if unwalled:
                            target = max(unwalled, key=lambda x: (2000 if x.get("card_id") == "u_06" else 0) + (1000 if x.get("taunt") else 0) + x.get("atk", 0) * 20)
                            score = 4500 + target.get("curr_hp", 0) * 100
                            if score > highest_score:
                                highest_score = score
                                best_play = {"card": c, "target": {"type": "unit", "id": target["instance_id"]}}

                    # 2-G. ユニット召喚
                    elif c_type == "unit" and len(my_board) < 7:
                        base_unit_score = cost * 1200
                        if cid == "u_07": base_unit_score += 6000 # 奇術師最優先
                        elif cid == "u_01" and my_mp == 1: base_unit_score += 5000 # 初手先鋒兵
                        elif cid == "u_02": base_unit_score += (5000 if opp_board else 3000) # 重装兵
                        elif cid == "u_04": base_unit_score += 4500 # 巨兵
                        elif cid == "u_06": base_unit_score += 4000 # 小人

                        if base_unit_score > highest_score:
                            highest_score = base_unit_score
                            best_play = {"card": c, "target": None}

                # 評価値が最も高いカードを実行
                if best_play:
                    c = best_play["card"]
                    tgt = best_play["target"]
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": c["instance_id"], "target": tgt})
                    await asyncio.sleep(0.01); continue

                # ======================================================
                # 3. MP余剰救済（MP5以上余りでのパスを絶対に防ぐ）
                # ======================================================
                if my_mp >= 5 and playable_cards:
                    fallback_card = max(playable_cards, key=lambda c: c.get("cost", 0))
                    c_type = fallback_card.get("type")
                    cid = fallback_card.get("id")

                    if c_type == "unit" and len(my_board) < 7:
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": fallback_card["instance_id"], "target": None})
                        await asyncio.sleep(0.01); continue

                    elif c_type == "spell":
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
                            await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": fallback_card["instance_id"], "target": None})
                            await asyncio.sleep(0.01); continue

                # 実行可能な手が完全になくなったらターン終了
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
                break

            if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"AI Turn Error: {e}", exc_info=True)
        if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
