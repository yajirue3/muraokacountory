import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("CardBot")

BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "村岡国王（影武者）"

HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}

# --- ドラフトシナジー＆基本評価マトリクス（超強気に調整） ---
CARD_SYNERGY_MATRIX = {
    # ユニット
    "u_01": {"score": 220, "counters": ["u_03", "u_06", "u_07"]}, # 先鋒兵 (1マナ速攻) - 数押しの神
    "u_02": {"score": 350, "counters": ["u_01"]},                 # 重装兵 (3マナ挑発) - 最優先コアユニット
    "u_03": {"score": 150, "counters": ["u_02"]},                 # 魔導士 (2マナ) - 序盤テンポ
    "u_04": {"score": 280, "counters": ["u_05"]},                 # 巨兵   (6マナ) - フィニッシャー
    "u_05": {"score": 100, "counters": []},                       # 大型ミニオン
    "u_06": {"score": 130, "counters": ["u_01"]},                 # 小人   (5マナ/3回攻撃)
    "u_07": {"score": 10,  "counters": []},                       # 奇術師 (4マナ運ゲー) - ほぼゴミ扱い
    
    # スペル
    "s_05": {"score": 400, "counters": ["u_04", "u_02"]},         # 暗殺者 (6マナ確定即死) - 神スペル
    "s_09": {"score": 380, "counters": ["u_01", "s_01"]},         # 城壁   (2マナ軽減) - 神スペル
    "s_02": {"score": 150, "counters": ["u_06", "u_01"]},         # 嵐     (4マナ全体) - 敵3体以上で発動
    "s_01": {"score": 15,  "counters": []},                       # 雷撃 - 基本ゴミ
    "s_03": {"score": 5,   "counters": []},                       # 治癒 - ゴミ
    "s_04": {"score": 10,  "counters": []},                       # 補充 - ゴミ
    "s_06": {"score": 5,   "counters": []},                       # 再編 - ゴミ
    "s_07": {"score": -99999, "counters": []},                   # 凍結 - バグのため絶対排除
    "s_08": {"score": 15,  "counters": []},                       # 火傷 - ゴミ
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

    def calculate_opp_max_threat(self) -> int:
        threat = sum(u.get("atk", 0) for u in self.opp_board)
        avail_mp = min(10, self.opp_mp + 1)
        for c in self.opp_hand:
            if c.get("cost", 99) <= avail_mp:
                if c.get("type") == "spell" and c.get("effect") == "damage":
                    threat += c.get("val", 0)
                elif c.get("type") == "unit" and c.get("haste"):
                    threat += c.get("atk", 0)
        return threat

    def evaluate_state(self, is_my_turn_eval: bool = True) -> float:
        if self.opp_hp <= 0: return 9999999.0
        if self.my_hp <= 0: return -9999999.0

        score = 0.0

        # リーサル（相手顔面を削り切れる状態）
        my_total_atk = sum(u.get("atk", 0) for u in self.my_board if u.get("can_attack") or u.get("haste"))
        if my_total_atk >= self.opp_hp:
            return 900000.0 if is_my_turn_eval else -900000.0

        # 相手からのリーサル脅威判定
        opp_threat = self.calculate_opp_max_threat()
        has_taunt = any(u.get("taunt") for u in self.my_board)
        if opp_threat >= self.my_hp and not has_taunt:
            score -= 300000.0

        # 「質より数」超強力加点
        score += len(self.my_board) * 120.0  # 盤面の数に超絶ボーナス

        for u in self.my_board:
            score += u.get("atk", 0) * 25.0 + u.get("curr_hp", 0) * 15.0
            if u.get("card_id") == "u_02": score += 150.0  # 重装兵が存在するだけで特大スコア
            if u.get("taunt"): score += 80.0
            if u.get("wall_turns", 0) > 0: score += 120.0  # 城壁状態は鉄壁

        for u in self.opp_board:
            score -= u.get("atk", 0) * 35.0 + u.get("curr_hp", 0) * 20.0
            if u.get("card_id") in ["u_02", "u_04"]: score -= 100.0 # 相手の重装兵・巨兵は危険視
            if u.get("taunt"): score -= 100.0

        score += (20 - self.opp_hp) * 50.0  # 相手HPを削っている評価を格段に引き上げ
        score += self.my_hp * 10.0
        score += len(self.my_hand) * 5.0

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
        # 1. 攻撃行動（重装兵は100%顔面 / その他はトレード or 顔面）
        # -------------------------------------------------------------
        for u in active_board:
            if u.get("can_attack") and u.get("frozen_turns", 0) == 0 and u.get("attacks_left", 0) > 0:
                is_u02 = (u.get("card_id") == "u_02")
                
                if opp_taunts:
                    # 相手の挑発だけは解除のために叩く
                    for t in opp_taunts:
                        actions.append({
                            "type": "ATTACK", "a_id": u["instance_id"], "t_type": "unit", "t_id": t["instance_id"], "score": 300,
                            "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                        })
                else:
                    # 重装兵（u_02）は絶対にユニットを殴らない（顔面100%）
                    if is_u02:
                        actions.append({
                            "type": "ATTACK", "a_id": u["instance_id"], "t_type": "hero", "t_id": None, "score": 800,
                            "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "hero", "id": enemy_id}}
                        })
                    else:
                        for t in enemy_board:
                            if t.get("curr_hp", 0) > 0:
                                trade_score = t.get("atk", 0) * 25 - u.get("atk", 0) * 2
                                actions.append({
                                    "type": "ATTACK", "a_id": u["instance_id"], "t_type": "unit", "t_id": t["instance_id"], "score": 150 + trade_score,
                                    "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                                })
                        # 相手ヒーロー顔面殴り
                        actions.append({
                            "type": "ATTACK", "a_id": u["instance_id"], "t_type": "hero", "t_id": None, "score": 400,
                            "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "hero", "id": enemy_id}}
                        })

        # -------------------------------------------------------------
        # 2. 手札プレイ行動（MP別優先度・城壁コンボ・即死爆撃）
        # -------------------------------------------------------------
        playable_units = [c for c in active_hand if c.get("type") == "unit" and c.get("cost", 99) <= active_mp]

        for i, c in enumerate(active_hand):
            c_cost = c.get("cost", 99)
            if c_cost > active_mp: continue
            
            cid_type = c.get("id") or c.get("card_id")
            c_type = c.get("type")
            eff = c.get("effect")
            inst_id = c.get("instance_id", f"dummy_{i}")

            # 凍結(s_07)は完全封印
            if cid_type == "s_07": continue

            # --- ユニット召喚 ---
            if c_type == "unit" and len(active_board) < 7:
                score_mod = 200
                # MP1〜5: 重装兵(u_02)最優先 ＋ 横展開
                if active_mp <= 5:
                    if cid_type == "u_02": score_mod = 1500  # 絶対に出す
                    elif cid_type == "u_01": score_mod = 600 # 1マナ速攻
                    elif cid_type == "u_03": score_mod = 500 # 2マナ展開
                    else: score_mod = 350 - (c_cost * 20)
                # MP6以上: 巨兵(u_04)最優先
                else:
                    if cid_type == "u_04": score_mod = 1200 # フィニッシャー降臨
                    elif cid_type == "u_02": score_mod = 800
                    else: score_mod = 300 - (c_cost * 10)

                # マナピッタリ消費にボーナス加点（MP無駄遣い防止）
                if active_mp - c_cost == 0: score_mod += 80

                actions.append({
                    "type": "PLAY", "c_inst_id": inst_id, "t_type": None, "t_id": None, "score": score_mod,
                    "api": {"action": "PLAY_HAND", "card_instance_id": inst_id, "target": None}
                })

            # --- スペルプレイ ---
            elif c_type == "spell":
                # 【城壁 s_09】: 重装兵へ最優先で重ねがけ（重複禁止）
                if eff == "wall" or cid_type == "s_09":
                    for my_u in active_board:
                        if my_u.get("wall_turns", 0) == 0: # 重複付与防止
                            score_wall = 2000 if my_u.get("card_id") == "u_02" else 400
                            actions.append({
                                "type": "PLAY", "c_inst_id": inst_id, "t_type": "unit", "t_id": my_u["instance_id"], "score": score_wall,
                                "api": {"action": "PLAY_HAND", "card_instance_id": inst_id, "target": {"type": "unit", "id": my_u["instance_id"]}}
                            })

                # 【暗殺者 s_05】: 手札の召喚ユニットが切れ、敵に「巨兵」か「重装兵」がいる時
                elif eff == "assassinate" or cid_type == "s_05":
                    has_target_big_unit = any(t.get("card_id") in ["u_04", "u_02"] for t in enemy_board)
                    if len(playable_units) == 0 and has_target_big_unit:
                        for t in enemy_board:
                            if t.get("card_id") in ["u_04", "u_02"]:
                                actions.append({
                                    "type": "PLAY", "c_inst_id": inst_id, "t_type": "unit", "t_id": t["instance_id"], "score": 1800,
                                    "api": {"action": "PLAY_HAND", "card_instance_id": inst_id, "target": {"type": "unit", "id": t["instance_id"]}}
                                })

                # 【嵐 s_02】: 相手盤面に3体以上並んだら一斉掃討
                elif eff == "aoe_damage" or cid_type == "s_02":
                    if len(enemy_board) >= 3:
                        actions.append({
                            "type": "PLAY", "c_inst_id": inst_id, "t_type": None, "t_id": None, "score": 1000,
                            "api": {"action": "PLAY_HAND", "card_instance_id": inst_id, "target": None}
                        })

                # その他のゴミスペル（低優先度）
                elif eff == "damage":
                    for t in enemy_board:
                        actions.append({
                            "type": "PLAY", "c_inst_id": inst_id, "t_type": "unit", "t_id": t["instance_id"], "score": 30,
                            "api": {"action": "PLAY_HAND", "card_instance_id": inst_id, "target": {"type": "unit", "id": t["instance_id"]}}
                        })

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
            c_inst_id = action.get("c_inst_id")
            card = next((c for c in active_hand if c.get("instance_id") == c_inst_id), None)
            if not card: return
            active_hand.remove(card)

            if is_bot_turn: self.my_mp -= card.get("cost", 0)
            else: self.opp_mp -= card.get("cost", 0)

            c_type = card.get("type")
            cid_type = card.get("id") or card.get("card_id")

            if c_type == "unit":
                max_atk_cnt = 3 if cid_type == "u_06" else 1 # u_06は3回攻撃
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

class SuperMinimaxSolver:
    def __init__(self, max_time_nodes=15000):
        self.max_nodes = max_time_nodes
        self.nodes_visited = 0
        self.transposition_table = {}

    def search_best_moves(self, root_state: SimState) -> List[dict]:
        best_seq = []
        for depth in range(1, 6): # 5手先まで高速探索
            if self.nodes_visited > self.max_nodes: break
            eval_val, seq = self.minimax(root_state, depth, -9999999.0, 9999999.0, True)
            if seq: best_seq = seq
            if eval_val > 800000.0: break # リーサル確定で終了
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
            for act in legal_actions[:15]:
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
            for act in legal_actions[:10]:
                next_state = state.clone()
                if act["type"] == "END_TURN":
                    next_state.my_mp = min(10, next_state.current_turn + 1)
                    next_state.current_turn += 1
                    if next_state.my_deck: next_state.my_hand.append(next_state.my_deck.pop(0))
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
    if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
        return

    # --- ドラフトフェーズ（数押し ＆ 神スペル超優先） ---
    if session.status == "DRAFT":
        opts = session.draft_options.get(BOT_USER_ID, [])
        if opts:
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            opp_deck = session.decks.get(opp_id, []) if opp_id else []
            opp_card_ids = [c.get("id") for c in opp_deck]

            best_card, best_score = None, -99999

            for cid in opts:
                c_data = card_database.get(cid, {})
                base_score = CARD_SYNERGY_MATRIX.get(cid, {}).get("score", 50)
                
                if cid == "s_07":
                    base_score = -999999 # 凍結は100%除外
                else:
                    c_cost = c_data.get("cost", 5)
                    # 低コストユニットは評価倍増（数を確保）
                    if c_data.get("type") == "unit":
                        base_score += (6 - c_cost) * 35

                for opp_cid in opp_card_ids:
                    if opp_cid in CARD_SYNERGY_MATRIX.get(cid, {}).get("counters", []):
                        base_score += 150 

                if base_score > best_score:
                    best_score = base_score
                    best_card = cid

            await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card or opts[0]})
            await asyncio.sleep(0.01)

            if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                await process_super_ai_turn(session, card_database, process_action_func)
        return

    # --- バトルフェーズ ---
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

        solver = SuperMinimaxSolver()
        actions_to_execute = solver.search_best_moves(state)

        for act in actions_to_execute:
            if session.status != "BATTLE" or session.turn_user_id != BOT_USER_ID: break
            await process_action_func(session, BOT_USER_ID, act)
            await asyncio.sleep(0.01)

        if session.turn_user_id == BOT_USER_ID and session.status == "BATTLE":
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
