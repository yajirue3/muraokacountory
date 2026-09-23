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

def evaluate_board_state(bot_hp: int, opp_hp: int, bot_board: List[dict], opp_board: List[dict]) -> float:
    """局面評価関数（将棋AIの静的評価関数）"""
    if opp_hp <= 0:
        return 999999.0
    if bot_hp <= 0:
        return -999999.0

    score = 0.0
    # ライフアドバンテージ（終盤ほど重みが増す）
    score += (bot_hp - opp_hp) * 15.0
    if opp_hp <= 8:
        score += (8 - opp_hp) * 35.0  # リーサル圏への強い誘導

    # 自軍盤面の戦力評価（駒の働き）
    for u in bot_board:
        atk = u.get("atk", 0)
        hp = u.get("curr_hp", 0)
        val = atk * 12.0 + hp * 8.0
        if u.get("taunt"):
            val += 25.0
        if u.get("ranged"):  # 小人は反撃無効＋3回攻撃で戦力係数が破格
            val += 60.0 + (u.get("attacks_left", 1) * 15.0)
        if u.get("wall_turns", 0) > 0:
            val += 30.0
        if u.get("lifesteal"):
            val += 20.0
        score += val

    # 敵軍盤面の脅威度評価（相手の駒得を削る）
    for u in opp_board:
        atk = u.get("atk", 0)
        hp = u.get("curr_hp", 0)
        val = atk * 16.0 + hp * 10.0
        if u.get("taunt"):
            val += 35.0  # 挑発の存在はマイナス評価
        if u.get("card_id") in ["u_04", "u_05", "u_06"]:
            val += 50.0  # 巨兵・吸血鬼・小人は放置厳禁
        if u.get("wall_turns", 0) > 0:
            val += 25.0
        score -= val

    return score

async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================================
        # 1. ドラフトフェーズ：駒得とテンポを極限まで計算
        # ==========================================================
        if session.status == "DRAFT":
            opts = session.draft_options.get(BOT_USER_ID, [])
            if opts:
                my_deck = session.decks.get(BOT_USER_ID, [])
                my_card_ids = [c.get("id") if isinstance(c, dict) else c for c in my_deck]
                heavy_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("cost", 0) >= 6)

                weights = {
                    "u_01": 95,  # 先鋒兵（テンポ・手数）
                    "s_05": 92 if heavy_count < 2 else 10,  # 暗殺者（大型確定除去）
                    "u_06": 90,  # 小人（無限有利トレード）
                    "u_03": 85,  # 魔導士（序盤標準）
                    "u_02": 82,  # 重装兵（防波堤）
                    "s_01": 80,  # 雷撃（柔軟な除去・打点）
                    "u_04": 75 if heavy_count < 2 else 5,   # 巨兵
                    "s_09": 70,  # 城壁（小人との凶悪コンボ）
                    "u_05": 65,  # 吸血鬼
                    "s_04": 60,  # 補充
                    "s_03": 40,  # 治癒
                    "u_07": -999 # 奇術師は運ゲー排除
                }

                best_card = max(opts, key=lambda cid: weights.get(cid, 20))
                await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})
                await asyncio.sleep(0.01)

                if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                    await process_super_ai_turn(session, card_database, process_action_func)
            return

        # ==========================================================
        # 2. バトルフェーズ：局面読み切りエンジン
        # ==========================================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            if not opp_id:
                return

            # --- 最適配牌（序盤テンポ＋中盤暗殺・小人の確定着地） ---
            if getattr(session, "_deck_stacked", False) is False:
                all_cards = session.hands.get(BOT_USER_ID, []) + session.decks.get(BOT_USER_ID, [])
                
                def deck_order_cost(c):
                    cid = c.get("id", "")
                    if cid == "u_01": return 1
                    if cid in ["u_03", "s_01"]: return 2
                    if cid == "u_02": return 3
                    if cid in ["s_09", "u_05"]: return 4
                    if cid == "u_06": return 5
                    if cid in ["s_05", "u_04"]: return 6
                    return 10

                all_cards.sort(key=deck_order_cost)
                for c in all_cards:
                    if "instance_id" not in c:
                        c["instance_id"] = str(uuid.uuid4())[:8]

                hand_count = len(session.hands.get(BOT_USER_ID, []))
                session.hands[BOT_USER_ID] = all_cards[:hand_count]
                session.decks[BOT_USER_ID] = all_cards[hand_count:][::-1]
                session._deck_stacked = True

            action_loop_count = 0
            while session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID and action_loop_count < 30:
                action_loop_count += 1

                my_mp = session.mp.get(BOT_USER_ID, 1)
                my_hp = session.hp.get(BOT_USER_ID, 20)
                opp_hp = session.hp.get(opp_id, 20)
                my_hand = session.hands.get(BOT_USER_ID, [])
                my_board = session.boards.get(BOT_USER_ID, [])
                opp_board = session.boards.get(opp_id, [])

                playable_cards = [c for c in my_hand if c.get("cost", 99) <= my_mp]
                active_units = [u for u in my_board if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0]
                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

                # 相手の最大打点（次ターン詰めろ警戒）
                opp_max_threat = sum(u.get("atk", 0) * u.get("attacks_left", 1) for u in opp_board)
                is_in_danger = (my_hp - opp_max_threat) <= 3

                # --------------------------------------------------
                # A. ドロー（リソース拡大）
                # --------------------------------------------------
                s04 = next((c for c in playable_cards if c.get("id") == "s_04"), None)
                if s04 and len(my_hand) < 7:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s04["instance_id"], "target": None})
                    await asyncio.sleep(0.01); continue

                # --------------------------------------------------
                # B. 暗殺者（即死除去：相手の巨兵・吸血鬼・厄介な重装兵を即時破壊）
                # --------------------------------------------------
                s05 = next((c for c in playable_cards if c.get("id") == "s_05"), None)
                if s05 and opp_board:
                    # 脅威度最高を狙い撃ち
                    kill_target = max(opp_board, key=lambda x: (x.get("atk", 0) * 15) + (x.get("curr_hp", 0) * 10) + (100 if x.get("taunt") else 0))
                    if kill_target.get("atk", 0) >= 3 or kill_target.get("curr_hp", 0) >= 4 or kill_target.get("taunt"):
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s05["instance_id"], "target": {"type": "unit", "id": kill_target["instance_id"]}})
                        await asyncio.sleep(0.01); continue

                # --------------------------------------------------
                # C. 城壁（小人または重装兵に即時付与して無敵要塞化）
                # --------------------------------------------------
                s09 = next((c for c in playable_cards if c.get("id") == "s_09"), None)
                if s09 and my_board:
                    candidates = [u for u in my_board if u.get("wall_turns", 0) == 0]
                    if candidates:
                        wall_target = max(candidates, key=lambda x: (500 if x.get("card_id") == "u_06" else 0) + (200 if x.get("taunt") else 0) + x.get("curr_hp", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s09["instance_id"], "target": {"type": "unit", "id": wall_target["instance_id"]}})
                        await asyncio.sleep(0.01); continue

                # --------------------------------------------------
                # D. 雷撃（挑発破壊または高打点ユニットの削り・リーサル）
                # --------------------------------------------------
                s01 = next((c for c in playable_cards if c.get("id") == "s_01"), None)
                if s01:
                    # 顔面リーサル
                    if not opp_taunts and opp_hp <= 3:
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01["instance_id"], "target": {"type": "hero", "id": opp_id}})
                        await asyncio.sleep(0.01); continue
                    
                    # 盤面除去：挑発持ち、または一方的に倒せる相手
                    if opp_board:
                        killable_taunt = next((t for t in opp_taunts if t.get("curr_hp", 0) <= (2 if t.get("wall_turns", 0) > 0 else 3)), None)
                        if killable_taunt:
                            await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01["instance_id"], "target": {"type": "unit", "id": killable_taunt["instance_id"]}})
                            await asyncio.sleep(0.01); continue
                        
                        target_u = max(opp_board, key=lambda x: (100 if x.get("taunt") else 0) + x.get("atk", 0) * 10)
                        if target_u.get("atk", 0) >= 3 or target_u.get("taunt") or is_in_danger:
                            await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01["instance_id"], "target": {"type": "unit", "id": target_u["instance_id"]}})
                            await asyncio.sleep(0.01); continue

                # --------------------------------------------------
                # E. ユニット展開（マナ完全燃焼＋危険時の挑発最優先）
                # --------------------------------------------------
                playable_units = [c for c in playable_cards if c.get("type") == "unit" and c.get("id") != "u_07"]
                if playable_units and len(my_board) < 7:
                    # 危機的状況なら重装兵（挑発）を絶対展開
                    taunt_card = next((c for c in playable_units if c.get("id") == "u_02"), None)
                    if is_in_danger and taunt_card:
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": taunt_card["instance_id"], "target": None})
                        await asyncio.sleep(0.01); continue

                    # マナ最大燃焼コンボ探索（DP / ナップサック）
                    best_combo = []
                    best_eval = -1
                    def solve_pack(idx, cur, cost, val):
                        nonlocal best_combo, best_eval
                        if cost > my_mp: return
                        if val > best_eval:
                            best_eval = val
                            best_combo = list(cur)
                        for i in range(idx, len(playable_units)):
                            u = playable_units[i]
                            # スタッツ効率とカード価値
                            u_val = (u.get("atk", 0) * 2 + u.get("hp", 0)) + (20 if u.get("id") == "u_06" else 0) + (10 if u.get("haste") else 0)
                            cur.append(u)
                            solve_pack(i + 1, cur, cost + u.get("cost", 0), val + u_val)
                            cur.pop()

                    solve_pack(0, [], 0, 0)
                    if best_combo:
                        play_unit = max(best_combo, key=lambda x: x.get("cost", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": play_unit["instance_id"], "target": None})
                        await asyncio.sleep(0.01); continue

                # --------------------------------------------------
                # F. 盤面攻撃（合算トレード・一方的狩り・詰み判定）
                # --------------------------------------------------
                if active_units:
                    attacker = active_units[0]
                    valid_targets = opp_taunts if opp_taunts else opp_board

                    best_move = None
                    best_score = -999999.0

                    # 1. 相手本体（顔面）殴り評価
                    if not opp_taunts:
                        face_dmg = attacker.get("atk", 0)
                        score = evaluate_board_state(
                            bot_hp=min(20, my_hp + (face_dmg if attacker.get("lifesteal") else 0)),
                            opp_hp=opp_hp - face_dmg,
                            bot_board=my_board,
                            opp_board=opp_board
                        )
                        # リーサルまたはアグロ加点
                        score += 30.0 + (face_dmg * 5.0)
                        if score > best_score:
                            best_score = score
                            best_move = {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": {"type": "hero", "id": opp_id}}

                    # 2. 敵ユニットへの攻撃（将棋の駒得計算）
                    for def_u in valid_targets:
                        atk_val = attacker.get("atk", 0)
                        if def_u.get("wall_turns", 0) > 0:
                            atk_val = max(0, atk_val - 1)

                        if atk_val <= 0 and not attacker.get("ranged"):
                            continue

                        def_atk = def_u.get("atk", 0)
                        if attacker.get("wall_turns", 0) > 0:
                            def_atk = max(0, def_atk - 1)

                        # シミュレーション
                        new_def_hp = def_u.get("curr_hp", 0) - atk_val
                        new_atk_hp = attacker.get("curr_hp", 0) - (0 if attacker.get("ranged") else def_atk)

                        # 仮想盤面生成
                        sim_bot_board = [copy.deepcopy(u) for u in my_board if u["instance_id"] != attacker["instance_id"]]
                        if new_atk_hp > 0:
                            surviving_atk = copy.deepcopy(attacker)
                            surviving_atk["curr_hp"] = new_atk_hp
                            sim_bot_board.append(surviving_atk)

                        sim_opp_board = [copy.deepcopy(u) for u in opp_board if u["instance_id"] != def_u["instance_id"]]
                        if new_def_hp > 0:
                            surviving_def = copy.deepcopy(def_u)
                            surviving_def["curr_hp"] = new_def_hp
                            sim_opp_board.append(surviving_def)

                        sim_bot_hp = min(20, my_hp + (atk_val if attacker.get("lifesteal") else 0))
                        move_score = evaluate_board_state(sim_bot_hp, opp_hp, sim_bot_board, sim_opp_board)

                        # 一方的トレード（駒得）の強烈な加点
                        if new_def_hp <= 0 and new_atk_hp > 0:
                            move_score += 80.0 + (def_u.get("atk", 0) * 10.0)
                        # 挑発の突破破壊加点
                        if new_def_hp <= 0 and def_u.get("taunt"):
                            move_score += 100.0

                        if move_score > best_score:
                            best_score = move_score
                            best_move = {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": {"type": "unit", "id": def_u["instance_id"]}}

                    if best_move:
                        await process_action_func(session, BOT_USER_ID, best_move)
                        await asyncio.sleep(0.01); continue

                # --------------------------------------------------
                # G. 余剰マナ雷撃（顔面フィニッシュ）
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
