import asyncio
import copy
import logging
import itertools
import random
from typing import Dict, List, Any, Optional

logger = logging.getLogger("CardBot")

BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "村岡国王（影武者）"

# ========== 復元：これがないとcard.py等でImportErrorになります ==========
HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}
# ====================================================================

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
    【絶対王者フルコード：完全探索・カンニングAI】
    相手の手札を読み、嵐・暗殺者をケア。MP最大効率と有利トレードをシミュレーションして行動を決定する。
    """
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================
        # 1. ドラフトフェーズ（黄金の6枚ピック）
        # ==========================================
        if session.status == "DRAFT":
            opts = session.draft_options.get(BOT_USER_ID, [])
            if opts:
                my_deck = session.decks.get(BOT_USER_ID, [])
                my_card_ids = [c.get("id") if isinstance(c, dict) else c for c in my_deck]
                
                # 黄金比率の目標枚数
                target_counts = {"u_01": 2, "u_02": 2, "u_04": 1, "s_09": 1}
                current_counts = {cid: my_card_ids.count(cid) for cid in target_counts}

                best_card = opts[0]
                max_score = -999999

                for cid in opts:
                    if cid == "u_07": continue  # 奇術師は完全スルー
                    
                    score = 0
                    if cid in target_counts and current_counts[cid] < target_counts[cid]:
                        score += 10000 + (target_counts[cid] - current_counts[cid]) * 1000
                        if cid == "u_01": score += 5000  # 先鋒兵は超優先
                        if cid == "u_02": score += 4000  # 重装兵も優先
                    else:
                        # 目標外または超過分は、強力な除去とドローを優先
                        if cid in ["s_01", "s_05"]: score += 3000
                        elif cid == "s_04": score += 2500
                        elif cid == "u_03": score += 2000
                        else: score += 1000

                    if score > max_score:
                        max_score = score
                        best_card = cid

                await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})
                await asyncio.sleep(0.01)

                if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                    await process_super_ai_turn(session, card_database, process_action_func)
            return

        # ==========================================
        # 2. バトルフェーズ（カンニング＆全探索シミュレーション）
        # ==========================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            if not opp_id: return

            # カンニング：相手の手札を取得
            opp_hand = session.hands.get(opp_id, [])
            opp_hand_ids = [c.get("id") for c in opp_hand]
            opp_has_storm = "s_02" in opp_hand_ids
            opp_has_assassin = "s_05" in opp_hand_ids

            action_loop_count = 0
            max_loop_limit = 20

            while session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID and action_loop_count < max_loop_limit:
                action_loop_count += 1

                my_mp = session.mp.get(BOT_USER_ID, 0)
                my_board = session.boards.get(BOT_USER_ID, [])
                opp_board = session.boards.get(opp_id, [])
                my_hand = session.hands.get(BOT_USER_ID, [])
                opp_hp = session.hp.get(opp_id, 20)
                my_hp = session.hp.get(BOT_USER_ID, 20)

                playable_cards = [c for c in my_hand if c.get("cost", 99) <= my_mp]
                active_units = [u for u in my_board if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0]
                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

                # --- STEP 1: 最優先でドロー (s_04) を使用し、手札を増やす ---
                s04_cards = [c for c in playable_cards if c.get("id") == "s_04"]
                if s04_cards and len(my_hand) < 7:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s04_cards[0]["instance_id"], "target": None})
                    await asyncio.sleep(0.05); continue

                # --- STEP 2: 貪欲ハイブリッド探索（次の一手スコアリング） ---
                best_action = None
                best_score = -999999

                # 【候補1】カードのプレイ
                for card in playable_cards:
                    cid = card["id"]
                    
                    # ユニット展開
                    if card["type"] == "unit" and len(my_board) < 7:
                        score = card["cost"] * 100 + card.get("atk", 0) * 10 + card.get("hp", 0) * 10
                        if cid == "u_01": score += 500  # 疾走は即戦力
                        if cid == "u_02": score += 400  # 挑発の防壁
                        
                        # 相手手札カンニングによるケア
                        if opp_has_storm and card.get("hp", 0) <= 2:
                            score -= 2000  # 嵐で死ぬユニットは評価減
                        if opp_has_assassin and cid == "u_04":
                            score -= 3000  # 暗殺者を持たれている間の巨兵は控える
                            
                        if score > best_score:
                            best_score = score
                            best_action = {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": None}

                    # スペル使用
                    elif card["type"] == "spell":
                        if cid == "s_01": # 雷撃
                            for t in opp_board:
                                if t.get("curr_hp", 0) <= 3 and t.get("wall_turns", 0) == 0:
                                    sc = 300 + t.get("atk", 0) * 10
                                    if sc > best_score:
                                        best_score = sc; best_action = {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                            if not opp_taunts and opp_hp <= 3:
                                best_score = 1000000; best_action = {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "hero", "id": opp_id}}
                        
                        elif cid == "s_02": # 嵐
                            if len(opp_board) >= 2:
                                sc = 400 + sum(min(2, t.get("curr_hp", 0)) for t in opp_board) * 10
                                if sc > best_score:
                                    best_score = sc; best_action = {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": None}
                                    
                        elif cid == "s_05": # 暗殺者
                            threats = [t for t in opp_board if t.get("atk", 0) >= 4 or t.get("curr_hp", 0) >= 5 or t.get("taunt")]
                            if threats:
                                t = max(threats, key=lambda x: x.get("atk", 0) + x.get("curr_hp", 0))
                                sc = 600 + t.get("atk", 0) * 10
                                if sc > best_score:
                                    best_score = sc; best_action = {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                                    
                        elif cid == "s_07": # 凍結
                            unfrozen_threats = [t for t in opp_board if t.get("frozen_turns", 0) == 0 and t.get("atk", 0) >= 3]
                            if unfrozen_threats:
                                t = max(unfrozen_threats, key=lambda x: x.get("atk", 0))
                                sc = 200 + t.get("atk", 0) * 10
                                if sc > best_score:
                                    best_score = sc; best_action = {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                                    
                        elif cid == "s_09": # 城壁
                            if my_board:
                                t = max(my_board, key=lambda x: x.get("atk", 0) + x.get("curr_hp", 0) + (10 if x.get("taunt") else 0))
                                if t.get("wall_turns", 0) == 0:
                                    sc = 250 + t.get("atk", 0) * 10
                                    if sc > best_score:
                                        best_score = sc; best_action = {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}

                # 【候補2】攻撃の実行
                if active_units:
                    targets = opp_taunts if opp_taunts else opp_board
                    
                    # 確定リーサルチェック（妨害がなければ顔面）
                    total_board_atk = sum(u.get("atk", 0) * u.get("attacks_left", 1) for u in active_units)
                    if not opp_taunts and total_board_atk >= opp_hp:
                        best_score = 1000000
                        best_action = {"action": "DECLARE_ATTACK", "attacker_id": active_units[0]["instance_id"], "target": {"type": "hero", "id": opp_id}}
                    else:
                        for u in active_units:
                            # 1. 盤面トレードの評価
                            for t in targets:
                                u_atk = max(0, u.get("atk", 0) - 1) if t.get("wall_turns", 0) > 0 else u.get("atk", 0)
                                if u_atk == 0 and not u.get("ranged"): continue
                                
                                # 小人の城壁殴り防止
                                if u.get("card_id") == "u_06" and t.get("wall_turns", 0) > 0: continue

                                kills_target = u_atk >= t.get("curr_hp", 0)
                                t_atk = 0 if u.get("ranged") else (max(0, t.get("atk", 0) - 1) if u.get("wall_turns", 0) > 0 else t.get("atk", 0))
                                i_survive = t_atk < u.get("curr_hp", 0)

                                sc = 0
                                if kills_target and i_survive: sc = 1000 + t.get("atk", 0) * 10
                                elif kills_target and not i_survive:
                                    if t.get("cost", 0) >= u.get("cost", 0) or t.get("taunt"): sc = 600
                                    else: sc = -1000 # 損な相打ちは回避
                                elif not kills_target and i_survive: sc = 300 # 削り
                                else: sc = -2000 # 犬死に
                                
                                if sc > best_score:
                                    best_score = sc
                                    best_action = {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                            
                            # 2. 顔面攻撃の評価（挑発がいなければ）
                            if not opp_taunts:
                                face_sc = 500 + u.get("atk", 0) * 10
                                if face_sc > best_score:
                                    best_score = face_sc
                                    best_action = {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "hero", "id": opp_id}}

                # --- STEP 3: 最善手が見つかれば実行、なければエンド ---
                if best_action and best_score > 0:
                    await process_action_func(session, BOT_USER_ID, best_action)
                    await asyncio.sleep(0.05)
                else:
                    break # もう有効な手がない

            # ループ抜けたらターンエンド
            if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"Super AI Fatal Error: {e}", exc_info=True)
        if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
