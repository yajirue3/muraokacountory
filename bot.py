import asyncio
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
    """
    【絶対王者フルコード：IF盛りだくさん版】
    順序固定：ドロー -> 除去スペル -> 有利トレード攻撃 -> ユニット展開(MP最優先) -> 顔面
    1体ずつ対処されるのを防ぐため、必ず「自分の盤面」を構築・防衛することを最優先。
    """
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================
        # 1. ドラフトフェーズ（ユニット過剰ピック設定）
        # ==========================================
        if session.status == "DRAFT":
            opts = session.draft_options.get(BOT_USER_ID, [])
            if opts:
                my_deck = session.decks.get(BOT_USER_ID, [])
                my_card_ids = [c.get("id") if isinstance(c, dict) else c for c in my_deck]
                spell_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("type") == "spell")

                best_card, max_score = opts[0], -999999

                for cid in opts:
                    c = BOT_CARD_DB.get(cid, {})
                    score = 0

                    if c.get("type") == "unit":
                        if c.get("atk", 0) == 0 and c.get("hp", 0) == 0:
                            score -= 20000 # 奇術師は絶対取らない
                        else:
                            score += 5000 + (c.get("atk", 0) * 150) + (c.get("hp", 0) * 100)
                            if cid == "u_01": score += 20000  # 先鋒兵：超最優先（盤面取り）
                            if cid == "u_02": score += 15000  # 重装兵：守り
                            if cid == "u_04": score += 10000  # 巨兵
                            if c.get("haste"): score += 1000
                            if c.get("taunt"): score += 800
                    else:
                        score += 2000
                        if cid in ["s_05", "s_01"]: score += 4000
                        elif cid == "s_04": score += 3000

                    # ユニット優先のため、スペルが3枚以上になったら極端にスペル評価を下げる
                    if c.get("type") == "spell" and spell_count >= 3:
                        score -= 10000

                    if score > max_score:
                        max_score = score
                        best_card = cid

                await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})
                await asyncio.sleep(0.05)

                if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                    await process_super_ai_turn(session, card_database, process_action_func)
            return

        # ==========================================
        # 2. バトルフェーズ（IFパイプライン）
        # ==========================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            if not opp_id: return

            action_loop_count = 0
            max_loop_limit = 30

            while session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID and action_loop_count < max_loop_limit:
                action_loop_count += 1

                my_mp = session.mp.get(BOT_USER_ID, 0)
                my_board = session.boards.get(BOT_USER_ID, [])
                opp_board = session.boards.get(opp_id, [])
                my_hand = session.hands.get(BOT_USER_ID, [])
                opp_hp = session.hp.get(opp_id, 20)
                my_hp = session.hp.get(BOT_USER_ID, 20)

                opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]
                playable_cards = [c for c in my_hand if c.get("cost", 99) <= my_mp]
                playable_units = [c for c in playable_cards if c.get("type") == "unit" and (c.get("atk",0) > 0 or c.get("hp",0) > 0)]
                playable_spells = [c for c in playable_cards if c.get("type") == "spell"]
                active_units = [u for u in my_board if u.get("can_attack") and u.get("frozen_turns", 0) <= 0 and u.get("attacks_left", 0) > 0]

                # --- [IF 1] 最優先：ドロー（補充 s_04） ---
                s04_cards = [c for c in playable_spells if c.get("id") == "s_04"]
                if s04_cards:
                    await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s04_cards[0]["instance_id"], "target": None})
                    await asyncio.sleep(0.15); continue

                # --- [IF 2] 確定リーサル（顔面特攻）判定 ---
                board_atk = sum(u.get("atk", 0) * u.get("attacks_left", 1) for u in active_units)
                spell_dmg = sum(3 for c in playable_spells if c.get("id") == "s_01")
                has_assassin = any(c.get("id") == "s_05" for c in playable_spells)
                taunt_hp_total = sum(t.get("curr_hp", 0) for t in opp_taunts)

                is_lethal = False
                if not opp_taunts and (board_atk + spell_dmg >= opp_hp):
                    is_lethal = True
                elif opp_taunts and (has_assassin or spell_dmg >= taunt_hp_total) and (board_atk >= opp_hp):
                    is_lethal = True

                if is_lethal and active_units and not opp_taunts:
                    # 邪魔者がいなければ顔面殴って終わらせる
                    await process_action_func(session, BOT_USER_ID, {"action": "DECLARE_ATTACK", "attacker_id": active_units[0]["instance_id"], "target": {"type": "hero", "id": opp_id}})
                    await asyncio.sleep(0.15); continue

                # --- [IF 3] ユニット展開前の盤面清掃（味方ユニットを守るためのスペル使用） ---
                # 人間に「1体ずつ対処」させないため、味方で殴る前にスペルで敵を消す
                if opp_board and not is_lethal:
                    # 嵐(s_02): 敵が2体以上いたら雑に掃討する
                    s02_cards = [c for c in playable_spells if c.get("id") == "s_02"]
                    if s02_cards and len(opp_board) >= 2:
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s02_cards[0]["instance_id"], "target": None})
                        await asyncio.sleep(0.15); continue

                    # 暗殺者(s_05): ATK4以上、HP5以上の厄介な敵や挑発を消す
                    s05_cards = [c for c in playable_spells if c.get("id") == "s_05"]
                    if s05_cards:
                        threats = [t for t in opp_board if t.get("atk", 0) >= 4 or t.get("curr_hp", 0) >= 5 or t.get("taunt")]
                        if threats:
                            target = max(threats, key=lambda x: x.get("atk", 0) + x.get("curr_hp", 0))
                            await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s05_cards[0]["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                            await asyncio.sleep(0.15); continue

                    # 雷撃(s_01): HP3以下を無傷で処理、またはウォール(壁)を剥がす
                    s01_cards = [c for c in playable_spells if c.get("id") == "s_01"]
                    if s01_cards:
                        killable = [t for t in opp_board if (t.get("curr_hp", 0) <= 3 and t.get("wall_turns", 0) == 0) or t.get("wall_turns", 0) > 0]
                        if killable:
                            target = max(killable, key=lambda x: x.get("atk", 0))
                            await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_cards[0]["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                            await asyncio.sleep(0.15); continue

                # --- [IF 4] 盤面攻撃（ユニット展開前に盤面をあける / 有利トレード） ---
                if active_units and opp_board and not is_lethal:
                    best_attack = None
                    best_attack_score = -9999
                    targets = opp_taunts if opp_taunts else opp_board

                    for u in active_units:
                        for t in targets:
                            if t.get("curr_hp", 0) <= 0: continue
                            
                            u_atk = max(0, u.get("atk", 0) - 1) if t.get("wall_turns", 0) > 0 else u.get("atk", 0)
                            if u_atk <= 0 and not u.get("ranged"): continue

                            kills_target = u_atk >= t.get("curr_hp", 0)
                            t_atk = 0 if u.get("ranged") else (max(0, t.get("atk", 0) - 1) if u.get("wall_turns", 0) > 0 else t.get("atk", 0))
                            i_survive = t_atk < u.get("curr_hp", 0)

                            score = 0
                            # 一方的撃破（自傷なし or 生存）
                            if kills_target and i_survive:
                                score = 10000 + t.get("atk", 0) * 100
                            # 相打ち（相手が高コスト・高ATKなら取る価値あり）
                            elif kills_target and not i_survive:
                                if t.get("cost", 0) >= u.get("cost", 0) or t.get("atk", 0) >= u.get("atk", 0) or t.get("taunt"):
                                    score = 5000 + t.get("atk", 0) * 100
                                else:
                                    score = -5000 # 損する相打ちは絶対にやらない（人間に対処される原因）
                            elif not kills_target and i_survive:
                                score = 1000 # 遠距離などで削るだけ
                            else:
                                score = -10000 # 無駄死に

                            if score > best_attack_score and score > 0:
                                best_attack_score = score
                                best_attack = {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}

                    if best_attack:
                        await process_action_func(session, BOT_USER_ID, best_attack)
                        await asyncio.sleep(0.15); continue

                # --- [IF 5] ユニット最優先展開（基本MPはユニットで使い切る） ---
                if playable_units and len(my_board) < 7:
                    # 特定の強力ユニット優先着地
                    u02_cards = [c for c in playable_units if c.get("id") == "u_02"] # 重装兵
                    u04_cards = [c for c in playable_units if c.get("id") == "u_04"] # 巨兵
                    u01_cards = [c for c in playable_units if c.get("id") == "u_01"] # 先鋒兵
                    
                    if u04_cards: # MP6あるなら巨兵最優先
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": u04_cards[0]["instance_id"], "target": None})
                        await asyncio.sleep(0.15); continue
                    elif u02_cards: # 守りを固める
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": u02_cards[0]["instance_id"], "target": None})
                        await asyncio.sleep(0.15); continue
                    elif u01_cards: # 即殴れる疾走
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": u01_cards[0]["instance_id"], "target": None})
                        await asyncio.sleep(0.15); continue
                    else:
                        # それ以外のユニットは「最もコストが高いもの」から出してMPを全力消化
                        best_unit = max(playable_units, key=lambda x: x.get("cost", 0) * 100 + x.get("atk", 0) * 10 + x.get("hp", 0))
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": best_unit["instance_id"], "target": None})
                        await asyncio.sleep(0.15); continue

                # --- [IF 6] ユニット展開後に余ったMPで支援・顔面スペル ---
                if playable_spells:
                    # s_09(城壁): 味方の最大戦力に貼って無敵化
                    s09_cards = [c for c in playable_spells if c.get("id") == "s_09"]
                    if s09_cards and my_board:
                        target = max(my_board, key=lambda x: x.get("atk", 0) + x.get("curr_hp", 0))
                        if target.get("wall_turns", 0) == 0:
                            await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s09_cards[0]["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}})
                            await asyncio.sleep(0.15); continue
                            
                    # s_03(治癒): HPが減っていれば使う
                    s03_cards = [c for c in playable_spells if c.get("id") == "s_03"]
                    if s03_cards and my_hp <= 12:
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s03_cards[0]["instance_id"], "target": None})
                        await asyncio.sleep(0.15); continue
                        
                    # s_01(雷撃): 余ったMPで、邪魔な挑発がいないなら顔面に叩き込む
                    s01_cards = [c for c in playable_spells if c.get("id") == "s_01"]
                    if s01_cards and not opp_taunts:
                        await process_action_func(session, BOT_USER_ID, {"action": "PLAY_HAND", "card_instance_id": s01_cards[0]["instance_id"], "target": {"type": "hero", "id": opp_id}})
                        await asyncio.sleep(0.15); continue

                # --- [IF 7] 盤面処理が終わって残ったユニットで顔面一斉攻撃 ---
                if active_units and not opp_taunts:
                    # 最初のユニットで顔面攻撃
                    await process_action_func(session, BOT_USER_ID, {"action": "DECLARE_ATTACK", "attacker_id": active_units[0]["instance_id"], "target": {"type": "hero", "id": opp_id}})
                    await asyncio.sleep(0.15); continue

                # --- [IF 8] やることがなくなったらターンエンド ---
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
                break

            # 万が一ループを抜けたらエンド
            if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"Super AI Fatal Error Fallback: {e}", exc_info=True)
        if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
