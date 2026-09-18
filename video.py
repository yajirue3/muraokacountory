import os
import tempfile
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

async def is_king(user_id: str) -> bool:
    try:
        client = await get_supabase()
        res = await client.table("profiles").select("role").eq("id", user_id).execute()
        if res.data and res.data[0].get("role") == "king":
            return True
    except Exception:
        pass
    return False

# --- Google Drive 認証 & アップロード ---
async def get_gdrive_access_token() -> str:
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        raise HTTPException(status_code=500, detail="Google Drive認証情報が未設定です")

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

async def upload_file_to_drive(file_path: str, filename: str, mime_type: str = "video/mp4") -> str:
    token = await get_gdrive_access_token()
    file_size = os.path.getsize(file_path)

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
            raise HTTPException(status_code=500, detail="Driveアップロード初期化に失敗しました")
        
        session_url = init_res.headers.get("Location")
        if not session_url:
            raise HTTPException(status_code=500, detail="セッションURLの取得に失敗しました")

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
                    raise HTTPException(status_code=500, detail=f"アップロード処理中にエラー発生: {upload_res.status_code}")
                start = end + 1

    if not file_id:
        raise HTTPException(status_code=500, detail="ファイルIDが返却されませんでした")

    perm_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions"
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(perm_url, headers={"Authorization": f"Bearer {token}"}, json={
            "role": "reader", "type": "anyone"
        })

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
async def get_videos(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    king_status = await is_king(user.id)
    supabase = await get_supabase()
    
    if king_status:
        res = await supabase.table("videos").select("*").order("created_at", desc=True).execute()
    else:
        res = await supabase.table("videos").select("*").or_(f"is_private.eq.false,user_id.eq.{user.id}").order("created_at", desc=True).execute()
    
    return {"videos": res.data or [], "is_king": king_status, "user_id": str(user.id)}

@router.post("/api/videos/upload")
async def upload_video(
    title: str = Form(...),
    description: str = Form(""),
    is_private: bool = Form(False),
    file: UploadFile = File(...),
    authorization: str = Header(...)
):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()

    prof_res = await supabase.table("profiles").select("nickname").eq("id", user.id).execute()
    nickname = prof_res.data[0]["nickname"] if prof_res.data else "名無し"

    suffix = Path(file.filename).suffix or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        drive_file_id = await upload_file_to_drive(tmp_path, file.filename, file.content_type or "video/mp4")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    insert_res = await supabase.table("videos").insert({
        "user_id": str(user.id),
        "author_name": nickname,
        "title": title,
        "description": description,
        "drive_file_id": drive_file_id,
        "is_private": is_private
    }).execute()

    return {"message": "映像の記録を完了しました！", "video": insert_res.data[0]}

@router.get("/api/videos/{video_id}")
async def get_video_detail(video_id: str, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()
    
    res = await supabase.table("videos").select("*").eq("id", video_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="指定の映像は見つかりません")
    
    video = res.data[0]
    if video.get("is_private") and video["user_id"] != str(user.id) and not await is_king(user.id):
        raise HTTPException(status_code=403, detail="この映像は閲覧が制限されています")
    
    try:
        await supabase.rpc("increment_video_views", {"p_video_id": video_id}).execute()
    except Exception:
        pass
    
    prof_res = await supabase.table("profiles").select("nickname").eq("id", video["user_id"]).execute()
    if prof_res.data:
        video["author_name"] = prof_res.data[0]["nickname"]

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
        raise HTTPException(status_code=400, detail="コメント本文を入力してください")

    supabase = await get_supabase()
    prof_res = await supabase.table("profiles").select("nickname").eq("id", user.id).execute()
    nickname = prof_res.data[0]["nickname"] if prof_res.data else "名無し"

    await supabase.table("video_comments").insert({
        "video_id": video_id,
        "user_id": str(user.id),
        "author_name": nickname,
        "content": data.content.strip()
    }).execute()
    return {"message": "コメントを書き残しました"}

@router.patch("/api/videos/{video_id}/toggle_private")
async def toggle_private(video_id: str, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()
    
    res = await supabase.table("videos").select("user_id, is_private").eq("id", video_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="映像が存在しません")
        
    if res.data[0]["user_id"] != str(user.id) and not await is_king(user.id):
        raise HTTPException(status_code=403, detail="権限がありません")
        
    new_status = not res.data[0].get("is_private", False)
    await supabase.table("videos").update({"is_private": new_status}).eq("id", video_id).execute()
    return {"message": "公開設定を切り替えました", "is_private": new_status}

@router.delete("/api/videos/{video_id}")
async def delete_video(video_id: str, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()
    
    res = await supabase.table("videos").select("user_id").eq("id", video_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="映像が存在しません")
        
    if res.data[0]["user_id"] != str(user.id) and not await is_king(user.id):
        raise HTTPException(status_code=403, detail="権限がありません")
        
    await supabase.table("videos").delete().eq("id", video_id).execute()
    return {"message": "映像を消去しました"}

@router.delete("/api/comments/{comment_id}")
async def delete_comment(comment_id: str, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()
    
    c_res = await supabase.table("video_comments").select("user_id, video_id").eq("id", comment_id).execute()
    if not c_res.data:
        raise HTTPException(status_code=404, detail="コメントが見つかりません")
    
    v_res = await supabase.table("videos").select("user_id").eq("id", c_res.data[0]["video_id"]).execute()
    video_owner = v_res.data[0]["user_id"] if v_res.data else None
    
    if c_res.data[0]["user_id"] != str(user.id) and video_owner != str(user.id) and not await is_king(user.id):
        raise HTTPException(status_code=403, detail="削除権限がありません")
        
    await supabase.table("video_comments").delete().eq("id", comment_id).execute()
    return {"message": "コメントを抹消しました"}

@router.get("/api/admin/my_comments")
async def get_my_video_comments(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()
    
    my_videos = await supabase.table("videos").select("id, title").eq("user_id", str(user.id)).execute()
    if not my_videos.data:
        return []
        
    v_map = {v["id"]: v["title"] for v in my_videos.data}
    v_ids = list(v_map.keys())
    
    comments_res = await supabase.table("video_comments").select("*").in_("video_id", v_ids).order("created_at", desc=True).execute()
    result = comments_res.data or []
    for c in result:
        c["video_title"] = v_map.get(c["video_id"], "不明な動画")
    return result
