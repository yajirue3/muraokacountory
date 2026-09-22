import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger("CardBot")

# ====================================================
# BOT基本設定
# ====================================================
BOT_USER_ID = "bot_super_ai[span_0](start_span)"[span_0](end_span)
BOT_USER_NAME = "クソザコBOT[span_1](start_span)"[span_1](end_span)

# カードの基本Tier（標準価値）
BOT_CARD_TIER = {
    "s_05": 100, "u_06": 98, "s_02": 95, "s_01": 90,
    "u_02": 88,  "s_09": 85, "u_05": 80, "s_04": 75,
    "u_04": 70,  "u_01": 65, "s_07": 60, "s_08": 55,
    "u_03": 50,  "u_07": 30, "s_03": 25, "s_06": 10,
}[span_2](start_span)[span_2](end_span)

# card.py 側からのインポートエラー回避用（今回の最強BOTは完全公開情報を直接見るため不使用）
HUMAN_DRAFT_MEMORIES: Dict[str, List[str]] = {}[span_3](start_span)[span_3](end_span)

class SuperBotEngine:
    def __init__(self, session: Any, card_database: Dict[str, dict]):
        self.session = session[span_4](start_span)[span_4](end_span)
        self.db = card_database[span_5](start_span)[span_5](end_span)
        self.bot_id = BOT_USER_ID[span_6](start_span)[span_6](end_span)

    # ----------------------------------------------------
    # 動的カード評価（状況とマナカーブに応じたTier変動）
    # ----------------------------------------------------
    def get_dynamic_tier(self, card_id: str, card_cost: int, my_hp: int, my_hand_len: int, opp_board_len: int, current_turn: int) -> int:
        base_score = BOT_CARD_TIER.get(card_id, 50)
        
        # 1. 序盤（1〜3ターン目）のテンポ制御：コストの重いカードの評価を下げ、軽いユニットを最優先
        if current_turn <= 3:
            if card_cost <= current_turn:
                base_score += 40  # 出せるカードを高く評価
            else:
                base_score -= 50  # 出せない重いカードは評価を下げる（序盤のゴミ化防止）

        # 2. 自身のHPがピンチ（10以下）なら防衛・回復カードを超爆上げ
        if my_hp <= 10:
            if card_id in ["s_03", "u_02"]:
                base_score += 50
            elif card_id == "s_09":
                base_score += 30

        # 3. 相手の盤面が展開されているなら全体攻撃（嵐）を最優先
        if opp_board_len >= 3 and card_id == "s_02":
            base_score += 60

        # 4. 手札が枯渇気味（2枚以下）ならドロー呪文を優先
        if my_hand_len <= 2 and card_id == "s_04":
            base_score += 45

        return base_score

    
    # ----------------------------------------------------
    # 未来予測：相手の次ターン確定最大攻撃力＋直火ダメージ
    # ----------------------------------------------------
    def predict_opponent_max_damage(self, opp_id: str) -> Tuple[int, int]:
        # 相手の手札を直接覗き見て「次ターンの確定最大ダメージ」を100%の精度で計算する
        opp_board = self.session.boards.get(opp_id, [])[span_19](start_span)[span_19](end_span)
        opp_hand = self.session.hands.get(opp_id, [])[span_20](start_span)[span_20](end_span)
        next_opp_mp = min(10, self.session.max_mp.get(opp_id, 1) + 1)[span_21](start_span)[span_21](end_span)

        board_dmg = sum(
            u.get("atk", 0) * u.get("max_attacks", 1)[span_22](start_span)[span_22](end_span)
            for u in opp_board if u.get("frozen_turns", 0) <= 1[span_23](start_span)[span_23](end_span)
        )

        spell_dmg = sum(
            c.get("val", 0) for c in opp_hand[span_24](start_span)[span_24](end_span)
            if c.get("type") == "spell" and c.get("effect") == "damage" and c.get("cost", 99) <= next_opp_mp[span_25](start_span)[span_25](end_span)
        )

        # 未知の手札に対する推測値（safety_buffer）は完全廃止し、確定ダメージのみを返す
        return board_dmg + spell_dmg, board_dmg[span_26](start_span)[span_26](end_span)

    # ----------------------------------------------------
    # メイン思考・行動決定ロジック
    # ----------------------------------------------------
    def decide_best_action(self) -> Optional[dict]:
        try:
            opp_id = next((uid for uid in self.session.player_order if uid != self.bot_id), None)[span_27](start_span)[span_27](end_span)
            if not opp_id: return None[span_28](start_span)[span_28](end_span)

            bot_hp = self.session.hp.get(self.bot_id, 0)[span_29](start_span)[span_29](end_span)
            opp_hp = self.session.hp.get(opp_id, 0)[span_30](start_span)[span_30](end_span)
            bot_mp = self.session.mp.get(self.bot_id, 0)[span_31](start_span)[span_31](end_span)
            current_turn = self.session.max_mp.get(self.bot_id, 1)[span_32](start_span)[span_32](end_span)
            next_opp_mp = min(10, self.session.max_mp.get(opp_id, 1) + 1)[span_33](start_span)[span_33](end_span)

            bot_board = self.session.boards.get(self.bot_id, [])[span_34](start_span)[span_34](end_span)
            opp_board = self.session.boards.get(opp_id, [])[span_35](start_span)[span_35](end_span)
            bot_hand = self.session.hands.get(self.bot_id, [])[span_36](start_span)[span_36](end_span)
            opp_hand = self.session.hands.get(opp_id, [])[span_37](start_span)[span_37](end_span)

            opp_taunts = [u for u in opp_board if u.get("taunt") and u.get("curr_hp", 0) > 0][span_38](start_span)[span_38](end_span)
            
            # 確定ダメージによるピンチ判定（完全情報）
            next_potential_dmg, _ = self.predict_opponent_max_damage(opp_id)[span_39](start_span)[span_39](end_span)
            is_in_desperate_danger = (bot_hp <= next_potential_dmg)[span_40](start_span)[span_40](end_span)

            # === 1. 確定リーサル（即勝ち）判定 ===
            board_atk_sum = sum(
                u.get("atk", 0) * u.get("attacks_left", 1)[span_41](start_span)[span_41](end_span)
                for u in bot_board if u.get("can_attack") and u.get("frozen_turns", 0) == 0 and u.get("attacks_left", 0) > 0[span_42](start_span)[span_42](end_span)
            )
            playable_spells = [c for c in bot_hand if c.get("type") == "spell" and c.get("cost", 99) <= bot_mp][span_43](start_span)[span_43](end_span)
            direct_dmg_spells = [c for c in playable_spells if c.get("effect") == "damage"][span_44](start_span)[span_44](end_span)
            hand_dmg_sum = sum(c.get("val", 0) for c in direct_dmg_spells)[span_45](start_span)[span_45](end_span)

            for spell in direct_dmg_spells:[span_46](start_span)[span_46](end_span)
                if spell.get("val", 0) >= opp_hp:[span_47](start_span)[span_47](end_span)
                    return {"action": "PLAY_HAND", "card_instance_id": spell["instance_id"], "target": {"type": "hero", "id": opp_id}}[span_48](start_span)[span_48](end_span)

            if opp_taunts and (board_atk_sum + hand_dmg_sum >= opp_hp):[span_49](start_span)[span_49](end_span)
                assassinate = next((c for c in playable_spells if c.get("id") == "s_05"), None)[span_50](start_span)[span_50](end_span)
                if assassinate:[span_51](start_span)[span_51](end_span)
                    return {"action": "PLAY_HAND", "card_instance_id": assassinate["instance_id"], "target": {"type": "unit", "id": opp_taunts[0]["instance_id"]}}[span_52](start_span)[span_52](end_span)

            if not opp_taunts and (board_atk_sum >= opp_hp or board_atk_sum + hand_dmg_sum >= opp_hp):[span_53](start_span)[span_53](end_span)
                for u in bot_board:[span_54](start_span)[span_54](end_span)
                    if u.get("can_attack") and u.get("frozen_turns", 0) == 0 and u.get("attacks_left", 0) > 0:[span_55](start_span)[span_55](end_span)
                        return {"action": "DECLARE_ATTACK", "attacker_id": u["instance_id"], "target": {"type": "hero", "id": opp_id}}[span_56](start_span)[span_56](end_span)

            # === 2. 高危険度目標の優先処理（魔導士等） ===
            priority_targets = [u for u in opp_board if u.get("card_id") == "u_03" or u.get("atk", 0) >= 4][span_57](start_span)[span_57](end_span)
            if priority_targets:[span_58](start_span)[span_58](end_span)
                target = max(priority_targets, key=lambda x: x.get("atk", 0))[span_59](start_span)[span_59](end_span)
                for spell in playable_spells:[span_60](start_span)[span_60](end_span)
                    if spell.get("effect") == "damage" and spell.get("val", 0) >= target.get("curr_hp", 0):[span_61](start_span)[span_61](end_span)
                        return {"action": "PLAY_HAND", "card_instance_id": spell["instance_id"], "target": {"type": "unit", "id": target["instance_id"]}}[span_62](start_span)[span_62](end_span)

            # === 3. 相手の確定リーサルに対するエマージェンシー防衛 ===
            if is_in_desperate_danger:[span_63](start_span)[span_63](end_span)
                assassinate = next((c for c in playable_spells if c.get("id") == "s_05"), None)[span_64](start_span)[span_64](end_span)
                if assassinate and opp_board:[span_65](start_span)[span_65](end_span)
                    best_target = max(opp_board, key=lambda x: x.get("atk", 0))[span_66](start_span)[span_66](end_span)
                    return {"action": "PLAY_HAND", "card_instance_id": assassinate["instance_id"], "target": {"type": "unit", "id": best_target["instance_id"]}}[span_67](start_span)[span_67](end_span)
                heal_spell = next((c for c in playable_spells if c.get("effect") == "heal"), None)[span_68](start_span)[span_68](end_span)
                if heal_spell:[span_69](start_span)[span_69](end_span)
                    return {"action": "PLAY_HAND", "card_instance_id": heal_spell["instance_id"], "target": None}[span_70](start_span)[span_70](end_span)
                
                # 追加防衛：挑発ユニットを持っていれば優先的に盾にする
                taunt_unit = next((c for c in bot_hand if c.get("taunt") and c.get("cost", 99) <= bot_mp), None)
                if taunt_unit:
                    return {"action": "PLAY_HAND", "card_instance_id": taunt_unit["instance_id"], "target": None}

            # === 4. 最適有利トレード ===
            for attacker in bot_board:[span_71](start_span)[span_71](end_span)
                if attacker.get("can_attack") and attacker.get("frozen_turns", 0) == 0 and attacker.get("attacks_left", 0) > 0:[span_72](start_span)[span_72](end_span)
                    atk_val = attacker.get("atk", 0)[span_73](start_span)[span_73](end_span)
                    
                    if attacker.get("card_id") == "u_06":[span_74](start_span)[span_74](end_span)
                        if opp_taunts:[span_75](start_span)[span_75](end_span)
                            target_t = next((u for u in opp_taunts if u.get("wall_turns", 0) == 0), opp_taunts[0])[span_76](start_span)[span_76](end_span)
                            return {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": {"type": "unit", "id": target_t["instance_id"]}}[span_77](start_span)[span_77](end_span)
                        elif opp_board:[span_78](start_span)[span_78](end_span)
                            target_e = max(opp_board, key=lambda x: x.get("atk", 0))[span_79](start_span)[span_79](end_span)
                            return {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": {"type": "unit", "id": target_e["instance_id"]}}[span_80](start_span)[span_80](end_span)
                        else:
                            return {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": {"type": "hero", "id": opp_id}}[span_81](start_span)[span_81](end_span)

                    if opp_taunts:[span_82](start_span)[span_82](end_span)
                        for taunt in opp_taunts:[span_83](start_span)[span_83](end_span)
                            dmg = max(0, atk_val - 1) if taunt.get("wall_turns", 0) > 0 else atk_val[span_84](start_span)[span_84](end_span)
                            if dmg > 0:[span_85](start_span)[span_85](end_span)
                                return {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": {"type": "unit", "id": taunt["instance_id"]}}[span_86](start_span)[span_86](end_span)
                    else:
                        for defender in opp_board:[span_87](start_span)[span_87](end_span)
                            dmg_to_def = max(0, atk_val - 1) if defender.get("wall_turns", 0) > 0 else atk_val[span_88](start_span)[span_88](end_span)
                            dmg_to_atk = max(0, defender.get("atk", 0) - 1) if attacker.get("wall_turns", 0) > 0 else defender.get("atk", 0)[span_89](start_span)[span_89](end_span)
                            if (dmg_to_def >= defender.get("curr_hp", 0) and dmg_to_atk < attacker.get("curr_hp", 0)) or (is_in_desperate_danger and dmg_to_def >= defender.get("curr_hp", 0)):[span_90](start_span)[span_90](end_span)
                                return {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": {"type": "unit", "id": defender["instance_id"]}}[span_91](start_span)[span_91](end_span)

            # === 5. 手札プレイ（相手の手札カンニング対応） ===
            playable = [c for c in bot_hand if c.get("cost", 99) <= bot_mp][span_92](start_span)[span_92](end_span)
            opp_hand_ids = [c.get("id") for c in opp_hand][span_93](start_span)[span_93](end_span)
            
            # 相手の手札を完全に覗き見た悪魔のカウンター制御
            if "s_02" in opp_hand_ids and next_opp_mp >= 4:[span_94](start_span)[span_94](end_span)
                playable = [c for c in playable if not (c.get("type") == "unit" and c.get("hp", 0) <= 2 and not c.get("haste"))][span_95](start_span)[span_95](end_span)
            if "s_05" in opp_hand_ids and next_opp_mp >= 6:[span_96](start_span)[span_96](end_span)
                playable = [c for c in playable if not (c.get("id") == "u_04")]

            if playable:[span_97](start_span)[span_97](end_span)
                has_board_unit = len(bot_board) > 0[span_98](start_span)[span_98](end_span)
                for c in playable:[span_99](start_span)[span_99](end_span)
                    score = self.get_dynamic_tier(c.get("id"), c.get("cost", 0), bot_hp, len(bot_hand), len(opp_board), current_turn)[span_100](start_span)[span_100](end_span)
                    if not has_board_unit and c.get("type") == "unit":[span_101](start_span)[span_101](end_span)
                        score += 50[span_102](start_span)[span_102](end_span)
                    c["_temp_score"] = score[span_103](start_span)[span_103](end_span)

                playable.sort(key=lambda c: c["_temp_score"], reverse=True)[span_104](start_span)[span_104](end_span)

                for card in playable:[span_105](start_span)[span_105](end_span)
                    c_type = card.get("type")[span_106](start_span)[span_106](end_span)
                    eff = card.get("effect")[span_107](start_span)[span_107](end_span)

                    if c_type == "unit":[span_108](start_span)[span_108](end_span)
                        if len(bot_board) < 7:[span_109](start_span)[span_109](end_span)
                            return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": None}[span_110](start_span)[span_110](end_span)

                    elif c_type == "spell":[span_111](start_span)[span_111](end_span)
                        # 修正箇所：ユニットゼロで城壁を使用しようとして止まるバグの防止
                        if card.get("id") == "s_09":[span_112](start_span)[span_112](end_span)
                            if not bot_board:
                                continue
                            taunt_units = [u for u in bot_board if u.get("taunt") and u.get("wall_turns", 0) == 0][span_113](start_span)[span_113](end_span)
                            target_unit = taunt_units[0] if taunt_units else max(bot_board, key=lambda x: x.get("curr_hp", 0))[span_114](start_span)[span_114](end_span)
                            return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": target_unit["instance_id"]}}[span_115](start_span)[span_115](end_span)

                        if eff == "assassinate":[span_116](start_span)[span_116](end_span)
                            targets = [u for u in opp_board if u.get("curr_hp", 0) >= 4 or u.get("atk", 0) >= 3][span_117](start_span)[span_117](end_span)
                            if targets:[span_118](start_span)[span_118](end_span)
                                best_t = max(targets, key=lambda x: x.get("atk", 0) + x.get("curr_hp", 0))[span_119](start_span)[span_119](end_span)
                                return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": best_t["instance_id"]}}[span_120](start_span)[span_120](end_span)
                            continue[span_121](start_span)[span_121](end_span)

                        elif eff == "aoe_damage":[span_122](start_span)[span_122](end_span)
                            if len(opp_board) >= 2 or any(u.get("curr_hp", 0) <= 2 for u in opp_board):[span_123](start_span)[span_123](end_span)
                                return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": None}[span_124](start_span)[span_124](end_span)
                            continue[span_125](start_span)[span_125](end_span)

                        elif eff == "draw":[span_126](start_span)[span_126](end_span)
                            if len(bot_hand) <= 5:[span_127](start_span)[span_127](end_span)
                                return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": None}[span_128](start_span)[span_128](end_span)
                            continue[span_129](start_span)[span_129](end_span)

                        elif eff == "heal":[span_130](start_span)[span_130](end_span)
                            if bot_hp <= 15:[span_131](start_span)[span_131](end_span)
                                return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": None}[span_132](start_span)[span_132](end_span)
                            continue[span_133](start_span)[span_133](end_span)

                        elif eff == "damage":[span_134](start_span)[span_134](end_span)
                            targets = [u for u in opp_board if u.get("curr_hp", 0) <= 3][span_135](start_span)[span_135](end_span)
                            if targets:[span_136](start_span)[span_136](end_span)
                                best_t = max(targets, key=lambda x: x.get("atk", 0))[span_137](start_span)[span_137](end_span)
                                return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": best_t["instance_id"]}}[span_138](start_span)[span_138](end_span)
                            return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "hero", "id": opp_id}}[span_139](start_span)[span_139](end_span)

                        elif eff == "freeze":[span_140](start_span)[span_140](end_span)
                            targets = [u for u in opp_board if u.get("frozen_turns", 0) == 0 and u.get("atk", 0) >= 2][span_141](start_span)[span_141](end_span)
                            if targets:[span_142](start_span)[span_142](end_span)
                                best_t = max(targets, key=lambda x: x.get("atk", 0))[span_143](start_span)[span_143](end_span)
                                return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": best_t["instance_id"]}}[span_144](start_span)[span_144](end_span)
                            continue[span_145](start_span)[span_145](end_span)

                        elif eff == "wall":[span_146](start_span)[span_146](end_span)
                            targets = [u for u in bot_board if u.get("wall_turns", 0) == 0][span_147](start_span)[span_147](end_span)
                            if targets:[span_148](start_span)[span_148](end_span)
                                best_t = max(targets, key=lambda x: x.get("curr_hp", 0))[span_149](start_span)[span_149](end_span)
                                return {"action": "PLAY_HAND", "card_instance_id": card["instance_id"], "target": {"type": "unit", "id": best_t["instance_id"]}}[span_150](start_span)[span_150](end_span)
                            continue[span_151](start_span)[span_151](end_span)

            # === 6. 残存ユニットでの顔面攻撃 ===
            for attacker in bot_board:[span_152](start_span)[span_152](end_span)
                if attacker.get("can_attack") and attacker.get("frozen_turns", 0) == 0 and attacker.get("attacks_left", 0) > 0:[span_153](start_span)[span_153](end_span)
                    if not opp_taunts:[span_154](start_span)[span_154](end_span)
                        return {"action": "DECLARE_ATTACK", "attacker_id": attacker["instance_id"], "target": {"type": "hero", "id": opp_id}}[span_155](start_span)[span_155](end_span)

        except Exception as e:[span_156](start_span)[span_156](end_span)
            logger.error(f"[SuperBotEngine Guard] Error processing decision: {e}")[span_157](start_span)[span_157](end_span)

        return None[span_158](start_span)[span_158](end_span)

# ====================================================
# 外側から非同期で安全呼び出しするメインエントリー
# ====================================================
async def process_super_ai_turn(session: Any, card_database: Dict[str, dict], process_action_func: Any):[span_159](start_span)[span_159](end_span)
    if session.status == "ENDED" or session.turn_user_id != BOT_USER_ID:[span_160](start_span)[span_160](end_span)
        return[span_161](start_span)[span_161](end_span)

    bot_engine = SuperBotEngine(session, card_database)[span_162](start_span)[span_162](end_span)
    opp_id = next((uid for uid in session.player_order if uid != BOT_USER_ID), None)[span_163](start_span)[span_163](end_span)

    if session.status == "DRAFT":[span_164](start_span)[span_164](end_span)
        opts = session.draft_options.get(BOT_USER_ID, [])[span_165](start_span)[span_165](end_span)
        if opts:[span_166](start_span)[span_166](end_span)
            my_deck_ids = [c.get("id") for c in session.decks.get(BOT_USER_ID, [])][span_167](start_span)[span_167](end_span)
            best_card, best_score = None, -1[span_168](start_span)[span_168](end_span)

            for cid in opts:[span_169](start_span)[span_169](end_span)
                score = BOT_CARD_TIER.get(cid, 0)[span_170](start_span)[span_170](end_span)
                if cid == "s_09" and any(card_database.get(c, {}).get("taunt") for c in my_deck_ids):[span_171](start_span)[span_171](end_span)
                    score += 25[span_172](start_span)[span_172](end_span)
                if cid == "u_06" and "u_02" in my_deck_ids:[span_173](start_span)[span_173](end_span)
                    score += 15[span_174](start_span)[span_174](end_span)
                if score > best_score:[span_175](start_span)[span_175](end_span)
                    best_score, best_card = score, cid[span_176](start_span)[span_176](end_span)

            await process_action_func(session, BOT_USER_ID, {"action": "PICK_CARD", "card_id": best_card or opts[0]})[span_177](start_span)[span_177](end_span)
            if session.status == "DRAFT" and session.turn_user_id == BOT_USER_ID:[span_178](start_span)[span_178](end_span)
                await process_super_ai_turn(session, card_database, process_action_func)[span_179](start_span)[span_179](end_span)
        return[span_180](start_span)[span_180](end_span)

    if session.status == "BATTLE":[span_181](start_span)[span_181](end_span)
        max_steps = 10[span_182](start_span)[span_182](end_span)
        step_count = 0[span_183](start_span)[span_183](end_span)

        while session.turn_user_id == BOT_USER_ID and session.status == "BATTLE" and step_count < max_steps:[span_184](start_span)[span_184](end_span)
            step_count += 1[span_185](start_span)[span_185](end_span)
            try:[span_186](start_span)[span_186](end_span)
                action = await asyncio.wait_for([span_187](start_span)[span_187](end_span)
                    asyncio.to_thread(bot_engine.decide_best_action),[span_188](start_span)[span_188](end_span)
                    timeout=0.5[span_189](start_span)[span_189](end_span)
                )[span_190](start_span)[span_190](end_span)
            except asyncio.TimeoutError:[span_191](start_span)[span_191](end_span)
                logger.warning("[CardBot] Decision timed out. Proceeding safely.")[span_192](start_span)[span_192](end_span)
                break[span_193](start_span)[span_193](end_span)

            if not action:[span_194](start_span)[span_194](end_span)
                break[span_195](start_span)[span_195](end_span)

            await process_action_func(session, BOT_USER_ID, action)[span_196](start_span)[span_196](end_span)

        if session.turn_user_id == BOT_USER_ID and session.status == "BATTLE":[span_197](start_span)[span_197](end_span)
            await process_action_func(session, BOT_USER_ID, {"action": "END_TURN"})[span_198](start_span)[span_198](end_span)
