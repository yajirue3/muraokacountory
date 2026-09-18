import os
import tempfile
from pathlib import Path
import httpx
from fastapi import APIRouter, HTTPException, Header, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

# データベース接続用の関数（既存の仕様に合わせる）
from db import get_supabase

router = APIRouter()
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# 環境変数
CLIENT_ID = os.getenv("GDRIVE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GDRIVE_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("GDRIVE_REFRESH_TOKEN")
FOLDER_ID = os.getenv("GDRIVE_FOLDER_ID")

class CommentCreate(BaseModel):
    content: str

# --- 認証・権限チェック ---
def get_user_from_token(authorization: str):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証トークンがありません")
    token = authorization.split(" ")[1]
    supabase = get_supabase()
    try:
        user_res = supabase.auth.get_user(token)
        if not user_res or not user_res.user:
            raise HTTPException(status_code=401, detail="ユーザー情報の取得に失敗しました")
        return user_res.user
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"無効なトークンです: {str(e)}")

def is_king(user_id: str) -> bool:
    try:
        supabase = get_supabase()
        res = supabase.table("profiles").select("role").eq("id", user_id).execute()
        if res.data and res.data[0].get("role") == "king":
            return True
    except Exception:
        pass
    return False

# --- Google Drive アップロード ---
async def upload_file_to_drive(file_path: str, filename: str, mime_type: str) -> str:
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        raise HTTPException(status_code=500, detail="Driveの認証情報が設定されていません")

    # 1. アクセストークンの取得
    token_url = "https://oauth2.googleapis.com/token"
    token_data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token"
    }
    
    async with httpx.AsyncClient() as client:
        token_res = await client.post(token_url, data=token_data)
        if token_res.status_code != 200:
            raise HTTPException(status_code=500, detail="Driveアクセストークンの取得に失敗しました")
        access_token = token_res.json()["access_token"]

    # 2. アップロードセッションの開始
    file_size = os.path.getsize(file_path)
    init_url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": mime_type,
        "X-Upload-Content-Length": str(file_size)
    }
    metadata = {"name": filename}
    if FOLDER_ID:
        metadata["parents"] = [FOLDER_ID]

    async with httpx.AsyncClient(timeout=120.0) as client:
        init_res = await client.post(init_url, headers=headers, json=metadata)
        if init_res.status_code != 200:
            raise HTTPException(status_code=500, detail="アップロードの初期化に失敗しました")
        
        upload_url = init_res.headers.get("Location")
        
        # 3. ファイル本体の送信
        with open(file_path, "rb") as f:
            file_data = f.read()
            upload_res = await client.put(
                upload_url, 
                headers={"Content-Length": str(file_size)}, 
                content=file_data
            )
            
            if upload_res.status_code not in (200, 201):
                raise HTTPException(status_code=500, detail=f"ファイル転送エラー: {upload_res.status_code}")
            
            file_id = upload_res.json()["id"]

    # 4. 誰でも閲覧できるように権限を変更
    perm_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions"
    async with httpx.AsyncClient() as client:
        await client.post(
            perm_url, 
            headers={"Authorization": f"Bearer {access_token}"}, 
            json={"role": "reader", "type": "anyone"}
        )

    return file_id

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

# --- API群 ---
@router.get("/api/videos")
def get_videos(authorization: str = Header(None)):
    user = get_user_from_token(authorization)
    king_status = is_king(user.id)
    supabase = get_supabase()
    
    if king_status:
        res = supabase.table("videos").select("*").order("created_at", desc=True).execute()
    else:
        res = supabase.table("videos").select("*").or_(f"is_private.eq.false,user_id.eq.{user.id}").order("created_at", desc=True).execute()
    
    return {"videos": res.data or [], "is_king": king_status, "user_id": str(user.id)}

@router.post("/api/videos/upload")
async def upload_video(
    title: str = Form(...),
    description: str = Form(""),
    is_private: bool = Form(False),
    file: UploadFile = File(...),
    authorization: str = Header(...)
):
    user = get_user_from_token(authorization)
    supabase = get_supabase()

    prof_res = supabase.table("profiles").select("nickname").eq("id", user.id).execute()
    nickname = prof_res.data[0]["nickname"] if prof_res.data else "名無し"

    suffix = Path(file.filename).suffix or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        mime = file.content_type or "video/mp4"
        drive_file_id = await upload_file_to_drive(tmp_path, file.filename, mime)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    insert_res = supabase.table("videos").insert({
        "user_id": str(user.id),
        "author_name": nickname,
        "title": title,
        "description": description,
        "drive_file_id": drive_file_id,
        "is_private": is_private
    }).execute()

    return {"message": "映像の記録を完了しました", "video": insert_res.data[0]}

@router.get("/api/videos/{video_id}")
def get_video_detail(video_id: str, authorization: str = Header(None)):
    user = get_user_from_token(authorization)
    supabase = get_supabase()
    
    res = supabase.table("videos").select("*").eq("id", video_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="指定の映像は見つかりません")
    
    video = res.data[0]
    if video.get("is_private") and video["user_id"] != str(user.id) and not is_king(user.id):
        raise HTTPException(status_code=403, detail="この映像は非公開です")
    
    # 閲覧数のインクリメント (エラーが起きても停止させない)
    try:
        current_views = video.get("views") or 0
        supabase.table("videos").update({"views": current_views + 1}).eq("id", video_id).execute()
        video["views"] = current_views + 1
    except Exception:
        pass

    return video

@router.get("/api/videos/{video_id}/comments")
def get_comments(video_id: str):
    supabase = get_supabase()
    res = supabase.table("video_comments").select("*").eq("video_id", video_id).order("created_at", desc=True).execute()
    return res.data or []

@router.post("/api/videos/{video_id}/comments")
def post_comment(video_id: str, data: CommentCreate, authorization: str = Header(None)):
    user = get_user_from_token(authorization)
    if not data.content.strip():
        raise HTTPException(status_code=400, detail="本文を入力してください")

    supabase = get_supabase()
    prof_res = supabase.table("profiles").select("nickname").eq("id", user.id).execute()
    nickname = prof_res.data[0]["nickname"] if prof_res.data else "名無し"

    supabase.table("video_comments").insert({
        "video_id": video_id,
        "user_id": str(user.id),
        "author_name": nickname,
        "content": data.content.strip()
    }).execute()
    return {"message": "コメントを投稿しました"}

@router.delete("/api/videos/{video_id}")
def delete_video(video_id: str, authorization: str = Header(None)):
    user = get_user_from_token(authorization)
    supabase = get_supabase()
    
    res = supabase.table("videos").select("user_id").eq("id", video_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="映像が存在しません")
        
    if res.data[0]["user_id"] != str(user.id) and not is_king(user.id):
        raise HTTPException(status_code=403, detail="権限がありません")
        
    supabase.table("videos").delete().eq("id", video_id).execute()
    return {"message": "映像を消去しました"}

@router.patch("/api/videos/{video_id}/toggle_private")
def toggle_private(video_id: str, authorization: str = Header(None)):
    user = get_user_from_token(authorization)
    supabase = get_supabase()
    
    res = supabase.table("videos").select("user_id, is_private").eq("id", video_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="映像が存在しません")
        
    if res.data[0]["user_id"] != str(user.id) and not is_king(user.id):
        raise HTTPException(status_code=403, detail="権限がありません")
        
    new_status = not res.data[0].get("is_private", False)
    supabase.table("videos").update({"is_private": new_status}).eq("id", video_id).execute()
    return {"message": "公開設定を変更しました", "is_private": new_status}

@router.delete("/api/comments/{comment_id}")
def delete_comment(comment_id: str, authorization: str = Header(None)):
    user = get_user_from_token(authorization)
    supabase = get_supabase()
    
    c_res = supabase.table("video_comments").select("user_id, video_id").eq("id", comment_id).execute()
    if not c_res.data:
        raise HTTPException(status_code=404, detail="コメントがありません")
    
    v_res = supabase.table("videos").select("user_id").eq("id", c_res.data[0]["video_id"]).execute()
    video_owner = v_res.data[0]["user_id"] if v_res.data else None
    
    if c_res.data[0]["user_id"] != str(user.id) and video_owner != str(user.id) and not is_king(user.id):
        raise HTTPException(status_code=403, detail="権限がありません")
        
    supabase.table("video_comments").delete().eq("id", comment_id).execute()
    return {"message": "コメントを削除しました"}
