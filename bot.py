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
    勝つためならなりふり構わない最強・貪欲法（Greedy-Plus）AIエンジン
    """
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================
        # 1. ドラフトフェーズ（最強ピック評価）
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
                    
                    # Ifガード: バグカード（s_07）絶対除外
                    if cid == "s_07": 
                        score -= 999999
                    
                    # 絶対原則: ユニットを最優先（盤面形成力）
                    if c.get("type") == "unit":
                        score += 5000 + (c.get("atk", 0) * 100) + (c.get("hp", 0) * 80)
                        if c.get("haste"): score += 500
                        if c.get("taunt"): score += 400
                    else:
                        score += 1000
                        
                    # Ifガード: スペル過多（3枚以上）を防止
                    if c.get("type") == "spell" and spell_count >= 3:
                        score -= 8000

                    # シナジー: 小人 + 城壁の最強コンボを最優先確保
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
        # 2. バトルフェーズ（完全自動・高威力貪欲実行）
        # ==========================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            if not opp_id: return

            action_loop_count = 0
            max_loop_limit = 25  # 無限ループ防止用の最大行動回数

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
                # A. 盤面ユニットの全攻撃パターン評価
                # ------------------------------------------
                for u in my_board:
                    if not u.get("can_attack") or u.get("frozen_turns", 0) > 0 or u.get("attacks_left", 0) <= 0:
                        continue
                    
                    u_atk = u.get("atk", 0)
                    u_cid = u.get("card_id", "")
                    u_inst = u.get("instance_id")

                    # Ifガード: 重装兵(u_02)は反撃死を避けるため顔面優先（挑発不在時）
                    if u_cid == "u_02" and opp_board and not opp_taunts:
                        continue

                    targets = opp_taunts if opp_taunts else opp_board

                    for t in targets:
                        t_hp = t.get("curr_hp", 0)
                        if t_hp <= 0: continue
                        
                        # Ifガード: 城壁(軽減1)の敵に攻撃力1以下で殴る（0ダメージ無駄撃ち）を禁止
                        if t.get("wall_turns", 0) > 0 and u_atk <= 1:
                            continue

                        score = 800
                        eff_dmg = u_atk - 1 if t.get("wall_turns", 0) > 0 else u_atk
                        
                        # 一撃で倒せる（有利トレード）
                        if eff_dmg >= t_hp: score += 1200 
                        # 危険カード（魔導士、小人、吸血鬼）の優先除去
                        if t.get("card_id") in ["u_03", "u_06", "u_05"]: score += 800 

                        possible_candidates.append({
                            "score": score,
                            "act": {"action": "DECLARE_ATTACK", "attacker_id": u_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                        })

                    # 挑発がいない場合の顔面攻撃
                    if not opp_taunts:
                        # 確定リーサル（一撃で相手を倒せるなら最優先度で即死させる）
                        score = 999999 if u_atk >= opp_hp else 1500
                        possible_candidates.append({
                            "score": score,
                            "act": {"action": "DECLARE_ATTACK", "attacker_id": u_inst, "target": {"type": "hero", "id": opp_id}}
                        })

                # ------------------------------------------
                # B. 手札使用の全パターン評価
                # ------------------------------------------
                for c in my_hand:
                    cost = c.get("cost", 99)
                    if cost > my_mp: continue  # マナ不足はスキップ
                    
                    cid = c.get("id")
                    c_type = c.get("type")
                    c_inst = c.get("instance_id")

                    # Ifガード: バグカード(s_07)排斥
                    if cid == "s_07": continue

                    # ユニット展開（マナ全使い切り・盤面展開を最優先）
                    if c_type == "unit" and len(my_board) < 7:
                        score = 3000 + (cost * 100)
                        if c.get("haste"): score += 500
                        if c.get("taunt"): score += 300
                        possible_candidates.append({
                            "score": score,
                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                        })

                    # スペル行使
                    elif c_type == "spell":
                        eff = c.get("effect")
                        
                        # 単体火傷・ダメージ・暗殺（敵がいる時のみ）
                        if eff in ["damage", "assassinate", "burn"] and opp_board:
                            for t in opp_board:
                                possible_candidates.append({
                                    "score": 1000 + (t.get("atk", 0) * 100),
                                    "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                                })
                        # 全体攻撃（敵が2体以上いる時に効率最大化）
                        elif eff == "aoe_damage" and len(opp_board) >= 2:
                            possible_candidates.append({
                                "score": 2500,
                                "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                            })
                        # 城壁（味方ユニットがいる時。小人なら超爆発的アドバンテージ）
                        elif eff == "wall" and my_board:
                            for my_u in my_board:
                                b_score = 10000 if my_u.get("card_id") == "u_06" else 1200
                                possible_candidates.append({
                                    "score": b_score,
                                    "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": my_u["instance_id"]}}
                                })
                        # 補充・回復
                        elif eff in ["draw", "heal", "reshape"]:
                            possible_candidates.append({
                                "score": 800,
                                "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                            })

                # ------------------------------------------
                # C. 実行選択と絶対ターン終了保証
                # ------------------------------------------
                if not possible_candidates:
                    # 手札もプレイできず、攻撃可能なユニットも存在しない場合は【確実にターン終了】
                    await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
                    break

                # スコアが最も高い「最強の1手」を選択して実行
                possible_candidates.sort(key=lambda x: x["score"], reverse=True)
                best_action = possible_candidates[0]["act"]

                await process_action_func(session, BOT_USER_ID, best_action)
                
                # 同期ズレ防止用待機（0.15秒）
                await asyncio.sleep(0.15)

            # ループ上限に達しても自身のターンの場合は安全にターン交代
            if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"Super AI Fatal Error Fallback: {e}", exc_info=True)
        # 万が一の予期せぬ例外発生時も絶対フリーズさせずターン終了を送信
        if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
