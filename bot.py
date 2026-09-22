import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("CardBot")

BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "村岡国王（影武者）"

# --- ドラフトシナジー＆基本評価マトリクス ---
CARD_SYNERGY_MATRIX = {
    # ユニット
    "u_01": {"score": 220, "counters": ["u_03", "u_06", "u_07"]}, # 先鋒兵 (1マナ速攻)
    "u_02": {"score": 450, "counters": ["u_01"]},                 # 重装兵 (3マナ挑発) - 最核心
    "u_03": {"score": 150, "counters": ["u_02"]},                 # 魔導士
    "u_04": {"score": 280, "counters": ["u_05"]},                 # 巨兵   (6マナ)
    "u_05": {"score": 100, "counters": []},
    "u_06": {"score": 130, "counters": ["u_01"]},                 # 小人   (3回攻撃)
    "u_07": {"score": 10,  "counters": []},                       # 奇術師 (ゴミ)
    
    # スペル
    "s_05": {"score": 450, "counters": ["u_04", "u_02"]},         # 暗殺者 (6マナ確定即死)
    "s_09": {"score": 420, "counters": ["u_01", "s_01"]},         # 城壁   (2マナ軽減)
    "s_02": {"score": 200, "counters": ["u_06", "u_01"]},         # 嵐     (4マナ全体)
    "s_01": {"score": 15,  "counters": []},
    "s_03": {"score": 5,   "counters": []},
    "s_04": {"score": 10,  "counters": []},
    "s_06": {"score": 5,   "counters": []},
    "s_07": {"score": -99999, "counters": []},                   # 凍結 - バグ回避排除
    "s_08": {"score": 15,  "counters": []},
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

    def predict_opp_max_damage(self) -> int:
        """相手の次ターンの最大攻撃力を予測（自分が詰まないかの判定用）"""
        board_dmg = sum(u.get("atk", 0) for u in self.opp_board if u.get("frozen_turns", 0) == 0)
        avail_mp = min(10, self.opp_mp + 1)
        
        # 相手の手札からの追加最大バーストダメージ（仮定値）
        hand_burst = 0
        for c in self.opp_hand:
            c_cost = c.get("cost", 99)
            if c_cost <= avail_mp:
                if c.get("type") == "spell" and c.get("effect") == "damage":
                    hand_burst = max(hand_burst, c.get("val", 0))
                elif c.get("type") == "unit" and c.get("haste"):
                    hand_burst = max(hand_burst, c.get("atk", 0))
        
        return board_dmg + hand_burst

    def evaluate_state(self, is_my_turn_eval: bool = True) -> float:
        if self.opp_hp <= 0: return 9999999.0
        if self.my_hp <= 0: return -9999999.0

        score = 0.0

        # --- 1. 相手へのリーサル確認（完全撃破） ---
        my_total_atk = sum(u.get("atk", 0) for u in self.my_board if u.get("can_attack") or u.get("haste"))
        if my_total_atk >= self.opp_hp:
            return 900000.0 if is_my_turn_eval else -900000.0

        # --- 2. 「自分が詰まない」防衛評価最優先 ---
        opp_max_dmg = self.predict_opp_max_damage()
        has_taunt = any(u.get("taunt") for u in self.my_board)
        
        # 挑発（壁）がない状態で即死リスクがある場合は特大ペナルティ
        if opp_max_dmg >= self.my_hp:
            if not has_taunt:
                score -= 500000.0  # 超超絶危険
            else:
                score -= 50000.0   # 壁はあるが危険

        # --- 3. 盤面強度スコア ---
        for u in self.my_board:
            score += u.get("atk", 0) * 20.0 + u.get("curr_hp", 0) * 15.0
            if u.get("card_id") == "u_02": score += 200.0   # 重装兵の存在自体が価値
            if u.get("taunt"): score += 120.0
            if u.get("wall_turns", 0) > 0: score += 150.0   # 要塞（城壁付与）状態

        for u in self.opp_board:
            score -= u.get("atk", 0) * 30.0 + u.get("curr_hp", 0) * 15.0
            if u.get("card_id") in ["u_02", "u_04"]: score -= 120.0 # 危険敵へのヘイト値

        # --- 4. その他のリソース・HP評価 ---
        score += self.my_hp * 15.0
        score -= self.opp_hp * 10.0
        score += len(self.my_board) * 100.0  # 横展開（数の優位）
        score += len(self.my_hand) * 8.0     # 選択肢の広さ

        return score if is_my_turn_eval else -score

    def get_legal_actions(self, is_bot_turn: bool) -> List[dict]:
        actions = []
        active_board = self.my_board if is_bot_turn else self.opp_board
        enemy_board = self.opp_board if is_bot_turn else self.my_board
        active_hand = self.my_hand if is_bot_turn else self.opp_hand
        active_mp = self.my_mp if is_bot_turn else self.opp_mp
        enemy_id = self.opp_id if is_bot_turn else self.my_id

        opp_taunts = [u for u in enemy_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

        # -------------------------------------------------------------
        # 1. 攻撃行動（壁の処理優先 / 安全なら顔面）
        # -------------------------------------------------------------
        for u in active_board:
            if u.get("can_attack") and u.get("frozen_turns", 0) == 0 and u.get("attacks_left", 0) > 0:
                is_u02 = (u.get("card_id") == "u_02")
                
                if opp_taunts:
                    # 敵の挑発（要塞）解除を最優先
                    for t in opp_taunts:
                        actions.append({
                            "type": "ATTACK", "a_id": u["instance_id"], "t_type": "unit", "t_id": t["instance_id"], "score": 500,
                            "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                        })
                else:
                    if is_u02:
                        # 重装兵（u_02）は削り用に基本顔面攻撃
                        actions.append({
                            "type": "ATTACK", "a_id": u["instance_id"], "t_type": "hero", "t_id": None, "score": 800,
                            "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "hero", "id": enemy_id}}
                        })
                    else:
                        # 危険な敵アタッカーとの安全なトレード
                        for t in enemy_board:
                            if t.get("curr_hp", 0) > 0:
                                trade_score = t.get("atk", 0) * 30 - u.get("atk", 0) * 5
                                actions.append({
                                    "type": "ATTACK", "a_id": u["instance_id"], "t_type": "unit", "t_id": t["instance_id"], "score": 200 + trade_score,
                                    "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                                })
                        actions.append({
                            "type": "ATTACK", "a_id": u["instance_id"], "t_type": "hero", "t_id": None, "score": 350,
                            "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "hero", "id": enemy_id}}
                        })

        # -------------------------------------------------------------
        # 2. 手札プレイ行動（要塞構築・完全除去優先）
        # -------------------------------------------------------------
        playable_units = [c for c in active_hand if c.get("type") == "unit" and c.get("cost", 99) <= active_mp]

        for i, c in enumerate(active_hand):
            c_cost = c.get("cost", 99)
            if c_cost > active_mp: continue
            
            cid_type = c.get("id") or c.get("card_id")
            c_type = c.get("type")
            eff = c.get("effect")
            inst_id = c.get("instance_id", f"dummy_{i}")

            if cid_type == "s_07": continue # 凍結(s_07)は排除

            # --- ユニット展開 ---
            if c_type == "unit" and len(active_board) < 7:
                score_mod = 200
                if active_mp <= 5:
                    if cid_type == "u_02": score_mod = 1800  # 壁（最優先）
                    elif cid_type == "u_01": score_mod = 700
                    elif cid_type == "u_03": score_mod = 500
                else:
                    if cid_type == "u_04": score_mod = 1400 # フィニッシャー
                    elif cid_type == "u_02": score_mod = 1000

                if active_mp - c_cost == 0: score_mod += 100 # マナピッタリ消費ボーナス

                actions.append({
                    "type": "PLAY", "c_inst_id": inst_id, "t_type": None, "t_id": None, "score": score_mod,
                    "api": {"action": "PLAY_HAND", "card_instance_id": inst_id, "target": None}
                })

            # --- スペル利用 ---
            elif c_type == "spell":
                # 【城壁 s_09】: 重装兵へ優先で重ね掛け（要塞完成）
                if eff == "wall" or cid_type == "s_09":
                    for my_u in active_board:
                        if my_u.get("wall_turns", 0) == 0:
                            score_wall = 2500 if my_u.get("card_id") == "u_02" else 500
                            actions.append({
                                "type": "PLAY", "c_inst_id": inst_id, "t_type": "unit", "t_id": my_u["instance_id"], "score": score_wall,
                                "api": {"action": "PLAY_HAND", "card_instance_id": inst_id, "target": {"type": "unit", "id": my_u["instance_id"]}}
                            })

                # 【暗殺者 s_05】: 相手の脅威ユニットを消滅させる
                elif eff == "assassinate" or cid_type == "s_05":
                    for t in enemy_board:
                        if t.get("card_id") in ["u_04", "u_02"]:
                            actions.append({
                                "type": "PLAY", "c_inst_id": inst_id, "t_type": "unit", "t_id": t["instance_id"], "score": 2000,
                                "api": {"action": "PLAY_HAND", "card_instance_id": inst_id, "target": {"type": "unit", "id": t["instance_id"]}}
                            })

                # 【嵐 s_02】: 相手の数が3体以上で全体掃除
                elif eff == "aoe_damage" or cid_type == "s_02":
                    if len(enemy_board) >= 3:
                        actions.append({
                            "type": "PLAY", "c_inst_id": inst_id, "t_type": None, "t_id": None, "score": 1200,
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
    def __init__(self, max_time_nodes=10000):
        self.max_nodes = max_time_nodes
        self.nodes_visited = 0
        self.transposition_table = {}

    def search_best_moves(self, root_state: SimState) -> List[dict]:
        best_seq = []
        for depth in range(1, 5):
            if self.nodes_visited > self.max_nodes: break
            eval_val, seq = self.minimax(root_state, depth, -9999999.0, 9999999.0, True)
            if seq: best_seq = seq
            if eval_val > 800000.0: break # リーサル確定
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
            for act in legal_actions[:12]:
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
            for act in legal_actions[:8]:
                next_state = state.clone()
                if act["type"] == "END_TURN":
                    next_state.my_mp = min(10, next_state.current_turn + 1)
                    next_state.current_turn += 1
                    eval_val, _ = self.minimax(next_state, depth - 1, alpha, beta, True)
                else:
                    next_state.step(act, is_bot_turn=False)
                    eval_val, seq = self.minimax(next_state, depth, alpha, beta, False)

                if eval_val < min_eval: min_eval = eval_val
                beta = min(beta, eval_val)
                if beta <= alpha: break
                
            self.transposition_table[state_hash] = min_eval
            return min_eval, []

async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    """再帰エラーを回避し、安全に探索を行うメイン駆動ループ"""
    while session.status != "ENDED" and session.turn_user_id == BOT_USER_ID:
        
        # --- ドラフトフェーズ ---
        if session.status == "DRAFT":
            opts = session.draft_options.get(BOT_USER_ID, [])
            if not opts: break

            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            opp_deck = session.decks.get(opp_id, []) if opp_id else []
            opp_card_ids = [c.get("id") for c in opp_deck]

            best_card, best_score = None, -99999

            for cid in opts:
                c_data = card_database.get(cid, {})
                base_score = CARD_SYNERGY_MATRIX.get(cid, {}).get("score", 50)
                
                if cid == "s_07":
                    base_score = -999999 # 凍結は除外
                else:
                    c_cost = c_data.get("cost", 5)
                    if c_data.get("type") == "unit":
                        base_score += (6 - c_cost) * 35

                for opp_cid in opp_card_ids:
                    if opp_cid in CARD_SYNERGY_MATRIX.get(cid, {}).get("counters", []):
                        base_score += 150 

                if base_score > best_score:
                    best_score = base_score
                    best_card = cid

            await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card or opts[0]})
            await asyncio.sleep(0.05)
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
                if session.status != "BATTLE" or session.turn_user_id == BOT_USER_ID:
                    await process_action_func(session, BOT_USER_ID, act)
                    await asyncio.sleep(0.05)

            if session.turn_user_id == BOT_USER_ID and session.status == "BATTLE":
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
            
            break
