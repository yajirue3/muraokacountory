import random
from fastapi import APIRouter, Request, HTTPException, Header
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
# 既存の認証・DB接続関数（環境に合わせてインポートしてください）
from app.dependencies import get_user_from_token, get_supabase

router = APIRouter()
templates = Jinja2Templates(directory="templates")

class ScoreSubmit(BaseModel):
    score: int
    max_tile: int

@router.get("/2048", response_class=HTMLResponse)
async def get_2048_page(request: Request):
    return templates.TemplateResponse(request=request, name="2048.html")

@router.post("/api/2048/score")
async def submit_score(data: ScoreSubmit, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()

    # 不正スコアの弾き (理論上、初手でクリア等の異常値を簡易ブロック)
    if data.score < 0 or data.max_tile < 2:
        raise HTTPException(status_code=400, detail="Invalid score")

    # ニックネームの取得[span_0](start_span)[span_0](end_span)
    profile_res = await supabase.table("profiles").select("nickname").eq("id", user.id).execute()
    nickname = profile_res.data[0].get("nickname") if profile_res.data else "UNKNOWN"
    if not nickname:
        nickname = "UNKNOWN"

    # スコアの保存
    await supabase.table("scores_2048").insert({
        "user_id": user.id,
        "nickname": nickname,
        "score": data.score,
        "max_tile": data.max_tile
    }).execute()

    # 最新ランキング（トップ10）の取得
    ranking_res = await supabase.table("scores_2048")\
        .select("nickname, score, max_tile")\
        .order("score", desc=True)\
        .limit(10).execute()
    
    ranking = ranking_res.data
    
    # 自分の順位計算（自分よりスコアが高いレコードの数 + 1）
    higher_scores = await supabase.table("scores_2048").select("id", count="exact").gt("score", data.score).execute()
    my_rank = higher_scores.count + 1

    return {
        "success": True,
        "my_rank": my_rank,
        "ranking": ranking
    }

@router.get("/api/2048/ranking")
async def get_ranking():
    supabase = await get_supabase()
    ranking_res = await supabase.table("scores_2048")\
        .select("nickname, score, max_tile")\
        .order("score", desc=True)\
        .limit(10).execute()
    return {"ranking": ranking_res.data}
