
import asyncio
import copy
import logging
import itertools
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
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================
        # 1. ドラフトフェーズ（先鋒・重装・巨兵・城壁・暗殺の完全制圧）
        # ==========================================
        if session.status == "DRAFT":
            opts = session.draft_options.get(BOT_USER_ID, [])
            if opts:
                my_deck = session.decks.get(BOT_USER_ID, [])
                my_card_ids = [c.get("id") if isinstance(c, dict) else c for c in my_deck]
                
                # 黄金比率目標: 先鋒2, 重装2, 巨兵1, 城壁1
                u01_cnt = my_card_ids.count("u_01")
                u02_cnt = my_card_ids.count("u_02")
                u04_cnt = my_card_ids.count("u_04")
                s09_cnt = my_card_ids.count("s_09")

                best_card, max_score = opts[0], -999999

                for cid in opts:
                    if cid == "u_07": # 奇術師は絶対に取らない
                        score = -999999
                    elif cid == "u_01" and u01_cnt < 2:
                        score = 50000 + (2 - u01_cnt) * 10000 # 先鋒兵超最優先
                    elif cid == "u_02" and u02_cnt < 2:
                        score = 40000 + (2 - u02_cnt) * 10000 # 重装兵優先
                    elif cid == "u_04" and u04_cnt < 1:
                        score = 30000 # 巨兵1枚確保
                    elif cid == "s_09" and s09_cnt < 1:
                        score = 25000 # 城壁1枚確保
                    elif cid == "s_05":
                        score = 20000 # 暗殺者
                    elif cid == "s_01":
                        score = 15000 # 雷撃
                    elif cid == "s_04":
                        score = 10000 # ドロー
                    elif cid == "u_03":
                        score = 8000  # 魔導士
                    else:
                        score = 1000

                    if score > max_score:
                        max_score = score
                        best_card = cid

                await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})
                await asyncio.sleep(0.01)

                if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                    await process_super_ai_turn(session, card_database, process_action_func)
            return

        # ==========================================
        # 2. バトルフェーズ
        # ==========================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            if not opp_id: return

            # カンニング情報
            opp_hand = session.hands.get(opp_id, [])
            opp_hand_ids = [c.get("id") for c in opp_hand]
            opp_max_mp = session.max_mp.get(opp_id, 1)
            # 相手が4マナ以上かつ手札に嵐がある場合のみ嵐警戒
            real_storm_threat = ("s_02" in opp_hand_ids) and (opp_max_mp >= 4)
            real_assassin_threat = ("s_05" in opp_hand_ids) and (opp_max_mp >= 6)

            action_loop_count = 0
            max_loop_limit = 25

            while session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID and action_loop_count < max_loop_limit:
                action_loop_count += 1

                my_mp = session.mp.get(BOT_USER_ID, 0)
                my_board = session.boards.get(BOT_USER_ID, [])
                opp_board = session.boards.get(opp_id, [])
                my_hand = session.hands.get(BOT_USER_ID, [])
                opp_hp = session.hp.get(opp_id, 20)

                playable_cards = [c for c in my_hand if c.get("cost", 99) <= my_mp]
                active_units = [u for u in my_board if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0]
                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

                # ----------------------------------------------------
                # [PHASE 1] ドロー呪文最優先
                # ----------------------------------------------------
                s04_cards = [c for c in playable_cards if c.get("id") == "s_04"]
                if s04_cards and len(my_hand) < 7:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s04_cards[0]["instance_id"], "target": None})
                    await asyncio.sleep(0.02); continue

                # ----------------------------------------------------
                # [PHASE 2] 手札の展開（ユニット＆必須スペル）
                # ※ 攻撃前に先鋒兵を出し切って戦闘に参加させる！
                # ----------------------------------------------------
                did_play = False
                
                # 1. 先鋒兵（疾走）を最優先で出す
                u01_cards = [c for c in playable_cards if c.get("id") == "u_01"]
                if u01_cards and len(my_board) < 7:
                    # 相手が嵐を撃てるターンかつ盤面が埋まっている時以外は絶対出す
                    if not (real_storm_threat and len(my_board) >= 3):
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": u01_cards[0]["instance_id"], "target": None})
                        await asyncio.sleep(0.02); continue

                # 2. 危険な敵への妨害・除去スペル
                s05_cards = [c for c in playable_cards if c.get("id") == "s_05"]
                if s05_cards and opp_board:
                    # ATK4以上またはHP5以上の脅威を即死
                    threats = [t for t in opp_board if t.get("atk", 0) >= 4 or t.get("curr_hp", 0) >= 5 or t.get("taunt")]
                    if threats:
                        target = max(threats, key=lambda x: x.get("atk", 0) + x.get("curr_hp", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s05_cards[0]["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                        await asyncio.sleep(0.02); continue

                s07_cards = [c for c in playable_cards if c.get("id") == "s_07"]
                if s07_cards and opp_board:
                    # 凍結していない最大打点を1マナで無力化
                    unfrozen = [t for t in opp_board if t.get("frozen_turns", 0) == 0 and t.get("atk", 0) >= 2]
                    if unfrozen:
                        target = max(unfrozen, key=lambda x: x.get("atk", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s07_cards[0]["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                        await asyncio.sleep(0.02); continue

                s01_cards = [c for c in playable_cards if c.get("id") == "s_01"]
                if s01_cards:
                    # HP3以下の敵を無傷除去
                    killable = [t for t in opp_board if t.get("curr_hp", 0) <= 3 and t.get("wall_turns", 0) == 0]
                    if killable:
                        target = max(killable, key=lambda x: x.get("atk", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_cards[0]["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                        await asyncio.sleep(0.02); continue

                # 3. 巨兵・重装兵・その他ユニットの着地
                playable_units = [c for c in playable_cards if c.get("type") == "unit" and c.get("id") != "u_07"]
                if playable_units and len(my_board) < 7:
                    u04_cards = [c for c in playable_units if c.get("id") == "u_04"]
                    u02_cards = [c for c in playable_units if c.get("id") == "u_02"]

                    if u04_cards and not real_assassin_threat: # 暗殺者警戒がなければ巨兵
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": u04_cards[0]["instance_id"], "target": None})
                        await asyncio.sleep(0.02); continue
                    elif u02_cards: # 重装兵で壁を立てる
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": u02_cards[0]["instance_id"], "target": None})
                        await asyncio.sleep(0.02); continue
                    else:
                        # 最大コスト順でマナを使い切る
                        best_u = max(playable_units, key=lambda x: x.get("cost", 0) * 10 + x.get("atk", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": best_u["instance_id"], "target": None})
                        await asyncio.sleep(0.02); continue

                # 4. 城壁を主力に貼る
                s09_cards = [c for c in playable_cards if c.get("id") == "s_09"]
                if s09_cards and my_board:
                    target = max(my_board, key=lambda x: x.get("atk", 0) + x.get("curr_hp", 0) + (5 if x.get("taunt") else 0))
                    if target.get("wall_turns", 0) == 0:
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s09_cards[0]["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                        await asyncio.sleep(0.02); continue

                # ----------------------------------------------------
                # [PHASE 3] 盤面攻撃（総攻撃＆有利トレード）
                # ----------------------------------------------------
                if active_units:
                    targets = opp_taunts if opp_taunts else opp_board
                    
                    # 確定リーサル（敵挑発なし＆総攻撃で削り切れる）
                    total_board_atk = sum(u.get("atk", 0) * u.get("attacks_left", 1) for u in active_units)
                    if not opp_taunts and total_board_atk >= opp_hp:
                        await process_action_func(session, BOT_USER_ID, {"action": "DECLARE_ATTACK", "attacker_id": active_units[0]["instance_id"], "target": {"type": "hero", "id": opp_id}})
                        await asyncio.sleep(0.02); continue

                    # 有利トレードの探索
                    best_attack = None
                    best_attack_score = -9999

                    for u in active_units:
                        for t in targets:
                            u_atk = max(0, u.get("atk", 0) - 1) if t.get("wall_turns", 0) > 0 else u.get("atk", 0)
                            if u_atk <= 0 and not u.get("ranged"): continue
                            if u.get("card_id") == "u_06" and t.get("wall_turns", 0) > 0: continue

                            kills_target = u_atk >= t.get("curr_hp", 0)
                            t_atk = 0 if u.get("ranged") else (max(0, t.get("atk", 0) - 1) if u.get("wall_turns", 0) > 0 else t.get("atk", 0))
                            i_survive = t_atk < u.get("curr_hp", 0)

                            score = 0
                            if kills_target and i_survive:
                                score = 1000 + t.get("atk", 0) * 10
                            elif kills_target and not i_survive:
                                if t.get("cost", 0) >= u.get("cost", 0) or t.get("taunt"):
                                    score = 500
                                else:
                                    score = -100
                            elif not kills_target and i_survive:
                                score = 200
                            else:
                                score = -500

                            if score > best_attack_score:
                                best_attack_score = score
                                best_attack = {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}

                    # 有利トレードがあれば殴る
                    if best_attack and best_attack_score > 0:
                        await process_action_func(session, BOT_USER_ID, best_attack)
                        await asyncio.sleep(0.02); continue
                    
                    # 敵の挑発がいなければ残ったユニットで顔面攻撃
                    if not opp_taunts:
                        await process_action_func(session, BOT_USER_ID, {"action": "DECLARE_ATTACK", "attacker_id": active_units[0]["instance_id"], "target": {"type": "hero", "id": opp_id}})
                        await asyncio.sleep(0.02); continue

                # ----------------------------------------------------
                # [PHASE 4] 余剰マナでの顔面スペル＆エンド
                # ----------------------------------------------------
                s01_cards = [c for c in playable_cards if c.get("id") == "s_01"]
                if s01_cards and not opp_taunts:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_cards[0]["instance_id"], "target": {"type": "hero", "id": opp_id}})
                    await asyncio.sleep(0.02); continue

                # やることがなくなったらターン終了
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
                break

            if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"Super AI Fatal Error: {e}", exc_info=True)
        if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
