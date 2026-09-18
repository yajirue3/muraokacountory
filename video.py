import os
from pathlib import Path
import httpx
from fastapi import APIRouter, HTTPException, Header, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from db import get_supabase

router = APIRouter()
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

CLIENT_ID = os.getenv("GDRIVE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GDRIVE_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("GDRIVE_REFRESH_TOKEN")
FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID")

class CommentCreate(BaseModel):
    content: str

# --- 共通関数 ---
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

async def is_king(user_id: str) -> bool:
    try:
        client = await get_supabase()
        res = await client.table("profiles").select("role").eq("id", user_id).execute()
        if res.data and res.data[0].get("role") == "king":
            return True
    except Exception:
        pass
    return False

# --- 画面ルーティング ---
@router.get("/videos", response_class=HTMLResponse)
async def get_videos_page(request: Request):
    return templates.TemplateResponse(request=request, name="videos.html")

@router.get("/watch", response_class=HTMLResponse)
async def get_watch_page(request: Request):
    return templates.TemplateResponse(request=request, name="videowatch.html")

@router.get("/manage_videos", response_class=HTMLResponse)
async def get_manage_page(request: Request):
    return templates.TemplateResponse(request=request, name="videomanage.html")


# --- API ---

@router.get("/api/videos")
async def get_videos(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    king_status = await is_king(user.id)
    supabase = await get_supabase()
    
    # 王様は全部見える、それ以外は「公開」または「自分の動画」のみ
    if king_status:
        res = await supabase.table("videos").select("*").order("created_at", desc=True).execute()
    else:
        res = await supabase.table("videos").select("*").or_(f"is_private.eq.false,user_id.eq.{user.id}").order("created_at", desc=True).execute()
    return {"videos": res.data or [], "is_king": king_status, "user_id": user.id}

@router.get("/api/videos/{video_id}")
async def get_video_detail(video_id: str, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()
    res = await supabase.table("videos").select("*").eq("id", video_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="映像が見つかりません")
    
    video = res.data[0]
    # 非公開チェック
    if video["is_private"] and video["user_id"] != user.id and not await is_king(user.id):
        raise HTTPException(status_code=403, detail="この映像は非公開です")
    
    # 投稿者情報
    prof_res = await supabase.table("profiles").select("nickname").eq("id", video["user_id"]).execute()
    author_name = prof_res.data[0]["nickname"] if prof_res.data else video["author_name"]
    video["author_name"] = author_name
    return video

@router.get("/api/videos/{video_id}/comments")
async def get_comments(video_id: str):
    supabase = await get_supabase()
    res = await supabase.table("video_comments").select("*, profiles:user_id(nickname)").eq("video_id", video_id).order("created_at", desc=True).execute()
    return res.data or []

@router.post("/api/videos/{video_id}/comments")
async def post_comment(video_id: str, data: CommentCreate, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()
    
    prof_res = await supabase.table("profiles").select("nickname").eq("id", user.id).execute()
    nickname = prof_res.data[0]["nickname"] if prof_res.data else "名無し"
    
    await supabase.table("video_comments").insert({
        "video_id": video_id,
        "user_id": user.id,
        "author_name": nickname,
        "content": data.content
    }).execute()
    return {"message": "コメントを書き残しました"}

@router.patch("/api/videos/{video_id}/toggle_private")
async def toggle_private(video_id: str, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()
    
    res = await supabase.table("videos").select("user_id, is_private").eq("id", video_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="映像がありません")
        
    if res.data[0]["user_id"] != user.id and not await is_king(user.id):
        raise HTTPException(status_code=403, detail="権限がありません")
        
    new_status = not res.data[0]["is_private"]
    await supabase.table("videos").update({"is_private": new_status}).eq("id", video_id).execute()
    return {"message": "公開設定を変更しました", "is_private": new_status}

@router.delete("/api/videos/{video_id}")
async def delete_video(video_id: str, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()
    
    res = await supabase.table("videos").select("user_id").eq("id", video_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="映像がありません")
        
    if res.data[0]["user_id"] != user.id and not await is_king(user.id):
        raise HTTPException(status_code=403, detail="権限がありません")
        
    await supabase.table("videos").delete().eq("id", video_id).execute()
    return {"message": "映像を完全に削除しました"}

@router.delete("/api/comments/{comment_id}")
async def delete_comment(comment_id: str, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()
    
    c_res = await supabase.table("video_comments").select("user_id, video_id").eq("id", comment_id).execute()
    if not c_res.data:
        raise HTTPException(status_code=404, detail="コメントがありません")
    
    v_res = await supabase.table("videos").select("user_id").eq("id", c_res.data[0]["video_id"]).execute()
    video_owner = v_res.data[0]["user_id"] if v_res.data else None
    
    # コメント投稿者本人 or 動画の投稿者(自分の動画についたコメント) or 国王 なら消せる
    if c_res.data[0]["user_id"] != user.id and video_owner != user.id and not await is_king(user.id):
        raise HTTPException(status_code=403, detail="削除権限がありません")
        
    await supabase.table("video_comments").delete().eq("id", comment_id).execute()
    return {"message": "コメントを消去しました"}
