import random
import uuid
from pathlib import Path
from fastapi import APIRouter, HTTPException, Header, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# 循環インポートを避けるため、dbモジュールから直接読み込み
from db import get_supabase

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# ==========================================
# 共通認証関数
# ==========================================
async def get_user_from_token(authorization: str):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証トークンがありません")
    token = authorization.split(" ")[1]
    try:
        supabase = await get_supabase()
        user_res = await supabase.auth.get_user(token)
        return user_res.user
    except Exception:
        raise HTTPException(status_code=401, detail="無効なトークンです")


# ==========================================
# 2048 用エンドポイント
# ==========================================

class Score2048Submit(BaseModel):
    score: int
    max_tile: int

# --- 画面配信 ---
@router.get("/2048", response_class=HTMLResponse)
async def get_2048_page(request: Request):
    return templates.TemplateResponse(request=request, name="2048.html")

# --- ランキング単体取得（いつでも見れる用） ---
@router.get("/api/2048/ranking")
async def get_2048_ranking():
    supabase = await get_supabase()
    
    try:
        daily_res = await supabase.table("view_2048_daily").select("*").order("score", desc=True).limit(10).execute()
        alltime_res = await supabase.table("view_2048_alltime").select("*").order("score", desc=True).limit(10).execute()
        
        return {
            "daily": daily_res.data or [],
            "alltime": alltime_res.data or []
        }
    except Exception as e:
        import traceback
        print("=== 2048 RANKING ERROR ===")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"ランキング取得エラー: {str(e)}")

# --- スコア登録 & ランキング返却 ---
@router.post("/api/2048/score")
async def submit_2048_score(data: Score2048Submit, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()

    if data.score < 0 or data.max_tile < 2:
        raise HTTPException(status_code=400, detail="無効なスコアデータです")

    try:
        nickname = "名無し"
        
        try:
            profile_res = await supabase.table("profiles").select("nickname").eq("id", str(user.id)).execute()
            if profile_res.data and len(profile_res.data) > 0:
                nickname = profile_res.data[0].get("nickname") or "名無し"
        except Exception as pe:
            print(f"[Warning] 2048 profiles取得スキップ: {pe}")
            nickname = "名無し"

        await supabase.table("scores_2048").insert({
            "user_id": str(user.id),
            "nickname": nickname,
            "score": data.score,
            "max_tile": data.max_tile
        }).execute()

        daily_res = await supabase.table("view_2048_daily").select("*").order("score", desc=True).limit(10).execute()
        alltime_res = await supabase.table("view_2048_alltime").select("*").order("score", desc=True).limit(10).execute()
        
        higher_scores = await supabase.table("view_2048_alltime").select("user_id", count="exact").gt("score", data.score).execute()
        my_rank = (higher_scores.count or 0) + 1

        return {
            "success": True,
            "my_rank": my_rank,
            "daily": daily_res.data or [],
            "alltime": alltime_res.data or []
        }
    except Exception as e:
        import traceback
        print("=== 2048 SCORE ERROR ===")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"DB処理エラー: {str(e)}")


# ==========================================
# さめがめ (SameGame) 用エンドポイント
# ==========================================

class ScoreSameGameSubmit(BaseModel):
    score: int
    remaining_blocks: int

# --- 画面配信 ---
@router.get("/samegame", response_class=HTMLResponse)
async def get_samegame_page(request: Request):
    return templates.TemplateResponse(request=request, name="samegame.html")

# --- ランキング単体取得（いつでも見れる用） ---
@router.get("/api/samegame/ranking")
async def get_samegame_ranking():
    supabase = await get_supabase()
    
    try:
        daily_res = await supabase.table("view_samegame_daily").select("*").order("score", desc=True).limit(10).execute()
        alltime_res = await supabase.table("view_samegame_alltime").select("*").order("score", desc=True).limit(10).execute()
        
        return {
            "daily": daily_res.data or [],
            "alltime": alltime_res.data or []
        }
    except Exception as e:
        import traceback
        print("=== SAMEGAME RANKING ERROR ===")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"ランキング取得エラー: {str(e)}")

# --- スコア登録 & ランキング返却 ---
@router.post("/api/samegame/score")
async def submit_samegame_score(data: ScoreSameGameSubmit, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()

    if data.score < 0 or data.remaining_blocks < 0:
        raise HTTPException(status_code=400, detail="無効なスコアデータです")

    try:
        nickname = "名無し"
        
        try:
            profile_res = await supabase.table("profiles").select("nickname").eq("id", str(user.id)).execute()
            if profile_res.data and len(profile_res.data) > 0:
                nickname = profile_res.data[0].get("nickname") or "名無し"
        except Exception as pe:
            print(f"[Warning] samegame profiles取得スキップ: {pe}")
            nickname = "名無し"

        await supabase.table("scores_samegame").insert({
            "user_id": str(user.id),
            "nickname": nickname,
            "score": data.score,
            "remaining_blocks": data.remaining_blocks
        }).execute()

        daily_res = await supabase.table("view_samegame_daily").select("*").order("score", desc=True).limit(10).execute()
        alltime_res = await supabase.table("view_samegame_alltime").select("*").order("score", desc=True).limit(10).execute()
        
        higher_scores = await supabase.table("view_samegame_alltime").select("user_id", count="exact").gt("score", data.score).execute()
        my_rank = (higher_scores.count or 0) + 1

        return {
            "success": True,
            "my_rank": my_rank,
            "daily": daily_res.data or [],
            "alltime": alltime_res.data or []
        }
    except Exception as e:
        import traceback
        print("=== SAMEGAME SCORE ERROR ===")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"DB処理エラー: {str(e)}")


# ==========================================
# こねこばくはつ (Exploding Kittens) ノートラストDB駆動完全版
# ==========================================

# --- Pydantic モデル ---
class kb_CreateRoomRequest(BaseModel):
    max_players: int = 4

class kb_JoinRoomRequest(BaseModel):
    room_id: str

class kb_StartGameRequest(BaseModel):
    room_id: str

class kb_PlayCardRequest(BaseModel):
    room_id: str
    card_index: int
    target_user_id: str = None  # 強奪用

class kb_DrawCardRequest(BaseModel):
    room_id: str
    from_bottom: bool = False  # ボトムドロー用


# --- 画面配信 ---
@router.get("/koneko", response_class=HTMLResponse)
async def kb_get_page(request: Request):
    return templates.TemplateResponse(request=request, name="koneko.html")


# --- 部屋一覧取得 API (待機中のみ) ---
@router.get("/api/kb_room/list")
async def kb_get_room_list(authorization: str = Header(None)):
    await get_user_from_token(authorization)
    supabase = await get_supabase()

    try:
        kb_rooms_res = await supabase.table("kb_rooms").select("*").eq("status", "waiting").order("created_at", desc=True).limit(20).execute()
        kb_rooms = kb_rooms_res.data or []

        kb_result = []
        for r in kb_rooms:
            kb_players_res = await supabase.table("kb_players").select("nickname").eq("room_id", r["room_id"]).order("seat_order").execute()
            kb_players = kb_players_res.data or []
            kb_host_name = kb_players[0]["nickname"] if kb_players else "名無し"
            
            kb_result.append({
                "room_id": r["room_id"],
                "host_name": kb_host_name,
                "current_players": len(kb_players),
                "max_players": r["max_players"]
            })

        return {"rooms": kb_result}
    except Exception as e:
        import traceback
        print("=== KB ROOM LIST ERROR ===")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"部屋一覧取得エラー: {str(e)}")


# --- 部屋作成 API ---
@router.post("/api/kb_room/create")
async def kb_create_room(data: kb_CreateRoomRequest, authorization: str = Header(None)):
    kb_user = await get_user_from_token(authorization)
    supabase = await get_supabase()

    if data.max_players < 2 or data.max_players > 4:
        raise HTTPException(status_code=400, detail="プレイ人数は2〜4人です")

    kb_room_id = str(uuid.uuid4())[:8]

    kb_nickname = "名無し"
    try:
        kb_prof = await supabase.table("profiles").select("nickname").eq("id", str(kb_user.id)).execute()
        if kb_prof.data and len(kb_prof.data) > 0:
            kb_nickname = kb_prof.data[0].get("nickname") or "名無し"
    except Exception:
        kb_nickname = "名無し"

    await supabase.table("kb_rooms").insert({
        "room_id": kb_room_id,
        "host_id": str(kb_user.id),
        "max_players": data.max_players,
        "status": "waiting"
    }).execute()

    await supabase.table("kb_players").insert({
        "room_id": kb_room_id,
        "user_id": str(kb_user.id),
        "nickname": kb_nickname,
        "seat_order": 0,
        "is_alive": True,
        "hand": []
    }).execute()

    return {"success": True, "room_id": kb_room_id}


# --- 部屋参加 API ---
@router.post("/api/kb_room/join")
async def kb_join_room(data: kb_JoinRoomRequest, authorization: str = Header(None)):
    kb_user = await get_user_from_token(authorization)
    supabase = await get_supabase()

    kb_room_res = await supabase.table("kb_rooms").select("*").eq("room_id", data.room_id).execute()
    if not kb_room_res.data:
        raise HTTPException(status_code=404, detail="部屋が存在しません")
    kb_room = kb_room_res.data[0]

    if kb_room["status"] != "waiting":
        raise HTTPException(status_code=400, detail="ゲームが既に開始されています")

    kb_players_res = await supabase.table("kb_players").select("*").eq("room_id", data.room_id).execute()
    kb_players = kb_players_res.data or []

    if any(p["user_id"] == str(kb_user.id) for p in kb_players):
        return {"success": True, "room_id": data.room_id}

    if len(kb_players) >= kb_room["max_players"]:
        raise HTTPException(status_code=400, detail="満員です")

    kb_nickname = "名無し"
    try:
        kb_prof = await supabase.table("profiles").select("nickname").eq("id", str(kb_user.id)).execute()
        if kb_prof.data and len(kb_prof.data) > 0:
            kb_nickname = kb_prof.data[0].get("nickname") or "名無し"
    except Exception:
        kb_nickname = "名無し"

    await supabase.table("kb_players").insert({
        "room_id": data.room_id,
        "user_id": str(kb_user.id),
        "nickname": kb_nickname,
        "seat_order": len(kb_players),
        "is_alive": True,
        "hand": []
    }).execute()

    return {"success": True, "room_id": data.room_id}


# --- ゲーム開始 API ---
@router.post("/api/kb_room/start")
async def kb_start_game(data: kb_StartGameRequest, authorization: str = Header(None)):
    kb_user = await get_user_from_token(authorization)
    supabase = await get_supabase()

    kb_room_res = await supabase.table("kb_rooms").select("*").eq("room_id", data.room_id).execute()
    if not kb_room_res.data:
        raise HTTPException(status_code=404, detail="部屋が見つかりません")
    kb_room = kb_room_res.data[0]

    if kb_room["host_id"] != str(kb_user.id):
        raise HTTPException(status_code=403, detail="ホストのみ開始できます")
    if kb_room["status"] != "waiting":
        raise HTTPException(status_code=400, detail="既に開始されています")

    kb_players_res = await supabase.table("kb_players").select("*").eq("room_id", data.room_id).order("seat_order").execute()
    kb_players = kb_players_res.data or []
    kb_player_count = len(kb_players)

    if kb_player_count < 2:
        raise HTTPException(status_code=400, detail="2人以上揃う必要があります")

    kb_actions = (
        ["拒否"] * 5 + ["アタック"] * 4 + ["スキップ"] * 4 +
        ["透視"] * 5 + ["予言"] * 4 + ["ボトムドロー"] * 4 +
        ["強奪"] * 4 + ["シャッフル"] * 4
    )
    random.shuffle(kb_actions)

    for p in kb_players:
        kb_hand = ["解除"] + [kb_actions.pop() for _ in range(3)]
        await supabase.table("kb_players").update({"hand": kb_hand}).eq("room_id", data.room_id).eq("user_id", p["user_id"]).execute()

    kb_deck = kb_actions + (["こねこばくはつ"] * (kb_player_count - 1))
    random.shuffle(kb_deck)

    await supabase.table("kb_rooms").update({
        "status": "playing",
        "turn_user_id": kb_players[0]["user_id"],
        "draws_remaining": 1,
        "deck": kb_deck,
        "discard_pile": []
    }).eq("room_id", data.room_id).execute()

    return {"success": True}


# --- 部屋状態取得 API ---
@router.get("/api/kb_room/{kb_room_id}")
async def kb_get_room_status(kb_room_id: str, authorization: str = Header(None)):
    kb_user = await get_user_from_token(authorization)
    supabase = await get_supabase()

    kb_room_res = await supabase.table("kb_rooms").select("*").eq("room_id", kb_room_id).execute()
    if not kb_room_res.data:
        raise HTTPException(status_code=404, detail="部屋が見つかりません")
    kb_room = kb_room_res.data[0]

    kb_players_res = await supabase.table("kb_players").select("*").eq("room_id", kb_room_id).order("seat_order").execute()
    kb_players = kb_players_res.data or []

    kb_sanitized_players = []
    kb_my_hand = []
    for p in kb_players:
        if str(p["user_id"]) == str(kb_user.id):
            kb_my_hand = p.get("hand") or []
        kb_sanitized_players.append({
            "user_id": p["user_id"],
            "nickname": p["nickname"],
            "seat_order": p["seat_order"],
            "is_alive": p["is_alive"],
            "card_count": len(p.get("hand") or [])
        })

    return {
        "room_id": kb_room["room_id"],
        "status": kb_room["status"],
        "turn_user_id": kb_room["turn_user_id"],
        "draws_remaining": kb_room["draws_remaining"],
        "deck_count": len(kb_room.get("deck") or []),
        "discard_top": (kb_room.get("discard_pile") or [])[-1:] if kb_room.get("discard_pile") else None,
        "winner_id": kb_room["winner_id"],
        "players": kb_sanitized_players,
        "my_hand": kb_my_hand
    }


# --- カードを引く API ---
@router.post("/api/kb_game/draw")
async def kb_draw_card(data: kb_DrawCardRequest, authorization: str = Header(None)):
    kb_user = await get_user_from_token(authorization)
    supabase = await get_supabase()
    kb_user_id_str = str(kb_user.id)

    kb_room_res = await supabase.table("kb_rooms").select("*").eq("room_id", data.room_id).execute()
    if not kb_room_res.data:
        raise HTTPException(status_code=404, detail="部屋が見つかりません")
    kb_room = kb_room_res.data[0]

    if kb_room["status"] != "playing":
        raise HTTPException(status_code=400, detail="対戦中ではありません")
    if kb_room["turn_user_id"] != kb_user_id_str:
        raise HTTPException(status_code=403, detail="あなたのターンではありません")

    kb_deck = kb_room.get("deck") or []
    if not kb_deck:
        raise HTTPException(status_code=400, detail="山札がありません")

    kb_player_res = await supabase.table("kb_players").select("*").eq("room_id", data.room_id).eq("user_id", kb_user_id_str).execute()
    if not kb_player_res.data:
        raise HTTPException(status_code=404, detail="プレイヤーが見つかりません")
    kb_player = kb_player_res.data[0]
    kb_hand = kb_player.get("hand") or []

    kb_drawn = kb_deck.pop() if data.from_bottom else kb_deck.pop(0)
    kb_exploded = False

    if kb_drawn == "こねこばくはつ":
        if "解除" in kb_hand:
            kb_hand.remove("解除")
            await supabase.table("kb_players").update({"hand": kb_hand}).eq("room_id", data.room_id).eq("user_id", kb_user_id_str).execute()
        else:
            kb_exploded = True
            await supabase.table("kb_players").update({"is_alive": False, "hand": []}).eq("room_id", data.room_id).eq("user_id", kb_user_id_str).execute()
    else:
        kb_hand.append(kb_drawn)
        await supabase.table("kb_players").update({"hand": kb_hand}).eq("room_id", data.room_id).eq("user_id", kb_user_id_str).execute()

    kb_all_players = (await supabase.table("kb_players").select("*").eq("room_id", data.room_id).order("seat_order").execute()).data or []
    kb_alive = [p for p in kb_all_players if p["is_alive"]]

    if len(kb_alive) <= 1:
        kb_winner = kb_alive[0]["user_id"] if kb_alive else None
        await supabase.table("kb_rooms").update({
            "status": "finished",
            "winner_id": kb_winner,
            "deck": kb_deck
        }).eq("room_id", data.room_id).execute()
        return {"success": True, "drawn": kb_drawn, "is_exploded": kb_exploded, "is_game_over": True}

    kb_draws = kb_room["draws_remaining"] - 1
    kb_next_turn_user = kb_room["turn_user_id"]

    if kb_exploded or kb_draws <= 0:
        kb_draws = 1
        current_idx = next(i for i, p in enumerate(kb_all_players) if p["user_id"] == kb_user_id_str)
        while True:
            current_idx = (current_idx + 1) % len(kb_all_players)
            if kb_all_players[current_idx]["is_alive"]:
                kb_next_turn_user = kb_all_players[current_idx]["user_id"]
                break

    await supabase.table("kb_rooms").update({
        "deck": kb_deck,
        "draws_remaining": kb_draws,
        "turn_user_id": kb_next_turn_user
    }).eq("room_id", data.room_id).execute()

    return {"success": True, "drawn": kb_drawn, "is_exploded": kb_exploded}


# --- カード使用 API ---
@router.post("/api/kb_game/play")
async def kb_play_card(data: kb_PlayCardRequest, authorization: str = Header(None)):
    kb_user = await get_user_from_token(authorization)
    supabase = await get_supabase()
    kb_user_id_str = str(kb_user.id)

    kb_room_res = await supabase.table("kb_rooms").select("*").eq("room_id", data.room_id).execute()
    if not kb_room_res.data:
        raise HTTPException(status_code=404, detail="部屋が見つかりません")
    kb_room = kb_room_res.data[0]

    if kb_room["status"] != "playing":
        raise HTTPException(status_code=400, detail="対戦中ではありません")

    kb_player_res = await supabase.table("kb_players").select("*").eq("room_id", data.room_id).eq("user_id", kb_user_id_str).execute()
    if not kb_player_res.data:
        raise HTTPException(status_code=404, detail="プレイヤー情報がありません")
    kb_player = kb_player_res.data[0]
    kb_hand = kb_player.get("hand") or []

    if data.card_index < 0 or data.card_index >= len(kb_hand):
        raise HTTPException(status_code=400, detail="不正なカード選択です")

    kb_card = kb_hand[data.card_index]

    if kb_card == "解除":
        raise HTTPException(status_code=400, detail="解除カードは直接使用できません")

    if kb_card != "拒否" and kb_room["turn_user_id"] != kb_user_id_str:
        raise HTTPException(status_code=403, detail="あなたのターンではありません")

    kb_hand.pop(data.card_index)
    await supabase.table("kb_players").update({"hand": kb_hand}).eq("room_id", data.room_id).eq("user_id", kb_user_id_str).execute()

    kb_discard = kb_room.get("discard_pile") or []
    kb_discard.append(kb_card)

    kb_deck = kb_room.get("deck") or []
    kb_draws = kb_room["draws_remaining"]
    kb_next_turn = kb_room["turn_user_id"]

    kb_all_players = (await supabase.table("kb_players").select("*").eq("room_id", data.room_id).order("seat_order").execute()).data or []

    kb_extra_response = {}

    if kb_card == "アタック":
        kb_draws += 2
        current_idx = next(i for i, p in enumerate(kb_all_players) if p["user_id"] == kb_user_id_str)
        while True:
            current_idx = (current_idx + 1) % len(kb_all_players)
            if kb_all_players[current_idx]["is_alive"]:
                kb_next_turn = kb_all_players[current_idx]["user_id"]
                break

    elif kb_card == "スキップ":
        kb_draws -= 1
        if kb_draws <= 0:
            kb_draws = 1
            current_idx = next(i for i, p in enumerate(kb_all_players) if p["user_id"] == kb_user_id_str)
            while True:
                current_idx = (current_idx + 1) % len(kb_all_players)
                if kb_all_players[current_idx]["is_alive"]:
                    kb_next_turn = kb_all_players[current_idx]["user_id"]
                    break

    elif kb_card == "透視":
        kb_extra_response["peek"] = kb_deck[:3]

    elif kb_card == "予言":
        try:
            kb_bomb_pos = kb_deck.index("こねこばくはつ") + 1
            kb_extra_response["bomb_distance"] = kb_bomb_pos
            kb_extra_response["message"] = f"次の爆弾は上から【 {kb_bomb_pos} 枚目 】にあります。"
        except ValueError:
            kb_extra_response["bomb_distance"] = None
            kb_extra_response["message"] = "山札に爆弾は存在しません。"

    elif kb_card == "シャッフル":
        random.shuffle(kb_deck)

    elif kb_card == "強奪":
        if data.target_user_id and data.target_user_id != kb_user_id_str:
            kb_target_res = await supabase.table("kb_players").select("*").eq("room_id", data.room_id).eq("user_id", data.target_user_id).execute()
            if kb_target_res.data:
                kb_t_player = kb_target_res.data[0]
                kb_t_hand = kb_t_player.get("hand") or []
                if kb_t_hand:
                    stolen_card = kb_t_hand.pop(random.randint(0, len(kb_t_hand) - 1))
                    kb_hand.append(stolen_card)
                    await supabase.table("kb_players").update({"hand": kb_t_hand}).eq("room_id", data.room_id).eq("user_id", data.target_user_id).execute()
                    await supabase.table("kb_players").update({"hand": kb_hand}).eq("room_id", data.room_id).eq("user_id", kb_user_id_str).execute()

    await supabase.table("kb_rooms").update({
        "deck": kb_deck,
        "discard_pile": kb_discard,
        "draws_remaining": kb_draws,
        "turn_user_id": kb_next_turn
    }).eq("room_id", data.room_id).execute()

    return {"success": True, "card": kb_card, **kb_extra_response}
