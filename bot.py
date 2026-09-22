import asyncio
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger("CardBot")

BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "村岡国王（影武者）"

HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}

# カード定義
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
    "s_07": {"id": "s_07", "name": "凍結", "type": "spell", "cost": 1, "effect": "freeze", "need_target": True}, # バグカード
    "s_08": {"id": "s_08", "name": "火傷", "type": "spell", "cost": 1, "effect": "burn", "need_target": True},
    "s_09": {"id": "s_09", "name": "城壁", "type": "spell", "cost": 2, "effect": "wall", "need_target": True},
}

async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    """
    純粋・貪欲法（Greedy）AIエンジン
    「今できる最強の行動」を1手ずつ評価して、できる限界まで行動し続ける
    """
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================
        # 1. ドラフトフェーズ（貪欲カード選択）
        # ==========================================
        if session.status == "DRAFT":
            opts = session.draft_options.get(BOT_USER_ID, [])
            if opts:
                my_deck = session.decks.get(BOT_USER_ID, [])
                my_card_ids = [c.get("id") if isinstance(c, dict) else c for c in my_deck]
                spell_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("type") == "spell")

                best_card, max_score = opts[0], -99999

                for cid in opts:
                    c = BOT_CARD_DB.get(cid, {})
                    score = 0
                    
                    # If制限: バグカード除外
                    if cid == "s_07": score -= 99999
                    
                    # 絶対原則: ユニット超優先
                    if c.get("type") == "unit":
                        score += 1000 + c.get("atk", 0) * 50 + c.get("hp", 0) * 30
                    else:
                        score += 200
                        
                    # If制限: スペル過多（3枚以上）防止
                    if c.get("type") == "spell" and spell_count >= 3:
                        score -= 3000

                    # シナジー: 小人 + 城壁
                    if cid == "s_09" and "u_06" in my_card_ids:
                        score += 2000

                    if score > max_score:
                        max_score = score
                        best_card = cid

                await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})
                await asyncio.sleep(0.01)

                if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                    await process_super_ai_turn(session, card_database, process_action_func)
            return

        # ==========================================
        # 2. バトルフェーズ（貪欲法的ループ実行）
        # ==========================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            if not opp_id: return

            # 行動がなくなるまでループし続ける
            while session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
                my_mp = session.mp.get(BOT_USER_ID, 0)
                my_board = session.boards.get(BOT_USER_ID, [])
                opp_board = session.boards.get(opp_id, [])
                my_hand = session.hands.get(BOT_USER_ID, [])
                opp_hp = session.hp.get(opp_id, 20)

                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]
                possible_candidates = []

                # ------------------------------------------
                # A. 攻撃行動の候補リスト作成
                # ------------------------------------------
                for u in my_board:
                    if not u.get("can_attack") or u.get("frozen_turns", 0) > 0 or u.get("attacks_left", 0) <= 0:
                        continue
                    
                    u_atk = u.get("atk", 0)
                    u_cid = u.get("card_id", "")
                    u_inst = u.get("instance_id")

                    # 重装兵(u_02)は反撃回避のためユニットを殴らない（挑発がなければ顔面専用）
                    if u_cid == "u_02" and opp_board and not opp_taunts:
                        continue

                    targets = opp_taunts if opp_taunts else opp_board

                    for t in targets:
                        t_hp = t.get("curr_hp", 0)
                        if t_hp <= 0: continue
                        
                        # 城壁持ちに攻撃力1以下で殴るのは無駄なので禁止
                        if t.get("wall_turns", 0) > 0 and u_atk <= 1:
                            continue

                        # 点数計算：有利トレード重視
                        score = 500
                        eff_dmg = u_atk - 1 if t.get("wall_turns", 0) > 0 else u_atk
                        if eff_dmg >= t_hp: score += 400 # 撃破できる
                        if t.get("card_id") in ["u_03", "u_06", "u_05"]: score += 300 # 危険敵

                        possible_candidates.append({
                            "score": score,
                            "act": {"action": "DECLARE_ATTACK", "attacker_id": u_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                        })

                    # 挑発がいなければ敵ヒーロー（顔面）攻撃
                    if not opp_taunts:
                        # 確定リーサル（勝ち）なら最優先
                        score = 10000 if u_atk >= opp_hp else 600
                        possible_candidates.append({
                            "score": score,
                            "act": {"action": "DECLARE_ATTACK", "attacker_id": u_inst, "target": {"type": "hero", "id": opp_id}}
                        })

                # ------------------------------------------
                # B. 手札プレイの候補リスト作成
                # ------------------------------------------
                for c in my_hand:
                    cost = c.get("cost", 99)
                    if cost > my_mp: continue
                    
                    cid = c.get("id")
                    c_type = c.get("type")
                    c_inst = c.get("instance_id")

                    # バグカード s_07 絶対排除
                    if cid == "s_07": continue

                    # --- ユニット（最優先展開） ---
                    if c_type == "unit" and len(my_board) < 7:
                        # スペル < ユニット：盤面に並べることを超優先（マナ使い切りも重視）
                        score = 2000 + cost * 50
                        if c.get("haste"): score += 200
                        if c.get("taunt"): score += 150

                        possible_candidates.append({
                            "score": score,
                            "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                        })

                    # --- スペル ---
                    elif c_type == "spell":
                        eff = c.get("effect")
                        if eff == "damage": # 雷撃など
                            for t in opp_board:
                                possible_candidates.append({
                                    "score": 400 + t.get("atk", 0) * 30,
                                    "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                                })
                        elif eff == "assassinate": # 暗殺者
                            for t in opp_board:
                                possible_candidates.append({
                                    "score": 700 + t.get("atk", 0) * 50,
                                    "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                                })
                        elif eff == "aoe_damage" and len(opp_board) >= 2: # 嵐
                            possible_candidates.append({
                                "score": 800,
                                "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                            })
                        elif eff == "wall": # 城壁
                            for my_u in my_board:
                                b_score = 3000 if my_u.get("card_id") == "u_06" else 300 # 小人なら特大スコア
                                possible_candidates.append({
                                    "score": b_score,
                                    "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": my_u["instance_id"]}}
                                })
                        elif eff == "draw": # 補充
                            possible_candidates.append({
                                "score": 350,
                                "act": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                            })

                # ------------------------------------------
                # C. 一番スコアが高い候補を実行
                # ------------------------------------------
                if not possible_candidates:
                    # これ以上できる行動がないならターン終了
                    await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
                    break

                # 最も評価が高い行動を1つ選んで実行
                possible_candidates.sort(key=lambda x: x["score"], reverse=True)
                best_action = possible_candidates[0]["act"]

                await process_action_func(session, BOT_USER_ID, best_action)
                await asyncio.sleep(0.01) # サーバー処理待ち

    except Exception as e:
        logger.error(f"Greedy AI Error Fallback: {e}", exc_info=True)
        # 万が一のエラー時も即座にターン終了して絶対にフリーズさせない
        if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
