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
    "s_07": {"id": "s_07", "name": "凍結", "type": "spell", "cost": 1, "effect": "freeze", "need_target": True}, # ★解禁！
    "s_08": {"id": "s_08", "name": "火傷", "type": "spell", "cost": 1, "effect": "burn", "need_target": True},
    "s_09": {"id": "s_09", "name": "城壁", "type": "spell", "cost": 2, "effect": "wall", "need_target": True},
}

async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    """
    【完全体】凍結解禁・全アンチメタ・アグレッシブ貪欲法AI
    """
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================
        # 1. ドラフトフェーズ（凍結の評価を引き上げ）
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
                    
                    # 凍結(s_07)は1コスト優秀スペルとして高評価
                    if cid == "s_07": 
                        score += 3500

                    if c.get("type") == "unit":
                        score += 5000 + (c.get("atk", 0) * 100) + (c.get("hp", 0) * 80)
                        if c.get("haste"): score += 500
                        if c.get("taunt"): score += 400
                    else:
                        score += 1000
                        
                    if c.get("type") == "spell" and spell_count >= 3:
                        score -= 8000

                    if cid == "s_09" and "u_06" in my_card_ids:
                        score += 10000

                    if score > max_score:
                        max_score = score
                        best_card = cid

                await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})
                await asyncio.sleep(0.05)

                if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                    await process_super_ai_turn(session, card_database, process_action_func)
            return

        # ==========================================
        # 2. バトルフェーズ（凍結コンボ組み込み）
        # ==========================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            if not opp_id: return

            action_loop_count = 0
            max_loop_limit = 25

            while session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID and action_loop_count < max_loop_limit:
                action_loop_count += 1

                my_mp = session.mp.get(BOT_USER_ID, 0)
                my_board = session.boards.get(BOT_USER_ID, [])
                opp_board = session.boards.get(opp_id, [])
                my_hand = session.hands.get(BOT_USER_ID, [])
                opp_hp = session.hp.get(opp_id, 20)

                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]
                possible_candidates = []

                # リーサル（確定即死）計算
                board_atk_total = sum(
                    u.get("atk", 0) for u in my_board 
                    if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0
                )
                hand_damage_total = sum(3 for c in my_hand if c.get("id") == "s_01" and c.get("cost", 99) <= my_mp)
                is_lethal = (not opp_taunts) and ((board_atk_total + hand_damage_total) >= opp_hp)

                # ------------------------------------------
                # A. 手札プレイ評価（凍結・妨害・展開）
                # ------------------------------------------
                for c in my_hand:
                    cost = c.get("cost", 99)
                    if cost > my_mp: continue
                    
                    cid = c.get("id")
                    c_type = c.get("type")
                    c_inst = c.get("instance_id")

                    # 1. 補充（ドロー）最優先
                    if c_type == "spell" and c.get("effect") == "draw":
                        possible_candidates.append({
                            "score": 10000,
                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                        })

                    # 2. 確定除去・【凍結（s_07）】による最強無力化
                    elif c_type == "spell" and c.get("effect") in ["assassinate", "damage", "burn", "freeze"]:
                        if opp_board:
                            for t in opp_board:
                                base_score = 6000
                                eff = c.get("effect")

                                # ★凍結のロジック：未凍結かつ高攻撃力の敵（巨兵など）を1マナで完全無効化する
                                if eff == "freeze":
                                    if t.get("frozen_turns", 0) == 0:
                                        score = 7000 + (t.get("atk", 0) * 300) # 攻撃力が高い敵ほど優先凍結！
                                        possible_candidates.append({
                                            "score": score,
                                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                                        })
                                    continue # 既に凍結中の敵には重ね掛けしない

                                # 城壁＋挑発の優先破壊
                                if t.get("taunt") and t.get("wall_turns", 0) > 0:
                                    base_score = 15000
                                elif t.get("taunt"):
                                    base_score = 8000
                                
                                score = base_score + (t.get("atk", 0) * 100)
                                possible_candidates.append({
                                    "score": score,
                                    "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                                })

                    # 3. ユニット展開（「嵐」対策＆「小人」保護）
                    elif c_type == "unit" and len(my_board) < 7:
                        if len(my_board) >= 3 and not is_lethal:
                            score = 1000
                        else:
                            score = 4000 + (cost * 100)
                            
                        if cid == "u_06":
                            has_wall_in_hand = any(hc.get("id") == "s_09" for hc in my_hand)
                            has_taunt_on_board = any(bu.get("taunt") for bu in my_board)
                            if (has_wall_in_hand and my_mp >= 7) or has_taunt_on_board:
                                score += 3000
                            else:
                                score -= 2000

                        possible_candidates.append({
                            "score": score,
                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                        })

                    # その他スペル
                    elif c_type == "spell":
                        eff = c.get("effect")
                        if eff == "aoe_damage" and len(opp_board) >= 2:
                            possible_candidates.append({
                                "score": 5000,
                                "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                            })
                        elif eff == "wall" and my_board:
                            for my_u in my_board:
                                b_score = 12000 if my_u.get("card_id") == "u_06" else 3000
                                possible_candidates.append({
                                    "score": b_score,
                                    "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": my_u["instance_id"]}}
                                })
                        elif eff in ["heal", "reshape"]:
                            possible_candidates.append({
                                "score": 2000,
                                "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                            })

                # ------------------------------------------
                # B. 盤面ユニット攻撃評価
                # ------------------------------------------
                for u in my_board:
                    if not u.get("can_attack") or u.get("frozen_turns", 0) > 0 or u.get("attacks_left", 0) <= 0:
                        continue
                    
                    u_atk = u.get("atk", 0)
                    u_cid = u.get("card_id", "")
                    u_inst = u.get("instance_id")

                    if u_cid == "u_02" and opp_board and not opp_taunts:
                        continue

                    targets = opp_taunts if opp_taunts else opp_board

                    if not is_lethal:
                        for t in targets:
                            t_hp = t.get("curr_hp", 0)
                            if t_hp <= 0: continue
                            
                            if t.get("wall_turns", 0) > 0 and u_atk <= 1:
                                continue

                            score = 1500
                            eff_dmg = u_atk - 1 if t.get("wall_turns", 0) > 0 else u_atk
                            
                            if eff_dmg >= t_hp: score += 1000
                            if t.get("card_id") in ["u_03", "u_06", "u_05"]: score += 800

                            possible_candidates.append({
                                "score": score,
                                "act": {"action": "DECLARE_ATTACK", "attacker_id": u_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                            })

                    if not opp_taunts:
                        score = 999999 if is_lethal or (u_atk >= opp_hp) else 3500
                        possible_candidates.append({
                            "score": score,
                            "act": {"action": "DECLARE_ATTACK", "attacker_id": u_inst, "target": {"type": "hero", "id": opp_id}}
                        })

                # ------------------------------------------
                # C. アクション決定＆安全終了
                # ------------------------------------------
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
