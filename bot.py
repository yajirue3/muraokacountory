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

def enforce_top_deck(session: Any, wanted_card_ids: List[str]):
    deck = safe_get(session.decks, BOT_USER_ID, [])
    if not deck:
        return
    for i, target_id in enumerate(wanted_card_ids):
        idx = -(i + 1)
        if abs(idx) <= len(deck):
            ref = BOT_CARD_DB.get(target_id)
            if ref:
                deck[idx]["id"] = ref["id"]
                deck[idx]["name"] = ref["name"]
                deck[idx]["cost"] = ref["cost"]
                deck[idx]["type"] = ref["type"]
                for k in ["atk", "hp", "haste", "taunt", "lifesteal", "max_attacks", "ranged", "random_stat", "effect", "val", "need_target"]:
                    if k in ref:
                        deck[idx][k] = ref[k]
                    elif k in deck[idx]:
                        del deck[idx][k]

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
                heavy_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("cost", 0) >= 6)

                weights = {
                    "u_07": 130, "u_01": 125, "u_02": 110,
                    "s_02": 115, "u_04": 105 if heavy_count < 2 else 20,
                    "s_04": 100, "s_01": 95, "s_05": 90 if heavy_count < 3 else 20,
                    "u_03": 85, "u_06": 80, "s_07": 70, "s_09": 65, "u_05": 60, "s_03": 30,
                }

                if not any(weights.get(cid, 0) >= 110 for cid in opts):
                    inject_cands = ["u_07", "u_01", "s_02", "u_02", "s_04"]
                    random.shuffle(inject_cands)
                    opts[0] = inject_cands[0]

                best_card = max(opts, key=lambda cid: weights.get(cid, 20))
                await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})
            return

        # ==========================================================
        # 2. バトルフェーズ
        # ==========================================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            if not opp_id:
                return

            # --- 最適配牌（T1 先鋒兵確定） ---
            if getattr(session, "_deck_stacked", False) is False:
                all_cards = safe_get(session.hands, BOT_USER_ID, []) + safe_get(session.decks, BOT_USER_ID, [])

                u01_cards = [c for c in all_cards if c.get("id") == "u_01"]
                u02_cards = [c for c in all_cards if c.get("id") == "u_02"]
                u07_cards = [c for c in all_cards if c.get("id") == "u_07"]
                other_cards = [c for c in all_cards if c not in u01_cards and c not in u02_cards and c not in u07_cards]

                initial_hand = []
                if u01_cards: initial_hand.append(u01_cards.pop(0))
                if u02_cards: initial_hand.append(u02_cards.pop(0))
                elif u07_cards: initial_hand.append(u07_cards.pop(0))

                remaining = u01_cards + u02_cards + u07_cards + other_cards
                random.shuffle(remaining)

                while len(initial_hand) < 3 and remaining:
                    initial_hand.append(remaining.pop(0))

                strong_cards = [c for c in remaining if c.get("id") in ["u_07", "s_02", "s_04", "u_04", "u_02"]]
                weak_cards = [c for c in remaining if c not in strong_cards]
                remaining = weak_cards + strong_cards

                for c in initial_hand + remaining:
                    if "instance_id" not in c:
                        c["instance_id"] = str(uuid.uuid4())[:8]

                session.hands[BOT_USER_ID] = initial_hand
                session.decks[BOT_USER_ID] = remaining
                session._deck_stacked = True

            # --- ★ボット専用イカサマ：毎ターンMP増加率+2（1ターンに2ずつ成長） ---
            # 1ターン目は初動テンポを保つためそのまま(1MP)、2ターン目以降に+1ボーナスして合計+2成長にする
            if session.max_mp.get(BOT_USER_ID, 1) > 1 and not getattr(session, f"_mp_boosted_t_{session.turn_idx}", False):
                session.max_mp[BOT_USER_ID] = min(10, session.max_mp[BOT_USER_ID] + 1)
                session.mp[BOT_USER_ID] = session.max_mp[BOT_USER_ID]
                setattr(session, f"_mp_boosted_t_{session.turn_idx}", True)

            # ------------------------------------------------------
            # PHASE 1: 手札カードプレイ（嵐最優先・暗殺者・ユニット展開）
            # ------------------------------------------------------
            local_mp = safe_get(session.mp, BOT_USER_ID, 1)
            local_hand = list(safe_get(session.hands, BOT_USER_ID, []))

            for _ in range(8):
                if local_mp <= 0 or not local_hand:
                    break

                opp_hp = safe_get(session.hp, opp_id, 20)
                opp_board = safe_get(session.boards, opp_id, [])
                my_board = safe_get(session.boards, BOT_USER_ID, [])
                opp_hand_ids = [c.get("id") for c in safe_get(session.hands, opp_id, [])]

                playable = [c for c in local_hand if c.get("cost", 99) <= local_mp]
                if not playable:
                    break

                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]
                action_taken = False

                # 1. 雷撃直接リーサル
                s01_lethal = next((c for c in playable if c.get("id") == "s_01"), None)
                if s01_lethal and not opp_taunts and opp_hp <= 3:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_lethal["instance_id"], "target": {"type": "hero", "id": opp_id}})
                    local_mp -= s01_lethal.get("cost", 2)
                    local_hand.remove(s01_lethal)
                    action_taken = True
                    continue

                # 2. 敵が3体以上なら「嵐 (s_02)」を超最優先
                s02 = next((c for c in playable if c.get("id") == "s_02"), None)
                if s02 and len(opp_board) >= 3:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s02["instance_id"], "target": None})
                    local_mp -= s02.get("cost", 4)
                    local_hand.remove(s02)
                    action_taken = True
                    continue

                # 3. 敵巨兵の即時暗殺
                s05 = next((c for c in playable if c.get("id") == "s_05"), None)
                if s05 and any(t.get("card_id") == "u_04" for t in opp_board):
                    target_titan = next(t for t in opp_board if t.get("card_id") == "u_04")
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s05["instance_id"], "target": {"type": "unit", "id": target_titan["instance_id"]}})
                    local_mp -= s05.get("cost", 6)
                    local_hand.remove(s05)
                    action_taken = True
                    continue

                # 4. 雷撃除去（挑発・小人・吸血鬼・魔導士）
                if s01_lethal and opp_board:
                    killable = [u for u in opp_board if u.get("curr_hp", 0) <= (2 if u.get("wall_turns", 0) > 0 else 3)]
                    if killable:
                        target = max(killable, key=lambda x: (3000 if x.get("taunt") else 0) + (2000 if x.get("card_id") == "u_06" else (1500 if x.get("card_id") == "u_05" else 500)) + x.get("atk", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_lethal["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                        local_mp -= s01_lethal.get("cost", 2)
                        local_hand.remove(s01_lethal)
                        action_taken = True
                        continue

                # 5. 「補充」使用時の確定サーチ
                s04 = next((c for c in playable if c.get("id") == "s_04"), None)
                if s04:
                    if len(opp_board) >= 3:
                        wanted = ["s_02", "u_07"]
                    elif any(t.get("card_id") == "u_04" for t in opp_board):
                        wanted = ["s_05", "u_07"]
                    else:
                        wanted = ["u_07", "u_02"]

                    enforce_top_deck(session, wanted)
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s04["instance_id"], "target": None})
                    local_mp -= s04.get("cost", 3)
                    local_hand.remove(s04)
                    action_taken = True
                    continue

                # 6. 「城壁」付与
                s09 = next((c for c in playable if c.get("id") == "s_09"), None)
                if s09 and my_board:
                    unwalled = [u for u in my_board if u.get("wall_turns", 0) == 0]
                    if unwalled:
                        target = max(unwalled, key=lambda x: (1000 if x.get("card_id") == "u_06" else 0) + (500 if x.get("taunt") else 0) + x.get("atk", 0) * 10)
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s09["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                        local_mp -= s09.get("cost", 2)
                        local_hand.remove(s09)
                        action_taken = True
                        continue

                # 7. ユニット展開
                playable_units = [c for c in playable if c.get("type") == "unit"]
                if playable_units and len(my_board) < 7:
                    enemy_has_assassin = "s_05" in opp_hand_ids

                    best_u = None
                    best_score = -9999

                    for u in playable_units:
                        cid = u.get("id")
                        cost = u.get("cost", 0)

                        score = cost * 1000
                        if cid == "u_01": score += 9000
                        elif cid == "u_07": score += 8000
                        elif cid == "u_02": score += 7000
                        elif cid == "u_04":
                            if enemy_has_assassin:
                                score += 2000
                            else:
                                score += 6000

                        if score > best_score:
                            best_score = score
                            best_u = u

                    if best_u and best_score > 0:
                        try:
                            await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": best_u["instance_id"], "target": None})
                            local_mp -= best_u.get("cost", 0)
                            local_hand.remove(best_u)
                            action_taken = True

                            # 奇術師着地即時バフ（ATK 3〜5 / HP 4〜6）
                            if best_u.get("id") == "u_07":
                                current_board = safe_get(session.boards, BOT_USER_ID, [])
                                for unit in reversed(current_board):
                                    if unit.get("card_id") == "u_07" and not unit.get("_buffed"):
                                        unit["atk"] = random.randint(3, 5)
                                        unit["curr_hp"] = random.randint(4, 6)
                                        unit["max_hp"] = unit["curr_hp"]
                                        unit["name"] = f"奇術師({unit['atk']}/{unit['curr_hp']})"
                                        unit["_buffed"] = True
                                        break
                            continue
                        except Exception:
                            local_hand.remove(best_u)
                            break

                if not action_taken:
                    break

            # ------------------------------------------------------
            # PHASE 2: 盤面総攻撃（挑発完全粉砕＆小人・吸血鬼の絶対殲滅）
            # ------------------------------------------------------
            for _ in range(15):
                my_board = safe_get(session.boards, BOT_USER_ID, [])
                opp_board = safe_get(session.boards, opp_id, [])
                opp_hp = safe_get(session.hp, opp_id, 20)

                active_units = [
                    u for u in my_board 
                    if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0 and u.get("atk", 0) > 0
                ]
                if not active_units:
                    break

                attacker = active_units[0]
                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

                # 1. 挑発がいる場合：最もHPが低い挑発を最速集中砲火で叩き割る
                if opp_taunts:
                    target_taunt = min(opp_taunts, key=lambda x: x.get("curr_hp", 0))
                    await process_action_func(session, BOT_USER_ID, {
                        "action": "DECLARE_ATTACK",
                        "attacker_id": attacker["instance_id"],
                        "target": {"type": "unit", "id": target_taunt["instance_id"]}
                    })
                    continue

                # 2. リーサル判定
                total_dmg = sum(u.get("atk", 0) * u.get("attacks_left", 1) for u in active_units)
                if total_dmg >= opp_hp or attacker.get("atk", 0) >= opp_hp:
                    await process_action_func(session, BOT_USER_ID, {
                        "action": "DECLARE_ATTACK",
                        "attacker_id": attacker["instance_id"],
                        "target": {"type": "hero", "id": opp_id}
                    })
                    continue

                # 3. トレード選定（小人・吸血鬼・巨兵を最優先排除）
                best_target = None
                best_val = -9999

                for t in opp_board:
                    u_atk = max(0, attacker.get("atk", 0) - 1) if t.get("wall_turns", 0) > 0 else attacker.get("atk", 0)
                    if u_atk <= 0 and not attacker.get("ranged"):
                        continue

                    t_atk = 0 if attacker.get("ranged") else (max(0, t.get("atk", 0) - 1) if attacker.get("wall_turns", 0) > 0 else t.get("atk", 0))
                    kills = u_atk >= t.get("curr_hp", 0)
                    survives = t_atk < attacker.get("curr_hp", 0)

                    threat = t.get("atk", 0) * 300
                    if t.get("card_id") == "u_06": threat += 30000  # 小人
                    if t.get("card_id") == "u_05": threat += 25000  # 吸血鬼
                    if t.get("card_id") == "u_04": threat += 20000  # 巨兵
                    if t.get("card_id") == "u_03": threat += 10000  # 魔導士
                    if t.get("card_id") == "u_01": threat += 5000   # 先鋒兵

                    if kills and survives:
                        val = 40000 + threat
                    elif kills and not survives:
                        val = 30000 + threat
                    elif not kills and survives:
                        val = 15000 + (u_atk * 50)
                    else:
                        val = threat - 2000

                    if val > best_val:
                        best_val = val
                        best_target = {"type": "unit", "id": t["instance_id"]}

                if not opp_board:
                    best_target = {"type": "hero", "id": opp_id}
                elif best_val < 5000:
                    best_target = {"type": "hero", "id": opp_id}

                await process_action_func(session, BOT_USER_ID, {
                    "action": "DECLARE_ATTACK",
                    "attacker_id": attacker["instance_id"],
                    "target": best_target
                })

            # 次ターンの通常ドロー確定操作
            opp_board_now = safe_get(session.boards, opp_id, [])
            if len(opp_board_now) >= 3:
                next_wanted = ["s_02"]
            elif any(t.get("card_id") == "u_04" for t in opp_board_now):
                next_wanted = ["s_05"]
            else:
                next_wanted = ["u_07"]

            enforce_top_deck(session, next_wanted)

            # ターン終了
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"AI Turn Error: {e}", exc_info=True)
        try:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
        except Exception:
            pass
