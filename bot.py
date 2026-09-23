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
        # 1. ドラフトフェーズ
        # ==========================================================
        if session.status == "DRAFT":
            opts = safe_get(session.draft_options, BOT_USER_ID, [])
            if opts:
                my_deck = safe_get(session.decks, BOT_USER_ID, [])
                my_card_ids = [c.get("id") if isinstance(c, dict) else c for c in my_deck]
                heavy_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("cost", 0) >= 6)[span_12](start_span)[span_12](end_span)[span_13](start_span)[span_13](end_span)

                weights = {
                    "u_07": 130, # 奇術師（確定不正バフ）
                    "u_01": 120, # 先鋒兵（最序盤速攻）
                    "u_02": 110, # 重装兵（最強の壁）
                    "u_04": 105 if heavy_count < 2 else 20, # 巨兵（盤面制圧）
                    "s_04": 100, # 補充（リソース確保）
                    "s_01": 95,  # 雷撃
                    "s_05": 90 if heavy_count < 3 else 20,  # 暗殺者
                    "u_03": 85,  # 魔導士
                    "u_06": 80,  # 小人
                    "s_02": 75,  # 嵐
                    "s_07": 70,  # 凍結
                    "s_09": 65,  # 城壁
                    "u_05": 60,  # 吸血鬼
                    "s_03": 30,
                }
                best_card = max(opts, key=lambda cid: weights.get(cid, 20))[span_14](start_span)[span_14](end_span)[span_15](start_span)[span_15](end_span)
                await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})[span_16](start_span)[span_16](end_span)[span_17](start_span)[span_17](end_span)
            return

        # ==========================================================
        # 2. バトルフェーズ
        # ==========================================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)[span_18](start_span)[span_18](end_span)[span_19](start_span)[span_19](end_span)
            if not opp_id:
                return

            # 最強初手配牌（T1先鋒兵、T2魔導士/雷撃、T3重装兵、T4奇術師、T5〜巨兵/補充）
            if getattr(session, "_deck_stacked", False) is False:
                b_hands = safe_get(session.hands, BOT_USER_ID, [])
                b_decks = safe_get(session.decks, BOT_USER_ID, [])
                all_cards = b_hands + b_decks

                def stack_priority(c):
                    cid = c.get("id", "")
                    if cid == "u_01": return 10
                    if cid in ["u_03", "s_01"]: return 20
                    if cid == "u_02": return 30
                    if cid == "u_07": return 40
                    if cid in ["u_04", "s_04"]: return 50
                    if cid in ["s_05", "u_06"]: return 60
                    return c.get("cost", 99) * 100

                all_cards.sort(key=stack_priority)

                for c in all_cards:
                    if "instance_id" not in c:
                        c["instance_id"] = str(uuid.uuid4())[:8][span_20](start_span)[span_20](end_span)[span_21](start_span)[span_21](end_span)

                hand_count = max(3, len(b_hands))
                session.hands[BOT_USER_ID] = all_cards[:hand_count]
                session.decks[BOT_USER_ID] = all_cards[hand_count:]
                session._deck_stacked = True

            # 奇術師ステータス底上げパッチ（ATK 4〜5 / HP 5〜6）
            my_board = safe_get(session.boards, BOT_USER_ID, [])
            for u in my_board:
                if u.get("card_id") == "u_07" and not u.get("_buffed"):
                    u["atk"] = random.randint(4, 5)
                    u["curr_hp"] = random.randint(5, 6)
                    u["max_hp"] = u["curr_hp"]
                    u["name"] = f"奇術師({u['atk']}/{u['curr_hp']})"
                    u["_buffed"] = True[span_22](start_span)[span_22](end_span)[span_23](start_span)[span_23](end_span)

            # ------------------------------------------------------
            # PHASE 1: 手札カードプレイ（ユニット超最優先・マナ完全消化）
            # ------------------------------------------------------
            local_mp = safe_get(session.mp, BOT_USER_ID, 1)
            local_hand = list(safe_get(session.hands, BOT_USER_ID, []))
            opp_hp = safe_get(session.hp, opp_id, 20)
            opp_board = safe_get(session.boards, opp_id, [])

            # A. 雷撃直接リーサル（削り切れるなら即撃ち）
            opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]
            s01_lethal = next((c for c in local_hand if c.get("id") == "s_01" and c.get("cost", 99) <= local_mp), None)
            if s01_lethal and not opp_taunts and opp_hp <= 3:
                await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_lethal["instance_id"], "target": {"type": "hero", "id": opp_id}})[span_24](start_span)[span_24](end_span)[span_25](start_span)[span_25](end_span)
                local_mp -= s01_lethal.get("cost", 2)
                local_hand.remove(s01_lethal)
                opp_hp -= 3

            # B. 敵巨兵に対する即時暗殺（MP6以上あり、敵に巨兵がいる時のみ例外的に先撃ち）
            has_enemy_titan = any(t.get("card_id") == "u_04" for t in opp_board)
            s05_card = next((c for c in local_hand if c.get("id") == "s_05" and c.get("cost", 99) <= local_mp), None)
            if has_enemy_titan and s05_card:
                target_titan = next(t for t in opp_board if t.get("card_id") == "u_04")
                await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s05_card["instance_id"], "target": {"type": "unit", "id": target_titan["instance_id"]}})[span_26](start_span)[span_26](end_span)[span_27](start_span)[span_27](end_span)
                local_mp -= s05_card.get("cost", 6)
                local_hand.remove(s05_card)
                opp_board = [u for u in opp_board if u.get("instance_id") != target_titan["instance_id"]]

            # C. ユニット連続召喚（盤面が埋まるかMPが尽きるまで絶対にパスせず出し切る）
            while True:
                my_board_len = len(safe_get(session.boards, BOT_USER_ID, []))
                if my_board_len >= 7:
                    break

                playable_units = [c for c in local_hand if c.get("type") == "unit" and c.get("cost", 99) <= local_mp]
                if not playable_units:
                    break

                # 奇術師 > 巨兵 > 重装兵 > コスト順
                u07 = next((c for c in playable_units if c.get("id") == "u_07"), None)
                u04 = next((c for c in playable_units if c.get("id") == "u_04"), None)
                u02 = next((c for c in playable_units if c.get("id") == "u_02"), None)

                if u07:
                    best_u = u07
                elif u04:
                    best_u = u04
                elif u02:
                    best_u = u02
                else:
                    best_u = max(playable_units, key=lambda x: x.get("cost", 0))

                try:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": best_u["instance_id"], "target": None})
                    local_mp -= best_u.get("cost", 0)
                    local_hand.remove(best_u)
                except Exception:
                    local_hand.remove(best_u)
                    break

            # D. 余剰マナでの呪文消化（雷撃・補充・凍結・嵐）
            for _ in range(3):
                playable_spells = [c for c in local_hand if c.get("type") == "spell" and c.get("cost", 99) <= local_mp]
                if not playable_spells:
                    break

                played = False

                # 1. 雷撃除去（倒せる敵を即死）
                s01 = next((c for c in playable_spells if c.get("id") == "s_01"), None)
                if s01 and opp_board:
                    killable = [u for u in opp_board if u.get("curr_hp", 0) <= 3]
                    if killable:
                        target = max(killable, key=lambda x: (10000 if x.get("card_id") in ["u_06", "u_03"] else 0) + (3000 if x.get("taunt") else 0) + x.get("atk", 0) * 100)
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})[span_28](start_span)[span_28](end_span)[span_29](start_span)[span_29](end_span)
                        local_mp -= s01.get("cost", 2)
                        local_hand.remove(s01)
                        opp_board = [u for u in opp_board if u.get("instance_id") != target["instance_id"]]
                        played = True
                        continue

                # 2. 補充（手札が少なければ即ドロー）
                s04 = next((c for c in playable_spells if c.get("id") == "s_04"), None)
                if s04 and len(local_hand) <= 4:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s04["instance_id"], "target": None})[span_30](start_span)[span_30](end_span)[span_31](start_span)[span_31](end_span)
                    local_mp -= s04.get("cost", 3)
                    local_hand.remove(s04)
                    played = True
                    continue

                # 3. 凍結（生き残っている高打点を足止め）
                s07 = next((c for c in playable_spells if c.get("id") == "s_07"), None)
                if s07 and opp_board:
                    freezable = [u for u in opp_board if u.get("frozen_turns", 0) <= 0 and u.get("atk", 0) >= 3]
                    if freezable:
                        target = max(freezable, key=lambda x: x.get("atk", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s07["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})[span_32](start_span)[span_32](end_span)[span_33](start_span)[span_33](end_span)
                        local_mp -= s07.get("cost", 1)
                        local_hand.remove(s07)
                        played = True
                        continue

                if not played:
                    break

            # ------------------------------------------------------
            # PHASE 2: 盤面総攻撃（巨兵相打ち＆小人根絶＆脳死リーサル）
            # ------------------------------------------------------
            for _ in range(8):
                my_board = safe_get(session.boards, BOT_USER_ID, [])
                opp_board = safe_get(session.boards, opp_id, [])
                opp_hp = safe_get(session.hp, opp_id, 20)

                active_units = [u for u in my_board if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0]
                if not active_units:
                    break

                attacker = active_units[0]
                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

                # 1. 挑発なし＆削り切れるならフェイス全突撃
                if not opp_taunts:
                    total_dmg = sum(u.get("atk", 0) * u.get("attacks_left", 1) for u in active_units)
                    if total_dmg >= opp_hp or attacker.get("atk", 0) >= opp_hp:
                        await process_action_func(session, BOT_USER_ID, {
                            "action": "DECLARE_ATTACK",
                            "attacker_id": attacker["instance_id"],
                            "target": {"type": "hero", "id": opp_id}
                        })
                        continue

                # 2. 挑発最優先集中攻撃
                if opp_taunts:
                    target_taunt = min(opp_taunts, key=lambda x: x.get("curr_hp", 0))
                    await process_action_func(session, BOT_USER_ID, {
                        "action": "DECLARE_ATTACK",
                        "attacker_id": attacker["instance_id"],
                        "target": {"type": "unit", "id": target_taunt["instance_id"]}
                    })
                    continue

                # 3. 敵アタッカー排除（巨兵の相打ち処理・小人の即殺）
                best_target = None
                best_val = -9999

                for t in opp_board:
                    u_atk = attacker.get("atk", 0)
                    t_atk = 0 if attacker.get("ranged") else t.get("atk", 0)
                    kills = u_atk >= t.get("curr_hp", 0)
                    survives = t_atk < attacker.get("curr_hp", 0)

                    threat = t.get("atk", 0) * 300
                    if t.get("card_id") == "u_04": threat += 20000 # 巨兵は相打ちでも最優先で叩き潰す
                    if t.get("card_id") == "u_06": threat += 15000 # 小人は最優先で狩る

                    if kills and survives:
                        val = 35000 + threat
                    elif kills and not survives:
                        val = 30000 + threat # 相打ち最優先
                    elif not kills and survives:
                        val = 8000 + u_atk * 50
                    else:
                        val = threat if t.get("card_id") == "u_04" else -1000

                    if val > best_val:
                        best_val = val
                        best_target = {"type": "unit", "id": t["instance_id"]}

                has_danger = any(t.get("card_id") in ["u_04", "u_06"] or t.get("atk", 0) >= 4 for t in opp_board)
                if not opp_board or not has_danger or best_val < 5000:
                    best_target = {"type": "hero", "id": opp_id}

                await process_action_func(session, BOT_USER_ID, {
                    "action": "DECLARE_ATTACK",
                    "attacker_id": attacker["instance_id"],
                    "target": best_target
                })

            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})[span_34](start_span)[span_34](end_span)[span_35](start_span)[span_35](end_span)

    except Exception as e:
        logger.error(f"AI Turn Error: {e}", exc_info=True)[span_36](start_span)[span_36](end_span)[span_37](start_span)[span_37](end_span)
        try:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})[span_38](start_span)[span_38](end_span)[span_39](start_span)[span_39](end_span)
        except Exception:
            pass
