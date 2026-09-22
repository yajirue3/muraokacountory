import asyncio
import logging
import copy
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("CardBot")

BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "村岡国王（影武者）"

HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}

# --- カードデータベース（マスター定義） ---
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
    "s_07": {"id": "s_07", "name": "凍結", "type": "spell", "cost": 1, "effect": "freeze", "need_target": True}, # バグカード：評価-9999
    "s_08": {"id": "s_08", "name": "火傷", "type": "spell", "cost": 1, "effect": "burn", "need_target": True},
    "s_09": {"id": "s_09", "name": "城壁", "type": "spell", "cost": 2, "effect": "wall", "need_target": True},
}

class SimState:
    """If探索ツリー用・シミュレーション盤面オブジェクト"""
    def __init__(self):
        self.my_hp = 20
        self.opp_hp = 20
        self.my_mp = 1
        self.opp_mp = 1
        self.my_board: List[dict] = []
        self.opp_board: List[dict] = []
        self.my_hand: List[dict] = []
        self.opp_hand: List[dict] = []
        self.my_deck: List[dict] = []
        self.opp_deck: List[dict] = []
        self.my_id = ""
        self.opp_id = ""
        self.current_turn = 1

    def clone(self) -> 'SimState':
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

    def get_hash(() -> str:
        mb = ",".join(f"{u.get('instance_id')}:{u.get('curr_hp')}:{u.get('attacks_left')}:{u.get('wall_turns',0)}" for u in self.my_board)
        ob = ",".join(f"{u.get('instance_id')}:{u.get('curr_hp')}:{u.get('wall_turns',0)}" for u in self.opp_board)
        mh = ",".join(c.get("instance_id", "") for c in self.my_hand)
        return f"{self.my_hp}|{self.opp_hp}|{self.my_mp}|[{mb}]|[{ob}]|[{mh}]"

    def calculate_opp_max_threat(self) -> int:
        """相手の次ターン最大火力を推測（生存モード判定用）"""
        threat = sum(u.get("atk", 0) for u in self.opp_board)
        avail_mp = min(10, self.opp_mp + 1)
        for c in self.opp_hand:
            if c.get("cost", 99) <= avail_mp:
                if c.get("type") == "spell" and c.get("effect") in ["damage", "aoe_damage"]:
                    threat += c.get("val", 0)
                elif c.get("type") == "unit" and c.get("haste"):
                    threat += c.get("atk", 0)
        return threat

    def evaluate_state(self, is_my_turn_eval: bool = True) -> float:
        """Ifツリー評価関数（盤面状態の数値化）"""
        if self.opp_hp <= 0: return 9999999.0
        if self.my_hp <= 0: return -9999999.0

        score = 0.0

        # --- 1. 確定リーサル判定 ---
        my_total_atk = sum(u.get("atk", 0) for u in self.my_board if (u.get("can_attack") or u.get("haste")) and u.get("attacks_left", 0) > 0)
        if my_total_atk >= self.opp_hp:
            return 900000.0 if is_my_turn_eval else -900000.0

        # --- 2. 生存・即死警戒（緊急回避モード） ---
        opp_threat = self.calculate_opp_max_threat()
        has_taunt = any(u.get("taunt") for u in self.my_board)
        if opp_threat >= self.my_hp and not has_taunt:
            score -= 200000.0

        # --- 3. スペル < ユニット：盤面のコマ数（数）を爆発的評価 ---
        score += len(self.my_board) * 1000.0 # 盤面にコマを並べることが最優先

        # 自陣ユニットのステータス評価
        for u in self.my_board:
            score += u.get("atk", 0) * 30.0 + u.get("curr_hp", 0) * 15.0
            if u.get("taunt"): score += 120.0
            if u.get("wall_turns", 0) > 0: score += 80.0
            # 強烈シナジー：小人(u_06) + 城壁
            if u.get("card_id") == "u_06" and u.get("wall_turns", 0) > 0:
                score += 2000.0

        # 敵陣ユニットのマイナス評価
        for u in self.opp_board:
            score -= u.get("atk", 0) * 40.0 + u.get("curr_hp", 0) * 20.0
            if u.get("taunt"): score -= 150.0
            if u.get("card_id") in ["u_03", "u_06", "u_05"]: # 危険ユニット
                score -= 300.0

        # --- 4. マナ使い切り（毎ターン全回復の活用） ---
        score -= self.my_mp * 100.0 # MP余りペナルティ

        # HP差アドバンテージ
        score += (20 - self.opp_hp) * 40.0
        score += self.my_hp * 15.0
        score += len(self.my_hand) * 20.0

        return score if is_my_turn_eval else -score

    def get_legal_actions(self, is_bot_turn: bool) -> List[dict]:
        """無駄手・バグ手を絶対排除した合法手リスト生成"""
        actions = []
        active_board = self.my_board if is_bot_turn else self.opp_board
        enemy_board = self.opp_board if is_bot_turn else self.my_board
        active_hand = self.my_hand if is_bot_turn else self.opp_hand
        active_mp = self.my_mp if is_bot_turn else self.opp_mp
        enemy_id = self.opp_id if is_bot_turn else self.my_id

        opp_taunts = [u for u in enemy_board if u.get("taunt") and u.get("curr_hp", 0) > 0]

        # ==========================================
        # 1. 攻撃行動（DECLARE_ATTACK）
        # ==========================================
        for u in active_board:
            if not u.get("can_attack") or u.get("frozen_turns", 0) > 0 or u.get("attacks_left", 0) <= 0:
                continue

            u_atk = u.get("atk", 0)
            u_cid = u.get("card_id", "")

            # 【Ifルール】重装兵(u_02)は反撃ダメ回避のため敵ユニット攻撃禁止（顔面専用）
            if u_cid == "u_02" and enemy_board and not opp_taunts:
                continue

            # 挑発がいる場合は挑発のみターゲット可能
            targets = opp_taunts if opp_taunts else enemy_board

            for t in targets:
                t_hp = t.get("curr_hp", 0)
                if t_hp <= 0: continue

                # 【Ifルール】城壁持ちへの「攻撃力1以下」はダメージ0で無駄なため攻撃禁止
                if t.get("wall_turns", 0) > 0 and u_atk <= 1:
                    continue

                # 有利トレード・トレード評価
                trade_score = 0
                eff_dmg = u_atk - 1 if t.get("wall_turns", 0) > 0 else u_atk
                if eff_dmg >= t_hp and u.get("curr_hp", 0) > t.get("atk", 0):
                    trade_score += 500 # 一方勝ち
                elif t.get("card_id") in ["u_03", "u_06", "u_05"]:
                    trade_score += 400 # 危険敵処理

                actions.append({
                    "type": "ATTACK", "a_id": u["instance_id"], "t_type": "unit", "t_id": t["instance_id"],
                    "score": 300 + trade_score,
                    "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                })

            # 挑発がいない場合、ヒーロー（顔面）攻撃
            if not opp_taunts:
                actions.append({
                    "type": "ATTACK", "a_id": u["instance_id"], "t_type": "hero", "t_id": None,
                    "score": 250,
                    "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "hero", "id": enemy_id}}
                })

        # ==========================================
        # 2. プレイ行動（PLAY_HAND）
        # ==========================================
        for i, c in enumerate(active_hand):
            cost = c.get("cost", 99)
            if cost > active_mp: continue

            c_type = c.get("type")
            cid = c.get("id")
            c_inst = c.get("instance_id", f"dummy_{i}")

            # 【Ifルール】バグカード s_07 (凍結) は絶対プレイしない
            if cid == "s_07": continue

            # --- ユニット（最優先展開） ---
            if c_type == "unit" and len(active_board) < 7:
                # 「スペル < ユニット」：極大加点で手札から即召喚
                score_mod = 1500 - (cost * 10)
                if c.get("taunt"): score_mod += 200
                if c.get("haste"): score_mod += 150

                actions.append({
                    "type": "PLAY", "h_idx": i, "t_type": None, "t_id": None, "score": score_mod,
                    "api": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                })

            # --- スペル ---
            elif c_type == "spell":
                eff = c.get("effect")

                if eff == "damage": # s_01 雷撃など
                    for t in enemy_board:
                        actions.append({
                            "type": "PLAY", "h_idx": i, "t_type": "unit", "t_id": t["instance_id"], "score": 200 + t.get("atk", 0) * 20,
                            "api": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                        })
                elif eff == "assassinate": # s_05 暗殺者
                    for t in enemy_board:
                        actions.append({
                            "type": "PLAY", "h_idx": i, "t_type": "unit", "t_id": t["instance_id"], "score": 300 + t.get("atk", 0) * 40,
                            "api": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": t["instance_id"]}}
                        })
                elif eff == "aoe_damage": # s_02 嵐 (敵が2体以上で発動)
                    if len(enemy_board) >= 2:
                        actions.append({
                            "type": "PLAY", "h_idx": i, "t_type": None, "t_id": None, "score": 400 + len(enemy_board) * 100,
                            "api": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                        })
                elif eff == "wall": # s_09 城壁
                    for my_u in active_board:
                        # 小人(u_06)への付与を爆発的評価
                        b_score = 1500 if my_u.get("card_id") == "u_06" else 300
                        actions.append({
                            "type": "PLAY", "h_idx": i, "t_type": "unit", "t_id": my_u["instance_id"], "score": b_score,
                            "api": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": {"type": "unit", "id": my_u["instance_id"]}}
                        })
                elif eff == "draw": # s_04 補充
                    actions.append({
                        "type": "PLAY", "h_idx": i, "t_type": None, "t_id": None, "score": 180,
                        "api": {"action": "PLAY_HAND", "card_instance_id": c_inst, "target": None}
                    })

        actions.sort(key=lambda x: x.get("score", 0), reverse=True)
        return actions

    def step(self, action: dict, is_bot_turn: bool):
        """シミュレーション盤面の更新"""
        act_type = action["type"]
        active_board = self.my_board if is_bot_turn else self.opp_board
        enemy_board = self.opp_board if is_bot_turn else self.my_board
        active_hand = self.my_hand if is_bot_turn else self.opp_hand
        active_deck = self.my_deck if is_bot_turn else self.opp_deck

        if act_type == "ATTACK":
            a_id, t_type, t_id = action["a_id"], action["t_type"], action["t_id"]
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
                    "instance_id": card.get("instance_id", "inst"),
                    "card_id": card.get("id"),
                    "atk": card.get("atk", 0), "curr_hp": card.get("hp", 1),
                    "can_attack": card.get("haste", False),
                    "taunt": card.get("taunt", False),
                    "ranged": card.get("ranged", False),
                    "attacks_left": card.get("max_attacks", 1),
                    "frozen_turns": 0, "wall_turns": 0
                })
            elif c_type == "spell":
                eff, val = card.get("effect"), card.get("val", 0)
                t_type, t_id = action.get("t_type"), action.get("t_id")

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
    """Ifアルファベータ探索ソルバー"""
    def __init__(self, max_nodes=5000):
        self.max_nodes = max_nodes
        self.nodes_visited = 0
        self.transposition_table = {}

    def search_best_moves(self, root_state: SimState) -> List[dict]:
        best_seq = []
        for depth in range(1, 4):
            if self.nodes_visited > self.max_nodes: break
            eval_val, seq = self.minimax(root_state, depth, -9999999.0, 9999999.0, True)
            if seq: best_seq = seq
            if eval_val > 800000.0: break # 確定勝利があれば即決定
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
            for act in legal_actions[:12]: # 探索幅制限で高速化
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
                    eval_val, seq = self.minimax(next_state, depth, alpha, beta, False)

                if eval_val < min_eval: min_eval = eval_val
                beta = min(beta, eval_val)
                if beta <= alpha: break
                
            self.transposition_table[state_hash] = min_eval
            return min_eval, []


async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    """
    メイン制御エントリーポイント
    エラー・フリーズを100%防ぐための例外安全ガード付き
    """
    try:
        if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
            return

        # ==========================================
        # 1. ドラフトフェーズの If 探索ロジック
        # ==========================================
        if session.status == "DRAFT":
            opts = session.draft_options.get(BOT_USER_ID, [])
            if opts:
                my_deck = session.decks.get(BOT_USER_ID, [])
                my_card_ids = [c.get("id") if isinstance(c, dict) else c for c in my_deck]

                best_card, best_priority = opts[0], -9999

                # 現状況の枚数集計
                unit_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("type") == "unit")
                spell_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("type") == "spell")
                low_cost_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("cost", 99) <= 2)
                high_cost_count = sum(1 for cid in my_card_ids if BOT_CARD_DB.get(cid, {}).get("cost", 0) >= 6)

                for cid in opts:
                    c_info = BOT_CARD_DB.get(cid, {})
                    p = 0

                    # 【Ifルール】バグカード s_07 (凍結) は -9999 で即時除外
                    if cid == "s_07":
                        p -= 9999

                    # スペル < ユニット絶対原則
                    if c_info.get("type") == "unit":
                        p += 800
                        p += c_info.get("atk", 0) * 50 + c_info.get("hp", 0) * 30
                    else:
                        p += 200

                    # スペル過多（30%超え）の強制制限
                    if c_info.get("type") == "spell" and spell_count >= 3:
                        p -= 2000

                    # マナカーブ制御
                    c_cost = c_info.get("cost", 0)
                    if low_cost_count < 2 and c_cost <= 2: p += 500 # 低コスト優先
                    if high_cost_count >= 2 and c_cost >= 6: p -= 1500 # 高コスト制限

                    # シナジー：小人(u_06)保有時の城壁(s_09)爆発的加点
                    if cid == "s_09" and "u_06" in my_card_ids: p += 1000

                    if p > best_priority:
                        best_priority = p
                        best_card = cid

                await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card})
                await asyncio.sleep(0.01)

                if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                    await process_super_ai_turn(session, card_database, process_action_func)
            return

        # ==========================================
        # 2. バトルフェーズの If 探索ロジック
        # ==========================================
        if session.status == "BATTLE":
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            if not opp_id: return

            state = SimState()
            state.my_hp = session.hp.get(BOT_USER_ID, 20)
            state.opp_hp = session.hp.get(opp_id, 20)
            state.my_mp = session.mp.get(BOT_USER_ID, 1)
            state.opp_mp = session.mp.get(opp_id, 1)
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

            # 行動の実行
            executed_any = False
            for act in actions_to_execute:
                if session.status != "BATTLE" or session.turn_user_id != BOT_USER_ID: break
                await process_action_func(session, BOT_USER_ID, act)
                executed_any = True
                await asyncio.sleep(0.01)

            # 安全装置（万が一何もしなかった場合のフォールバック：即時ターン終了でフリーズ回避）
            if session.turn_user_id == BOT_USER_ID and session.status == "BATTLE":
                await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})

    except Exception as e:
        logger.error(f"Super AI Error Fallback: {e}", exc_info=True)
        # エラー発生時も止まらず即座にターン終了して対戦進行を維持
        if session.status == "BATTLE" and session.turn_user_id == BOT_USER_ID:
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
