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
                heavy_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("cost", 0) >= 6)

                weights = {
                    "u_07": 130, "u_01": 120, "u_02": 110,
                    "u_04": 105 if heavy_count < 2 else 20,
                    "s_04": 100, "s_01": 95,
                    "s_05": 90 if heavy_count < 3 else 20,
                    "u_03": 85, "u_06": 80, "s_02": 75,
                    "s_07": 70, "s_09": 65, "u_05": 60, "s_03": 30,
                }

                # 裏抽選（疑似リロール）：候補に強カードがなければ強制的に差し替える
                if not any(weights.get(cid, 0) >= 100 for cid in opts):
                    inject_cands = ["u_07", "u_01", "s_04", "u_02"]
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

            # --- 自然なイカサマ配牌（マナカーブの確定安定化） ---
            if getattr(session, "_deck_stacked", False) is False:
                all_cards = safe_get(session.hands, BOT_USER_ID, []) + safe_get(session.decks, BOT_USER_ID, [])
                random.shuffle(all_cards)

                cost_1_2 = [c for c in all_cards if c.get("cost", 99) <= 2]
                cost_3 = [c for c in all_cards if c.get("cost", 99) == 3]
                cost_4_plus = [c for c in all_cards if c.get("cost", 99) >= 4]

                initial_hand = []
                if cost_1_2: initial_hand.append(cost_1_2.pop())
                if cost_3: initial_hand.append(cost_3.pop())
                if cost_4_plus: initial_hand.append(cost_4_plus.pop())

                remaining = cost_1_2 + cost_3 + cost_4_plus
                random.shuffle(remaining)

                while len(initial_hand) < 3 and remaining:
                    initial_hand.append(remaining.pop())

                # 山札のトップ付近に強力なカードを固める
                wanted_top = []
                other_rem = []
                for c in remaining:
                    if c.get("id") in ["u_07", "s_04", "u_04"]:
                        wanted_top.append(c)
                    else:
                        other_rem.append(c)
                remaining = other_rem + wanted_top

                for c in initial_hand + remaining:
                    if "instance_id" not in c:
                        c["instance_id"] = str(uuid.uuid4())[:8]

                session.hands[BOT_USER_ID] = initial_hand
                session.decks[BOT_USER_ID] = remaining
                session._deck_stacked = True

            # --- 奇術師ステータス底上げパッチ（ATK 3〜5 / HP 4〜6） ---
            my_board = safe_get(session.boards, BOT_USER_ID, [])
            for u in my_board:
                if u.get("card_id") == "u_07" and not u.get("_buffed"):
                    u["atk"] = random.randint(3, 5)
                    u["curr_hp"] = random.randint(4, 6)
                    u["max_hp"] = u["curr_hp"]
                    u["name"] = f"奇術師({u['atk']}/{u['curr_hp']})"
                    u["_buffed"] = True

            # ------------------------------------------------------
            # PHASE 1: 手札プレイ（手札透視＋確定サーチ＋最適展開）
            # ------------------------------------------------------
            for _ in range(10):
                local_mp = safe_get(session.mp, BOT_USER_ID, 1)
                local_hand = list(safe_get(session.hands, BOT_USER_ID, []))
                opp_hp = safe_get(session.hp, opp_id, 20)
                opp_board = list(safe_get(session.boards, opp_id, []))
                my_board = list(safe_get(session.boards, BOT_USER_ID, []))
                opp_hand_ids = [c.get("id") for c in safe_get(session.hands, opp_id, [])]

                playable = [c for c in local_hand if c.get("cost", 99) <= local_mp]
                if not playable:
                    break

                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]
                action_taken = False

                # 1. 直接雷撃リーサル
                s01_lethal = next((c for c in playable if c.get("id") == "s_01"), None)
                if s01_lethal and not opp_taunts and opp_hp <= 3:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_lethal["instance_id"], "target": {"type": "hero", "id": opp_id}})
                    continue

                # 2. 敵巨兵の即時暗殺
                s05 = next((c for c in playable if c.get("id") == "s_05"), None)
                if s05 and any(t.get("card_id") == "u_04" for t in opp_board):
                    target_titan = next(t for t in opp_board if t.get("card_id") == "u_04")
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s05["instance_id"], "target": {"type": "unit", "id": target_titan["instance_id"]}})
                    continue

                # 3. 雷撃による厄介な敵（小人・魔導士）の優先除去
                if s01_lethal and opp_board:
                    killable = [u for u in opp_board if u.get("curr_hp", 0) <= 3 and u.get("card_id") in ["u_06", "u_03"]]
                    if killable:
                        target = max(killable, key=lambda x: (1000 if x.get("card_id") == "u_06" else 0) + x.get("atk", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_lethal["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                        continue

                # 4. 「城壁」による雷撃ケア（手札透視連動）
                s09 = next((c for c in playable if c.get("id") == "s_09"), None)
                if s09 and my_board:
                    unwalled = [u for u in my_board if u.get("wall_turns", 0) == 0]
                    if unwalled:
                        enemy_has_lightning = "s_01" in opp_hand_ids
                        if enemy_has_lightning:
                            targets = [u for u in unwalled if u.get("curr_hp", 0) <= 3 and u.get("card_id") in ["u_06", "u_07"]]
                            if targets:
                                target = max(targets, key=lambda x: x.get("atk", 0))
                                await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s09["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                                continue

                # 5. 「補充」使用時のピンポイント確定ドローサーチ
                s04 = next((c for c in playable if c.get("id") == "s_04"), None)
                if s04:
                    my_deck = session.decks.get(BOT_USER_ID, [])
                    if len(my_deck) >= 2:
                        wanted = []
                        if any(t.get("card_id") == "u_04" for t in opp_board):
                            wanted = ["s_05", "u_02", "u_07"]
                        elif "s_05" in opp_hand_ids:
                            wanted = ["u_02", "u_06", "u_03", "u_07"]
                        elif opp_board:
                            wanted = ["s_01", "s_02", "u_07", "u_04"]
                        else:
                            wanted = ["u_07", "u_04", "u_02", "s_04"]

                        pulled = []
                        for w in wanted:
                            for i in range(len(my_deck)):
                                if my_deck[i].get("id") == w:
                                    pulled.append(my_deck.pop(i))
                                    break
                            if len(pulled) == 2:
                                break
                        my_deck.extend(pulled[::-1])

                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s04["instance_id"], "target": None})
                    continue

                # 6. ユニット最適展開（手札透視によるメタ読み制御）
                playable_units = [c for c in playable if c.get("type") == "unit"]
                if playable_units and len(my_board) < 7:
                    enemy_has_assassin = "s_05" in opp_hand_ids
                    enemy_has_storm = "s_02" in opp_hand_ids

                    best_u = None
                    best_score = -9999

                    for u in playable_units:
                        cid = u.get("id")
                        cost = u.get("cost", 0)
                        base_hp = BOT_CARD_DB.get(cid, {}).get("hp", 1)

                        score = cost * 1000
                        if cid == "u_07": score += 8000
                        if cid == "u_02": score += 5000

                        # 相手が暗殺者を持っているなら巨兵は出さない
                        if cid == "u_04":
                            if enemy_has_assassin:
                                score -= 10000
                            else:
                                score += 6000

                        # 相手が嵐を持っているなら低HPは優先度を下げる
                        if enemy_has_storm and base_hp <= 2 and cid != "u_07":
                            score -= 3000

                        if score > best_score:
                            best_score = score
                            best_u = u

                    if best_u and best_score > 0:
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": best_u["instance_id"], "target": None})
                        action_taken = True
                        continue

                # アクションが尽きたらフェーズ終了
                if not action_taken:
                    break

            # ------------------------------------------------------
            # PHASE 2: 盤面総攻撃
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

                # 1. 挑発なし＆削り切れるならフェイス突撃
                if not opp_taunts:
                    total_dmg = sum(u.get("atk", 0) * u.get("attacks_left", 1) for u in active_units)
                    if total_dmg >= opp_hp or attacker.get("atk", 0) >= opp_hp:
                        await process_action_func(session, BOT_USER_ID, {
                            "action": "DECLARE_ATTACK",
                            "attacker_id": attacker["instance_id"],
                            "target": {"type": "hero", "id": opp_id}
                        })
                        continue

                # 2. 挑発集中突破
                if opp_taunts:
                    target_taunt = min(opp_taunts, key=lambda x: x.get("curr_hp", 0))
                    await process_action_func(session, BOT_USER_ID, {
                        "action": "DECLARE_ATTACK",
                        "attacker_id": attacker["instance_id"],
                        "target": {"type": "unit", "id": target_taunt["instance_id"]}
                    })
                    continue

                # 3. 最適トレード選定（脅威度調整済み）
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
                    if t.get("card_id") == "u_04": threat += 10000
                    if t.get("card_id") == "u_06": threat += 8000
                    if t.get("card_id") == "u_03": threat += 5000

                    if kills and survives:
                        val = 25000 + threat
                    elif kills and not survives:
                        val = 20000 + threat
                    elif not kills and survives:
                        val = 5000 + (u_atk * 50)
                    else:
                        val = threat - 2000

                    if val > best_val:
                        best_val = val
                        best_target = {"type": "unit", "id": t["instance_id"]}

                has_danger = any(t.get("card_id") in ["u_04", "u_06", "u_03"] or t.get("atk", 0) >= 3 for t in opp_board)
                face_val = 6000 + (attacker.get("atk", 0) * 200) if not has_danger else 500

                if not opp_board or face_val > best_val:
                    best_target = {"type": "hero", "id": opp_id}

                await process_action_func(session, BOT_USER_ID, {
                    "action": "DECLARE_ATTACK",
                    "attacker_id": attacker["instance_id"],
                    "target": best_target
                })

            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"AI Turn Error: {e}", exc_info=True)
        try:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
        except Exception:
            pass
