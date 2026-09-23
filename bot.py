import asyncio
import copy
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
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================================
        # 1. ドラフトフェーズ：盤面を作るための現実的ピック
        # ==========================================================
        if session.status == "DRAFT":
            opts = session.draft_options.get(BOT_USER_ID, [])
            if opts:
                my_deck = session.decks.get(BOT_USER_ID, [])
                my_card_ids = [c.get("id") if isinstance(c, dict) else c for c in my_deck]

                picked_units = [cid for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("type") == "unit"]
                picked_spells = [cid for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("type") == "spell"]
                heavy_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("cost", 0) >= 5)
                remaining_picks = 6 - len(my_card_ids)

                best_card, max_score = opts[0], -9999999

                for cid in opts:
                    c = BOT_CARD_DB.get(cid, {})
                    c_type = c.get("type")
                    c_cost = c.get("cost", 0)

                    # 奇術師は論外
                    if cid == "u_07":
                        score = -9999999
                    # ユニット4枚ノルマ
                    elif c_type == "spell" and (len(picked_units) + remaining_picks <= 4):
                        score = -9999999
                    # スペルは2枚まで
                    elif c_type == "spell" and len(picked_spells) >= 2:
                        score = -9999999
                    # 重いカードは1枚まで
                    elif c_cost >= 5 and heavy_count >= 1:
                        score = -9999999
                    else:
                        # 基礎点：スタッツとコストの比率
                        score = (c.get("atk", 0) * 200) + (c.get("hp", 0) * 150) - (c_cost * 100)
                        if cid == "u_01": score += 30000  # 1ターン目から殴れる先鋒兵
                        elif cid == "u_02": score += 25000 # 盤面を支える重装兵
                        elif cid == "u_04": score += 20000 # 巨兵
                        elif cid == "s_09": score += 18000 # 城壁
                        elif cid == "s_05": score += 15000 # 暗殺者
                        elif cid == "s_01": score += 12000 # 雷撃
                        elif cid == "s_04": score += 10000 # 補充
                        elif cid == "u_03": score += 8000  # 魔導士

                    if score > max_score:
                        max_score = score
                        best_card = cid

                await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})
                await asyncio.sleep(0.01)

                if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                    await process_super_ai_turn(session, card_database, process_action_func)
            return

        # ==========================================================
        # 2. バトルフェーズ：毎ターンの最大効率追求
        # ==========================================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            if not opp_id: return

            action_loop_count = 0
            max_loop_limit = 25

            while session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID and action_loop_count < max_loop_limit:
                action_loop_count += 1

                # 厳密なMP取得
                my_mp = session.mp.get(BOT_USER_ID)
                if my_mp is None:
                    my_mp = session.max_mp.get(BOT_USER_ID, 1)
                    session.mp[BOT_USER_ID] = my_mp

                my_board = session.boards.get(BOT_USER_ID, [])
                opp_board = session.boards.get(opp_id, [])
                my_hand = session.hands.get(BOT_USER_ID, [])
                opp_hp = session.hp.get(opp_id, 20)

                playable_cards = [c for c in my_hand if c.get("cost", 99) <= my_mp and c.get("id") != "u_07"]
                active_units = [u for u in my_board if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0]
                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

                # --- 1. ドロー先行（不純物の除去） ---
                s04_cards = [c for c in playable_cards if c.get("id") == "s_04"]
                if s04_cards and len(my_hand) < 7:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s04_cards[0]["instance_id"], "target": None})
                    await asyncio.sleep(0.01); continue

                # --- 2. 確定リーサル（1手で終わるなら迷わず終わらせる） ---
                board_dmg = sum(u.get("atk", 0) * u.get("attacks_left", 1) for u in active_units)
                spell_dmg = sum(3 for c in playable_cards if c.get("id") == "s_01")
                haste_dmg = sum(2 for c in playable_cards if c.get("id") == "u_01")

                if not opp_taunts and (board_dmg + spell_dmg + haste_dmg >= opp_hp):
                    # 雷撃で即死
                    s01 = next((c for c in playable_cards if c.get("id") == "s_01"), None)
                    if s01:
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01["instance_id"], "target": {"type": "hero", "id": opp_id}})
                        await asyncio.sleep(0.01); continue
                    # 疾走を出して即殴る
                    u01 = next((c for c in playable_cards if c.get("id") == "u_01"), None)
                    if u01 and len(my_board) < 7:
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": u01["instance_id"], "target": None})
                        await asyncio.sleep(0.01); continue
                    # 盤面ユニットで殴る
                    if active_units:
                        await process_action_func(session, BOT_USER_ID, {"action": "DECLARE_ATTACK", "attacker_id": active_units[0]["instance_id"], "target": {"type": "hero", "id": opp_id}})
                        await asyncio.sleep(0.01); continue

                # --- 3. スペルによる障害排除（攻撃前の露払い） ---
                # 暗殺者：相手の最高戦力（特に挑発や巨兵）を即消す
                s05 = next((c for c in playable_cards if c.get("id") == "s_05"), None)
                if s05 and opp_board:
                    target = max(opp_board, key=lambda x: (1000 if x.get("taunt") else 0) + x.get("atk", 0) * 100 + x.get("curr_hp", 0))
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s05["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                    await asyncio.sleep(0.01); continue

                # 嵐：敵が2体以上なら一掃
                s02 = next((c for c in playable_cards if c.get("id") == "s_02"), None)
                if s02 and len(opp_board) >= 2:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s02["instance_id"], "target": None})
                    await asyncio.sleep(0.01); continue

                # 雷撃：HP3以下を盤面を傷つけずに消す
                s01 = next((c for c in playable_cards if c.get("id") == "s_01"), None)
                if s01 and opp_board:
                    killable = [t for t in opp_board if t.get("curr_hp", 0) <= (2 if t.get("wall_turns", 0) > 0 else 3)]
                    if killable:
                        target = max(killable, key=lambda x: (500 if x.get("taunt") else 0) + x.get("atk", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                        await asyncio.sleep(0.01); continue

                # 凍結：止められる最大打点を止める
                s07 = next((c for c in playable_cards if c.get("id") == "s_07"), None)
                if s07 and opp_board:
                    unfrozen = [t for t in opp_board if t.get("frozen_turns", 0) == 0 and t.get("atk", 0) >= 3]
                    if unfrozen:
                        target = max(unfrozen, key=lambda x: x.get("atk", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s07["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                        await asyncio.sleep(0.01); continue

                # --- 4. ユニット展開（マナを余らせず盤面を埋める） ---
                playable_units = [c for c in playable_cards if c.get("type") == "unit"]
                if playable_units and len(my_board) < 7:
                    # 優先度：先鋒兵(即打点) > 重装兵(盤面保護) > 巨兵 > 最大コスト
                    u01 = next((c for c in playable_units if c.get("id") == "u_01"), None)
                    u02 = next((c for c in playable_units if c.get("id") == "u_02"), None)
                    u04 = next((c for c in playable_units if c.get("id") == "u_04"), None)

                    target_unit = u01 if u01 else (u02 if u02 else (u04 if u04 else max(playable_units, key=lambda x: x.get("cost", 0))))
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": target_unit["instance_id"], "target": None})
                    await asyncio.sleep(0.01); continue

                # --- 5. 城壁の付与（盤面の最強ユニットを要塞化） ---
                s09 = next((c for c in playable_cards if c.get("id") == "s_09"), None)
                if s09 and my_board:
                    unwalled = [u for u in my_board if u.get("wall_turns", 0) == 0]
                    if unwalled:
                        target = max(unwalled, key=lambda x: (1000 if x.get("taunt") else 0) + x.get("atk", 0) * 10 + x.get("curr_hp", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s09["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                        await asyncio.sleep(0.01); continue

                # --- 6. 盤面攻撃（実効ダメージと盤面掃除の最大化） ---
                if active_units:
                    targets = opp_taunts if opp_taunts else opp_board

                    # 挑発がいないなら全員で顔面を削る（重装兵も無傷で殴れる）
                    if not opp_taunts:
                        await process_action_func(session, BOT_USER_ID, {"action": "DECLARE_ATTACK", "attacker_id": active_units[0]["instance_id"], "target": {"type": "hero", "id": opp_id}})
                        await asyncio.sleep(0.01); continue

                    # 挑発がいる場合：重装兵以外でトレードを計算
                    best_attack = None
                    best_val = -9999

                    for u in active_units:
                        # 重装兵は盾なのでユニットに突撃させない
                        if u.get("card_id") == "u_02":
                            continue

                        for t in targets:
                            # 城壁持ちに小人で殴る無駄手を排除
                            if u.get("card_id") == "u_06" and t.get("wall_turns", 0) > 0:
                                continue

                            u_atk = max(0, u.get("atk", 0) - 1) if t.get("wall_turns", 0) > 0 else u.get("atk", 0)
                            if u_atk <= 0 and not u.get("ranged"):
                                continue

                            kills = u_atk >= t.get("curr_hp", 0)
                            t_atk = 0 if u.get("ranged") else (max(0, t.get("atk", 0) - 1) if u.get("wall_turns", 0) > 0 else t.get("atk", 0))
                            survives = t_atk < u.get("curr_hp", 0)

                            # 愚直な価値計算
                            val = 0
                            if kills and survives:
                                val = 10000 + t.get("atk", 0) * 100 # 一方的撃破
                            elif kills and not survives:
                                val = 5000 + (t.get("cost", 0) - u.get("cost", 0)) * 500 # 相打ち
                            elif not kills and survives:
                                val = 1000 + u_atk * 50 # 削り
                            else:
                                val = -10000 # 無駄死に

                            if val > best_val:
                                best_val = val
                                best_attack = {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}

                    if best_attack and best_val > 0:
                        await process_action_func(session, BOT_USER_ID, best_attack)
                        await asyncio.sleep(0.01); continue

                # --- 7. 余剰マナの顔面焼き ---
                s01 = next((c for c in playable_cards if c.get("id") == "s_01"), None)
                if s01 and not opp_taunts:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01["instance_id"], "target": {"type": "hero", "id": opp_id}})
                    await asyncio.sleep(0.01); continue

                # できることが尽きたら終了
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
                break

            if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"AI Turn Error: {e}", exc_info=True)
        if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
