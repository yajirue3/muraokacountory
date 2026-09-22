import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("CardBot")

# ====================================================
# BOT基本設定
# ====================================================
BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "【絶望】絶対勝てないBOT"

# イカサマなしで最強のテンポ・コントロールを押し付ける極悪Tier
BOT_CARD_TIER = {
    "s_02": 100, # 全体2点（最強の盤面リセット）
    "s_09": 98,  # 城壁（挑発につけるとゲームが終わる）
    "u_02": 95,  # 挑発（防御の要）
    "s_05": 90,  # 暗殺（確定除去）
    "s_01": 88,  # 雷撃（盤面制圧にも顔面にも）
    "u_01": 85,  # 速攻（即時トレード要因）
    "u_06": 80,  # 小人（細かい除去）
    "u_04": 75,  # 巨兵（フィニッシャー）
    "u_05": 70,  # 吸血鬼
    "s_04": 65,  # 補充
    "s_07": 60,  # 凍結
    "s_08": 50,  # 火傷
    "u_03": 45,  # 魔導士
    "s_03": 30,  # 治癒
    "u_07": 20,  # 奇術師（運要素は避ける）
    "s_06": 10,  # 再編
}

HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}

# ====================================================
# 冷酷無比な高速シミュレータ
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
        self.opp_deck_count = 6

    def clone(self):
        new_s = SimState()
        new_s.my_hp = self.my_hp
        new_s.opp_hp = self.opp_hp
        new_s.my_mp = self.my_mp
        new_s.next_opp_mp = self.next_opp_mp
        new_s.my_id = self.my_id
        new_s.opp_id = self.opp_id
        new_s.opp_deck_count = self.opp_deck_count
        new_s.my_board = [dict(u) for u in self.my_board]
        new_s.opp_board = [dict(u) for u in self.opp_board]
        new_s.my_hand = [dict(c) for c in self.my_hand]
        new_s.opp_hand_ids = list(self.opp_hand_ids)
        return new_s

    def get_hash(self) -> str:
        mb = ",".join(f"{u.get('instance_id', 'new')}:{u.get('curr_hp', 0)}:{u.get('attacks_left', 0)}:{u.get('wall_turns', 0)}" for u in self.my_board)
        ob = ",".join(f"{u.get('instance_id', 'new')}:{u.get('curr_hp', 0)}:{u.get('frozen_turns', 0)}:{u.get('burn_turns', 0)}:{u.get('wall_turns', 0)}" for u in self.opp_board)
        mh = ",".join(c.get('instance_id', 'dummy') for c in self.my_hand)
        return f"{self.my_hp}|{self.opp_hp}|{self.my_mp}|{mb}|{ob}|{mh}"

    def evaluate(self) -> float:
        # 1. 確定勝利・敗北の絶対評価
        if self.opp_hp <= 0: return 9999999.0
        
        # 相手の手札と盤面からの最大打点を厳密に算出
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
        max_opp_spell_dmg = max(dp) if dp else 0
        
        active_opp_board_atk = sum(u.get("atk", 0) for u in self.opp_board if u.get("frozen_turns", 0) == 0)
        potential_opp_dmg = active_opp_board_atk + max_opp_spell_dmg

        # ★絶対防衛ライン：死ぬ行動は絶対に選ばない
        if self.my_hp <= potential_opp_dmg: return -9999999.0

        score = 0.0
        
        # 2. HPと盤面の評価（相手の盤面を徹底的に更地にする）
        score += (20 - self.opp_hp) * 10.0
        score += self.my_hp * 5.0

        my_board_atk = 0
        for u in self.my_board:
            score += u.get("atk", 0) * 8.0 + u.get("curr_hp", 0) * 5.0
            if u.get("taunt"): score += 25.0
            if u.get("wall_turns", 0) > 0: score += 15.0
            my_board_atk += u.get("atk", 0)
            
        for u in self.opp_board:
            # 敵の存在は絶対悪。徹底的にスコアを下げて除去を強要する
            score -= u.get("atk", 0) * 15.0 + u.get("curr_hp", 0) * 10.0
            if u.get("taunt"): score -= 30.0
            if u.get("wall_turns", 0) > 0: score -= 20.0
            
        # 相手の盤面が0体ならボーナス（完全制圧状態）
        if len(self.opp_board) == 0:
            score += 50.0

        # 3. 手札とマナの評価
        score += len(self.my_hand) * 15.0
        score -= self.my_mp * 10.0 # マナを残す行為を許さない

        # 4. 次ターン確定リーサルのセットアップ
        if my_board_atk >= self.opp_hp:
            score += 2000.0

        return score

    def quick_eval_action(self, action: dict) -> int:
        act_type = action["type"]
        val = 0
        if act_type == "PLAY":
            card = self.my_hand[action["h_idx"]]
            val += card.get("cost", 0) * 20 
            eff = card.get("effect")
            if eff == "aoe_damage": val += len(self.opp_board) * 30
            if eff == "assassinate": val += 40
        elif act_type == "ATTACK":
            if action["t_type"] == "hero": val += 10
            else:
                tgt = next((u for u in self.opp_board if u.get("instance_id") == action["t_id"]), None)
                if tgt:
                    if tgt.get("taunt"): val += 50
                    val += tgt.get("atk", 0) * 10 + tgt.get("curr_hp", 0) * 5
        return val

    def get_legal_actions(self) -> List[dict]:
        actions = []
        opp_taunts = [u for u in self.opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]
        
        for u in self.my_board:
            if u.get("can_attack") and u.get("frozen_turns", 0) == 0 and u.get("attacks_left", 0) > 0:
                if opp_taunts:
                    for t in self.opp_board:
                        if t.get("taunt") and t.get("curr_hp", 0) > 0:
                            actions.append({
                                "type": "ATTACK", "a_id": u["instance_id"], "t_type": "unit", "t_id": t["instance_id"],
                                "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                            })
                else:
                    actions.append({
                        "type": "ATTACK", "a_id": u["instance_id"], "t_type": "hero", "t_id": None,
                        "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "hero", "id": self.opp_id}}
                    })
                    for t in self.opp_board:
                        if t.get("curr_hp", 0) > 0:
                            actions.append({
                                "type": "ATTACK", "a_id": u["instance_id"], "t_type": "unit", "t_id": t["instance_id"],
                                "api": {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "unit", "id": t["instance_id"]}}
                            })
                            
        for i, c in enumerate(self.my_hand):
            if c.get("cost", 99) > self.my_mp: continue
            
            c_type = c.get("type")
            eff = c.get("effect")
            cid = c.get("instance_id", f"dummy_{i}")
            
            if c_type == "unit":
                if len(self.my_board) < 7:
                    actions.append({
                        "type": "PLAY", "h_idx": i, "t_type": None, "t_id": None,
                        "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": None}
                    })
            elif c_type == "spell":
                if eff == "damage":
                    actions.append({
                        "type": "PLAY", "h_idx": i, "t_type": "hero", "t_id": None,
                        "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "hero", "id": self.opp_id}}
                    })
                    for t in self.opp_board:
                        actions.append({
                            "type": "PLAY", "h_idx": i, "t_type": "unit", "t_id": t["instance_id"],
                            "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "unit", "id": t["instance_id"]}}
                        })
                elif eff in ["assassinate", "freeze", "burn"]:
                    for t in self.opp_board:
                        # 確実に有利になる相手にしか使わない
                        if eff == "assassinate" and t.get("cost", 0) < 3 and t.get("max_hp", 0) < 4 and not t.get("taunt"): continue
                        actions.append({
                            "type": "PLAY", "h_idx": i, "t_type": "unit", "t_id": t["instance_id"],
                            "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "unit", "id": t["instance_id"]}}
                        })
                elif eff == "wall":
                    for u in self.my_board:
                        actions.append({
                            "type": "PLAY", "h_idx": i, "t_type": "unit", "t_id": u["instance_id"],
                            "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": {"type": "unit", "id": u["instance_id"]}}
                        })
                else: 
                    actions.append({
                        "type": "PLAY", "h_idx": i, "t_type": None, "t_id": None,
                        "api": {"action": "PLAY_HAND", "card_instance_id": cid, "target": None}
                    })
                    
        # ビーム幅を広げてより深く緻密なコンボを探す
        actions.sort(key=lambda a: self.quick_eval_action(a), reverse=True)
        return actions[:12]

    def step(self, action: dict):
        act_type = action["type"]
        
        if act_type == "ATTACK":
            a_id = action["a_id"]
            t_type = action["t_type"]
            t_id = action["t_id"]
            
            attacker = next((u for u in self.my_board if u.get("instance_id") == a_id), None)
            if not attacker: return
            
            attacker["attacks_left"] = attacker.get("attacks_left", 1) - 1
            if attacker["attacks_left"] <= 0:
                attacker["can_attack"] = False
                
            atk_val = attacker.get("atk", 0)
            
            if t_type == "hero":
                self.opp_hp -= atk_val
                if attacker.get("lifesteal"): self.my_hp = min(20, self.my_hp + atk_val)
            elif t_type == "unit":
                defender = next((u for u in self.opp_board if u.get("instance_id") == t_id), None)
                if not defender: return
                
                def_atk = defender.get("atk", 0) if not attacker.get("ranged") else 0
                
                dmg_to_def = max(0, atk_val - 1) if defender.get("wall_turns", 0) > 0 else atk_val
                dmg_to_atk = max(0, def_atk - 1) if attacker.get("wall_turns", 0) > 0 else def_atk
                
                defender["curr_hp"] -= dmg_to_def
                attacker["curr_hp"] -= dmg_to_atk
                if attacker.get("lifesteal"): self.my_hp = min(20, self.my_hp + dmg_to_def)
                
        elif act_type == "PLAY":
            h_idx = action["h_idx"]
            if h_idx >= len(self.my_hand): return
            card = self.my_hand.pop(h_idx)
            self.my_mp -= card.get("cost", 0)
            
            c_type = card.get("type")
            if c_type == "unit":
                atk = card.get("atk", 0)
                hp = card.get("hp", 1)
                if card.get("random_stat"): atk, hp = 4, 4 # 期待値を強めに設定
                self.my_board.append({
                    "instance_id": card.get("instance_id", "new_summon"),
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
                t_id = action.get("t_id")
                
                if eff == "damage":
                    if t_type == "hero": self.opp_hp -= val
                    elif t_type == "unit":
                        tgt = next((u for u in self.opp_board if u.get("instance_id") == t_id), None)
                        if tgt: tgt["curr_hp"] -= max(0, val - 1) if tgt.get("wall_turns", 0) > 0 else val
                elif eff == "aoe_damage":
                    for u in self.opp_board:
                        u["curr_hp"] -= max(0, val - 1) if u.get("wall_turns", 0) > 0 else val
                elif eff == "heal":
                    self.my_hp = min(20, self.my_hp + val)
                elif eff == "draw":
                    for _ in range(val): self.my_hand.append({"type": "dummy", "cost": 99})
                elif eff == "assassinate":
                    tgt = next((u for u in self.opp_board if u.get("instance_id") == t_id), None)
                    if tgt: tgt["curr_hp"] = 0
                elif eff == "reshape":
                    if self.my_hand: self.my_hand.pop(0)
                    self.my_hand.append({"type": "dummy", "cost": 99})
                elif eff == "freeze":
                    tgt = next((u for u in self.opp_board if u.get("instance_id") == t_id), None)
                    if tgt: tgt["frozen_turns"] = 1
                elif eff == "burn":
                    tgt = next((u for u in self.opp_board if u.get("instance_id") == t_id), None)
                    if tgt: tgt["burn_turns"] = 3
                elif eff == "wall":
                    tgt = next((u for u in self.my_board if u.get("instance_id") == t_id), None)
                    if tgt: tgt["wall_turns"] = 3

        self.my_board = [u for u in self.my_board if u.get("curr_hp", 0) > 0]
        self.opp_board = [u for u in self.opp_board if u.get("curr_hp", 0) > 0]

# ====================================================
# 深さ優先探索（DFS）エンジン：限界までの深読み
# ====================================================
class DFSSolver:
    def __init__(self):
        self.max_nodes = 2000 # 限界まで読む
        self.nodes_visited = 0
        self.visited_states = set()
        
    def search(self, state: SimState, depth: int) -> Tuple[float, List[dict]]:
        self.nodes_visited += 1
        
        state_hash = state.get_hash()
        if state_hash in self.visited_states:
            return -999999.0, []
        self.visited_states.add(state_hash)
        
        best_score = state.evaluate()
        best_seq = []
        
        if depth >= 8 or state.opp_hp <= 0 or self.nodes_visited >= self.max_nodes:
            return best_score, best_seq
            
        legal_actions = state.get_legal_actions()
        for act in legal_actions:
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
        try:
            opp_id = next((uid for uid in self.session.player_order if uid != self.bot_id), None)
            if not opp_id: return []

            state = SimState()
            state.my_hp = self.session.hp.get(self.bot_id, 0)
            state.opp_hp = self.session.hp.get(opp_id, 0)
            state.my_mp = self.session.mp.get(self.bot_id, 0)
            state.next_opp_mp = min(10, self.session.max_mp.get(opp_id, 1) + 1)
            state.my_id = self.bot_id
            state.opp_id = opp_id
            
            opp_deck = self.session.decks.get(opp_id, [])
            state.opp_deck_count = len(opp_deck)

            state.my_board = [dict(u) for u in self.session.boards.get(self.bot_id, [])]
            state.opp_board = [dict(u) for u in self.session.boards.get(opp_id, [])]
            state.my_hand = [dict(c) for c in self.session.hands.get(self.bot_id, [])]
            state.opp_hand_ids = [c.get("id") for c in self.session.hands.get(opp_id, [])]

            solver = DFSSolver()
            _, best_seq = solver.search(state, 0)

            return [act["api"] for act in best_seq]
            
        except Exception as e:
            logger.error(f"[BOT DFS ERROR] {e}")
            fallback_actions = []
            opp_id = next((uid for uid in self.session.player_order if uid != self.bot_id), None)
            for u in self.session.boards.get(self.bot_id, []):
                if u.get("can_attack") and u.get("frozen_turns", 0) == 0 and u.get("attacks_left", 0) > 0:
                    fallback_actions.append({
                        "action": "DECLARE_ATTACK",
                        "attacker_id": u["instance_id"],
                        "target": {"type": "hero", "id": opp_id}
                    })
            return fallback_actions

# ====================================================
# 外側から非同期で安全呼び出しするメインエントリー
# ====================================================
async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):
    if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:
        return

    bot_engine = SuperBotEngine(session, card_database)

    # -------------------------
    # ドラフトフェーズ：妥協なき最強カードの独占
    # -------------------------
    if session.status == "DRAFT":
        opts = session.draft_options.get(BOT_USER_ID, [])
        if opts:
            my_deck = session.decks.get(BOT_USER_ID, [])
            opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)
            opp_deck = session.decks.get(opp_id, []) if opp_id else []
            
            my_deck_ids = [c.get("id") for c in my_deck]
            opp_deck_ids = [c.get("id") for c in opp_deck]
            
            best_card, best_score = None, -9999
            
            for cid in opts:
                score = BOT_CARD_TIER.get(cid, 50)
                
                # コンボの超絶強化
                if cid == "s_09" and "u_02" in my_deck_ids: score += 50 # 挑発＋城壁を最優先
                if cid == "s_02" and "s_02" not in my_deck_ids: score += 40 # 嵐は絶対に1枚確保
                
                # 相手にコンボを組ませない極悪カット
                if cid == "s_09" and "u_02" in opp_deck_ids: score += 60 
                if cid == "s_02": score += 20 # 相手に全体除去を渡さない
                
                if score > best_score:
                    best_score = score
                    best_card = cid

            await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card or opts[0]})
            await asyncio.sleep(0.05) # 考えるフリすらしない即決
            
            if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                await process_super_ai_turn(session, card_database, process_action_func)
        return

    # -------------------------
    # バトルフェーズ
    # -------------------------
    if session.status == "BATTLE":
        actions_to_execute = bot_engine.plan_turn()

        # 人間が反応できない速度（0.05秒間隔）で機械的に最適解を叩き込む
        for act in actions_to_execute:
            if session.status != "BATTLE" or session.turn_user_id != BOT_USER_ID:
                break
            await process_action_func(session, BOT_USER_ID, act)
            await asyncio.sleep(0.05) 

        if session.turn_user_id == BOT_USER_ID and session.status == "BATTLE":
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
