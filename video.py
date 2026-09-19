import os
import time
from pathlib import Path
from typing import Optional
import httpx
from fastapi import APIRouter, HTTPException, Header, Request, UploadFile, File, Form, Query
from fastapi.responses import HTMLResponse, StreamingResponse
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

_view_history = {}

# --- 【究極高速化】コネクションプールの拡張 ---
# 800MB動画のシーク（複数回リクエスト）にも即座に応答できるよう最大接続数を大幅引き上げ
http_client = httpx.AsyncClient(
    timeout=120.0, 
    limits=httpx.Limits(max_keepalive_connections=100, max_connections=500)
)

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
    
    res = await http_client.post(url, data=data)
    if res.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Googleトークン取得エラー: {res.status_code}")
    return res.json()["access_token"]

async def upload_file_to_drive(file_obj, filename: str, mime_type: str, file_size: int) -> str:
    token = await get_gdrive_access_token()

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

    init_res = await http_client.post(init_url, headers=headers, json=metadata)
    if init_res.status_code != 200:
        raise HTTPException(status_code=500, detail="アップロードセッション作成失敗")
    
    session_url = init_res.headers.get("Location")
    
    chunk_size = 32 * 1024 * 1024  
    file_id = None

    start = 0
    file_obj.seek(0)
    
    while start < file_size:
        chunk = file_obj.read(chunk_size)
        if not chunk: break
        end = start + len(chunk) - 1
        chunk_headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(len(chunk))
        }
        upload_res = await http_client.put(session_url, headers=chunk_headers, content=chunk)
        
        if upload_res.status_code in (200, 201):
            file_id = upload_res.json().get("id")
            break
        elif upload_res.status_code != 308:
            raise HTTPException(status_code=500, detail="ファイル転送エラー")
        start = end + 1

    if not file_id:
        raise HTTPException(status_code=500, detail="ファイルIDの取得失敗")

    perm_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions"
    await http_client.post(perm_url, headers={"Authorization": f"Bearer {token}"}, json={"role": "reader", "type": "anyone"})
        
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

@router.get("/channel", response_class=HTMLResponse)
async def get_channel_page(request: Request):
    return templates.TemplateResponse(request=request, name="channel.html")

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

@router.get("/api/subscriptions")
async def get_my_subscriptions(authorization: str = Header(None)):
