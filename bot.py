import asyncio
import logging
import itertools
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
    """
    【絶対王者】マナカーブ最適化 ＆ 有利トレード・リーサル特化型AI
    """
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================
        # 1. ドラフトフェーズ（評価基準の完全適正化）
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
                        # 奇術師(0/0)を除外評価
                        if c.get("atk", 0) == 0 and c.get("hp", 0) == 0:
                            score -= 20000
                        else:
                            score += 5000 + (c.get("atk", 0) * 150) + (c.get("hp", 0) * 100)
                            if c.get("haste"): score += 1000
                            if c.get("taunt"): score += 800
                            if cid == "u_04": score += 2000  # 巨兵などフィニッシャーを評価
                    else:
                        score += 2000
                        # 確定除去・強力スペル評価
                        if cid in ["s_05", "s_01", "s_07"]:
                            score += 3000
                        elif cid == "s_04":
                            score += 2000

                    # 呪文多すぎのペナルティ
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
        # 2. バトルフェーズ
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
                possible_candidates = []

                # ------------------------------------------
                # 超精密リーサル計算
                # ------------------------------------------
                board_atk_total = sum(
                    u.get("atk", 0) * u.get("attacks_left", 1) for u in my_board 
                    if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0
                )
                hand_damage_total = sum(3 for c in my_hand if c.get("id") == "s_01" and c.get("cost", 99) <= my_mp)
                is_lethal = (not opp_taunts) and ((board_atk_total + hand_damage_total) >= opp_hp)

                # ==========================================
                # A. 手札プレイ評価（マナ使い切り最適化プランニング）
                # ==========================================
                playable_cards = [c for c in my_hand if c.get("cost", 99) <= my_mp]

                # ナップサック問題的な組み合わせ全探索で「今ターン最大の盤面価値を作る手札セット」を割り出す
                best_combo_card_inst_ids = set()
                if playable_cards and not is_lethal:
                    best_combo_score = -1
                    for r in range(1, len(playable_cards) + 1):
                        for combo in itertools.combinations(playable_cards, r):
                            cost_sum = sum(c.get("cost", 0) for c in combo)
                            if cost_sum > my_mp: continue

                            combo_score = 0
                            for c in combo:
                                c_type = c.get("type")
                                cid = c.get("id")
                                if c_type == "unit":
                                    combo_score += 5000 + (c.get("cost", 0) * 800) + (c.get("atk", 0) * 300)
                                elif cid == "s_05":  # 暗殺者
                                    combo_score += 8000 if opp_board else 1000
                                elif cid == "s_01":  # 雷撃
                                    combo_score += 6000
                                elif cid == "s_04":  # 補充（手札が少ない場合のみ優先）
                                    combo_score += 7000 if len(my_hand) <= 2 else 2000
                                else:
                                    combo_score += 3000

                            # マナを余らせないボーナス
                            combo_score += (cost_sum * 400)

                            if combo_score > best_combo_score:
                                best_combo_score = combo_score
                                best_combo_card_inst_ids = {c.get("instance_id") for c in combo}

                for c in playable_cards:
                    cost = c.get("cost", 99)
                    cid = c.get("id")
                    c_type = c.get("type")
                    c_inst = c.get("instance_id")

                    # マナ計画に含まれているカードならスコア大幅加算
                    combo_bonus = 15000 if c_inst in best_combo_card_inst_ids else 0

                    # 1. 補充（ドロー）
                    if c_type == "spell" and c.get("effect") == "draw":
                        # 手札が枯渇している時のみ最優先
                        draw_score = (8000 if len(my_hand) <= 2 else 3000) + combo_bonus
                        possible_candidates.append({
                            "score": draw_score,
                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                        })

                    # 2. 単体妨害・除去スペル
                    elif c_type == "spell" and c.get("effect") in ["assassinate", "damage", "burn", "freeze"]:
                        if c.get("effect") == "damage":
                            h_score = 999999 if (3 >= opp_hp) else (2000 + combo_bonus)
                            possible_candidates.append({
                                "score": h_score,
                                "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "hero", "id": opp_id}}
                            })

                        if opp_board:
                            for t in opp_board:
                                eff = c.get("effect")
                                t_atk = t.get("atk", 0)
                                t_hp = t.get("curr_hp", 0)
                                is_frozen = t.get("frozen_turns", 0) > 0
                                is_walled = t.get("wall_turns", 0) > 0

                                # 【凍結】
                                if eff == "freeze":
                                    if not is_frozen and t_atk >= 2:
                                        possible_candidates.append({
                                            "score": 7000 + (t_atk * 500) + combo_bonus,
                                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                                        })
                                    continue

                                # 【火傷】
                                elif eff == "burn":
                                    if t.get("burn_turns", 0) == 0:
                                        possible_candidates.append({
                                            "score": 5000 + (t_hp * 200) + combo_bonus,
                                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                                        })
                                    continue

                                # 【暗殺者】（大型を最優先除去）
                                elif eff == "assassinate":
                                    score = 10000 + (t_atk * 500) + (t_hp * 300) + combo_bonus
                                    if is_walled: score += 4000
                                    possible_candidates.append({
                                        "score": score,
                                        "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                                    })
                                    continue

                                # 【雷撃】
                                elif eff == "damage":
                                    real_dmg = 2 if is_walled else 3
                                    score = 5000 + (t_atk * 300) + combo_bonus
                                    if real_dmg >= t_hp: score += 5000  # 確定撃破
                                    possible_candidates.append({
                                        "score": score,
                                        "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                                    })

                    # 3. 【城壁】（高攻撃力・キーユニット優先保護）
                    elif c_type == "spell" and c.get("effect") == "wall":
                        for my_u in my_board:
                            if my_u.get("wall_turns", 0) == 0:
                                b_score = 6000 + (my_u.get("atk", 0) * 400) + combo_bonus
                                possible_candidates.append({
                                    "score": b_score,
                                    "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": my_u["instance_id"]}}
                                })

                    # 4. 【全体攻撃（嵐）】
                    elif c_type == "spell" and c.get("effect") == "aoe_damage":
                        if len(opp_board) >= 2 or any(u.get("curr_hp", 0) <= 2 for u in opp_board):
                            possible_candidates.append({
                                "score": 8000 + (len(opp_board) * 2000) + combo_bonus,
                                "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                            })

                    # 5. 【回復・手札再編】
                    elif c_type == "spell" and c.get("effect") in ["heal", "reshape"]:
                        h_score = (8000 if my_hp <= 10 else 1000) + combo_bonus
                        possible_candidates.append({
                            "score": h_score,
                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                        })

                    # 6. 【ユニット召喚】（ステータス反映＆奇術師除外）
                    elif c_type == "unit" and len(my_board) < 7:
                        if c.get("atk", 0) == 0 and c.get("hp", 0) == 0:
                            continue  # 奇術師などのステータス0は出さない
                        
                        score = 6000 + (cost * 300) + (c.get("atk", 0) * 200) + combo_bonus
                        possible_candidates.append({
                            "score": score,
                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                        })

                # ==========================================
                # B. 盤面ユニット攻撃評価（有利トレードロジック）
                # ==========================================
                for u in my_board:
                    if not u.get("can_attack") or u.get("frozen_turns", 0) > 0 or u.get("attacks_left", 0) <= 0:
                        continue
                    
                    u_atk = u.get("atk", 0)
                    u_hp = u.get("curr_hp", 0)
                    u_inst = u.get("instance_id")
                    is_ranged = u.get("ranged", False)

                    targets = opp_taunts if opp_taunts else opp_board

                    if not is_lethal:
                        for t in targets:
                            t_hp = t.get("curr_hp", 0)
                            t_atk = t.get("atk", 0)
                            if t_hp <= 0: continue

                            eff_dmg = max(0, u_atk - 1) if t.get("wall_turns", 0) > 0 else u_atk
                            if eff_dmg <= 0 and not is_ranged: continue

                            # 判定: 敵を撃破できるか＆自分が生き残るか
                            kills_target = eff_dmg >= t_hp
                            counter_dmg = 0 if is_ranged else (max(0, t_atk - 1) if u.get("wall_turns", 0) > 0 else t_atk)
                            i_survive = counter_dmg < u_hp

                            score = 2000
                            if kills_target and i_survive:
                                score += 12000  # 【一方的撃破】超高評価
                            elif kills_target and not i_survive:
                                # 相打ちは相手のコスト・ATKが高い場合のみ許容
                                if t_atk >= u_atk:
                                    score += 4000
                                else:
                                    score -= 3000  # 不利相打ちは回避
                            elif not kills_target and not i_survive:
                                score -= 10000  # 【無駄死に】絶対に避ける

                            if is_ranged: score += 2000  # 遠距離は積極攻撃

                            possible_candidates.append({
                                "score": score,
                                "act": {"action": "DECLARE_ATTACK", "attacker_id": u_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                            })

                    # ヒーロー直接攻撃
                    if not opp_taunts:
                        score = 999999 if (is_lethal or u_atk >= opp_hp) else 4500
                        possible_candidates.append({
                            "score": score,
                            "act": {"action": "DECLARE_ATTACK", "attacker_id": u_inst, "target": {"type": "hero", "id": opp_id}}
                        })

                # ==========================================
                # C. アクション実行
                # ==========================================
                if not possible_candidates:
                    await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
                    break

                possible_candidates.sort(key=lambda x: x["score"], reverse=True)
                best_action = possible_candidates[0]["act"]

                await process_action_func(session, BOT_USER_ID, best_action)
                await asyncio.sleep(0.15)

            if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"Super AI Fatal Error Fallback: {e}", exc_info=True)
        if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
