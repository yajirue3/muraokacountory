import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple
from itertools import combinations

logger = logging.getLogger("CardBot")

BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "村岡国王（影武者）"

HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}

# --- カード基本能力値＆評価ベース ---
CARD_SYNERGY_MATRIX = {
    "u_01": {"score": 300, "type": "unit", "cost": 1}, # 先鋒兵
    "u_02": {"score": 400, "type": "unit", "cost": 3}, # 重装兵（適正評価に修正）
    "u_03": {"score": 350, "type": "unit", "cost": 2}, # 魔導士
    "u_04": {"score": 500, "type": "unit", "cost": 6}, # 巨兵
    "u_05": {"score": 100, "type": "unit", "cost": 4},
    "u_06": {"score": 350, "type": "unit", "cost": 3}, # 小人
    "u_07": {"score": -999, "type": "unit", "cost": 1},
    
    "s_05": {"score": 500, "type": "spell", "cost": 6}, # 暗殺者
    "s_09": {"score": 400, "type": "spell", "cost": 2}, # 城壁
    "s_02": {"score": 450, "type": "spell", "cost": 4}, # 嵐
    "s_01": {"score": 10,  "type": "spell", "cost": 1},
    "s_03": {"score": 5,   "type": "spell", "cost": 1},
    "s_04": {"score": 10,  "type": "spell", "cost": 1},
    "s_06": {"score": 5,   "type": "spell", "cost": 1},
    "s_07": {"score": -99999, "type": "spell", "cost": 1},
    "s_08": {"score": 10,  "type": "spell", "cost": 1},
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
        return f"{self.my_hp}|{self.opp_hp}|{self.my_mp}|{self.opp_mp}|[{mb}]|[{ob}]|[{mh}]"

    def evaluate_state(self, is_my_turn_eval: bool = True) -> float:
        if self.opp_hp <= 0: return 9999999.0
        if self.my_hp <= 0: return -9999999.0

        score = 0.0

        # 1. 完全リーサルチェック
        my_total_atk = sum(u.get("atk", 0) for u in self.my_board if u.get("can_attack") or u.get("haste"))
        opp_taunts = [u for u in self.opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]
        
        if len(opp_taunts) == 0 and my_total_atk >= self.opp_hp:
            return 9000000.0 if is_my_turn_eval else -9000000.0

        # 2. 残MPペナルティ（手札にプレイ可能カードがあるのにMPを残した場合は強烈なマイナス）
        if is_my_turn_eval:
            playable = [c for c in self.my_hand if c.get("cost", 99) <= self.my_mp and c.get("id") != "s_07"]
            if playable and self.my_mp > 0 and len(self.my_board) < 7:
                score -= 50000.0 * self.my_mp

        # 3. 純粋な盤面スタッツ評価（総攻撃力＋総耐久力）
        my_board_atk = sum(u.get("atk", 0) for u in self.my_board)
        my_board_hp = sum(u.get("curr_hp", 0) for u in self.my_board)
        opp_board_atk = sum(u.get("atk", 0) for u in self.opp_board)
        opp_board_hp = sum(u.get("curr_hp", 0) for u in self.opp_board)

        score += (my_board_atk * 60 + my_board_hp * 40)
        score -= (opp_board_atk * 80 + opp_board_hp * 50)

        # 4. ライフアドバンテージ
        score += (20 - self.opp_hp) * 120.0
        score += self.my_hp * 15.0

        return score if is_my_turn_eval else -score

    def get_legal_actions(self, is_bot_turn: bool) -> List[dict]:
        actions = []
        active_board = self.my_board if is_bot_turn else self.opp_board
        enemy_board = self.opp_board if is_bot_turn else self.my_board
        active_hand = self.my_hand if is_bot_turn else self.opp_hand
        active_mp = self.my_mp if is_bot_turn else self.opp_mp
        enemy_id = self.opp_id if is_bot_turn else self.my_id
        enemy_hp = self.opp_hp if is_bot_turn else self.my_hp

        opp_taunts = [u for u in enemy_board if u.get("taunt") and u.get("curr_hp", 0) > 0]
        total_board_atk = sum(u.get("atk", 0) for u in active_board if u.get("can_attack") and u.get("frozen_turns", 0) == 0)

        # --- 1. 攻撃行動（最適判断） ---
        for u in active_board:
            if u.get("can_attack") and u.get("frozen_turns", 0) == 0 and u.get("attacks_left", 0) > 0:
                u_atk = u.get("atk", 0)
                u_hp = u.get("curr_hp", 0)

                if opp_taunts:
                    for t in opp_taunts:
                        actions.append({
                            "type": "ATTACK", "a_id": u["instance_id"], "t_type": "unit", "t_id": t["instance_id"], "score": 4000,
                            "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                        })
                else:
                    if total_board_atk >= enemy_hp:
                        actions.append({
                            "type": "ATTACK", "a_id": u["instance_id"], "t_type": "hero", "t_id": None, "score": 999999,
                            "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "hero", "id": enemy_id}}
                        })
                        continue

                    for t in enemy_board:
                        if t.get("curr_hp", 0) > 0:
                            t_atk = t.get("atk", 0)
                            t_hp = t.get("curr_hp", 0)
                            
                            # 神トレード（一方的に倒せる）
                            if u_atk >= t_hp and u_hp > t_atk:
                                actions.append({
                                    "type": "ATTACK", "a_id": u["instance_id"], "t_type": "unit", "t_id": t["instance_id"], "score": 3800,
                                    "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                                })
                            # 高脅威ユニット（攻撃力3以上）の処理
                            elif t_atk >= 3 and u_atk >= t_hp:
                                actions.append({
                                    "type": "ATTACK", "a_id": u["instance_id"], "t_type": "unit", "t_id": t["instance_id"], "score": 2800,
                                    "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                                })

                    # 顔面打点
                    hero_score = 2200
                    if enemy_hp <= 10: hero_score += 2000
                    actions.append({
                        "type": "ATTACK", "a_id": u["instance_id"], "t_type": "hero", "t_id": None, "score": hero_score,
                        "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "hero", "id": enemy_id}}
                    })

        # --- 2. 手札プレイ行動（現在のMPで出せるベストパフォーマンス） ---
        for i, c in enumerate(active_hand):
            c_cost = c.get("cost", 99)
            if c_cost > active_mp: continue
            
            cid_type = c.get("id") or c.get("card_id")
            c_type = c.get("type")
            eff = c.get("effect")
            inst_id = c.get("instance_id", f"dummy_{i}")

            if cid_type == "s_07": continue

            if c_type == "unit" and len(active_board) < 7:
                # 重装依存を排除し、マナコストとスタッツの効率（マナカーブ）で評価
                u_atk = c.get("atk", 1)
                u_hp = c.get("hp", 1)
                efficiency_score = (u_atk * 2 + u_hp) * 100
                
                score_mod = 1500 + efficiency_score
                
                # ピッタリマナを使い切れるプレイに加点
                if active_mp - c_cost == 0:
                    score_mod += 4000

                actions.append({
                    "type": "PLAY", "c_inst_id": inst_id, "t_type": None, "t_id": None, "score": score_mod,
                    "api": {"action": "PLAY_HAND", "card_instance_id": inst_id, "target": None}
                })

            elif c_type == "spell":
                if eff == "assassinate" or cid_type == "s_05":
                    for t in enemy_board:
                        t_cid = t.get("card_id")
                        ass_score = 2000
                        if t_cid == "u_04": ass_score = 7000
                        elif t.get("atk", 0) >= 3: ass_score = 4000

                        actions.append({
                            "type": "PLAY", "c_inst_id": inst_id, "t_type": "unit", "t_id": t["instance_id"], "score": ass_score,
                            "api": {"action": "PLAY_HAND", "card_instance_id": inst_id, "target": {"type": "unit", "id": t["instance_id"]}}
                        })

                elif eff == "wall" or cid_type == "s_09":
                    for my_u in active_board:
                        if my_u.get("wall_turns", 0) == 0:
                            score_wall = 2000 + (my_u.get("atk", 0) * 500) # 攻撃力が高いユニットを守る
                            actions.append({
                                "type": "PLAY", "c_inst_id": inst_id, "t_type": "unit", "t_id": my_u["instance_id"], "score": score_wall,
                                "api": {"action": "PLAY_HAND", "card_instance_id": inst_id, "target": {"type": "unit", "id": my_u["instance_id"]}}
                            })

                elif eff == "aoe_damage" or cid_type == "s_02":
                    if len(enemy_board) >= 2:
                        actions.append({
                            "type": "PLAY", "c_inst_id": inst_id, "t_type": None, "t_id": None, "score": 3500,
                            "api": {"action": "PLAY_HAND", "card_instance_id": inst_id, "target": None}
                        })

        actions.sort(key=lambda x: x.get("score", 0), reverse=True)
        return actions

    def step(self, action: dict, is_bot_turn: bool):
        act_type = action["type"]
        active_board = self.my_board if is_bot_turn else self.opp_board
        enemy_board = self.opp_board if is_bot_turn else self.my_board
        active_hand = self.my_hand if is_bot_turn else self.opp_hand

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
                def_atk = defender.get("atk", 0)

                dmg_to_def = max(0, atk_val - 1) if defender.get("wall_turns", 0) > 0 else atk_val
                dmg_to_atk = max(0, def_atk - 1) if attacker.get("wall_turns", 0) > 0 else def_atk
                defender["curr_hp"] -= dmg_to_def
                attacker["curr_hp"] -= dmg_to_atk

        elif act_type == "PLAY":
            c_inst_id = action.get("c_inst_id")
            card = next((c for c in active_hand if c.get("instance_id") == c_inst_id), None)
            if not card: return
            active_hand.remove(card)

            if is_bot_turn: self.my_mp -= card.get("cost", 0)
            else: self.opp_mp -= card.get("cost", 0)

            c_type = card.get("type")
            cid_type = card.get("id") or card.get("card_id")

            if c_type == "unit":
                max_atk_cnt = 3 if cid_type == "u_06" else 1
                active_board.append({
                    "instance_id": card.get("instance_id", "s"),
                    "card_id": cid_type,
                    "atk": card.get("atk", 0), "curr_hp": card.get("hp", 1),
                    "can_attack": card.get("haste", False),
                    "taunt": card.get("taunt", False),
                    "attacks_left": max_atk_cnt,
                    "frozen_turns": 0, "wall_turns": 0
                })
            elif c_type == "spell":
                eff = card.get("effect")
                val = card.get("val", 0)
                t_type = action.get("t_type")
                t_id = action.get("t_id")

                if eff == "assassinate":
                    tgt = next((u for u in enemy_board if u.get("instance_id") == t_id), None)
                    if tgt: tgt["curr_hp"] = 0
                elif eff == "wall":
                    tgt = next((u for u in active_board if u.get("instance_id") == t_id), None)
                    if tgt: tgt["wall_turns"] = 3
                elif eff == "aoe_damage":
                    for u in enemy_board: u["curr_hp"] -= val

        self.my_board = [u for u in self.my_board if u.get("curr_hp", 0) > 0]
        self.opp_board = [u for u in self.opp_board if u.get("curr_hp", 0) > 0]

class SuperMinimaxSolver:
    def __init__(self, max_time_nodes=30000):
        self.max_nodes = max_time_nodes
        self.nodes_visited = 0
        self.transposition_table = {}

    def search_best_moves(self, root_state: SimState) -> List[dict]:
        best_seq = []
        for depth in range(1, 6):
            if self.nodes_visited > self.max_nodes: break
            eval_val, seq = self.minimax(root_state, depth, -9999999.0, 9999999.0, True)
            if seq: best_seq = seq
            if eval_val > 8000000.0: break
        return [act["api"] for act in best_seq if act.get("type") != "END_TURN"]

    def minimax(self, state: SimState, depth: int, alpha: float, beta: float, is_max: bool) -> Tuple[float, List[dict]]:
        self.nodes_visited += 1

        state_hash = state.get_hash() + f"|{depth}|{is_max}"
        if state_hash in self.transposition_table:
            return self.transposition_table[state_hash], []

        if depth == 0 or state.opp_hp <= 0 or state.my_hp <= 0 or self.nodes_visited >= self.max_nodes:
            return state.evaluate_state(is_my_turn_eval=True), []

        legal_actions = state.get_legal_actions(is_bot_turn=is_max)
        legal_actions.append({"type": "END_TURN", "api": {"action": "END_TURN"}, "score": -100})

        best_seq = []

        if is_max:
            max_eval = -9999999.0
            for act in legal_actions[:10]:
                next_state = state.clone()
                if act["type"] == "END_TURN":
                    next_state.opp_mp = min(10, next_state.current_turn + 1)
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
            for act in legal_actions[:6]:
                next_state = state.clone()
                if act["type"] == "END_TURN":
                    next_state.my_mp = min(10, next_state.current_turn + 1)
                    next_state.current_turn += 1
                    eval_val, _ = self.minimax(next_state, depth - 1, alpha, beta, True)
                else:
                    next_state.step(act, is_bot_turn=False)
                    # 【完全修復】 alpha, beta, is_max(=False) をすべて正しく伝播
                    eval_val, _ = self.minimax(next_state, depth, alpha, beta, False)

                if eval_val < min_eval: min_eval = eval_val
                beta = min(beta, eval_val)
                if beta <= alpha: break
                
            self.transposition_table[state_hash] = min_eval
            return min_eval, []

async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    while session.status != "ENDED" and session.turn_user_id == BOT_USER_ID:
        
        # --- 戦略的ドラフトフェーズ（マナカーブ重視） ---
        if session.status == "DRAFT":
            opts = session.draft_options.get(BOT_USER_ID, [])
            if not opts: break

            my_deck = session.decks.get(BOT_USER_ID, [])
            my_card_ids = [c.get("id") for c in my_deck]

            low_cost_count = sum(1 for cid in my_card_ids if CARD_SYNERGY_MATRIX.get(cid, {}).get("cost", 5) <= 2)
            mid_cost_count = sum(1 for cid in my_card_ids if CARD_SYNERGY_MATRIX.get(cid, {}).get("cost", 5) in (3, 4))

            best_card, best_score = None, -99999

            for cid in opts:
                c_info = CARD_SYNERGY_MATRIX.get(cid, {})
                base_score = c_info.get("score", 50)
                c_cost = c_info.get("cost", 5)

                if cid == "s_07" or cid == "u_07":
                    base_score = -999999
                else:
                    # デッキのマナバランス（低コスト/中コスト）を均等に保つ評価
                    if low_cost_count < 3 and c_cost <= 2 and c_info.get("type") == "unit":
                        base_score += 350
                    elif mid_cost_count < 3 and c_cost in (3, 4) and c_info.get("type") == "unit":
                        base_score += 250

                if base_score > best_score:
                    best_score = base_score
                    best_card = cid

            await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card or opts[0]})
            await asyncio.sleep(0.01)
            continue

        # --- バトルフェーズ ---
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            if not opp_id: break

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

            solver = SuperMinimaxSolver()
            actions_to_execute = solver.search_best_moves(state)

            for act in actions_to_execute:
                if session.status != "BATTLE" or session.turn_user_id != BOT_USER_ID: break
                await process_action_func(session, BOT_USER_ID, act)
                await asyncio.sleep(0.01)

            if session.turn_user_id == BOT_USER_ID and session.status == "BATTLE":
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
            
            break
