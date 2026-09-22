import asyncio
import copy
import logging
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("CardBot")

# ====================================================
# BOT基本設定
# ====================================================
BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "クソザコBOT"

# 基本Tier（ドラフト時の基礎点として使用）
BOT_CARD_TIER = {
    "s_05": 100, "u_06": 98, "s_02": 95, "s_01": 90,
    "u_02": 88,  "s_09": 85, "u_05": 80, "s_04": 75,
    "u_04": 70,  "u_01": 65, "s_07": 60, "s_08": 55,
    "u_03": 50,  "u_07": 30, "s_03": 25, "s_06": 10,
}

HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}

# ====================================================
# 内部シミュレータ（ターン内の全手順を読み切るための仮想空間）
# ====================================================
class SimState:
    def __init__(self):
        self.my_hp = 20
        self.opp_hp = 20
        self.my_mp = 1
        self.next_opp_mp = 1
        self.my_board = []
        self.opp_board = []
        self.my_hand = []
        self.opp_hand_ids = []
        self.my_id = ""
        self.opp_id = ""

    def clone(self):
        new_s = SimState()
        new_s.my_hp = self.my_hp
        new_s.opp_hp = self.opp_hp
        new_s.my_mp = self.my_mp
        new_s.next_opp_mp = self.next_opp_mp
        new_s.my_id = self.my_id
        new_s.opp_id = self.opp_id
        # 軽量コピーで高速化
        new_s.my_board = [dict(u) for u in self.my_board]
        new_s.opp_board = [dict(u) for u in self.opp_board]
        new_s.my_hand = [dict(c) for c in self.my_hand]
        new_s.opp_hand_ids = list(self.opp_hand_ids)
        return new_s

    def evaluate(self) -> int:
        # 1. 確定リーサルの無限スコア化
        if self.opp_hp <= 0: return 9999999
        if self.my_hp <= 0: return -9999999

        # 2. 基本HPと盤面スタッツの評価
        score = (20 - self.opp_hp) * 15 - (20 - self.my_hp) * 15
        
        my_board_atk = 0
        for u in self.my_board:
            score += u["atk"] * 3 + u["curr_hp"] * 2
            if u.get("taunt"): score += 12
            if u.get("haste") and u.get("can_attack"): score += 2
            my_board_atk += u["atk"]
            
        opp_board_atk = 0
        for u in self.opp_board:
            score -= u["atk"] * 3 + u["curr_hp"] * 2
            if u.get("taunt"): score -= 12
            opp_board_atk += u["atk"]

        # 3. 手札価値とマナ消費の評価
        score += len(self.my_hand) * 8
        score -= self.my_mp * 3 # マナを残さず使い切るほどスコアが高い

        # 4. 次ターン相手の反撃予測とケア（DPによる厳密カンニング）
        opp_spell_dmgs = []
        for cid in self.opp_hand_ids:
            if cid == "s_01": opp_spell_dmgs.append((2, 3))
            elif cid == "s_02": opp_spell_dmgs.append((4, 2))
            
        dp = [-1] * (self.next_opp_mp + 1)
        dp[0] = 0
        for cost, val in opp_spell_dmgs:
            for w in range(self.next_opp_mp, cost - 1, -1):
                if dp[w - cost] != -1:
                    dp[w] = max(dp[w], dp[w - cost] + val)
        max_opp_spell_dmg = max(dp)
        
        potential_opp_dmg = opp_board_atk + max_opp_spell_dmg
        if self.my_hp <= potential_opp_dmg:
            score -= 50000 # 死ぬ危険がある盤面を徹底的に避ける

        return score

    def get_legal_actions(self) -> List[dict]:
        actions = []
        opp_taunts = [u for u in self.opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]
        
        # --- ユニット攻撃の列挙 ---
        for i, u in enumerate(self.my_board):
            if u.get("can_attack") and u.get("frozen_turns", 0) == 0 and u.get("attacks_left", 0) > 0:
                if opp_taunts:
                    for j, t in enumerate(self.opp_board):
                        if t.get("taunt") and t.get("curr_hp", 0) > 0:
                            actions.append({
                                "type": "ATTACK", "a_idx": i, "t_type": "unit", "t_idx": j,
                                "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                            })
                else:
                    actions.append({
                        "type": "ATTACK", "a_idx": i, "t_type": "hero", "t_idx": None,
                        "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "hero", "id": self.opp_id}}
                    })
                    for j, t in enumerate(self.opp_board):
                        if t.get("curr_hp", 0) > 0:
                            actions.append({
                                "type": "ATTACK", "a_idx": i, "t_type": "unit", "t_idx": j,
                                "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                            })
                            
        # --- 手札プレイの列挙 ---
        for i, c in enumerate(self.my_hand):
            if c.get("cost", 99) > self.my_mp: continue
            
            c_type = c.get("type")
            eff = c.get("effect")
            cid = c["instance_id"]
            
            if c_type == "unit":
                if len(self.my_board) < 7:
                    actions.append({
                        "type": "PLAY", "h_idx": i, "t_type": None, "t_idx": None,
                        "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": None}
                    })
            elif c_type == "spell":
                if eff == "damage":
                    actions.append({
                        "type": "PLAY", "h_idx": i, "t_type": "hero", "t_idx": None,
                        "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "hero", "id": self.opp_id}}
                    })
                    for j, t in enumerate(self.opp_board):
                        actions.append({
                            "type": "PLAY", "h_idx": i, "t_type": "unit", "t_idx": j,
                            "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "unit", "id": t["instance_id"]}}
                        })
                elif eff in ["assassinate", "freeze", "burn"]:
                    for j, t in enumerate(self.opp_board):
                        actions.append({
                            "type": "PLAY", "h_idx": i, "t_type": "unit", "t_idx": j,
                            "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "unit", "id": t["instance_id"]}}
                        })
                elif eff == "wall":
                    for j, u in enumerate(self.my_board):
                        actions.append({
                            "type": "PLAY", "h_idx": i, "t_type": "unit", "t_idx": j,
                            "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "unit", "id": u["instance_id"]}}
                        })
                else: # AoE, Draw, Heal, Reshape
                    actions.append({
                        "type": "PLAY", "h_idx": i, "t_type": None, "t_idx": None,
                        "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": None}
                    })
        return actions

    def step(self, action: dict):
        act_type = action["type"]
        
        if act_type == "ATTACK":
            a_idx = action["a_idx"]
            t_type = action["t_type"]
            t_idx = action["t_idx"]
            
            attacker = self.my_board[a_idx]
            attacker["attacks_left"] -= 1
            if attacker["attacks_left"] <= 0:
                attacker["can_attack"] = False
                
            atk_val = attacker.get("atk", 0)
            
            if t_type == "hero":
                self.opp_hp -= atk_val
                if attacker.get("lifesteal"): self.my_hp = min(20, self.my_hp + atk_val)
            elif t_type == "unit":
                defender = self.opp_board[t_idx]
                def_atk = defender.get("atk", 0) if not attacker.get("ranged") else 0
                
                dmg_to_def = max(0, atk_val - 1) if defender.get("wall_turns", 0) > 0 else atk_val
                dmg_to_atk = max(0, def_atk - 1) if attacker.get("wall_turns", 0) > 0 else def_atk
                
                defender["curr_hp"] -= dmg_to_def
                attacker["curr_hp"] -= dmg_to_atk
                if attacker.get("lifesteal"): self.my_hp = min(20, self.my_hp + dmg_to_def)
                
        elif act_type == "PLAY":
            h_idx = action["h_idx"]
            card = self.my_hand.pop(h_idx)
            self.my_mp -= card["cost"]
            
            c_type = card.get("type")
            if c_type == "unit":
                atk = card.get("atk", 0)
                hp = card.get("hp", 1)
                if card.get("random_stat"): atk, hp = 3, 3 # 仮想期待値
                self.my_board.append({
                    "instance_id": card["instance_id"],
                    "atk": atk, "curr_hp": hp, "max_hp": hp,
                    "can_attack": card.get("haste", False),
                    "taunt": card.get("taunt", False),
                    "lifesteal": card.get("lifesteal", False),
                    "attacks_left": card.get("max_attacks", 1),
                    "ranged": card.get("ranged", False),
                    "frozen_turns": 0, "burn_turns": 0, "wall_turns": 0
                })
            elif c_type == "spell":
                eff = card.get("effect")
                val = card.get("val", 0)
                t_type = action.get("t_type")
                t_idx = action.get("t_idx")
                
                if eff == "damage":
                    if t_type == "hero": self.opp_hp -= val
                    elif t_type == "unit":
                        tgt = self.opp_board[t_idx]
                        tgt["curr_hp"] -= max(0, val - 1) if tgt.get("wall_turns", 0) > 0 else val
                elif eff == "aoe_damage":
                    for u in self.opp_board:
                        u["curr_hp"] -= max(0, val - 1) if u.get("wall_turns", 0) > 0 else val
                elif eff == "heal":
                    self.my_hp = min(20, self.my_hp + val)
                elif eff == "draw":
                    for _ in range(val): self.my_hand.append({"type": "dummy", "cost": 99})
                elif eff == "assassinate":
                    self.opp_board[t_idx]["curr_hp"] = 0
                elif eff == "reshape":
                    if self.my_hand: self.my_hand.pop(0)
                    self.my_hand.append({"type": "dummy", "cost": 99})
                elif eff == "freeze":
                    self.opp_board[t_idx]["frozen_turns"] = 1
                elif eff == "burn":
                    self.opp_board[t_idx]["burn_turns"] = 3
                elif eff == "wall":
                    self.my_board[t_idx]["wall_turns"] = 3

        # 死亡判定の即時適用
        self.my_board = [u for u in self.my_board if u["curr_hp"] > 0]
        self.opp_board = [u for u in self.opp_board if u["curr_hp"] > 0]


# ====================================================
# 深さ優先探索（DFS）によるコンボ最適化エンジン
# ====================================================
class DFSSolver:
    def __init__(self):
        self.max_nodes = 1500 # 爆速（約0.05秒以内）で完了する計算上限
        self.nodes_visited = 0
        
    def search(self, state: SimState, depth: int) -> Tuple[int, List[dict]]:
        self.nodes_visited += 1
        
        best_score = state.evaluate()
        best_seq = []
        
        # 探索制限（深さ6手、またはリーサル、または計算上限）
        if depth >= 6 or state.opp_hp <= 0 or self.nodes_visited >= self.max_nodes:
            return best_score, best_seq
            
        for act in state.get_legal_actions():
            next_state = state.clone()
            next_state.step(act)
            
            score, seq = self.search(next_state, depth + 1)
            
            if score > best_score:
                best_score = score
                best_seq = [act] + seq
                
        return best_score, best_seq


class SuperBotEngine:
    def __init__(self, session: Any, card_database: Dict[str, dict]):
        self.session = session
        self.db = card_database
        self.bot_id = BOT_USER_ID

    def plan_turn(self) -> List[dict]:
        opp_id = next((uid for uid in self.session.player_order if uid != self.bot_id), None)
        if not opp_id: return []

        state = SimState()
        state.my_hp = self.session.hp.get(self.bot_id, 0)
        state.opp_hp = self.session.hp.get(opp_id, 0)
        state.my_mp = self.session.mp.get(self.bot_id, 0)
        state.next_opp_mp = min(10, self.session.max_mp.get(opp_id, 1) + 1)
        state.my_id = self.bot_id
        state.opp_id = opp_id

        state.my_board = [dict(u) for u in self.session.boards.get(self.bot_id, [])]
        state.opp_board = [dict(u) for u in self.session.boards.get(opp_id, [])]
        state.my_hand = [dict(c) for c in self.session.hands.get(self.bot_id, [])]
        state.opp_hand_ids = [c.get("id") for c in self.session.hands.get(opp_id, [])]

        solver = DFSSolver()
        best_score, best_seq = solver.search(state, 0)

        # 抽出した最善手順のAPIペイロードだけを抽出して返す
        return [act["api"] for act in best_seq]


# ====================================================
# 外側から非同期で安全呼び出しするメインエントリー
# ====================================================
async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
        return

    bot_engine = SuperBotEngine(session, card_database)

    # -------------------------
    # ドラフトフェーズ
    # -------------------------
    if session.status == "DRAFT":
        opts = session.draft_options.get(BOT_USER_ID, [])
        if opts:
            my_deck = session.decks.get(BOT_USER_ID, [])
            my_deck_ids = [c.get("id") for c in my_deck]
            best_card, best_score = None, -1
            
            unit_count = sum(1 for c in my_deck if c.get("type") == "unit")
            spell_count = len(my_deck) - unit_count
            avg_cost = sum(c.get("cost", 0) for c in my_deck) / max(1, len(my_deck)) if my_deck else 3.0

            for cid in opts:
                c_data = card_database.get(cid, {})
                score = BOT_CARD_TIER.get(cid, 50)
                
                if cid == "s_09" and any(c.get("taunt") for c in my_deck): score += 25
                if cid == "u_06" and "u_02" in my_deck_ids: score += 15
                
                if c_data.get("type") == "unit" and unit_count < 4: score += 30
                if c_data.get("type") == "spell" and spell_count >= 3: score -= 30
                
                if avg_cost > 3.5 and c_data.get("cost", 0) <= 2: score += 20
                if avg_cost < 2.5 and c_data.get("cost", 0) >= 4: score += 15
                
                if score > best_score:
                    best_score, best_card = score, cid

            await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card or opts[0]})
            await asyncio.sleep(0.1)
            
            if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                await process_super_ai_turn(session, card_database, process_action_func)
        return

    # -------------------------
    # バトルフェーズ
    # -------------------------
    if session.status == "BATTLE":
        # 思考時間(0.01〜0.05秒)でターン内の完全な順序最適化を一括計算
        actions_to_execute = bot_engine.plan_turn()

        # 人間に絶望を刻み込むため、算出したコンボを0.15秒のウェイト付きで流麗に実行
        for act in actions_to_execute:
            if session.status != "BATTLE" or session.turn_user_id != BOT_USER_ID:
                break
            await process_action_func(session, BOT_USER_ID, act)
            await asyncio.sleep(0.15) 

        # 最後に確実にターンエンド
        if session.turn_user_id == BOT_USER_ID and session.status == "BATTLE":
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
