import asyncio
import copy
import logging
from typing import Dict, List, Any, Optional

logger = logging.getLogger("CardBot")

BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "村岡国王（影武者）"

# ========== card.py 連携用 ==========
HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}
# ====================================

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
        # 1. ドラフトフェーズ（動的制約・事故完全排除）
        # ==========================================================
        if session.status == "DRAFT":
            opts = session.draft_options.get(BOT_USER_ID, [])
            if opts:
                my_deck = session.decks.get(BOT_USER_ID, [])
                my_card_ids = [c.get("id") if isinstance(c, dict) else c for c in my_deck]

                # 現在のピック状況集計
                picked_units = [cid for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("type") == "unit"]
                picked_spells = [cid for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("type") == "spell"]
                heavy_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("cost", 0) >= 5)
                remaining_picks = 6 - len(my_card_ids)

                u01_cnt = picked_units.count("u_01")
                u02_cnt = picked_units.count("u_02")
                s09_cnt = picked_spells.count("s_09")

                best_card, max_score = opts[0], -9999999

                for cid in opts:
                    c = BOT_CARD_DB.get(cid, {})
                    c_type = c.get("type")
                    c_cost = c.get("cost", 0)

                    # [絶対制約1] 奇術師は問答無用で除外
                    if cid == "u_07":
                        score = -9999999
                    # [絶対制約2] ユニット最低4枚保証（残り枠をすべてユニットにしないと4枚に届かないならスペル排除）
                    elif c_type == "spell" and (len(picked_units) + remaining_picks <= 4):
                        score = -9999999
                    # [絶対制約3] スペル上限（最大2枚まで）
                    elif c_type == "spell" and len(picked_spells) >= 2:
                        score = -9999999
                    # [絶対制約4] 重量級（コスト5以上）はデッキに1枚まで
                    elif c_cost >= 5 and heavy_count >= 1:
                        score = -9999999
                    # 黄金比率に基づく通常スコアリング
                    else:
                        if cid == "u_01" and u01_cnt < 2:
                            score = 50000 + (2 - u01_cnt) * 10000 # 先鋒兵
                        elif cid == "u_02" and u02_cnt < 2:
                            score = 40000 + (2 - u02_cnt) * 10000 # 重装兵
                        elif cid == "u_04":
                            score = 35000 # 巨兵
                        elif cid == "s_09" and s09_cnt < 1:
                            score = 30000 # 城壁
                        elif cid == "s_05":
                            score = 25000 # 暗殺者
                        elif cid == "s_01":
                            score = 20000 # 雷撃
                        elif cid == "s_04":
                            score = 15000 # 補充
                        elif cid == "u_03":
                            score = 10000 # 魔導士
                        else:
                            score = 1000

                    if score > max_score:
                        max_score = score
                        best_card = cid

                await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})
                await asyncio.sleep(0.01)

                # 連続ピック処理
                if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                    await process_super_ai_turn(session, card_database, process_action_func)
            return

        # ==========================================================
        # 2. バトルフェーズ（定型強要・ダメージ最大化）
        # ==========================================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            if not opp_id: return

            # カンニングによる環境認識
            opp_hand = session.hands.get(opp_id, [])
            opp_hand_ids = [c.get("id") for c in opp_hand]
            opp_max_mp = session.max_mp.get(opp_id, 1)

            real_storm_threat = ("s_02" in opp_hand_ids) and (opp_max_mp >= 4)
            real_assassin_threat = ("s_05" in opp_hand_ids) and (opp_max_mp >= 6)

            action_loop_count = 0
            max_loop_limit = 30

            while session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID and action_loop_count < max_loop_limit:
                action_loop_count += 1

                # 正確なMPを取得（初期化漏れ対策）[span_4](start_span)[span_4](end_span)
                my_mp = session.mp.get(BOT_USER_ID)
                if my_mp is None:
                    my_mp = session.max_mp.get(BOT_USER_ID, 1)
                    session.mp[BOT_USER_ID] = my_mp

                my_board = session.boards.get(BOT_USER_ID, [])
                opp_board = session.boards.get(opp_id, [])
                my_hand = session.hands.get(BOT_USER_ID, [])
                opp_hp = session.hp.get(opp_id, 20)

                playable_cards = [c for c in my_hand if c.get("cost", 99) <= my_mp]
                active_units = [u for u in my_board if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0]
                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

                # --------------------------------------------------
                # [フェーズ1] ドロー先行消化（手札確定）
                # --------------------------------------------------
                s04_cards = [c for c in playable_cards if c.get("id") == "s_04"]
                if s04_cards and len(my_hand) < 7:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s04_cards[0]["instance_id"], "target": None})
                    await asyncio.sleep(0.01); continue

                # --------------------------------------------------
                # [フェーズ2] ユニット・スペルの展開（攻撃前配置）
                # --------------------------------------------------
                did_play = False
                best_play = None
                best_play_score = -999999

                for card in playable_cards:
                    cid = card["id"]
                    
                    if card["type"] == "unit" and len(my_board) < 7:
                        # ユニット定型評価
                        if cid == "u_01": # 先鋒兵：最優先即効2ダメージ
                            score = 2500
                        elif cid == "u_02": # 重装兵：絶対要塞
                            score = 2200
                        elif cid == "u_04": # 巨兵：フィニッシャー
                            score = 1000 if real_assassin_threat else 3000
                        else:
                            score = card["cost"] * 100 + card.get("atk", 0) * 50 + card.get("hp", 0) * 20
                        
                        # 嵐警戒時の展開抑制（ただし先鋒兵は突撃させる）
                        if real_storm_threat and card.get("hp", 0) <= 2 and cid != "u_01" and len(my_board) >= 2:
                            score -= 2000

                        if score > best_play_score:
                            best_play_score = score
                            best_play = {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": None}
                    
                    elif card["type"] == "spell":
                        # 暗殺者（挑発・大型除去）
                        if cid == "s_05" and opp_board:
                            threats = [t for t in opp_board if t.get("atk", 0) >= 4 or t.get("curr_hp", 0) >= 5 or t.get("taunt")]
                            if threats:
                                target = max(threats, key=lambda x: x.get("atk", 0) + x.get("curr_hp", 0))
                                score = 2400 + target.get("atk", 0) * 100 + target.get("curr_hp", 0) * 100
                                if score > best_play_score:
                                    best_play_score = score
                                    best_play = {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}}
                        # 雷撃（HP3以下の除去、挑発の突破）
                        elif cid == "s_01" and opp_board:
                            for t in opp_board:
                                dmg = 2 if t.get("wall_turns", 0) > 0 else 3
                                if dmg >= t.get("curr_hp", 0):
                                    score = 1800 + t.get("atk", 0) * 100 + (1000 if t.get("taunt") else 0)
                                    if score > best_play_score:
                                        best_play_score = score
                                        best_play = {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                        # 凍結
                        elif cid == "s_07" and opp_board:
                            unfrozen = [t for t in opp_board if t.get("frozen_turns", 0) == 0 and t.get("atk", 0) >= 2]
                            if unfrozen:
                                target = max(unfrozen, key=lambda x: x.get("atk", 0))
                                score = 1500 + target.get("atk", 0) * 100
                                if score > best_play_score:
                                    best_play_score = score
                                    best_play = {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}}
                        # 嵐
                        elif cid == "s_02" and len(opp_board) >= 2:
                            score = 1700 + len(opp_board) * 200
                            if score > best_play_score:
                                best_play_score = score
                                best_play = {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": None}
                        # 城壁
                        elif cid == "s_09" and my_board:
                            target = max(my_board, key=lambda x: x.get("atk", 0) + x.get("curr_hp", 0) + (10 if x.get("taunt") else 0))
                            if target.get("wall_turns", 0) == 0:
                                score = 2000 + target.get("atk", 0) * 100
                                if score > best_play_score:
                                    best_play_score = score
                                    best_play = {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}}

                if best_play and best_play_score > 0:
                    await process_action_func(session, BOT_USER_ID, best_play)
                    await asyncio.sleep(0.01)
                    continue

                # --------------------------------------------------
                # [フェーズ3] 盤面攻撃（総攻撃・有利トレード）
                # --------------------------------------------------
                if active_units:
                    targets = opp_taunts if opp_taunts else opp_board
                    
                    # 確定リーサル判定
                    total_board_atk = sum(u.get("atk", 0) * u.get("attacks_left", 1) for u in active_units)
                    if not opp_taunts and total_board_atk >= opp_hp:
                        await process_action_func(session, BOT_USER_ID, {"action": "DECLARE_ATTACK", "attacker_id": active_units[0]["instance_id"], "target": {"type": "hero", "id": opp_id}})
                        await asyncio.sleep(0.01); continue

                    best_attack = None
                    best_attack_score = -999999

                    for u in active_units:
                        # 【絶対制約】重装兵は盾として温存（ヒーローへの顔面攻撃のみ許可）[span_5](start_span)[span_5](end_span)
                        if not opp_taunts and u.get("card_id") == "u_02":
                            score = 1400 + u.get("atk", 0) * 100
                            if score > best_attack_score:
                                best_attack_score = score
                                best_attack = {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "hero", "id": opp_id}}
                            continue

                        # 通常ユニットのトレード評価
                        for t in targets:
                            # 敵ユニット攻撃禁止の重装兵はスキップ
                            if u.get("card_id") == "u_02": continue
                            # 小人が城壁持ちを殴る無駄手を排除
                            if u.get("card_id") == "u_06" and t.get("wall_turns", 0) > 0: continue

                            u_atk = max(0, u.get("atk", 0) - 1) if t.get("wall_turns", 0) > 0 else u.get("atk", 0)
                            if u_atk <= 0 and not u.get("ranged"): continue

                            kills_target = u_atk >= t.get("curr_hp", 0)
                            t_atk = 0 if u.get("ranged") else (max(0, t.get("atk", 0) - 1) if u.get("wall_turns", 0) > 0 else t.get("atk", 0))
                            i_survive = t_atk < u.get("curr_hp", 0)

                            score = 0
                            if kills_target and i_survive:
                                score = 5000 + t.get("atk", 0) * 150 + (1000 if t.get("taunt") else 0)
                            elif kills_target and not i_survive:
                                score = 2000 if t.get("taunt") or t.get("atk", 0) >= u.get("atk", 0) else -1000
                            elif not kills_target and i_survive:
                                score = 1000
                            else:
                                score = -3000

                            if score > best_attack_score:
                                best_attack_score = score
                                best_attack = {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                        
                        # 顔面攻撃評価（挑発がいない場合）
                        if not opp_taunts:
                            face_score = 1400 + u.get("atk", 0) * 100
                            if face_score > best_attack_score:
                                best_attack_score = face_score
                                best_attack = {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "hero", "id": opp_id}}

                    if best_attack and best_attack_score > 0:
                        await process_action_func(session, BOT_USER_ID, best_attack)
                        await asyncio.sleep(0.01)
                        continue

                # --------------------------------------------------
                # [フェーズ4] 余剰マナでの顔面雷撃
                # --------------------------------------------------
                s01_cards = [c for c in playable_cards if c.get("id") == "s_01"]
                if s01_cards and not opp_taunts:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_cards[0]["instance_id"], "target": {"type": "hero", "id": opp_id}})
                    await asyncio.sleep(0.01)
                    continue

                # どのアクションも実行できなければターンエンド
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
                break

            if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"Super AI Fatal Error: {e}", exc_info=True)
        if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
