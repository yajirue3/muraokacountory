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
                    "u_01": 110, # 先鋒兵（最序盤テンポ）
                    "u_07": 105, # 奇術師（不正ステータス）
                    "s_05": 100 if heavy_count < 2 else 20, # 暗殺者（巨兵絶対即殺）
                    "u_02": 95,  # 重装兵（壁）
                    "u_03": 92,  # 魔導士
                    "s_01": 90,  # 雷撃
                    "s_04": 88,  # 補充（リソース切れ防止）
                    "u_06": 82,  # 小人
                    "s_02": 80,  # 嵐
                    "s_07": 75,  # 凍結
                    "s_09": 70,  # 城壁
                    "u_04": 65 if heavy_count < 2 else 5,
                    "u_05": 60,
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

            # 最適初手配牌（低マナ確約）
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
            while session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID and action_loop_count < 40:
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
                # A. 盤面ユニットの攻撃ルーチン（巨兵・高打点ユニット最優先殲滅）
                # ======================================================
                if active_units:
                    attacker = active_units[0]

                    # 1. 挑発がいる場合は最も落としやすい挑発を突破
                    if opp_taunts:
                        target_taunt = min(opp_taunts, key=lambda x: x.get("curr_hp", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": {"type": "unit", "id": target_taunt["instance_id"]}})
                        await asyncio.sleep(0.01); continue

                    # 2. 挑発がいない場合：貪欲法による最良ターゲット選定
                    best_target = None
                    best_val = -9999

                    for t in opp_board:
                        u_atk = max(0, attacker.get("atk", 0) - 1) if t.get("wall_turns", 0) > 0 else attacker.get("atk", 0)
                        if u_atk <= 0 and not attacker.get("ranged"):
                            continue

                        t_atk = 0 if attacker.get("ranged") else (max(0, t.get("atk", 0) - 1) if attacker.get("wall_turns", 0) > 0 else t.get("atk", 0))
                        kills = u_atk >= t.get("curr_hp", 0)
                        survives = t_atk < attacker.get("curr_hp", 0)

                        # 脅威度（巨兵やATKの高い敵を危険視）
                        threat = (t.get("atk", 0) * 300) + (1500 if t.get("card_id") == "u_04" else 0)

                        if kills and survives:
                            val = 10000 + threat
                        elif kills and not survives:
                            val = 7000 + threat
                        elif not kills and survives:
                            val = 4000 + (u_atk * 100)
                        else:
                            val = 1000 + threat if t.get("atk", 0) >= 4 else -500

                        if val > best_val:
                            best_val = val
                            best_target = {"type": "unit", "id": t["instance_id"]}

                    # 盤面に危険な敵（巨兵やATK4以上）がいる時は顔面に行かず盤面処理を最優先
                    has_high_threat = any(t.get("card_id") == "u_04" or t.get("atk", 0) >= 4 for t in opp_board)
                    face_val = 5000 + (attacker.get("atk", 0) * 150) if not has_high_threat else 500

                    if opp_hp <= attacker.get("atk", 0): # リーサル最優先
                        face_val = 99999

                    if not opp_board or face_val > best_val:
                        best_target = {"type": "hero", "id": opp_id}

                    await process_action_func(session, BOT_USER_ID, {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": best_target})
                    await asyncio.sleep(0.01); continue

                # ======================================================
                # B. 貪欲法による手札カードの選定・プレイ（スコアリング評価）
                # ======================================================
                best_play = None
                highest_score = -1

                for c in playable_cards:
                    cid = c.get("id")
                    cost = c.get("cost", 0)
                    c_type = c.get("type")

                    # 1. 暗殺者 (s_05)
                    if cid == "s_05" and opp_board:
                        # 巨兵(u_04)や重装兵(u_02)を最優先でスコアリング
                        for t in opp_board:
                            t_score = 9000 + (t.get("atk", 0) * 400) + (3000 if t.get("card_id") == "u_04" else 0) + (1500 if t.get("taunt") else 0)
                            if t_score > highest_score:
                                highest_score = t_score
                                best_play = {"card": c, "target": {"type": "unit", "id": t["instance_id"]}}

                    # 2. 凍結 (s_07)
                    elif cid == "s_07" and opp_board:
                        freezable = [t for t in opp_board if t.get("frozen_turns", 0) <= 0]
                        if freezable:
                            target = max(freezable, key=lambda x: (x.get("atk", 0) * 200) + (1000 if x.get("card_id") == "u_04" else 0))
                            score = 4000 + target.get("atk", 0) * 300
                            if score > highest_score:
                                highest_score = score
                                best_play = {"card": c, "target": {"type": "unit", "id": target["instance_id"]}}

                    # 3. 雷撃 (s_01)
                    elif cid == "s_01":
                        if not opp_taunts and opp_hp <= 3:
                            score = 99999 # リーサル
                            if score > highest_score:
                                highest_score = score
                                best_play = {"card": c, "target": {"type": "hero", "id": opp_id}}
                        elif opp_board:
                            # 倒せる敵を優先
                            for t in opp_board:
                                eff_hp = t.get("curr_hp", 0)
                                score = 2000 + (t.get("atk", 0) * 200)
                                if eff_hp <= 3:
                                    score += 5000 + (1000 if t.get("taunt") else 0)
                                if score > highest_score:
                                    highest_score = score
                                    best_play = {"card": c, "target": {"type": "unit", "id": t["instance_id"]}}

                    # 4. 全体攻撃・嵐 (s_02)
                    elif cid == "s_02":
                        total_dmg_potential = sum(min(2, t.get("curr_hp", 0)) for t in opp_board)
                        score = 1500 * len(opp_board) + (total_dmg_potential * 300)
                        if score > highest_score and len(opp_board) >= 2:
                            highest_score = score
                            best_play = {"card": c, "target": None}

                    # 5. 補充・ドロー (s_04)
                    elif cid == "s_04":
                        score = 4500 if len(my_hand) <= 4 else (2000 if len(my_hand) <= 6 else 500)
                        if score > highest_score:
                            highest_score = score
                            best_play = {"card": c, "target": None}

                    # 6. 城壁 (s_09)
                    elif cid == "s_09" and my_board:
                        unwalled = [u for u in my_board if u.get("wall_turns", 0) == 0]
                        if unwalled:
                            target = max(unwalled, key=lambda x: (1000 if x.get("card_id") == "u_06" else 0) + (500 if x.get("taunt") else 0) + x.get("atk", 0) * 20)
                            score = 3500 + target.get("curr_hp", 0) * 100
                            if score > highest_score:
                                highest_score = score
                                best_play = {"card": c, "target": {"type": "unit", "id": target["instance_id"]}}

                    # 7. ユニット召喚
                    elif c_type == "unit" and len(my_board) < 7:
                        base_unit_score = cost * 1000
                        if cid == "u_07": base_unit_score += 5000 # 奇術師特権
                        elif cid == "u_01" and my_mp == 1: base_unit_score += 4000 # 初手先鋒兵
                        elif cid == "u_02": base_unit_score += (4000 if opp_board else 2500) # 重装兵
                        elif cid == "u_06": base_unit_score += 3500 # 小人

                        if base_unit_score > highest_score:
                            highest_score = base_unit_score
                            best_play = {"card": c, "target": None}

                # 最高スコアのアクションを実行
                if best_play:
                    c = best_play["card"]
                    tgt = best_play["target"]
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": c["instance_id"], "target": tgt})
                    await asyncio.sleep(0.01); continue

                # プレイ可能な手がなくなればターン終了
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
                break

            if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"AI Turn Error: {e}", exc_info=True)
        if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
