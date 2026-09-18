import os
import tempfile
from pathlib import Path
import httpx
from fastapi import APIRouter, HTTPException, Header, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# データベース接続
from db import get_supabase

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# 環境変数からDrive APIの認証情報を取得
CLIENT_ID = os.getenv("GDRIVE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GDRIVE_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("GDRIVE_REFRESH_TOKEN")
FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID")

# --- 共通認証関数 ---
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

# --- Google Drive: アクセストークン取得 ---
async def get_gdrive_access_token() -> str:
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        raise HTTPException(status_code=500, detail="Google Driveの認証情報が設定されていません")

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
            raise HTTPException(status_code=500, detail=f"Googleトークン取得エラー: {res.text}")
        return res.json()["access_token"]

# --- Google Drive: レジュマブル(分割)アップロード ---
async def upload_file_to_drive(file_path: str, filename: str, mime_type: str = "video/mp4") -> str:
    token = await get_gdrive_access_token()
    file_size = os.path.getsize(file_path)

    # 1. セッション初期化
    init_url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": mime_type,
        "X-Upload-Content-Length": str(file_size)
    }
    metadata = {
        "name": filename,
        "parents": [FOLDER_ID] if FOLDER_ID else []
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        init_res = await client.post(init_url, headers=headers, json=metadata)
        if init_res.status_code != 200:
            raise HTTPException(status_code=500, detail="Driveアップロードセッション作成に失敗しました")
        
        session_url = init_res.headers.get("Location")
        if not session_url:
            raise HTTPException(status_code=500, detail="セッションURLが取得できませんでした")

        # 2. チャンク転送（8MBずつ転送してメモリを節約）
        chunk_size = 8 * 1024 * 1024
        file_id = None

        with open(file_path, "rb") as f:
            start = 0
            while start < file_size:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                
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
                    raise HTTPException(status_code=500, detail=f"アップロードエラー: {upload_res.status_code}")
                
                start = end + 1

    if not file_id:
        raise HTTPException(status_code=500, detail="ファイルIDが返却されませんでした")

    # 3. ファイルの公開設定（リンクを知っている全員が閲覧可能）
    perm_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions"
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(perm_url, headers={"Authorization": f"Bearer {token}"}, json={
            "role": "reader",
            "type": "anyone"
        })

    return file_id

# --- 画面ルーティング ---
@router.get("/theater", response_class=HTMLResponse)
async def get_theater_page(request: Request):
    return templates.TemplateResponse(request=request, name="theater.html")

# --- API: 動画一覧取得 ---
@router.get("/api/videos")
async def get_videos():
    supabase = await get_supabase()
    res = await supabase.table("videos").select("*").order("created_at", desc=True).limit(50).execute()
    return res.data or []

# --- API: 動画アップロード ---
@router.post("/api/videos/upload")
async def upload_video(
    title: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File
