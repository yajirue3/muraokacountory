import asyncio
import copy
import logging
import random
import uuid
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

def safe_get(container: Any, key: str, default: Any = None) -> Any:
    if isinstance(container, dict):
        return container.get(key, default)
    return getattr(container, key, default)

async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================================
        # 1. ドラフトフェーズ（最速・単発レスポンス）
        # ==========================================================
        if session.status == "DRAFT":
            opts = safe_get(session.draft_options, BOT_USER_ID, [])
            if opts:
                my_deck = safe_get(session.decks, BOT_USER_ID, [])
                my_card_ids = [c.get("id") if isinstance(c, dict) else c for c in my_deck]
                heavy_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("cost", 0) >= 6)[span_6](start_span)[span_6](end_span)[span_7](start_span)[span_7](end_span)

                # 最強デッキ構築重み
                weights = {
                    "u_07": 120, # 奇術師（不正ステータス確定枠）
                    "u_01": 110, # 先鋒兵（序盤マスト）
                    "s_05": 105 if heavy_count < 2 else 20, # 暗殺者
                    "s_04": 100, # 補充（息切れ防止・最優先確保）
                    "u_02": 95,  # 重装兵
                    "s_01": 90,  # 雷撃
                    "u_03": 85,  # 魔導士
                    "u_04": 80 if heavy_count < 2 else 10, # 巨兵
                    "u_06": 78,  # 小人
                    "s_02": 75,  # 嵐
                    "s_07": 70,  # 凍結
                    "s_09": 65,  # 城壁
                    "u_05": 60,  # 吸血鬼
                    "s_03": 30,
                }
                best_card = max(opts, key=lambda cid: weights.get(cid, 20))[span_8](start_span)[span_8](end_span)[span_9](start_span)[span_9](end_span)
                await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})[span_10](start_span)[span_10](end_span)[span_11](start_span)[span_11](end_span)
            return

        # ==========================================================
        # 2. バトルフェーズ
        # ==========================================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)[span_12](start_span)[span_12](end_span)[span_13](start_span)[span_13](end_span)
            if not opp_id:
                return

            # --- 最強積み込み（初手：先鋒兵・魔導士/雷撃・重装兵・奇術師） ---
            if getattr(session, "_deck_stacked", False) is False:
                b_hands = safe_get(session.hands, BOT_USER_ID, [])
                b_decks = safe_get(session.decks, BOT_USER_ID, [])
                all_cards = b_hands + b_decks

                def stack_priority(c):
                    cid = c.get("id", "")
                    if cid == "u_01": return 10 # T1
                    if cid in ["u_03", "s_01"]: return 20 # T2
                    if cid == "u_02": return 30 # T3
                    if cid == "u_07": return 40 # T4着地
                    if cid in ["s_04", "s_05"]: return 50
                    if cid in ["u_04", "u_06"]: return 60
                    return c.get("cost", 99) * 100

                all_cards.sort(key=stack_priority)

                for c in all_cards:
                    if "instance_id" not in c:
                        c["instance_id"] = str(uuid.uuid4())[:8][span_14](start_span)[span_14](end_span)[span_15](start_span)[span_15](end_span)

                hand_count = max(3, len(b_hands))
                session.hands[BOT_USER_ID] = all_cards[:hand_count]
                session.decks[BOT_USER_ID] = all_cards[hand_count:]
                session._deck_stacked = True

            # ------------------------------------------------------
            # 盤面ステータス即時補正（奇術師バフを確定ATK4〜5/HP5〜6へ）
            # ------------------------------------------------------
            my_board = safe_get(session.boards, BOT_USER_ID, [])
            for u in my_board:
                if u.get("card_id") == "u_07" and not u.get("_buffed"):
                    u["atk"] = random.randint(4, 5) # 最低打点を4に底上げ
                    u["curr_hp"] = random.randint(5, 6) # 耐久も強化
                    u["max_hp"] = u["curr_hp"]
                    u["name"] = f"奇術師({u['atk']}/{u['curr_hp']})"
                    u["_buffed"] = True[span_16](start_span)[span_16](end_span)[span_17](start_span)[span_17](end_span)

            # ------------------------------------------------------
            # 思考ステップ1：手札プレイ（貪欲一括実行）
            # ------------------------------------------------------
            for _ in range(5): # 1ターン最大5アクションに絞り高速化
                my_mp = safe_get(session.mp, BOT_USER_ID, 1)
                opp_hp = safe_get(session.hp, opp_id, 20)
                my_hand = safe_get(session.hands, BOT_USER_ID, [])
                my_board = safe_get(session.boards, BOT_USER_ID, [])
                opp_board = safe_get(session.boards, opp_id, [])

                playable = [c for c in my_hand if c.get("cost", 99) <= my_mp]
                if not playable:
                    break

                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

                # A. 直接雷撃リーサル
                s01_kill = next((c for c in playable if c.get("id") == "s_01"), None)
                if s01_kill and not opp_taunts and opp_hp <= 3:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_kill["instance_id"], "target": {"type": "hero", "id": opp_id}})[span_18](start_span)[span_18](end_span)[span_19](start_span)[span_19](end_span)
                    continue

                action_taken = False

                # B. 暗殺者：敵の巨兵(u_04)や挑発・危険ユニット即死
                s05 = next((c for c in playable if c.get("id") == "s_05"), None)
                if s05 and opp_board:
                    target = max(opp_board, key=lambda x: (10000 if x.get("card_id") == "u_04" else 0) + (5000 if x.get("taunt") else 0) + x.get("atk", 0) * 100)
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s05["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})[span_20](start_span)[span_20](end_span)[span_21](start_span)[span_21](end_span)
                    action_taken = True; continue

                # C. 嵐：敵盤面が2体以上
                s02 = next((c for c in playable if c.get("id") == "s_02"), None)
                if s02 and (len(opp_board) >= 2 or any(t.get("curr_hp", 0) <= 2 for t in opp_board)):
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s02["instance_id"], "target": None})[span_22](start_span)[span_22](end_span)[span_23](start_span)[span_23](end_span)
                    action_taken = True; continue

                # D. 雷撃除去：倒せる敵を即キル
                if s01_kill and opp_board:
                    killable = [u for u in opp_board if u.get("curr_hp", 0) <= 3]
                    if killable:
                        target = max(killable, key=lambda x: (2000 if x.get("taunt") else 0) + (1500 if x.get("card_id") == "u_06" else 0) + x.get("atk", 0) * 50)
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_kill["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})[span_24](start_span)[span_24](end_span)[span_25](start_span)[span_25](end_span)
                        action_taken = True; continue

                # E. 補充：手札が5枚以下なら即ドロー
                s04 = next((c for c in playable if c.get("id") == "s_04"), None)
                if s04 and len(my_hand) <= 5:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s04["instance_id"], "target": None})[span_26](start_span)[span_26](end_span)[span_27](start_span)[span_27](end_span)
                    action_taken = True; continue

                # F. 凍結：倒せない敵巨兵や高打点を足止め
                s07 = next((c for c in playable if c.get("id") == "s_07"), None)
                if s07 and opp_board:
                    freezable = [u for u in opp_board if u.get("frozen_turns", 0) <= 0 and u.get("atk", 0) >= 3]
                    if freezable:
                        target = max(freezable, key=lambda x: x.get("atk", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s07["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                        action_taken = True; continue

                # G. ユニット展開：マナ最大化
                playable_units = [c for c in playable if c.get("type") == "unit"]
                if playable_units and len(my_board) < 7:
                    # 奇術師最優先
                    u07 = next((c for c in playable_units if c.get("id") == "u_07"), None)
                    if u07:
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": u07["instance_id"], "target": None})[span_28](start_span)[span_28](end_span)[span_29](start_span)[span_29](end_span)
                        action_taken = True; continue

                    # 最大コストのユニットを召喚
                    best_u = max(playable_units, key=lambda x: x.get("cost", 0))
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": best_u["instance_id"], "target": None})
                    action_taken = True; continue

                if not action_taken:
                    break

            # ------------------------------------------------------
            # 思考ステップ2：盤面総攻撃（ノータイム殲滅）
            # ------------------------------------------------------
            for _ in range(8): # 自軍最大盤面数＋αで攻撃を完結
                my_board = safe_get(session.boards, BOT_USER_ID, [])
                opp_board = safe_get(session.boards, opp_id, [])
                opp_hp = safe_get(session.hp, opp_id, 20)

                active_units = [u for u in my_board if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0]
                if not active_units:
                    break

                attacker = active_units[0]
                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

                # 1. 挑発なし＆削り切れるなら脳死フェイスアタック
                if not opp_taunts:
                    total_dmg = sum(u.get("atk", 0) * u.get("attacks_left", 1) for u in active_units)
                    if total_dmg >= opp_hp or attacker.get("atk", 0) >= opp_hp:
                        await process_action_func(session, BOT_USER_ID, {
                            "action": "DECLARE_ATTACK",
                            "attacker_id": attacker["instance_id"],
                            "target": {"type": "hero", "id": opp_id}
                        })
                        continue

                # 2. 挑発がいる場合は集中突破
                if opp_taunts:
                    target_taunt = min(opp_taunts, key=lambda x: x.get("curr_hp", 0))
                    await process_action_func(session, BOT_USER_ID, {
                        "action": "DECLARE_ATTACK",
                        "attacker_id": attacker["instance_id"],
                        "target": {"type": "unit", "id": target_taunt["instance_id"]}
                    })
                    continue

                # 3. 有利トレード または 敵巨兵・小人の排除
                best_target = None
                best_val = -9999

                for t in opp_board:
                    u_atk = attacker.get("atk", 0)
                    t_atk = 0 if attacker.get("ranged") else t.get("atk", 0)
                    kills = u_atk >= t.get("curr_hp", 0)
                    survives = t_atk < attacker.get("curr_hp", 0)

                    val = 0
                    threat = (5000 if t.get("card_id") == "u_04" else 0) + (3000 if t.get("card_id") == "u_06" else 0) + t.get("atk", 0) * 200

                    if kills and survives:
                        val = 10000 + threat
                    elif kills and not survives:
                        val = 6000 + threat # 相打ち
                    elif not kills and survives:
                        val = 3000 + u_atk * 50
                    else:
                        val = -1000

                    if val > best_val:
                        best_val = val
                        best_target = {"type": "unit", "id": t["instance_id"]}

                # 殴るべき危険な敵がいなければ即座にフェイス
                has_danger = any(t.get("card_id") in ["u_04", "u_06"] or t.get("atk", 0) >= 4 for t in opp_board)
                if not opp_board or not has_danger or best_val < 3000:
                    best_target = {"type": "hero", "id": opp_id}

                await process_action_func(session, BOT_USER_ID, {
                    "action": "DECLARE_ATTACK",
                    "attacker_id": attacker["instance_id"],
                    "target": best_target
                })

            # ターン終了
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})[span_30](start_span)[span_30](end_span)[span_31](start_span)[span_31](end_span)

    except Exception as e:
        logger.error(f"AI Turn Error: {e}", exc_info=True)[span_32](start_span)[span_32](end_span)[span_33](start_span)[span_33](end_span)
        try:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})[span_34](start_span)[span_34](end_span)[span_35](start_span)[span_35](end_span)
        except Exception:
            pass
