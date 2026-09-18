from pathlib import Path
from fastapi import APIRouter, HTTPException, Header, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# db.py から直接インポート（循環インポートなし）
from db import get_supabase

router = APIRouter()

# テンプレートのパス設定（casino.py と同一仕様）
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# --- 共通認証関数（casino.py と同一ロジックを内包） ---
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


# --- リクエストモデル ---
class Score2048Submit(BaseModel):
    score: int
    max_tile: int


# --------------------------------------------------
# 2048 画面配信ルート
# --------------------------------------------------
@router.get("/2048", response_class=HTMLResponse)
async def get_2048_page(request: Request):
    return templates.TemplateResponse(request=request, name="2048.html")


# --------------------------------------------------
# 2048 API：スコア登録 & 順位取得
# --------------------------------------------------
@router.post("/api/2048/score")
async def submit_2048_score(data: Score2048Submit, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()

    # 理論値・負数などの簡易チート対策
    if data.score < 0 or data.max_tile < 2:
        raise HTTPException(status_code=400, detail="無効なスコアデータです")

    # プレイヤーのニックネーム取得
    profile_res = await supabase.table("profiles").select("nickname").eq("id", user.id).execute()
    nickname = profile_res.data[0].get("nickname") if profile_res.data else "名無し国民"
    if not nickname:
        nickname = "名無し国民"

    # スコアの記録
    await supabase.table("scores_2048").insert({
        "user_id": str(user.id),
        "nickname": nickname,
        "score": data.score,
        "max_tile": data.max_tile
    }).execute()

    # 最新ランキング（トップ10）取得
    ranking_res = await supabase.table("scores_2048")\
        .select("nickname, score, max_tile")\
        .order("score", desc=True)\
        .limit(10).execute()
    
    ranking = ranking_res.data or []
    
    # 順位判定（自分より上のスコア件数 + 1）
    higher_scores = await supabase.table("scores_2048").select("id", count="exact").gt("score", data.score).execute()
    my_rank = (higher_scores.count or 0) + 1

    return {
        "success": True,
        "my_rank": my_rank,
        "ranking": ranking
    }


# --------------------------------------------------
# 2048 API：ランキング単体取得
# --------------------------------------------------
@router.get("/api/2048/ranking")
async def get_2048_ranking():
    supabase = await get_supabase()
    ranking_res = await supabase.table("scores_2048")\
        .select("nickname, score, max_tile")\
        .order("score", desc=True)\
        .limit(10).execute()
    return {"ranking": ranking_res.data or []}
