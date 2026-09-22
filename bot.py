import asyncio
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger("CardBot")

BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "村岡国王（影武者）"

HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}

# --- カードデータベース（card.pyの仕様に完全準拠） ---
BOT_CARD_DB = {
    "u_01": {"id": "u_01", "name": "先鋒兵", "type": "unit", "cost": 1, "atk": 2, "hp": 1, "haste": True},
    "u_02": {"id": "u_02", "name": "重装兵", "type": "unit", "cost": 3, "atk": 2, "hp": 5, "taunt": True},
    "u_03": {"id": "u_03", "name": "魔導士", "type": "unit", "cost": 2, "atk": 3, "hp": 2},
    "u_04": {"id": "u_04", "name": "巨兵", "type": "unit", "cost": 6, "atk": 7, "hp": 6},
    "u_05": {"id": "u_05", "name": "吸血鬼", "type": "unit", "cost": 4, "atk": 3, "hp": 4, "lifesteal": True},
    "u_06": {"id": "u_06", "name": "小人", "type": "unit", "cost": 5, "atk": 1, "hp": 2, "max_attacks": 3, "ranged": True},
    "u_07": {"id": "u_07", "name": "奇術師", "type": "unit", "cost": 4, "atk": 0, "hp": 0, "random_stat": True},
    
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


def decide_best_actions(
    my_hp: int, 
    opp_hp: int, 
    my_mp: int, 
    my_board: List[dict], 
    opp_board: List[dict], 
    my_hand: List[dict]
) -> List[dict]:
    """
    大量の if 分岐による「完全自律型・総合判断」エンジン
    """
    actions = []

    # 1. 相手の場にいる「挑発」ユニットの抽出
    opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]
    
    # 2. 相手盤面の最大打点計算（生存判定の基礎）
    opp_total_atk = sum(u.get("atk", 0) for u in opp_board)
    
    # ==========================================
    # PHASE A: 【最優先】絶対生存・リーサル回避判定
    # ==========================================
    is_in_danger = my_hp <= (opp_total_atk + 3) # 次のターンに死ぬリスクがあるか
    
    if is_in_danger:
        # A-1: 治癒（回復）があれば即使う
        heal_card = next((c for c in my_hand if c.get("id") == "s_03" and c.get("cost", 99) <= my_mp), None)
        if heal_card:
            actions.append({"action": "PLAY_HAND", "card_instance_id": heal_card["instance_id"], "target": None})
            my_mp -= heal_card["cost"]
            my_hand.remove(heal_card)

        # A-2: 重装兵（挑発）があれば即座に壁として立てる
        taunt_card = next((c for c in my_hand if c.get("id") == "u_02" and c.get("cost", 99) <= my_mp), None)
        if taunt_card and len(my_board) < 7:
            actions.append({"action": "PLAY_HAND", "card_instance_id": taunt_card["instance_id"], "target": None})
            my_mp -= taunt_card["cost"]
            my_hand.remove(taunt_card)

        # A-3: 暗殺者や雷撃で脅威の大型・アタッカーを消す
        assassinate_card = next((c for c in my_hand if c.get("id") == "s_05" and c.get("cost", 99) <= my_mp), None)
        if assassinate_card and opp_board:
            # 最も攻撃力の高い敵を狙う
            target_opp = max(opp_board, key=lambda x: x.get("atk", 0))
            actions.append({
                "action": "PLAY_HAND", 
                "card_instance_id": assassinate_card["instance_id"], 
                "target": {"type": "unit", "id": target_opp["instance_id"]}
            })
            my_mp -= assassinate_card["cost"]
            my_hand.remove(assassinate_card)
            opp_board.remove(target_opp)

    # ==========================================
    # PHASE B: ユニット優先の盤面制圧（物量展開）
    # ==========================================
    # スペルよりもユニットを死ぬ気で横並びさせる
    playable_units = [c for c in my_hand if c.get("type") == "unit" and c.get("cost", 99) <= my_mp]
    # コストの重い順、または場持ちの良い順に展開
    playable_units.sort(key=lambda x: x.get("cost", 0), reverse=True)

    for unit_card in playable_units:
        if len(my_board) >= 7:
            break
        if my_mp >= unit_card["cost"]:
            actions.append({
                "action": "PLAY_HAND", 
                "card_instance_id": unit_card["instance_id"], 
                "target": None
            })
            my_mp -= unit_card["cost"]
            my_board.append({
                "instance_id": unit_card["instance_id"],
                "card_id": unit_card["id"],
                "name": unit_card["name"],
                "atk": unit_card.get("atk", 0),
                "curr_hp": unit_card.get("hp", 1),
                "can_attack": unit_card.get("haste", False),
                "attacks_left": unit_card.get("max_attacks", 1),
                "ranged": unit_card.get("ranged", False)
            })
            my_hand.remove(unit_card)

    # ==========================================
    # PHASE C: 余ったマナで除去スペルによる焦土化
    # ==========================================
    spell_cards = [c for c in my_hand if c.get("type") == "spell" and c.get("cost", 99) <= my_mp]
    for spell in spell_cards:
        s_id = spell.get("id")
        if s_id == "s_01" and opp_board: # 雷撃（3点）
            target_opp = min(opp_board, key=lambda x: x.get("curr_hp", 99)) # 倒せるやつを優先
            actions.append({
                "action": "PLAY_HAND", 
                "card_instance_id": spell["instance_id"], 
                "target": {"type": "unit", "id": target_opp["instance_id"]}
            })
            my_mp -= spell["cost"]
            my_hand.remove(spell)
            break
        elif s_id == "s_02" and len(opp_board) >= 2: # 嵐（全体2点、敵が2体以上なら撃つ）
            actions.append({"action": "PLAY_HAND", "card_instance_id": spell["instance_id"], "target": None})
            my_mp -= spell["cost"]
            my_hand.remove(spell)
            break

    # ==========================================
    # PHASE D: 戦闘（アタック）の自律総合判断
    # ==========================================
    for u in my_board:
        if not u.get("can_attack") or u.get("attacks_left", 0) <= 0:
            continue

        u_atk = u.get("atk", 0)
        u_hp = u.get("curr_hp", 0)

        # D-1: 挑発がいる場合は、挑発を優先して殴る
        if opp_taunts:
            target_t = opp_taunts[0]
            actions.append({
                "action": "DECLARE_ATTACK",
                "attacker_id": u["instance_id"],
                "target": {"type": "unit", "id": target_t["instance_id"]}
            })
            u["attacks_left"] -= 1
            if u["attacks_left"] <= 0:
                u["can_attack"] = False
            continue

        # D-2: 小人（遠隔・3回攻撃）の特殊自律処理
        if u.get("ranged") and opp_board:
            # 遠隔なので反撃を受けない。最もHPの低い敵を3回まで安全に削る
            target_opp = min(opp_board, key=lambda x: x.get("curr_hp", 99))
            while u.get("attacks_left", 0) > 0 and target_opp in opp_board:
                actions.append({
                    "action": "DECLARE_ATTACK",
                    "attacker_id": u["instance_id"],
                    "target": {"type": "unit", "id": target_opp["instance_id"]}
                })
                target_opp["curr_hp"] -= u_atk
                u["attacks_left"] -= 1
                if target_opp["curr_hp"] <= 0:
                    opp_board.remove(target_opp)
                    break
            u["can_attack"] = False
            continue

        # D-3: 通常ユニットの戦闘判断（有利トレード or 顔面）
        if opp_board:
            # 「一方的に勝てる（自分のHPが残り、相手を倒せる）」トレードを探す
            advantage_target = None
            for opp_u in opp_board:
                opp_atk = opp_u.get("atk", 0)
                opp_hp = opp_u.get("curr_hp", 0)
                if u_atk >= opp_hp and u_hp > opp_atk:
                    advantage_target = opp_u
                    break

            if advantage_target:
                actions.append({
                    "action": "DECLARE_ATTACK",
                    "attacker_id": u["instance_id"],
                    "target": {"type": "unit", "id": advantage_target["instance_id"]}
                })
                u["attacks_left"] -= 1
                if u["attacks_left"] <= 0:
                    u["can_attack"] = False
                continue
            
            # 相手の場に強力なユニット（攻撃力3以上）がいる場合、相打ちでも処理しに行く判断
            desperate_target = next((ou for ou in opp_board if ou.get("atk", 0) >= 3 and u_atk >= ou.get("curr_hp", 99)), None)
                if desperate_target:
                actions.append({
                    "action": "DECLARE_ATTACK",
                    "attacker_id": u["instance_id"],
                    "target": {"type": "unit", "id": desperate_target["instance_id"]}
                })
                u["attacks_left"] -= 1
                if u["attacks_left"] <= 0:
                    u["can_attack"] = False
                continue

        # D-4: 相手の場が安全（更地に近い）なら、迷わずヒーロー（顔面）を殴る
        if not opp_board:
            actions.append({
                "action": "DECLARE_ATTACK",
                "attacker_id": u["instance_id"],
                "target": {"type": "hero", "id": "opponent_hero_placeholder"} # 実際の実行時にカードエンジン側が解決
            })
            u["attacks_left"] -= 1
            if u["attacks_left"] <= 0:
                u["can_attack"] = False

    return actions


async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    while session.status != "ENDED" and session.turn_user_id == BOT_USER_ID:
        
        # --- ドラフトフェーズの自律判断 ---
        if session.status == "DRAFT":
            opts = session.draft_options.get(BOT_USER_ID, [])
            if not opts:
                break

            # ユニット優先、かつ1マナ速攻や高スタッツを最優先でピック
            best_card = opts[0]
            best_priority = -999

            for cid in opts:
                c_info = BOT_CARD_DB.get(cid, {})
                priority = 0
                if c_info.get("type") == "unit":
                    priority += 500  # スペルよりユニット絶対優先
                    priority += c_info.get("atk", 0) * 100
                    priority += c_info.get("hp", 0) * 50
                    if c_info.get("haste") or c_info.get("taunt"):
                        priority += 300
                else:
                    priority += 100  # スペルは控えめ

                if priority > best_priority:
                    best_priority = priority
                    best_card = cid

            await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})
            await asyncio.sleep(0.01)
            continue

        # --- バトルフェーズの自律判断 ---
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            if not opp_id:
                break

            my_hp = session.hp.get(BOT_USER_ID, 20)
            opp_hp = session.hp.get(opp_id, 20)
            my_mp = session.mp.get(BOT_USER_ID, 1)
            
            my_board = [dict(u) for u in session.boards.get(BOT_USER_ID, [])]
            opp_board = [dict(u) for u in session.boards.get(opp_id, [])]
            my_hand = [dict(c) for c in session.hands.get(BOT_USER_ID, [])]

            # 自律判断エンジンの実行
            planned_actions = decide_best_actions(my_hp, opp_hp, my_mp, my_board, opp_board, my_hand)

            for act in planned_actions:
                if session.status != "BATTLE" or session.turn_user_id != BOT_USER_ID:
                    break
                
                # 顔面殴りのプレースホルダーを正しい対戦相手IDに置き換え
                if act.get("action") == "DECLARE_ATTACK" and act.get("target", {}).get("type") == "hero":
                    act["target"]["id"] = opp_id

                await process_action_func(session, BOT_USER_ID, act)
                await asyncio.sleep(0.01)

            # ターン終了
            if session.turn_user_id == BOT_USER_ID and session.status == "BATTLE":
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
            
            break
