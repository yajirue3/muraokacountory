import os
import tempfile
from pathlib import Path
from typing import Optional
import httpx
from fastapi import APIRouter, HTTPException, Header, Request, UploadFile, File, Form, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from db import get_supabase
from main import get_user_from_token, is_king

router = APIRouter()
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

CLIENT_ID = os.getenv("GDRIVE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GDRIVE_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("GDRIVE_REFRESH_TOKEN")
FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID")

class CommentCreate(BaseModel):
    content: str

async def get_gdrive_access_token() -> str:
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        raise HTTPException(status_code=500, detail="認証情報が設定されていません")
    
    url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token"
    }
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.post(url, data=data)
        if res.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Googleトークン取得エラー: {res.status_code}")
        return res.json()["access_token"]

async def upload_file_to_drive(file_path: str, filename: str, mime_type: str) -> str:
    token = await get_gdrive_access_token()
    file_size = os.path.getsize(file_path)

    init_url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": mime_type,
        "X-Upload-Content-Length": str(file_size)
    }
    metadata = {"name": filename}
    if FOLDER_ID:
        metadata["parents"] = [FOLDER_ID]

    async with httpx.AsyncClient(timeout=60.0) as client:
        init_res = await client.post(init_url, headers=headers, json=metadata)
        if init_res.status_code != 200:
            raise HTTPException(status_code=500, detail="アップロードセッション作成失敗")
        
        session_url = init_res.headers.get("Location")
        chunk_size = 8 * 1024 * 1024  
        file_id = None

        with open(file_path, "rb") as f:
            start = 0
            while start < file_size:
                chunk = f.read(chunk_size)
                if not chunk: break
                end = start + len(chunk) - 1
                chunk_headers = {
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Content-Length": str(len(chunk))
                }
                upload_res = await client.put(session_url, headers=chunk_headers, content=chunk)
                
                if upload_res.status_code in (200, 201):
                    file_id = upload_res.json().get("id")
                    break
                elif upload_res.status_code != 308:
                    raise HTTPException(status_code=500, detail="ファイル転送エラー")
                start = end + 1

    if not file_id:
        raise HTTPException(status_code=500, detail="ファイルIDの取得失敗")

    perm_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions"
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(perm_url, headers={"Authorization": f"Bearer {token}"}, json={"role": "reader", "type": "anyone"})
        
    return file_id

@router.get("/videos", response_class=HTMLResponse)
async def get_videos_page(request: Request):
    return templates.TemplateResponse(request=request, name="videos.html")

@router.get("/watch", response_class=HTMLResponse)
async def get_watch_page(request: Request):
    return templates.TemplateResponse(request=request, name="videowatch.html")

@router.get("/manage_videos", response_class=HTMLResponse)
async def get_manage_page(request: Request):
    return templates.TemplateResponse(request=request, name="videomanage.html")

@router.get("/api/videos")
async def get_videos(page: int = Query(1, ge=1), authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    king_status = await is_king(user.id)
    supabase = await get_supabase()
    
    limit = 24
    offset = (page - 1) * limit
    
    if king_status:
        res = await supabase.table("videos").select("*").order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    else:
        res = await supabase.table("videos").select("*").or_(f"is_private.eq.false,user_id.eq.{user.id}").order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    
    return {"videos": res.data or [], "is_king": king_status, "user_id": str(user.id)}

@router.post("/api/videos/upload")
async def upload_video(
    title: str = Form(...),
    description: str = Form(""),
    is_private: bool = Form(False),
    file: UploadFile = File(...),
    thumbnail: Optional[UploadFile] = File(None),
    authorization: str = Header(None)
):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()

    prof_res = await supabase.table("profiles").select("nickname").eq("id", user.id).execute()
    nickname = prof_res.data[0]["nickname"] if prof_res.data else "不明"

    # 1. 動画の保存とアップロード
    v_suffix = Path(file.filename).suffix or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=v_suffix) as tmp:
        tmp_path = tmp.name
        
    try:
        with open(tmp_path, "wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk: break
                buffer.write(chunk)
                
        mime = file.content_type or "video/mp4"
        drive_file_id = await upload_file_to_drive(tmp_path, file.filename, mime)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    # 2. サムネイルの保存とアップロード（バグ修正済み：チャンク書き込みで確実にディスクに保存）
    thumbnail_drive_id = None
    if thumbnail is not None and thumbnail.filename:
        t_suffix = Path(thumbnail.filename).suffix or ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=t_suffix) as t_tmp:
            t_tmp_path = t_tmp.name
            
        try:
            with open(t_tmp_path, "wb") as t_buffer:
                while True:
                    t_chunk = await thumbnail.read(1024 * 1024)
                    if not t_chunk: break
                    t_buffer.write(t_chunk)
                    
            t_mime = thumbnail.content_type or "image/jpeg"
            thumbnail_drive_id = await upload_file_to_drive(t_tmp_path, thumbnail.filename, t_mime)
        finally:
            if os.path.exists(t_tmp_path):
                os.remove(t_tmp_path)

    # 3. データベースへ書き込み
    insert_res = await supabase.table("videos").insert({
        "user_id": str(user.id),
        "author_name": nickname,
        "title": title,
        "description": description,
        "drive_file_id": drive_file_id,
        "thumbnail_drive_id": thumbnail_drive_id,
        "is_private": is_private,
        "views": 0
    }).execute()

    return {"message": "アップロード完了", "video": insert_res.data[0]}

@router.get("/api/videos/{video_id}")
async def get_video_detail(video_id: str, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    king_status = await is_king(user.id)
    supabase = await get_supabase()
    
    res = await supabase.table("videos").select("*").eq("id", video_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="映像が見つかりません")
    
    video = res.data[0]
    if video.get("is_private") and video["user_id"] != str(user.id) and not king_status:
        raise HTTPException(status_code=403, detail="非公開の動画です")
    
    try:
        current_views = video.get("views") or 0
        await supabase.table("videos").update({"views": current_views + 1}).eq("id", video_id).execute()
        video["views"] = current_views + 1
    except Exception:
        pass
    return video

@router.get("/api/videos/{video_id}/comments")
async def get_comments(video_id: str):
    supabase = await get_supabase()
    res = await supabase.table("video_comments").select("*").eq("video_id", video_id).order("created_at", desc=True).execute()
    return res.data or []

@router.post("/api/videos/{video_id}/comments")
async def post_comment(video_id: str, data: CommentCreate, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    if not data.content.strip():
        raise HTTPException(status_code=400, detail="本文を入力してください")

    supabase = await get_supabase()
    prof_res = await supabase.table("profiles").select("nickname").eq("id", user.id).execute()
    nickname = prof_res.data[0]["nickname"] if prof_res.data else "不明"

    await supabase.table("video_comments").insert({
        "video_id": video_id,
        "user_id": str(user.id),
        "author_name": nickname,
        "content": data.content.strip()
    }).execute()
    return {"message": "コメントを投稿しました"}

@router.delete("/api/videos/{video_id}")
async def delete_video(video_id: str, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    king_status = await is_king(user.id)
    supabase = await get_supabase()
    
    res = await supabase.table("videos").select("user_id").eq("id", video_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="映像が存在しません")
        
    if res.data[0]["user_id"] != str(user.id) and not king_status:
        raise HTTPException(status_code=403, detail="権限がありません")
        
    await supabase.table("videos").delete().eq("id", video_id).execute()
    return {"message": "削除しました"}

@router.patch("/api/videos/{video_id}/toggle_private")
async def toggle_private(video_id: str, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    king_status = await is_king(user.id)
    supabase = await get_supabase()
    
    res = await supabase.table("videos").select("user_id, is_private").eq("id", video_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="映像が存在しません")
        
    if res.data[0]["user_id"] != str(user.id) and not king_status:
        raise HTTPException(status_code=403, detail="権限がありません")
        
    new_status = not res.data[0].get("is_private", False)
    await supabase.table("videos").update({"is_private": new_status}).eq("id", video_id).execute()
    return {"message": "設定を変更しました", "is_private": new_status}

@router.delete("/api/comments/{comment_id}")
async def delete_comment(comment_id: str, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    king_status = await is_king(user.id)
    supabase = await get_supabase()
    
    c_res = await supabase.table("video_comments").select("user_id, video_id").eq("id", comment_id).execute()
    if not c_res.data:
        raise HTTPException(status_code=404, detail="コメントがありません")
    
    v_res = await supabase.table("videos").select("user_id").eq("id", c_res.data[0]["video_id"]).execute()
    video_owner = v_res.data[0]["user_id"] if v_res.data else None
    
    if c_res.data[0]["user_id"] != str(user.id) and video_owner != str(user.id) and not king_status:
        raise HTTPException(status_code=403, detail="権限がありません")
        
    await supabase.table("video_comments").delete().eq("id", comment_id).execute()
    return {"message": "削除しました"}
