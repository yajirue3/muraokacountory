import asyncio
import copy
import logging
import random
import uuid
from typing import Dict, List, Any, Optional

logger = logging.getLogger("CardBot")

BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "村岡皇太子"

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
    "s_10": {"id": "s_10", "name": "王家の矛", "type": "spell", "cost": 8, "effect": "royal_spear", "val": 1, "need_target": False},
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

def bot_internal_draw(session: Any, user_id: str):
    hand = safe_get(session.hands, user_id, [])
    deck = safe_get(session.decks, user_id, [])
    if len(hand) >= 7:
        return
    if deck:
        c = deck.pop()
    else:
        normal_keys = [k for k in BOT_CARD_DB.keys() if k != "s_10"]
        rand_id = random.choice(normal_keys)
        c = copy.deepcopy(BOT_CARD_DB[rand_id])
    c["instance_id"] = str(uuid.uuid4())[:8]
    hand.append(c)

async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================================
        # 1. ドラフトフェーズ（小人の過剰依存を撤廃、バランス構築）
        # ==========================================================
        if session.status == "DRAFT":
            opts = safe_get(session.draft_options, BOT_USER_ID, [])
            if opts:
                my_deck = safe_get(session.decks, BOT_USER_ID, [])
                my_card_ids = [c.get("id") if isinstance(c, dict) else c for c in my_deck]
                heavy_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("cost", 0) >= 6)

                weights = {
                    "u_01": 150, # 先鋒兵（テンポ重視）
                    "u_07": 135, # 奇術師（高スタッツ）
                    "u_02": 130, # 重装兵（強固な壁）
                    "s_02": 120, # 嵐
                    "u_04": 115 if heavy_count < 2 else 20, # 巨兵
                    "s_01": 105, # 雷撃
                    "u_05": 100, # 吸血鬼
                    "s_04": 95,  # 補充
                    "u_06": 90,  # 小人（優先度を正常化）
                    "u_03": 85,  # 魔導士
                    "s_05": 80 if heavy_count < 2 else 20, # 暗殺者
                    "s_07": 60, "s_09": 60, "s_03": 30,
                }

                if not any(weights.get(cid, 0) >= 110 for cid in opts):
                    inject_cands = ["u_01", "u_07", "u_02", "s_02", "u_04"]
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

            # 初期HP40化
            if getattr(session, "_boss_hp_set", False) is False:
                session.hp[BOT_USER_ID] = 40
                session._boss_hp_set = True

            # 初手先鋒兵強制セット
            if getattr(session, "_hand_ensured", False) is False:
                b_hand = safe_get(session.hands, BOT_USER_ID, [])
                if b_hand:
                    ref = BOT_CARD_DB["u_01"]
                    b_hand[0]["id"] = ref["id"]
                    b_hand[0]["name"] = ref["name"]
                    b_hand[0]["cost"] = ref["cost"]
                    b_hand[0]["type"] = ref["type"]
                    b_hand[0]["atk"] = ref["atk"]
                    b_hand[0]["hp"] = ref["hp"]
                    b_hand[0]["haste"] = True
                session._hand_ensured = True

            # 毎ターン確定2ドロー
            bot_internal_draw(session, BOT_USER_ID)

            # ------------------------------------------------------
            # PHASE 1: 手札カードプレイ
            # ------------------------------------------------------
            for _ in range(12):
                cur_mp = safe_get(session.mp, BOT_USER_ID, 0)
                cur_hand = list(safe_get(session.hands, BOT_USER_ID, []))
                if cur_mp <= 0 or not cur_hand:
                    break

                opp_hp = safe_get(session.hp, opp_id, 20)
                opp_board = safe_get(session.boards, opp_id, [])
                my_board = safe_get(session.boards, BOT_USER_ID, [])
                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

                playable = [c for c in cur_hand if c.get("cost", 99) <= cur_mp]
                if not playable:
                    break

                action_taken = False

                # 1. 雷撃直接リーサル
                s01_lethal = next((c for c in playable if c.get("id") == "s_01"), None)
                if s01_lethal and not opp_taunts and opp_hp <= 3:
                    try:
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_lethal["instance_id"], "target": {"type": "hero", "id": opp_id}})
                        action_taken = True
                        continue
                    except Exception:
                        pass

                # 2. 王家の矛（s_10）：味方が2体以上並んでいれば即発動
                s10 = next((c for c in playable if c.get("id") == "s_10"), None)
                if s10:
                    buffable_units = [u for u in my_board if u.get("card_id") != "u_06"]
                    if len(buffable_units) >= 2 or (len(buffable_units) >= 1 and opp_hp <= 8):
                        try:
                            await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s10["instance_id"], "target": None})
                            action_taken = True
                            continue
                        except Exception:
                            pass

                # 3. 敵が3体以上、または2体以上で相手総HPが高いなら「嵐 (s_02)」
                s02 = next((c for c in playable if c.get("id") == "s_02"), None)
                if s02 and (len(opp_board) >= 3 or (len(opp_board) >= 2 and any(u.get("curr_hp", 0) <= 2 for u in opp_board))):
                    try:
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s02["instance_id"], "target": None})
                        action_taken = True
                        continue
                    except Exception:
                        pass

                # 4. 暗殺者：巨兵(u_04)・吸血鬼(u_05)・奇術師(u_07)を即殺
                s05 = next((c for c in playable if c.get("id") == "s_05"), None)
                if s05 and opp_board:
                    killable_targets = [
                        t for t in opp_board 
                        if t.get("card_id") != "u_08" and not t.get("cannot_assassinate") and t.get("curr_hp", 0) > 0 and t.get("card_id") in ["u_04", "u_05", "u_07", "u_02"]
                    ]
                    if killable_targets:
                        target_boss = max(killable_targets, key=lambda x: (x.get("atk", 0) * 10) + x.get("curr_hp", 0))
                        try:
                            await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s05["instance_id"], "target": {"type": "unit", "id": target_boss["instance_id"]}})
                            action_taken = True
                            continue
                        except Exception:
                            pass

                # 5. 雷撃除去（挑発・吸血鬼・小人を即撃破）
                s01 = next((c for c in playable if c.get("id") == "s_01"), None)
                if s01 and opp_board:
                    killable = [u for u in opp_board if u.get("curr_hp", 0) <= (2 if u.get("wall_turns", 0) > 0 else 3)]
                    if killable:
                        target = max(killable, key=lambda x: (3000 if x.get("taunt") else 0) + (2500 if x.get("card_id") == "u_05" else 0) + (2000 if x.get("card_id") == "u_06" else 0) + x.get("atk", 0))
                        try:
                            await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                            action_taken = True
                            continue
                        except Exception:
                            pass

                # 6. ユニット展開（小人の過度な偏りを防ぎ、アタッカーを積極展開）
                playable_units = [c for c in playable if c.get("type") == "unit"]
                if playable_units and len(my_board) < 7:
                    best_u = None
                    best_score = -9999

                    for u in playable_units:
                        cid = u.get("id")
                        cost = u.get("cost", 0)
                        score = cost * 1000

                        if cid == "u_01": score += 14000    # 先鋒兵最優先（テンポ）
                        elif cid == "u_07": score += 12000  # 奇術師
                        elif cid == "u_02": score += 11000  # 重装兵（壁）
                        elif cid == "u_04": score += 10500  # 巨兵
                        elif cid == "u_05": score += 10000  # 吸血鬼
                        elif cid == "u_06": score += 6000   # 小人（適正評価にダウン）

                        if score > best_score:
                            best_score = score
                            best_u = u

                    if best_u:
                        try:
                            await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": best_u["instance_id"], "target": None})
                            action_taken = True

                            current_board = safe_get(session.boards, BOT_USER_ID, [])
                            # 小人5回攻撃
                            if best_u.get("id") == "u_06":
                                for unit in reversed(current_board):
                                    if unit.get("card_id") == "u_06" and not unit.get("_custom_buffed"):
                                        unit["max_attacks"] = 5
                                        unit["attacks_left"] = 5
                                        unit["desc"] = "1点×5回攻撃(対象自由・反撃無効)"
                                        unit["_custom_buffed"] = True
                                        break

                            # 奇術師ステータス盛（3〜5 / 4〜6）
                            if best_u.get("id") == "u_07":
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
                            pass

                # 7. 「補充」使用
                s04 = next((c for c in playable if c.get("id") == "s_04"), None)
                if s04:
                    if len(opp_board) >= 3:
                        wanted = ["s_02", "u_01"]
                    elif any(t.get("card_id") in ["u_04", "u_05"] for t in opp_board):
                        wanted = ["s_05", "u_01"]
                    else:
                        wanted = ["u_07", "u_01"]

                    enforce_top_deck(session, wanted)
                    try:
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s04["instance_id"], "target": None})
                        action_taken = True
                        continue
                    except Exception:
                        pass

                if not action_taken:
                    break

            # ------------------------------------------------------
            # PHASE 2: 盤面総攻撃（確実に攻撃を完遂するルーチン）
            # ------------------------------------------------------
            for _ in range(25):
                my_units = [
                    u for u in safe_get(session.boards, BOT_USER_ID, [])
                    if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0 and u.get("atk", 0) > 0
                ]
                if not my_units:
                    break

                attacker = my_units[0]
                opp_board = safe_get(session.boards, opp_id, [])
                opp_hp = safe_get(session.hp, opp_id, 20)
                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

                target = None

                # 挑発がいる場合は挑発を最優先で破壊
                if opp_taunts:
                    target = {"type": "unit", "id": min(opp_taunts, key=lambda x: x.get("curr_hp", 0))["instance_id"]}
                # 顔面リーサル
                elif attacker.get("atk", 0) >= opp_hp:
                    target = {"type": "hero", "id": opp_id}
                else:
                    # トレード優先度
                    best_target = None
                    best_val = -9999
                    for t in opp_board:
                        u_atk = max(0, attacker.get("atk", 0) - 1) if t.get("wall_turns", 0) > 0 else attacker.get("atk", 0)
                        t_atk = 0 if attacker.get("ranged") else (max(0, t.get("atk", 0) - 1) if attacker.get("wall_turns", 0) > 0 else t.get("atk", 0))
                        kills = u_atk >= t.get("curr_hp", 0)
                        survives = t_atk < attacker.get("curr_hp", 0)

                        threat = t.get("atk", 0) * 300
                        if t.get("card_id") == "u_05": threat += 30000 # 吸血鬼
                        elif t.get("card_id") == "u_06": threat += 25000 # 小人
                        elif t.get("card_id") == "u_04": threat += 20000 # 巨兵
                        elif t.get("card_id") == "u_07": threat += 18000 # 奇術師

                        if kills and survives: val = 40000 + threat
                        elif kills: val = 25000 + threat
                        elif survives: val = 15000 + threat
                        else: val = threat

                        if val > best_val:
                            best_val = val
                            best_target = {"type": "unit", "id": t["instance_id"]}

                    if best_target and best_val > 10000:
                        target = best_target
                    else:
                        target = {"type": "hero", "id": opp_id}

                try:
                    await process_action_func(session, BOT_USER_ID, {
                        "action": "DECLARE_ATTACK",
                        "attacker_id": attacker["instance_id"],
                        "target": target
                    })
                except Exception:
                    attacker["can_attack"] = False
                    continue

            # 次ターンの通常ドロー確定操作
            opp_board_now = safe_get(session.boards, opp_id, [])
            if len(opp_board_now) >= 3:
                next_wanted = ["s_02"]
            elif any(t.get("card_id") in ["u_04", "u_05"] for t in opp_board_now):
                next_wanted = ["s_05"]
            else:
                next_wanted = ["u_01"]

            enforce_top_deck(session, next_wanted)

            # ターン終了
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"AI Turn Error: {e}", exc_info=True)
        try:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
        except Exception:
            pass
