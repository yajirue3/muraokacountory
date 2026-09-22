import asyncio
import logging
import copy
import time
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("CardBot")

BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "村岡国王（影武者）"

HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}

CARD_SYNERGY_MATRIX = {
    "u_01": {"score": 150, "counters": ["u_03", "u_06", "u_07"]}, # 速攻
    "s_02": {"score": 130, "counters": ["u_06", "u_01", "u_07"]}, # 全体2点
    "s_05": {"score": 160, "counters": ["u_04", "u_05", "u_02"]}, # 暗殺
    "s_09": {"score": 140, "counters": ["u_01", "s_01"]},         # 城壁
    "s_01": {"score": 40,  "counters": ["u_03", "u_07"]},         # 電撃
    "u_02": {"score": 130, "counters": ["u_01"]},                 # 挑発
}

class SimState:
    def __init__(self):
        self.my_hp = 20
        self.opp_hp = 20
        self.my_mp = 1
        self.opp_mp = 1
        self.my_board = []
        self.opp_board = []
        self.my_hand = []
        self.opp_hand = []
        self.my_deck = []
        self.opp_deck = []
        self.my_id = ""
        self.opp_id = ""
        self.current_turn = 1

    def clone(self):
        new_s = SimState()
        new_s.my_hp, new_s.opp_hp = self.my_hp, self.opp_hp
        new_s.my_mp, new_s.opp_mp = self.my_mp, self.opp_mp
        new_s.my_id, new_s.opp_id = self.my_id, self.opp_id
        new_s.current_turn = self.current_turn
        new_s.my_board = [dict(u) for u in self.my_board]
        new_s.opp_board = [dict(u) for u in self.opp_board]
        new_s.my_hand = [dict(c) for c in self.my_hand]
        new_s.opp_hand = [dict(c) for c in self.opp_hand]
        new_s.my_deck = [dict(c) for c in self.my_deck]
        new_s.opp_deck = [dict(c) for c in self.opp_deck]
        return new_s

    def get_hash(self) -> str:
        mb = ",".join(f"{u.get('instance_id')}:{u.get('curr_hp')}:{u.get('attacks_left')}:{u.get('wall_turns',0)}" for u in self.my_board)
        ob = ",".join(f"{u.get('instance_id')}:{u.get('curr_hp')}:{u.get('wall_turns',0)}" for u in self.opp_board)
        mh = ",".join(c.get("instance_id", "") for c in self.my_hand)
        oh = ",".join(c.get("instance_id", "") for c in self.opp_hand)
        return f"{self.my_hp}|{self.opp_hp}|{self.my_mp}|{self.opp_mp}|[{mb}]|[{ob}]|[{mh}]|[{oh}]"

    def calculate_opp_absolute_max_threat(self) -> int:
        """相手の手札＋山札トップの神引きを含めた『最大理論打点』を徹底算出"""
        threat = sum(u.get("atk", 0) for u in self.opp_board)
        avail_mp = min(10, self.opp_mp + 1)
        
        # 手札からの最大火力
        for c in self.opp_hand:
            if c.get("cost", 99) <= avail_mp:
                if c.get("type") == "spell" and c.get("effect") == "damage":
                    threat += c.get("val", 0)
                elif c.get("type") == "unit" and c.get("haste"):
                    threat += c.get("atk", 0)
                    
        # 山札トップ解決（最高火力カード）のリスクも加算
        threat += 4 # 神引き想定マージン
        return threat

    def evaluate_state(self, is_my_turn_eval: bool = True) -> float:
        if self.opp_hp <= 0: return 9999999.0
        if self.my_hp <= 0: return -9999999.0

        score = 0.0

        # 確定リーサル
        my_total_atk = sum(u.get("atk", 0) for u in self.my_board if u.get("can_attack") or u.get("haste"))
        if my_total_atk >= self.opp_hp:
            return 900000.0 if is_my_turn_eval else -900000.0

        # 神引き含めた相手の最大打点で死ぬ可能性があるなら極小評価（完全封殺）
        opp_max_threat = self.calculate_opp_absolute_max_threat()
        has_taunt = any(u.get("taunt") for u in self.my_board)
        
        if opp_max_threat >= self.my_hp and not has_taunt:
            score -= 500000.0

        # 盤面の数（アグロ支配）の絶対評価
        score += len(self.my_board) * 50.0

        for u in self.my_board:
            score += u.get("atk", 0) * 25.0 + u.get("curr_hp", 0) * 15.0
            if u.get("taunt"): score += 90.0
            if u.get("wall_turns", 0) > 0: score += 60.0

        for u in self.opp_board:
            score -= u.get("atk", 0) * 40.0 + u.get("curr_hp", 0) * 20.0
            if u.get("taunt"): score -= 100.0

        score += (20 - self.opp_hp) * 40.0
        score += self.my_hp * 10.0
        score += len(self.my_hand) * 20.0

        return score if is_my_turn_eval else -score

    def get_legal_actions(self, is_bot_turn: bool) -> List[dict]:
        actions = []
        active_board = self.my_board if is_bot_turn else self.opp_board
        enemy_board = self.opp_board if is_bot_turn else self.my_board
        active_hand = self.my_hand if is_bot_turn else self.opp_hand
        active_mp = self.my_mp if is_bot_turn else self.opp_mp
        enemy_id = self.opp_id if is_bot_turn else self.my_id

        opp_taunts = [u for u in enemy_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

        # --- 攻撃行動 ---
        for u in active_board:
            if u.get("can_attack") and u.get("frozen_turns", 0) == 0 and u.get("attacks_left", 0) > 0:
                if opp_taunts:
                    for t in opp_taunts:
                        actions.append({
                            "type": "ATTACK", "a_id": u["instance_id"], "t_type": "unit", "t_id": t["instance_id"], "score": 200,
                            "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                        })
                else:
                    for t in enemy_board:
                        if t.get("curr_hp", 0) > 0:
                            trade_score = t.get("atk", 0) * 25 - u.get("atk", 0) * 2
                            actions.append({
                                "type": "ATTACK", "a_id": u["instance_id"], "t_type": "unit", "t_id": t["instance_id"], "score": 230 + trade_score,
                                "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                            })
                    actions.append({
                        "type": "ATTACK", "a_id": u["instance_id"], "t_type": "hero", "t_id": None, "score": 190,
                        "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "hero", "id": enemy_id}}
                    })

        # --- プレイ行動 ---
        for i, c in enumerate(active_hand):
            if c.get("cost", 99) > active_mp: continue
            c_type = c.get("type")
            eff = c.get("effect")
            cid = c.get("instance_id", f"dummy_{i}")

            if c_type == "unit" and len(active_board) < 7:
                score_mod = 350 - (c.get("cost", 0) * 10)
                if c.get("taunt"): score_mod += 80
                actions.append({"type": "PLAY", "h_idx": i, "t_type": None, "t_id": None, "score": score_mod, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": None}})
            elif c_type == "spell":
                if eff == "damage":
                    actions.append({"type": "PLAY", "h_idx": i, "t_type": "hero", "t_id": None, "score": 10, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "hero", "id": enemy_id}}})
                    for t in enemy_board:
                        actions.append({"type": "PLAY", "h_idx": i, "t_type": "unit", "t_id": t["instance_id"], "score": 160, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "unit", "id": t["instance_id"]}}})
                elif eff in ["assassinate", "burn"]:
                    for t in enemy_board:
                        score_boost = t.get("atk", 0) * 40 if eff == "assassinate" else 150
                        actions.append({"type": "PLAY", "h_idx": i, "t_type": "unit", "t_id": t["instance_id"], "score": score_boost, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "unit", "id": t["instance_id"]}}})
                elif eff == "aoe_damage":
                    actions.append({"type": "PLAY", "h_idx": i, "t_type": None, "t_id": None, "score": len(enemy_board) * 220, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": None}})
                elif eff == "wall":
                    for my_u in active_board:
                        actions.append({"type": "PLAY", "h_idx": i, "t_type": "unit", "t_id": my_u["instance_id"], "score": 180, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "unit", "id": my_u["instance_id"]}}})
                elif eff == "draw":
                    actions.append({"type": "PLAY", "h_idx": i, "t_type": None, "t_id": None, "score": 150, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": None}})

        actions.sort(key=lambda x: x.get("score", 0), reverse=True)
        return actions

    def step(self, action: dict, is_bot_turn: bool):
        act_type = action["type"]
        active_board = self.my_board if is_bot_turn else self.opp_board
        enemy_board = self.opp_board if is_bot_turn else self.my_board
        active_hand = self.my_hand if is_bot_turn else self.opp_hand
        active_deck = self.my_deck if is_bot_turn else self.opp_deck

        if act_type == "ATTACK":
            a_id = action["a_id"]
            t_type = action["t_type"]
            t_id = action["t_id"]

            attacker = next((u for u in active_board if u.get("instance_id") == a_id), None)
            if not attacker: return

            attacker["attacks_left"] = attacker.get("attacks_left", 1) - 1
            if attacker["attacks_left"] <= 0: attacker["can_attack"] = False
            atk_val = attacker.get("atk", 0)

            if t_type == "hero":
                if is_bot_turn: self.opp_hp -= atk_val
                else: self.my_hp -= atk_val
            elif t_type == "unit":
                defender = next((u for u in enemy_board if u.get("instance_id") == t_id), None)
                if not defender: return
                def_atk = defender.get("atk", 0) if not attacker.get("ranged") else 0

                dmg_to_def = max(0, atk_val - 1) if defender.get("wall_turns", 0) > 0 else atk_val
                dmg_to_atk = max(0, def_atk - 1) if attacker.get("wall_turns", 0) > 0 else def_atk
                defender["curr_hp"] -= dmg_to_def
                attacker["curr_hp"] -= dmg_to_atk

        elif act_type == "PLAY":
            h_idx = action["h_idx"]
            if h_idx >= len(active_hand): return
            card = active_hand.pop(h_idx)

            if is_bot_turn: self.my_mp -= card.get("cost", 0)
            else: self.opp_mp -= card.get("cost", 0)

            c_type = card.get("type")
            if c_type == "unit":
                active_board.append({
                    "instance_id": card.get("instance_id", "s"),
                    "atk": card.get("atk", 0), "curr_hp": card.get("hp", 1),
                    "can_attack": card.get("haste", False),
                    "taunt": card.get("taunt", False),
                    "attacks_left": card.get("max_attacks", 1),
                    "frozen_turns": 0, "wall_turns": 0
                })
            elif c_type == "spell":
                eff = card.get("effect")
                val = card.get("val", 0)
                t_type = action.get("t_type")
                t_id = action.get("t_id")

                if eff == "damage":
                    if t_type == "hero":
                        if is_bot_turn: self.opp_hp -= val
                        else: self.my_hp -= val
                    elif t_type == "unit":
                        tgt = next((u for u in enemy_board if u.get("instance_id") == t_id), None)
                        if tgt: tgt["curr_hp"] -= val
                elif eff == "assassinate":
                    tgt = next((u for u in enemy_board if u.get("instance_id") == t_id), None)
                    if tgt: tgt["curr_hp"] = 0
                elif eff == "wall":
                    tgt = next((u for u in active_board if u.get("instance_id") == t_id), None)
                    if tgt: tgt["wall_turns"] = 3
                elif eff == "aoe_damage":
                    for u in enemy_board: u["curr_hp"] -= val
                elif eff == "draw":
                    for _ in range(val):
                        if active_deck: active_hand.append(active_deck.pop(0))

        self.my_board = [u for u in self.my_board if u.get("curr_hp", 0) > 0]
        self.opp_board = [u for u in self.opp_board if u.get("curr_hp", 0) > 0]

class UltraTimeMinimaxSolver:
    def __init__(self, time_limit_sec=0.8):
        self.time_limit = time_limit_sec
        self.start_time = 0.0
        self.transposition_table = {}

    def search_best_moves(self, root_state: SimState) -> List[dict]:
        self.start_time = time.time()
        best_seq = []
        
        # 1秒制限の範囲内で限界まで深く先読み（深さ1から順に深める）
        for depth in range(1, 12):
            if time.time() - self.start_time > self.time_limit:
                break
            eval_val, seq = self.minimax(root_state, depth, -9999999.0, 9999999.0, True)
            if seq: best_seq = seq
            if eval_val > 800000.0: break # 100%勝ち確定なら即終了

        return [act["api"] for act in best_seq if act.get("type") != "END_TURN"]

    def minimax(self, state: SimState, depth: int, alpha: float, beta: float, is_max: bool) -> Tuple[float, List[dict]]:
        # 0.8秒超過チェック
        if time.time() - self.start_time > self.time_limit:
            return state.evaluate_state(is_my_turn_eval=True), []

        state_hash = state.get_hash() + f"|{depth}|{is_max}"
        if state_hash in self.transposition_table:
            return self.transposition_table[state_hash], []

        if depth == 0 or state.opp_hp <= 0 or state.my_hp <= 0:
            return state.evaluate_state(is_my_turn_eval=True), []

        legal_actions = state.get_legal_actions(is_bot_turn=is_max)
        legal_actions.append({"type": "END_TURN", "api": {"action": "END_TURN"}, "score": -100})

        best_seq = []

        if is_max:
            max_eval = -9999999.0
            for act in legal_actions[:12]:
                next_state = state.clone()
                if act["type"] == "END_TURN":
                    next_state.opp_mp = min(10, next_state.current_turn + 1)
                    if next_state.opp_deck: next_state.opp_hand.append(next_state.opp_deck.pop(0))
                    eval_val, _ = self.minimax(next_state, depth - 1, alpha, beta, False)
                else:
                    next_state.step(act, is_bot_turn=True)
                    eval_val, seq = self.minimax(next_state, depth, alpha, beta, True)

                if eval_val > max_eval:
                    max_eval = eval_val
                    best_seq = [act] + (seq if act["type"] != "END_TURN" else [])
                alpha = max(alpha, eval_val)
                if beta <= alpha: break
            
            self.transposition_table[state_hash] = max_eval
            return max_eval, best_seq
        else:
            min_eval = 9999999.0
            for act in legal_actions[:8]:
                next_state = state.clone()
                if act["type"] == "END_TURN":
                    next_state.my_mp = min(10, next_state.current_turn + 1)
                    next_state.current_turn += 1
                    if next_state.my_deck: next_state.my_hand.append(next_state.my_deck.pop(0))
                    eval_val, _ = self.minimax(next_state, depth - 1, alpha, beta, True)
                else:
                    next_state.step(act, is_bot_turn=False)
                    eval_val, seq = self.minimax(next_state, depth, False if act["type"] == "END_TURN" else False)

                if eval_val < min_eval: min_eval = eval_val
                beta = min(beta, eval_val)
                if beta <= alpha: break
                
            self.transposition_table[state_hash] = min_eval
            return min_eval, []

async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
        return

    # ドラフト（完璧な敵害妨害ピック）
    if session.status == "DRAFT":
        opts = session.draft_options.get(BOT_USER_ID, [])
        if opts:
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            opp_deck = session.decks.get(opp_id, []) if opp_id else []
            opp_card_ids = [c.get("id") for c in opp_deck]

            best_card, best_score = None, -9999

            for cid in opts:
                c_data = card_database.get(cid, {})
                base_score = CARD_SYNERGY_MATRIX.get(cid, {}).get("score", 50)
                
                # 相手がドラフトで欲しがりそうなパワカ・コンボパーツを「カット（横取り）」する妨害評価点
                if cid in ["s_05", "s_02", "u_01"]:
                    base_score += 150 # 超強力スペル・速攻を相手に渡さないカット補正

                c_cost = c_data.get("cost", 5)
                base_score += (6 - c_cost) * 20

                for opp_cid in opp_card_ids:
                    if opp_cid in CARD_SYNERGY_MATRIX.get(cid, {}).get("counters", []):
                        base_score += 100 

                if base_score > best_score:
                    best_score = base_score
                    best_card = cid

            await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card or opts[0]})
            await asyncio.sleep(0.01)

            if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                await process_super_ai_turn(session, card_database, process_action_func)
        return

    # バトルフェーズ
    if session.status == "BATTLE":
        opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
        if not opp_id: return

        state = SimState()
        state.my_hp = session.hp.get(BOT_USER_ID, 0)
        state.opp_hp = session.hp.get(opp_id, 0)
        state.my_mp = session.mp.get(BOT_USER_ID, 0)
        state.opp_mp = session.mp.get(opp_id, 0)
        state.my_id, state.opp_id = BOT_USER_ID, opp_id
        state.current_turn = session.max_mp.get(BOT_USER_ID, 1)

        state.my_board = [dict(u) for u in session.boards.get(BOT_USER_ID, [])]
        state.opp_board = [dict(u) for u in session.boards.get(opp_id, [])]
        
        state.my_hand = [dict(c) for c in session.hands.get(BOT_USER_ID, [])]
        state.opp_hand = [dict(c) for c in session.hands.get(opp_id, [])]
        state.my_deck = [dict(c) for c in session.decks.get(BOT_USER_ID, [])]
        state.opp_deck = [dict(c) for c in session.decks.get(opp_id, [])]

        # 0.8秒間限界まで先読み探索
        solver = UltraTimeMinimaxSolver(time_limit_sec=0.8)
        actions_to_execute = solver.search_best_moves(state)

        for act in actions_to_execute:
            if session.status != "BATTLE" or session.turn_user_id != BOT_USER_ID: break
            await process_action_func(session, BOT_USER_ID, act)
            await asyncio.sleep(0.01)

        if session.turn_user_id == BOT_USER_ID and session.status == "BATTLE":
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
