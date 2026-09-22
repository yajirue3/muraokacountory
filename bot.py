import asyncio
import logging
from typing import Dict, List, Any, Tuple

logger = logging.getLogger("CardBot")

BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "村岡国王（絶対不可侵）"

HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}

CARD_SYNERGY_MATRIX = {
    "u_01": {"score": 150, "counters": ["u_03", "u_06", "u_07"]},
    "s_02": {"score": 130, "counters": ["u_06", "u_01", "u_07"]},
    "s_05": {"score": 160, "counters": ["u_04", "u_05", "u_02"]},
    "s_09": {"score": 140, "counters": ["u_01", "s_01"]},
    "s_01": {"score": 40,  "counters": ["u_03", "u_07"]},
    "u_02": {"score": 130, "counters": ["u_01"]},
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

    def evaluate_state(self, is_my_turn_eval: bool = True) -> float:
        if self.opp_hp <= 0: return 999999.0
        if self.my_hp <= 0: return -999999.0

        score = 0.0
        my_total_atk = sum(u.get("atk", 0) for u in self.my_board if u.get("can_attack") or u.get("haste"))
        if my_total_atk >= self.opp_hp:
            return 90000.0 if is_my_turn_eval else -90000.0

        # 盤面の数（アグロ展開）の評価
        score += len(self.my_board) * 50.0

        for u in self.my_board:
            score += u.get("atk", 0) * 25.0 + u.get("curr_hp", 0) * 15.0
            if u.get("taunt"): score += 90.0

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

        # 攻撃
        for u in active_board:
            if u.get("can_attack") and u.get("frozen_turns", 0) == 0 and u.get("attacks_left", 0) > 0:
                if opp_taunts:
                    for t in opp_taunts:
                        actions.append({
                            "type": "ATTACK", "a_id": u["instance_id"], "score": 200,
                            "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                        })
                else:
                    for t in enemy_board:
                        if t.get("curr_hp", 0) > 0:
                            actions.append({
                                "type": "ATTACK", "a_id": u["instance_id"], "score": 230,
                                "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                            })
                    actions.append({
                        "type": "ATTACK", "a_id": u["instance_id"], "score": 190,
                        "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "hero", "id": enemy_id}}
                    })

        # プレイ（数重視：コストが低いほど優先して即出す）
        for i, c in enumerate(active_hand):
            if c.get("cost", 99) > active_mp: continue
            c_type = c.get("type")
            cid = c.get("instance_id", f"dummy_{i}")

            if c_type == "unit" and len(active_board) < 7:
                score_mod = 400 - (c.get("cost", 0) * 20) # 1マナなら380点、高コストは後回し
                actions.append({"type": "PLAY", "h_idx": i, "score": score_mod, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": None}})
            elif c_type == "spell":
                eff = c.get("effect")
                if eff == "damage":
                    for t in enemy_board:
                        actions.append({"type": "PLAY", "h_idx": i, "score": 160, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "unit", "id": t["instance_id"]}}})
                    actions.append({"type": "PLAY", "h_idx": i, "score": 100, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "hero", "id": enemy_id}}})
                elif eff in ["assassinate", "burn"]:
                    for t in enemy_board:
                        actions.append({"type": "PLAY", "h_idx": i, "score": 250, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "unit", "id": t["instance_id"]}}})
                elif eff == "aoe_damage":
                    actions.append({"type": "PLAY", "h_idx": i, "score": 300, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": None}})
                elif eff == "draw":
                    actions.append({"type": "PLAY", "h_idx": i, "score": 150, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": None}})

        actions.sort(key=lambda x: x.get("score", 0), reverse=True)
        return actions

    def step(self, action: dict, is_bot_turn: bool):
        act_type = action["type"]
        active_board = self.my_board if is_bot_turn else self.opp_board
        enemy_board = self.opp_board if is_bot_turn else self.my_board
        active_hand = self.my_hand if is_bot_turn else self.opp_hand
        active_deck = self.my_deck if is_bot_turn else self.opp_deck

        if act_type == "ATTACK":
            a_id = action.get("a_id")
            api_target = action["api"].get("target", {})
            t_type = api_target.get("type")
            t_id = api_target.get("id")

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
                defender["curr_hp"] -= atk_val
                attacker["curr_hp"] -= defender.get("atk", 0)

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
                    "attacks_left": 1, "frozen_turns": 0, "wall_turns": 0
                })

        self.my_board = [u for u in self.my_board if u.get("curr_hp", 0) > 0]
        self.opp_board = [u for u in self.opp_board if u.get("curr_hp", 0) > 0]

class FastMinimaxSolver:
    def search_best_moves(self, root_state: SimState) -> List[dict]:
        # 爆速で3手先まで確実に行動列を生成
        _, best_seq = self.minimax(root_state, 3, -999999.0, 999999.0, True)
        return [act["api"] for act in best_seq if act.get("type") != "END_TURN"]

    def minimax(self, state: SimState, depth: int, alpha: float, beta: float, is_max: bool) -> Tuple[float, List[dict]]:
        if depth == 0 or state.opp_hp <= 0 or state.my_hp <= 0:
            return state.evaluate_state(is_my_turn_eval=True), []

        legal_actions = state.get_legal_actions(is_bot_turn=is_max)
        legal_actions.append({"type": "END_TURN", "api": {"action": "END_TURN"}, "score": -100})

        best_seq = []

        if is_max:
            max_eval = -999999.0
            for act in legal_actions[:8]: # 上位8手に絞ることで超高速化
                next_state = state.clone()
                if act["type"] == "END_TURN":
                    eval_val, _ = self.minimax(next_state, depth - 1, alpha, beta, False)
                else:
                    next_state.step(act, is_bot_turn=True)
                    eval_val, seq = self.minimax(next_state, depth - 1, alpha, beta, True)

                if eval_val > max_eval:
                    max_eval = eval_val
                    best_seq = [act] + (seq if act["type"] != "END_TURN" else [])
                alpha = max(alpha, eval_val)
                if beta <= alpha: break
            return max_eval, best_seq
        else:
            min_eval = 999999.0
            for act in legal_actions[:6]:
                next_state = state.clone()
                if act["type"] == "END_TURN":
                    eval_val, _ = self.minimax(next_state, depth - 1, alpha, beta, True)
                else:
                    next_state.step(act, is_bot_turn=False)
                    eval_val, seq = self.minimax(next_state, depth - 1, alpha, beta, False)

                if eval_val < min_eval:
                    min_eval = eval_val
                beta = min(beta, eval_val)
                if beta <= alpha: break
            return min_eval, []

async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
        return

    # ドラフト
    if session.status == "DRAFT":
        opts = session.draft_options.get(BOT_USER_ID, [])
        if opts:
            best_card = opts[0]
            best_score = -9999
            for cid in opts:
                c_data = card_database.get(cid, {})
                score = CARD_SYNERGY_MATRIX.get(cid, {}).get("score", 50)
                score += (6 - c_data.get("cost", 3)) * 30 # 低コストを最優先ピック
                if score > best_score:
                    best_score = score
                    best_card = cid

            await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})
            await asyncio.sleep(0.01)

            if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                await process_super_ai_turn(session, card_database, process_action_func)
        return

    # バトルフェーズ
    if session.status == "BATTLE":
        opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
        if not opp_id: return

        state = SimState()
        state.my_hp = session.hp.get(BOT_USER_ID, 20)
        state.opp_hp = session.hp.get(opp_id, 20)
        state.my_mp = session.mp.get(BOT_USER_ID, 1)
        state.opp_mp = session.mp.get(opp_id, 1)
        state.my_id, state.opp_id = BOT_USER_ID, opp_id

        state.my_board = [dict(u) for u in session.boards.get(BOT_USER_ID, [])]
        state.opp_board = [dict(u) for u in session.boards.get(opp_id, [])]
        state.my_hand = [dict(c) for c in session.hands.get(BOT_USER_ID, [])]
        state.opp_hand = [dict(c) for c in session.hands.get(opp_id, [])]

        solver = FastMinimaxSolver()
        actions_to_execute = solver.search_best_moves(state)

        for act in actions_to_execute:
            if session.status != "BATTLE" or session.turn_user_id != BOT_USER_ID: break
            await process_action_func(session, BOT_USER_ID, act)
            await asyncio.sleep(0.01)

        # 確実にターンエンドを執行
        if session.turn_user_id == BOT_USER_ID and session.status == "BATTLE":
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
