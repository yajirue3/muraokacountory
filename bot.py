import asyncio
import logging
import itertools
from typing import Dict, List, Any, Optional

logger = logging.getLogger("CardBot")

BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "村岡国王（影武者）"

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
    """
    【絶対王者フルコード】
    1ターン内パイプラインif ＋ 盤面制圧特化・泳がせゼロ・マナ使い切り型
    """
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================
        # 1. ドラフトフェーズ（先鋒兵・重装兵の超優先）
        # ==========================================
        if session.status == "DRAFT":
            opts = session.draft_options.get(BOT_USER_ID, [])
            if opts:
                my_deck = session.decks.get(BOT_USER_ID, [])
                my_card_ids = [c.get("id") if isinstance(c, dict) else c for c in my_deck]
                spell_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("type") == "spell")

                best_card, max_score = opts[0], -999999

                for cid in opts:
                    c = BOT_CARD_DB.get(cid, {})
                    score = 0

                    if c.get("type") == "unit":
                        if c.get("atk", 0) == 0 and c.get("hp", 0) == 0:
                            score -= 20000 # 奇術師は不要
                        else:
                            score += 5000 + (c.get("atk", 0) * 150) + (c.get("hp", 0) * 100)
                            
                            # 序盤のテンポを絶対に渡さないための特別評価
                            if cid == "u_01": score += 15000  # 先鋒兵：最優先ピック
                            if cid == "u_02": score += 10000  # 重装兵：盤面固め
                            if cid == "u_04": score += 2000   # フィニッシャー
                            
                            if c.get("haste"): score += 1000
                            if c.get("taunt"): score += 800
                    else:
                        score += 2000
                        if cid in ["s_05", "s_01", "s_07"]: score += 3000
                        elif cid == "s_04": score += 2000

                    if c.get("type") == "spell" and spell_count >= 4:
                        score -= 8000

                    if score > max_score:
                        max_score = score
                        best_card = cid

                await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})
                await asyncio.sleep(0.05)

                if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                    await process_super_ai_turn(session, card_database, process_action_func)
            return

        # ==========================================
        # 2. バトルフェーズ（パイプラインif ＋ 貪欲法）
        # ==========================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            if not opp_id: return

            action_loop_count = 0
            max_loop_limit = 30

            while session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID and action_loop_count < max_loop_limit:
                action_loop_count += 1

                my_mp = session.mp.get(BOT_USER_ID, 0)
                my_board = session.boards.get(BOT_USER_ID, [])
                opp_board = session.boards.get(opp_id, [])
                my_hand = session.hands.get(BOT_USER_ID, [])
                opp_hp = session.hp.get(opp_id, 20)
                my_hp = session.hp.get(BOT_USER_ID, 20)

                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]
                playable_cards = [c for c in my_hand if c.get("cost", 99) <= my_mp]
                
                # --- [PIPELINE 1] ドローの最優先実行 ---
                playable_draws = [c for c in playable_cards if c.get("id") == "s_04"]
                if playable_draws:
                    await process_action_func(session, BOT_USER_ID, {
                        "action": "PLAY_HAND", "card_instance_id": playable_draws[0]["instance_id"], "target": None
                    })
                    await asyncio.sleep(0.15)
                    continue  # ドロー後、即座にループを最初からやり直す

                # --- [PIPELINE 2] 複合リーサル判定（挑発除去＋全軍突撃） ---
                board_atk_total = sum(
                    u.get("atk", 0) * u.get("attacks_left", 1) for u in my_board 
                    if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0
                )
                hand_damage_total = sum(3 for c in playable_cards if c.get("id") == "s_01")
                
                # 挑発の総HPと、それを除去できる手札があるか確認
                taunt_hp_total = sum(t.get("curr_hp", 0) for t in opp_taunts)
                has_assassinate = any(c for c in playable_cards if c.get("id") == "s_05")
                
                is_lethal = False
                if not opp_taunts and ((board_atk_total + hand_damage_total) >= opp_hp):
                    is_lethal = True
                elif opp_taunts and (has_assassinate or hand_damage_total >= taunt_hp_total):
                    # 挑発を突破して勝てる場合
                    if board_atk_total >= opp_hp:
                        is_lethal = True

                # --- [PIPELINE 3] 重装兵の絶対着地（3MP以上） ---
                playable_taunts = [c for c in playable_cards if c.get("id") == "u_02"]
                if playable_taunts and not is_lethal and len(my_board) < 7:
                    await process_action_func(session, BOT_USER_ID, {
                        "action": "PLAY_HAND", "card_instance_id": playable_taunts[0]["instance_id"], "target": None
                    })
                    await asyncio.sleep(0.15)
                    continue

                # ------------------------------------------
                # 以降は「1ターンのマナ使い切り・盤面破壊」の貪欲評価
                # ------------------------------------------
                possible_candidates = []
                best_combo_card_inst_ids = set()

                if playable_cards and not is_lethal:
                    best_combo_score = -1
                    for r in range(1, len(playable_cards) + 1):
                        for combo in itertools.combinations(playable_cards, r):
                            cost_sum = sum(c.get("cost", 0) for c in combo)
                            if cost_sum > my_mp: continue

                            combo_score = 0
                            for c in combo:
                                cid = c.get("id")
                                if c.get("type") == "unit":
                                    combo_score += 5000 + (c.get("cost", 0) * 800) + (c.get("atk", 0) * 300)
                                elif cid == "s_05": combo_score += 8000 if opp_board else 1000
                                elif cid == "s_01": combo_score += 6000
                                else: combo_score += 3000

                            combo_score += (cost_sum * 400) # マナ使い切りボーナス
                            if combo_score > best_combo_score:
                                best_combo_score = combo_score
                                best_combo_card_inst_ids = {c.get("instance_id") for c in combo}

                # 手札プレイの候補リスト構築
                for c in playable_cards:
                    cid = c.get("id")
                    c_type = c.get("type")
                    c_inst = c.get("instance_id")
                    combo_bonus = 15000 if c_inst in best_combo_card_inst_ids else 0

                    if c_type == "spell" and c.get("effect") in ["assassinate", "damage", "burn", "freeze"]:
                        if c.get("effect") == "damage":
                            h_score = 999999 if (3 >= opp_hp or is_lethal) else (2000 + combo_bonus)
                            possible_candidates.append({
                                "score": h_score,
                                "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "hero", "id": opp_id}}
                            })

                        if opp_board:
                            for t in opp_board:
                                eff = c.get("effect")
                                is_walled = t.get("wall_turns", 0) > 0
                                
                                if eff == "assassinate":
                                    score = 10000 + (t.get("atk", 0) * 500) + (t.get("curr_hp", 0) * 300) + combo_bonus
                                    if t.get("taunt"): score += 5000  # リーサルのための挑発除去
                                    if is_walled: score += 4000
                                    possible_candidates.append({
                                        "score": score,
                                        "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                                    })
                                elif eff == "damage":
                                    real_dmg = 2 if is_walled else 3
                                    score = 5000 + (t.get("atk", 0) * 300) + combo_bonus
                                    if real_dmg >= t.get("curr_hp", 0): score += 5000
                                    if t.get("taunt"): score += 4000
                                    possible_candidates.append({
                                        "score": score,
                                        "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                                    })
                                # (他スペルの簡易化省略: 必要に応じて凍結・火傷を追加)

                    elif c_type == "unit" and len(my_board) < 7:
                        if c.get("atk", 0) == 0 and c.get("hp", 0) == 0: continue
                        score = 6000 + (c.get("cost", 0) * 300) + (c.get("atk", 0) * 200) + combo_bonus
                        possible_candidates.append({
                            "score": score,
                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                        })

                # 盤面ユニット攻撃の候補リスト構築
                for u in my_board:
                    if not u.get("can_attack") or u.get("frozen_turns", 0) > 0 or u.get("attacks_left", 0) <= 0:
                        continue
                    
                    u_atk, u_hp, u_inst = u.get("atk", 0), u.get("curr_hp", 0), u["instance_id"]
                    is_ranged = u.get("ranged", False)
                    targets = opp_taunts if opp_taunts else opp_board

                    if not is_lethal:
                        for t in targets:
                            if t.get("curr_hp", 0) <= 0: continue
                            eff_dmg = max(0, u_atk - 1) if t.get("wall_turns", 0) > 0 else u_atk
                            if eff_dmg <= 0 and not is_ranged: continue

                            kills_target = eff_dmg >= t.get("curr_hp", 0)
                            counter_dmg = 0 if is_ranged else (max(0, t.get("atk", 0) - 1) if u.get("wall_turns", 0) > 0 else t.get("atk", 0))
                            i_survive = counter_dmg < u_hp

                            score = 2000
                            if kills_target and i_survive: score += 12000
                            elif kills_target and not i_survive:
                                score += 4000 if t.get("atk", 0) >= u_atk else -3000
                            elif not kills_target and not i_survive:
                                score -= 10000

                            if is_ranged: score += 2000 # 小人（u_06）など遠距離の積極的盤面掃除
                            if t.get("taunt"): score += 1000

                            possible_candidates.append({
                                "score": score,
                                "act": {"action": "DECLARE_ATTACK", "attacker_id": u_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                            })

                    # 顔面攻撃
                    if not opp_taunts or is_lethal:
                        score = 999999 if (is_lethal or u_atk >= opp_hp) else 4500
                        possible_candidates.append({
                            "score": score,
                            "act": {"action": "DECLARE_ATTACK", "attacker_id": u_inst, "target": {"type": "hero", "id": opp_id}}
                        })

                # アクションの実行
                if not possible_candidates:
                    await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
                    break

                possible_candidates.sort(key=lambda x: x["score"], reverse=True)
                await process_action_func(session, BOT_USER_ID, possible_candidates[0]["act"])
                await asyncio.sleep(0.15)

            if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"Super AI Fatal Error Fallback: {e}", exc_info=True)
        if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
