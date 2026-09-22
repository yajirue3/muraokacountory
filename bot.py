import asyncio
import logging
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
    【完全体】全ステータス・特殊効果対応型 アサルト貪欲法AI
    """
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================
        # 1. ドラフトフェーズ
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

                    # 呪文評価（凍結・暗殺・城壁・全体攻撃の優先度向上）
                    if cid in ["s_07", "s_05", "s_02"]: 
                        score += 4000
                    elif cid in ["s_01", "s_08", "s_09"]:
                        score += 3000

                    if c.get("type") == "unit":
                        score += 5000 + (c.get("atk", 0) * 120) + (c.get("hp", 0) * 80)
                        if c.get("haste"): score += 600
                        if c.get("taunt"): score += 500
                        if c.get("id") == "u_06": score += 2000 # 小人は強力
                    else:
                        score += 1000
                        
                    # 呪文の取りすぎ防止（最大3枚程度）
                    if c.get("type") == "spell" and spell_count >= 3:
                        score -= 7000

                    # 「小人」と「城壁」のシナジー評価
                    if cid == "s_09" and "u_06" in my_card_ids:
                        score += 8000

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

                # --- リーサル判定 ---
                board_atk_total = sum(
                    u.get("atk", 0) * u.get("attacks_left", 1) for u in my_board 
                    if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0
                )
                hand_damage_total = sum(3 for c in my_hand if c.get("id") == "s_01" and c.get("cost", 99) <= my_mp)
                is_lethal = (not opp_taunts) and ((board_atk_total + hand_damage_total) >= opp_hp)

                # ==========================================
                # A. 手札プレイ評価
                # ==========================================
                for c in my_hand:
                    cost = c.get("cost", 99)
                    if cost > my_mp: continue
                    
                    cid = c.get("id")
                    c_type = c.get("type")
                    c_inst = c.get("instance_id")

                    # ドロー（手札補給）
                    if c_type == "spell" and c.get("effect") == "draw":
                        possible_candidates.append({
                            "score": 10000,
                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                        })

                    # 単体妨害・除去スペル（凍結 / 火傷 / 暗殺 / 雷撃）
                    elif c_type == "spell" and c.get("effect") in ["assassinate", "damage", "burn", "freeze"]:
                        # ヒーロー対象（雷撃でリーサルまたは顔詰め）
                        if c.get("effect") == "damage":
                            h_score = 999999 if (3 >= opp_hp) else 3000
                            possible_candidates.append({
                                "score": h_score,
                                "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "hero", "id": opp_id}}
                            })

                        # 敵ユニット対象
                        if opp_board:
                            for t in opp_board:
                                eff = c.get("effect")
                                t_atk = t.get("atk", 0)
                                t_hp = t.get("curr_hp", 0)
                                is_frozen = t.get("frozen_turns", 0) > 0
                                is_walled = t.get("wall_turns", 0) > 0

                                score = 0

                                # 【凍結】攻撃力が一番高い未凍結の敵を止める
                                if eff == "freeze":
                                    if not is_frozen and t_atk >= 2:
                                        score = 8000 + (t_atk * 500)
                                        possible_candidates.append({
                                            "score": score,
                                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                                        })
                                    continue

                                # 【火傷】高HPまたは挑発持ちにスリップダメージ
                                elif eff == "burn":
                                    if t.get("burn_turns", 0) == 0:
                                        score = 5500 + (t_hp * 200) + (1000 if t.get("taunt") else 0)
                                        possible_candidates.append({
                                            "score": score,
                                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                                        })
                                    continue

                                # 【暗殺者】大型ユニット・挑発を即死
                                elif eff == "assassinate":
                                    score = 9000 + (t_atk * 400) + (t_hp * 200)
                                    if is_walled: score += 5000 # 城壁持ちは優先除去
                                    possible_candidates.append({
                                        "score": score,
                                        "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                                    })
                                    continue

                                # 【雷撃】ダメージスペル
                                elif eff == "damage":
                                    real_dmg = 2 if is_walled else 3
                                    score = 6000 + (t_atk * 200)
                                    if real_dmg >= t_hp: score += 4000 # 撃破できるなら超高評価
                                    possible_candidates.append({
                                        "score": score,
                                        "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                                    })

                    # 【城壁】自ユニット強化（小人または高火力ユニットに付与）
                    elif c_type == "spell" and c.get("effect") == "wall":
                        for my_u in my_board:
                            if my_u.get("wall_turns", 0) == 0:
                                b_score = 15000 if my_u.get("card_id") == "u_06" else (4000 + my_u.get("atk", 0) * 300)
                                possible_candidates.append({
                                    "score": b_score,
                                    "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": my_u["instance_id"]}}
                                })

                    # 【全体攻撃（嵐）】
                    elif c_type == "spell" and c.get("effect") == "aoe_damage":
                        if len(opp_board) >= 2 or any(u.get("curr_hp", 0) <= 2 for u in opp_board):
                            possible_candidates.append({
                                "score": 7000 + (len(opp_board) * 1500),
                                "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                            })

                    # 【回復・手札再編】
                    elif c_type == "spell" and c.get("effect") in ["heal", "reshape"]:
                        h_score = 6000 if (c.get("effect") == "heal" and my_hp <= 12) else 1500
                        possible_candidates.append({
                            "score": h_score,
                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                        })

                    # 【ユニット召喚】
                    elif c_type == "unit" and len(my_board) < 7:
                        score = 4000 + (cost * 150)
                        if cid == "u_06": score += 3000 # 小人は優先召喚
                        possible_candidates.append({
                            "score": score,
                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                        })

                # ==========================================
                # B. 盤面ユニット攻撃評価
                # ==========================================
                for u in my_board:
                    # 凍結中・攻撃不可能・攻撃権消費済みはスキップ
                    if not u.get("can_attack") or u.get("frozen_turns", 0) > 0 or u.get("attacks_left", 0) <= 0:
                        continue
                    
                    u_atk = u.get("atk", 0)
                    u_inst = u.get("instance_id")

                    targets = opp_taunts if opp_taunts else opp_board

                    # 1. 敵ユニット攻撃計算
                    if not is_lethal:
                        for t in targets:
                            t_hp = t.get("curr_hp", 0)
                            if t_hp <= 0: continue

                            # 城壁持ちへのダメージ減衰計算
                            eff_dmg = max(0, u_atk - 1) if t.get("wall_turns", 0) > 0 else u_atk
                            
                            # ダメージが通らない場合は無駄打ちしない
                            if eff_dmg <= 0 and not u.get("ranged"):
                                continue

                            score = 2000
                            if eff_dmg >= t_hp: score += 3000 # 一撃撃破
                            if t.get("card_id") in ["u_03", "u_06", "u_05"]: score += 1000 # 危険な敵を優先
                            if u.get("ranged"): score += 1500 # 遠距離（反撃なし）は積極的に盤面処理

                            possible_candidates.append({
                                "score": score,
                                "act": {"action": "DECLARE_ATTACK", "attacker_id": u_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                            })

                    # 2. 敵ヒーロー直接攻撃
                    if not opp_taunts:
                        score = 999999 if (is_lethal or u_atk >= opp_hp) else 3500
                        possible_candidates.append({
                            "score": score,
                            "act": {"action": "DECLARE_ATTACK", "attacker_id": u_inst, "target": {"type": "hero", "id": opp_id}}
                        })

                # ==========================================
                # C. 最善手のアクション実行
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
