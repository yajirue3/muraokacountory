import asyncio
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger("CardBot")

BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "村岡国王（影武者）"

HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}

# カードデータベース定義
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
    "s_07": {"id": "s_07", "name": "凍結", "type": "spell", "cost": 1, "effect": "freeze", "need_target": True}, # バグカード排除対象
    "s_08": {"id": "s_08", "name": "火傷", "type": "spell", "cost": 1, "effect": "burn", "need_target": True},
    "s_09": {"id": "s_09", "name": "城壁", "type": "spell", "cost": 2, "effect": "wall", "need_target": True},
}

async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    """
    順序最適化型・最強アグレッシブ貪欲法AIエンジン
    """
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================
        # 1. ドラフトフェーズ（ピック優先度最適化）
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
                    
                    # バグカード（s_07）は絶対選ばない
                    if cid == "s_07": 
                        score -= 999999
                    
                    # ユニット最優先
                    if c.get("type") == "unit":
                        score += 5000 + (c.get("atk", 0) * 100) + (c.get("hp", 0) * 80)
                        if c.get("haste"): score += 500
                        if c.get("taunt"): score += 400
                    else:
                        score += 1000
                        
                    # スペル過多（3枚以上）防止
                    if c.get("type") == "spell" and spell_count >= 3:
                        score -= 8000

                    # シナジー：小人 + 城壁
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
        # 2. バトルフェーズ（順序制御・貪欲ループ）
        # ==========================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            if not opp_id: return

            action_loop_count = 0
            max_loop_limit = 25  # 無限ループ防止用の最大限界数

            while session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID and action_loop_count < max_loop_limit:
                action_loop_count += 1

                my_mp = session.mp.get(BOT_USER_ID, 0)
                my_board = session.boards.get(BOT_USER_ID, [])
                opp_board = session.boards.get(opp_id, [])
                my_hand = session.hands.get(BOT_USER_ID, [])
                opp_hp = session.hp.get(opp_id, 20)

                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]
                possible_candidates = []

                # ------------------------------------------
                # リーサル（確定即死）判定
                # ------------------------------------------
                board_atk_total = sum(
                    u.get("atk", 0) for u in my_board 
                    if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0
                )
                hand_damage_total = 0
                for c in my_hand:
                    if c.get("cost", 99) <= my_mp:
                        if c.get("id") == "s_01": hand_damage_total += 3  # 雷撃

                is_lethal = (not opp_taunts) and ((board_atk_total + hand_damage_total) >= opp_hp)

                # ------------------------------------------
                # A. 手札プレイ候補の評価（優先順位順）
                # ------------------------------------------
                for c in my_hand:
                    cost = c.get("cost", 99)
                    if cost > my_mp: continue  # マナ不足
                    
                    cid = c.get("id")
                    c_type = c.get("type")
                    c_inst = c.get("instance_id")

                    if cid == "s_07": continue  # バグカード排除

                    # --- 【1番目の優先度】ドロー（補充）を最優先（手札補充） ---
                    if c_type == "spell" and c.get("effect") == "draw":
                        possible_candidates.append({
                            "score": 10000,
                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                        })

                    # --- 【2番目の優先度】障害物・挑発・敵の除去スペル（攻撃前の道あけ） ---
                    elif c_type == "spell" and c.get("effect") in ["assassinate", "damage", "burn"]:
                        if opp_board:
                            for t in opp_board:
                                # 挑発持ち、または高攻撃力ユニットを優先除去
                                base_score = 8000 if t.get("taunt") else 6000
                                score = base_score + (t.get("atk", 0) * 100)
                                possible_candidates.append({
                                    "score": score,
                                    "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                                })

                    # --- 【3番目の優先度】ユニット展開（最速盤面構築） ---
                    elif c_type == "unit" and len(my_board) < 7:
                        score = 4000 + (cost * 100)
                        if c.get("haste"): score += 500
                        if c.get("taunt"): score += 300
                        possible_candidates.append({
                            "score": score,
                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                        })

                    # --- その他スペル ---
                    elif c_type == "spell":
                        eff = c.get("effect")
                        if eff == "aoe_damage" and len(opp_board) >= 2:
                            possible_candidates.append({
                                "score": 5000,
                                "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                            })
                        elif eff == "wall" and my_board:
                            for my_u in my_board:
                                b_score = 9000 if my_u.get("card_id") == "u_06" else 3000
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
                # B. 盤面ユニット攻撃候補の評価
                # ------------------------------------------
                for u in my_board:
                    if not u.get("can_attack") or u.get("frozen_turns", 0) > 0 or u.get("attacks_left", 0) <= 0:
                        continue
                    
                    u_atk = u.get("atk", 0)
                    u_cid = u.get("card_id", "")
                    u_inst = u.get("instance_id")

                    # 重装兵(u_02)は敵ユニットへ突撃せず顔面専念
                    if u_cid == "u_02" and opp_board and not opp_taunts:
                        continue

                    targets = opp_taunts if opp_taunts else opp_board

                    # リーサルでない場合のみ有利トレードの攻撃を計算
                    if not is_lethal:
                        for t in targets:
                            t_hp = t.get("curr_hp", 0)
                            if t_hp <= 0: continue
                            
                            # 城壁(軽減1)に攻撃力1以下で突撃（0ダメ無駄撃ち）を排除
                            if t.get("wall_turns", 0) > 0 and u_atk <= 1:
                                continue

                            score = 1500
                            eff_dmg = u_atk - 1 if t.get("wall_turns", 0) > 0 else u_atk
                            
                            # 一撃撃破（有利トレード）
                            if eff_dmg >= t_hp: score += 1000
                            # 危険敵の撃滅
                            if t.get("card_id") in ["u_03", "u_06", "u_05"]: score += 800

                            possible_candidates.append({
                                "score": score,
                                "act": {"action": "DECLARE_ATTACK", "attacker_id": u_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                            })

                    # 挑発がいない場合、または確定リーサル時は顔面攻撃
                    if not opp_taunts:
                        # リーサル状態なら超絶優先度で即死させる
                        score = 999999 if is_lethal or (u_atk >= opp_hp) else 3500
                        possible_candidates.append({
                            "score": score,
                            "act": {"action": "DECLARE_ATTACK", "attacker_id": u_inst, "target": {"type": "hero", "id": opp_id}}
                        })

                # ------------------------------------------
                # C. 実行または安全なターン終了判定
                # ------------------------------------------
                if not possible_candidates:
                    # これ以上できる行動が無い場合は【確実にターン終了】
                    await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
                    break

                # 評価値が最高の「最強手」を実行
                possible_candidates.sort(key=lambda x: x["score"], reverse=True)
                best_action = possible_candidates[0]["act"]

                await process_action_func(session, BOT_USER_ID, best_action)
                
                # サーバー通信待ち（同期ズレ防止）
                await asyncio.sleep(0.15)

            # 最大ループに達しても自身のターンの場合は安全終了
            if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"Super AI Fatal Error Fallback: {e}", exc_info=True)
        # 例外発生時もフリーズさせずにターンを終了
        if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
