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
        # 1人1枠のビューからトップ10を取得
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
        
        # profilesテーブルの取得でエラーが起きてもクラッシュしないように保護
        try:
            profile_res = await supabase.table("profiles").select("nickname").eq("id", str(user.id)).execute()
            if profile_res.data and len(profile_res.data) > 0:
                nickname = profile_res.data[0].get("nickname") or "名無し"
        except Exception as pe:
            print(f"[Warning] 2048 profiles取得スキップ: {pe}")
            nickname = "名無し"

        # ログとして全記録をInsertする（ランキング抽出はビューが自動で処理する）
        await supabase.table("scores_2048").insert({
            "user_id": str(user.id),
            "nickname": nickname,
            "score": data.score,
            "max_tile": data.max_tile
        }).execute()

        # 更新後の最新ランキングを取得
        daily_res = await supabase.table("view_2048_daily").select("*").order("score", desc=True).limit(10).execute()
        alltime_res = await supabase.table("view_2048_alltime").select("*").order("score", desc=True).limit(10).execute()
        
        # 自分の累計順位を判定
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
    remaining_blocks: int  # 0なら全消しボーナス達成などの指標に利用

# --- 画面配信 ---
@router.get("/samegame", response_class=HTMLResponse)
async def get_samegame_page(request: Request):
    return templates.TemplateResponse(request=request, name="samegame.html")

# --- ランキング単体取得（いつでも見れる用） ---
@router.get("/api/samegame/ranking")
async def get_samegame_ranking():
    supabase = await get_supabase()
    
    try:
        # 1人1枠のビューからトップ10を取得
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
        
        # profilesテーブルの取得でエラーが起きてもクラッシュしないように保護
        try:
            profile_res = await supabase.table("profiles").select("nickname").eq("id", str(user.id)).execute()
            if profile_res.data and len(profile_res.data) > 0:
                nickname = profile_res.data[0].get("nickname") or "名無し"
        except Exception as pe:
            print(f"[Warning] samegame profiles取得スキップ: {pe}")
            nickname = "名無し"

        # ログとして全記録をInsertする
        await supabase.table("scores_samegame").insert({
            "user_id": str(user.id),
            "nickname": nickname,
            "score": data.score,
            "remaining_blocks": data.remaining_blocks
        }).execute()

        # 更新後の最新ランキングを取得
        daily_res = await supabase.table("view_samegame_daily").select("*").order("score", desc=True).limit(10).execute()
        alltime_res = await supabase.table("view_samegame_alltime").select("*").order("score", desc=True).limit(10).execute()
        
        # 自分の累計順位を判定
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
