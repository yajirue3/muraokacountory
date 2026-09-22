import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("CardBot")

# ====================================================
# BOT基本設定
# ====================================================
BOT_USER_ID = "bot_super_ai"
BOT_USER_NAME = "真・究極生命体AI"

BOT_CARD_TIER = {
    "s_05": 100, "u_06": 98, "s_02": 95, "s_01": 90,
    "u_02": 88,  "s_09": 85, "u_05": 80, "s_04": 75,
    "u_04": 70,  "u_01": 65, "s_07": 60, "s_08": 55,
    "u_03": 50,  "u_07": 30, "s_03": 25, "s_06": 10,
}

HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}

# ====================================================
# 高速シミュレータ（状態ハッシュによる重複計算の完全排除とクラッシュ対策）
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

    # 重複計算を防ぐハッシュ（ダミーカード対策として get() を使用）
    def get_hash(self) -> str:
        mb = ",".join(f"{u.get('instance_id', 'new')}:{u.get('curr_hp', 0)}:{u.get('attacks_left', 0)}:{u.get('wall_turns', 0)}" for u in self.my_board)
        ob = ",".join(f"{u.get('instance_id', 'new')}:{u.get('curr_hp', 0)}:{u.get('frozen_turns', 0)}:{u.get('burn_turns', 0)}:{u.get('wall_turns', 0)}" for u in self.opp_board)
        mh = ",".join(c.get('instance_id', 'dummy') for c in self.my_hand)
        return f"{self.my_hp}|{self.opp_hp}|{self.my_mp}|{mb}|{ob}|{mh}"

    def evaluate(self) -> float:
        if self.opp_hp <= 0: return 9999999.0
        if self.my_hp <= 0: return -9999999.0

        score = 0.0
        
        # 1. HPの重み付け（後半ほど重要度増大）
        hp_weight = 20.0 if self.my_hp <= 10 else 10.0
        score += (20 - self.opp_hp) * 15.0
        score -= (20 - self.my_hp) * hp_weight

        # 2. 盤面支配力（目先の強さ）
        my_board_atk = 0
        has_lifesteal = False
        for u in self.my_board:
            score += u.get("atk", 0) * 4.0 + u.get("curr_hp", 0) * 3.0
            if u.get("taunt"): score += 18.0
            if u.get("wall_turns", 0) > 0: score += 12.0
            if u.get("lifesteal"): has_lifesteal = True
            my_board_atk += u.get("atk", 0)
            
        opp_trash_count = 0
        for u in self.opp_board:
            score -= u.get("atk", 0) * 4.5 + u.get("curr_hp", 0) * 2.5
            if u.get("taunt"): score -= 18.0
            if u.get("frozen_turns", 0) > 0: score += u.get("atk", 0) * 3.5 # 無力化ボーナス
            if u.get("burn_turns", 0) > 0: score += min(u.get("curr_hp", 0), 3) * 2.5
            
            # 盤面ロック＆吸血鬼バッテリーの評価
            if u.get("atk", 0) <= 1 and not u.get("taunt"):
                opp_trash_count += 1
                if has_lifesteal: score += 10.0

        if opp_trash_count >= 3 and len(self.opp_board) >= 5:
            score += 40.0 # 相手の盤面枠圧迫による行動封殺

        # 3. 長期戦リソース（裏の強さ）
        score += len(self.my_hand) * 12.0
        score -= self.my_mp * 5.0 # マナを残さない

        # 4. 反撃予測（Min-Max）
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
        
        score -= potential_opp_dmg * 2.5
        
        if self.my_hp <= potential_opp_dmg:
            score -= 50000.0 # 死ぬ危険がある盤面を徹底排除
            
        if self.opp_deck_count == 0 and len(self.opp_hand_ids) <= 2:
            score += self.my_hp * 6.0
            score += sum(u.get("curr_hp", 0) * 4 for u in self.my_board if u.get("taunt"))

        if my_board_atk >= self.opp_hp:
            score += 1500.0 # 次ターン確定リーサルのセットアップ

        return score

    # 行動候補の事前評価（ビームサーチ用ヒューリスティック）
    def quick_eval_action(self, action: dict) -> int:
        act_type = action["type"]
        val = 0
        if act_type == "PLAY":
            card = self.my_hand[action["h_idx"]]
            val += card.get("cost", 0) * 10 # 高コスト優先
            eff = card.get("effect")
            if eff == "draw": val += 50 # ドロー優先
            if eff == "aoe_damage": val += len(self.opp_board) * 15
        elif act_type == "ATTACK":
            if action["t_type"] == "hero": val += 20
            else:
                tgt = next((u for u in self.opp_board if u.get("instance_id") == action["t_id"]), None)
                if tgt:
                    if tgt.get("taunt"): val += 40
                    val += tgt.get("atk", 0) * 5
        return val

    def get_legal_actions(self) -> List[dict]:
        actions = []
        opp_taunts = [u for u in self.opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0]
        
        for u in self.my_board:
            if u.get("can_attack") and u.get("frozen_turns", 0) == 0 and u.get("attacks_left", 0) > 0:
                if opp_taunts:
                    for t in self.opp_board:
                        if t.get("taunt") and t.get("curr_hp", 0) > 0:
                            # 削除ズレを防ぐため、インデックス(t_idx)ではなくID(t_id)で追跡
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
                        if eff == "assassinate" and t.get("cost", 0) < 3 and t.get("max_hp", 0) < 4: continue # 無駄撃ち防止
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
                    
        # ビームサーチ：無数にある行動の中から「賢い行動」上位8手に絞り込む
        actions.sort(key=lambda a: self.quick_eval_action(a), reverse=True)
        return actions[:8]

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
                if card.get("random_stat"): atk, hp = 3, 3
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
# 深さ優先探索（DFS）エンジン：メモ化とビームサーチによる高速化
# ====================================================
class DFSSolver:
    def __init__(self):
        self.max_nodes = 800
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
        
        if depth >= 7 or state.opp_hp <= 0 or self.nodes_visited >= self.max_nodes:
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
            
            # 絶対に止まらせないための最終防衛ライン（フォールバック行動）
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
    # ドラフトフェーズ：ヘイトピックと黄金比率の完全管理
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
            
            unit_count = sum(1 for c in my_deck if c.get("type") == "unit")
            spell_count = len(my_deck) - unit_count
            avg_cost = sum(c.get("cost", 0) for c in my_deck) / max(1, len(my_deck)) if my_deck else 3.0

            for cid in opts:
                c_data = card_database.get(cid, {})
                score = BOT_CARD_TIER.get(cid, 50)
                
                # --- 黄金比率の構築 ---
                if c_data.get("type") == "unit":
                    if unit_count < 4: score += 40
                if c_data.get("type") == "spell":
                    if spell_count >= 2: score -= 40
                    
                if avg_cost > 3.0 and c_data.get("cost", 0) <= 2: score += 30
                if avg_cost < 2.5 and c_data.get("cost", 0) >= 4: score += 20
                
                # --- シナジー ---
                if cid == "s_09" and any(c.get("taunt") for c in my_deck): score += 35
                if cid == "u_06" and "u_02" in my_deck_ids: score += 20
                if cid == "u_05" and "s_09" in my_deck_ids: score += 25 
                
                # --- ヘイトピック（カット戦術） ---
                if cid == "s_02": score += 25 
                if cid == "s_09" and "u_02" in opp_deck_ids: score += 50 
                if cid == "u_04" and "s_05" not in my_deck_ids: score += 20 
                
                if score > best_score:
                    best_score = score
                    best_card = cid

            await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card or opts[0]})
            await asyncio.sleep(0.1)
            
            if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:
                await process_super_ai_turn(session, card_database, process_action_func)
        return

    # -------------------------
    # バトルフェーズ
    # -------------------------
    if session.status == "BATTLE":
        actions_to_execute = bot_engine.plan_turn()

        # 算出したコンボを0.15秒のウェイト付きで流麗に実行
        for act in actions_to_execute:
            if session.status != "BATTLE" or session.turn_user_id != BOT_USER_ID:
                break
            await process_action_func(session, BOT_USER_ID, act)
            await asyncio.sleep(0.15) 

        if session.turn_user_id == BOT_USER_ID and session.status == "BATTLE":
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})
