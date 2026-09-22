import asyncio
import logging
import copy
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("CardBot")

# ====================================================
# BOT基本設定（絶対神モード）
# ====================================================
BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "村岡国王（代理）"

# メモリー：相手のドラフトピック履歴と山札順を完全に記録・透視
HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}

# カードの役割マトリクス（ドラフト時の完全カウンター用）
CARD_SYNERGY_MATRIX = {
    "u_01": {"score": 100, "counters": ["u_03", "u_06", "u_07"]}, # 速攻
    "s_02": {"score": 110, "counters": ["u_06", "u_01", "u_07"]}, # 全体2点（小粒キラー）
    "s_05": {"score": 95,  "counters": ["u_04", "u_05", "u_02"]}, # 暗殺（大型キラー）
    "s_09": {"score": 90,  "counters": ["u_01", "s_01"]},         # 城壁
    "s_01": {"score": 95,  "counters": ["u_03", "u_07"]},         # 雷撃
    "u_02": {"score": 85,  "counters": ["u_01"]},                 # 挑発
}

# ====================================================
# 完全情報・未来予知シミュレータ
# ====================================================
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
        self.my_deck = []   # 自分の山札（ドロー予測用）
        self.opp_deck = []  # 相手の山札（ドロー予測用）
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
        # 探索状態のキャッシュ用（置換表）
        mb = ",".join(f"{u.get('instance_id')}:{u.get('curr_hp')}:{u.get('attacks_left')}" for u in self.my_board)
        ob = ",".join(f"{u.get('instance_id')}:{u.get('curr_hp')}" for u in self.opp_board)
        return f"{self.my_hp}|{self.opp_hp}|{self.my_mp}|{self.opp_mp}|{mb}|{ob}|{len(self.my_hand)}|{len(self.opp_hand)}"

    def evaluate_state(self, is_my_turn_eval: bool = True) -> float:
        if self.opp_hp <= 0: return 9999999.0
        if self.my_hp <= 0: return -9999999.0

        score = 0.0
        
        # ライフアドバンテージ（相手が瀕死なら指数関数的に評価）
        if self.opp_hp <= 10:
            score += (20 - self.opp_hp) * 50.0
        else:
            score += (20 - self.opp_hp) * 15.0
        score += self.my_hp * 5.0

        # 盤面支配力計算
        my_total_atk = sum(u.get("atk", 0) for u in self.my_board)
        opp_total_atk = sum(u.get("atk", 0) for u in self.opp_board)

        for u in self.my_board:
            score += u.get("atk", 0) * 15.0 + u.get("curr_hp", 0) * 10.0
            if u.get("taunt"): score += 40.0
            if u.get("wall_turns", 0) > 0: score += 25.0

        for u in self.opp_board:
            score -= u.get("atk", 0) * 20.0 + u.get("curr_hp", 0) * 12.0
            if u.get("taunt"): score -= 45.0
            if u.get("wall_turns", 0) > 0: score -= 25.0

        if len(self.opp_board) == 0: score += 100.0

        # マナ効率（テンポロスを極端に嫌う）
        if is_my_turn_eval: score -= self.my_mp * 30.0

        score += len(self.my_hand) * 20.0
        score -= len(self.opp_hand) * 20.0

        # 次ターンリーサルの先読み評価
        if my_total_atk >= self.opp_hp: score += 100000.0

        return score if is_my_turn_eval else -score

    def get_legal_actions(self, is_bot_turn: bool) -> List[dict]:
        actions = []
        active_board = self.my_board if is_bot_turn else self.opp_board
        enemy_board = self.opp_board if is_bot_turn else self.my_board
        active_hand = self.my_hand if is_bot_turn else self.opp_hand
        active_mp = self.my_mp if is_bot_turn else self.opp_mp
        enemy_id = self.opp_id if is_bot_turn else self.my_id

        opp_taunts = [u for u in enemy_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

        # 攻撃行動
        for u in active_board:
            if u.get("can_attack") and u.get("frozen_turns", 0) == 0 and u.get("attacks_left", 0) > 0:
                if opp_taunts:
                    for t in enemy_board:
                        if t.get("taunt") and t.get("curr_hp", 0) > 0:
                            actions.append({
                                "type": "ATTACK", "a_id": u["instance_id"], "t_type": "unit", "t_id": t["instance_id"], "score": 100,
                                "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                            })
                else:
                    # 顔面優先
                    actions.append({
                        "type": "ATTACK", "a_id": u["instance_id"], "t_type": "hero", "t_id": None, "score": 200,
                        "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "hero", "id": enemy_id}}
                    })
                    # 有利トレード
                    for t in enemy_board:
                        if t.get("curr_hp", 0) > 0:
                            trade_score = t.get("atk",0)*10 - u.get("atk",0)*5
                            actions.append({
                                "type": "ATTACK", "a_id": u["instance_id"], "t_type": "unit", "t_id": t["instance_id"], "score": 50 + trade_score,
                                "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                            })

        # プレイ行動
        for i, c in enumerate(active_hand):
            if c.get("cost", 99) > active_mp: continue
            c_type = c.get("type")
            eff = c.get("effect")
            cid = c.get("instance_id", f"dummy_{i}")

            if c_type == "unit" and len(active_board) < 7:
                actions.append({"type": "PLAY", "h_idx": i, "t_type": None, "t_id": None, "score": 150 - c.get("cost",0)*10, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": None}})
            elif c_type == "spell":
                if eff == "damage":
                    actions.append({"type": "PLAY", "h_idx": i, "t_type": "hero", "t_id": None, "score": 120, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "hero", "id": enemy_id}}})
                    for t in enemy_board:
                        actions.append({"type": "PLAY", "h_idx": i, "t_type": "unit", "t_id": t["instance_id"], "score": 130, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "unit", "id": t["instance_id"]}}})
                elif eff in ["assassinate", "freeze", "burn"]:
                    for t in enemy_board:
                        score_boost = t.get("atk", 0)*20 if eff == "assassinate" else 80
                        actions.append({"type": "PLAY", "h_idx": i, "t_type": "unit", "t_id": t["instance_id"], "score": score_boost, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "unit", "id": t["instance_id"]}}})
                elif eff == "aoe_damage":
                    actions.append({"type": "PLAY", "h_idx": i, "t_type": None, "t_id": None, "score": len(enemy_board)*100, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": None}})
                elif eff == "draw":
                    actions.append({"type": "PLAY", "h_idx": i, "t_type": None, "t_id": None, "score": 180, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": None}})
                else:
                    actions.append({"type": "PLAY", "h_idx": i, "t_type": None, "t_id": None, "score": 50, "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": None}})

        # 行動のソート（Alpha-Beta剪枝の効率化）
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
                elif eff == "aoe_damage":
                    for u in enemy_board: u["curr_hp"] -= val
                elif eff == "draw":
                    # 山札完全透視によるドローシミュレーション
                    for _ in range(val):
                        if active_deck: active_hand.append(active_deck.pop(0))

        self.my_board = [u for u in self.my_board if u.get("curr_hp", 0) > 0]
        self.opp_board = [u for u in self.opp_board if u.get("curr_hp", 0) > 0]

# ====================================================
# 反復深化付き置換表ミニマックス（神の頭脳）
# ====================================================
class SuperMinimaxSolver:
    def __init__(self, max_time_nodes=4000):
        self.max_nodes = max_time_nodes
        self.nodes_visited = 0
        self.transposition_table = {}

    def search_best_moves(self, root_state: SimState) -> List[dict]:
        best_seq = []
        # 反復深化（時間許容内で深く読む）
        for depth in range(1, 4):
            if self.nodes_visited > self.max_nodes: break
            eval_val, seq = self.minimax(root_state, depth, -9999999.0, 9999999.0, True)
            if seq: best_seq = seq
            # 確殺が見つかったら探索終了
            if eval_val > 9000000.0: break
        return [act["api"] for act in best_seq if act.get("type") != "END_TURN"]

    def minimax(self, state: SimState, depth: int, alpha: float, beta: float, is_max: bool) -> Tuple[float, List[dict]]:
        self.nodes_visited += 1

        state_hash = state.get_hash() + str(depth) + str(is_max)
        if state_hash in self.transposition_table:
            return self.transposition_table[state_hash], []

        if depth == 0 or state.opp_hp <= 0 or state.my_hp <= 0 or self.nodes_visited >= self.max_nodes:
            return state.evaluate_state(is_my_turn_eval=True), []

        legal_actions = state.get_legal_actions(is_bot_turn=is_max)
        if not legal_actions or depth > 1:
            legal_actions.append({"type": "END_TURN", "api": {"action": "END_TURN"}, "score": -100})

        best_seq = []

        if is_max:
            max_eval = -9999999.0
            for act in legal_actions[:12]:
                next_state = state.clone()
                if act["type"] == "END_TURN":
                    next_state.opp_mp = min(10, next_state.current_turn + 1)
                    if next_state.opp_deck: next_state.opp_hand.append(next_state.opp_deck.pop(0)) # 相手の開始時ドロー透視
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
                    eval_val, _ = self.minimax(next_state, depth, alpha, beta, False)

                if eval_val < min_eval: min_eval = eval_val
                beta = min(beta, eval_val)
                if beta <= alpha: break
                
            self.transposition_table[state_hash] = min_eval
            return min_eval, []

# ====================================================
# エントリーポイント
# ====================================================
async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
        return

    # -------------------------
    # ドラフトフェーズ（完全カウンターピック）
    # -------------------------
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
                
                # 相手のデッキを透視し、ハードカウンターカードの価値を跳ね上げる
                for opp_cid in opp_card_ids:
                    if opp_cid in CARD_SYNERGY_MATRIX.get(cid, {}).get("counters", []):
                        base_score += 80 
                
                # 低コスト優先補正
                base_score += (10 - c_data.get("cost", 0)) * 5

                if base_score > best_score:
                    best_score = base_score
                    best_card = cid

            await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card or opts[0]})
            await asyncio.sleep(0.01) # 最速処理

            if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                await process_super_ai_turn(session, card_database, process_action_func)
        return

    # -------------------------
    # バトルフェーズ
    # -------------------------
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
        
        # 手札・山札の完全透視
        state.my_hand = [dict(c) for c in session.hands.get(BOT_USER_ID, [])]
        state.opp_hand = [dict(c) for c in session.hands.get(opp_id, [])]
        state.my_deck = [dict(c) for c in session.decks.get(BOT_USER_ID, [])]
        state.opp_deck = [dict(c) for c in session.decks.get(opp_id, [])]

        solver = SuperMinimaxSolver()
        actions_to_execute = solver.search_best_moves(state)

        for act in actions_to_execute:
            if session.status != "BATTLE" or session.turn_user_id != BOT_USER_ID: break
            await process_action_func(session, BOT_USER_ID, act)
            await asyncio.sleep(0.01) # 遅延をほぼゼロに（超高速連打）

        if session.turn_user_id == BOT_USER_ID and session.status == "BATTLE":
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
