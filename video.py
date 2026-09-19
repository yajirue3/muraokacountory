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

# --- 高速化の要 ---
# HTTP接続を使い回す（コネクションプーリング）ことで、リクエストごとのTLSハンドシェイクを削減
http_client = httpx.AsyncClient(
    timeout=120.0, 
    limits=httpx.Limits(max_keepalive_connections=50, max_connections=100)
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
    
    # 共通のクライアントを使用
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

    # 共通のクライアントを使用
    init_res = await http_client.post(init_url, headers=headers, json=metadata)
    if init_res.status_code != 200:
        raise HTTPException(status_code=500, detail="アップロードセッション作成失敗")
    
    session_url = init_res.headers.get("Location")
    
    # チャンクサイズを 32MB に拡大（転送回数を減らし高速化）
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
        # 共通のクライアントを使用
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
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()

    try:
        sub_res = await supabase.table("subscriptions").select("channel_id").eq("subscriber_id", str(user.id)).execute()
        if not sub_res.data:
            return []

        channel_ids = [str(s["channel_id"]) for s in sub_res.data if s.get("channel_id")]
        if not channel_ids:
            return []

        prof_res = await supabase.table("profiles").select("id, nickname, avatar_drive_id").in_("id", channel_ids).execute()
        return prof_res.data or []
    except Exception as e:
        print(f"[ERROR] get_my_subscriptions: {e}")
        return []

@router.get("/api/videos/subscribed")
async def get_subscribed_videos(page: int = Query(1, ge=1), authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()

    try:
        sub_res = await supabase.table("subscriptions").select("channel_id").eq("subscriber_id", str(user.id)).execute()
        if not sub_res.data:
            return {"videos": []}

        channel_ids = [str(s["channel_id"]) for s in sub_res.data if s.get("channel_id")]
        if not channel_ids:
            return {"videos": []}

        limit = 24
        offset = (page - 1) * limit

        vid_res = await supabase.table("videos") \
            .select("*") \
            .in_("user_id", channel_ids) \
            .eq("is_private", False) \
            .order("created_at", desc=True) \
            .range(offset, offset + limit - 1) \
            .execute()

        videos = vid_res.data or []
        if not videos:
            return {"videos": []}

        author_ids = list(set([str(v["user_id"]) for v in videos if v.get("user_id")]))
        if author_ids:
            try:
                prof_res = await supabase.table("profiles").select("id, avatar_drive_id").in_("id", author_ids).execute()
                prof_map = {str(p["id"]): p.get("avatar_drive_id") for p in (prof_res.data or [])}

                for v in videos:
                    v["author_avatar_drive_id"] = prof_map.get(str(v.get("user_id")))
            except Exception as p_err:
                print(f"[WARNING] プロフィール取得失敗: {p_err}")

        return {"videos": videos}

    except Exception as e:
        print(f"[ERROR] get_subscribed_videos: {e}")
        return {"videos": []}

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

    # --- 高速化: 一時ファイル廃止・直接転送 ---
    file.file.seek(0, os.SEEK_END)
    file_size = file.file.tell()
    file.file.seek(0)
    mime = file.content_type or "video/mp4"
    
    drive_file_id = await upload_file_to_drive(file.file, file.filename, mime, file_size)

    thumbnail_drive_id = None
    if thumbnail is not None and thumbnail.filename:
        # サムネイルも同様に直接転送
        thumbnail.file.seek(0, os.SEEK_END)
        t_file_size = thumbnail.file.tell()
        thumbnail.file.seek(0)
        t_mime = thumbnail.content_type or "image/jpeg"
        
        thumbnail_drive_id = await upload_file_to_drive(thumbnail.file, thumbnail.filename, t_mime, t_file_size)

    # MIMEタイプもDBに保存する
    insert_res = await supabase.table("videos").insert({
        "user_id": str(user.id),
        "author_name": nickname,
        "title": title,
        "description": description,
        "drive_file_id": drive_file_id,
        "thumbnail_drive_id": thumbnail_drive_id,
        "is_private": is_private,
        "views": 0,
        "mime_type": mime
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
    
    prof_res = await supabase.table("profiles").select("avatar_drive_id, channel_desc, external_link").eq("id", video["user_id"]).execute()
    if prof_res.data:
        prof = prof_res.data[0]
        video["avatar_drive_id"] = prof.get("avatar_drive_id")
        video["channel_desc"] = prof.get("channel_desc")
        video["external_link"] = prof.get("external_link")
    
    now = time.time()
    view_key = f"{user.id}_{video_id}"
    last_viewed = _view_history.get(view_key, 0)
    
    if now - last_viewed > 3600:
        try:
            current_views = video.get("views") or 0
            await supabase.table("videos").update({"views": current_views + 1}).eq("id", video_id).execute()
            video["views"] = current_views + 1
            _view_history[view_key] = now
        except Exception:
            pass

    return video

@router.get("/api/videos/{video_id}/stream")
async def stream_video(video_id: str, request: Request):
    supabase = await get_supabase()
    # mime_typeも取得する
    res = await supabase.table("videos").select("drive_file_id, mime_type").eq("id", video_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="映像が見つかりません")
    
    drive_file_id = res.data[0]["drive_file_id"]
    mime_type = res.data[0].get("mime_type") or "video/mp4"
    token = await get_gdrive_access_token()
    url = f"https://www.googleapis.com/drive/v3/files/{drive_file_id}?alt=media"
    
    headers = {"Authorization": f"Bearer {token}"}
    range_header = request.headers.get("Range")
    if range_header:
        headers["Range"] = range_header

    # ストリーミング再生時は長時間の接続保持となるため、個別のClientを生成
    client = httpx.AsyncClient()
    req = client.build_request("GET", url, headers=headers)
    r = await client.send(req, stream=True)

    resp_headers = {}
    for k, v in r.headers.items():
        if k.lower() in ["content-length", "content-range", "accept-ranges"]:
            resp_headers[k] = v
            
    # データベースから取得したMIMEタイプを動的に割り当てる
    resp_headers["Content-Type"] = mime_type

    async def iter_file():
        try:
            async for chunk in r.aiter_bytes(chunk_size=1024 * 1024):
                yield chunk
        finally:
            await r.aclose()
            await client.aclose()

    return StreamingResponse(iter_file(), status_code=r.status_code, headers=resp_headers)

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

@router.get("/api/profile/me")
async def get_my_profile(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()
    res = await supabase.table("profiles").select("*").eq("id", user.id).execute()
    return res.data[0] if res.data else {}

@router.post("/api/profile/update")
async def update_profile(
    nickname: str = Form(...),
    channel_desc: str = Form(""),
    external_link: str = Form(""),
    avatar: Optional[UploadFile] = File(None),
    authorization: str = Header(None)
):
    user = await get_user_from_token(authorization)
    supabase = await get_supabase()

    update_data = {
        "nickname": nickname,
        "channel_desc": channel_desc,
        "external_link": external_link
    }

    if avatar and avatar.filename:
        # プロフィール画像も直接転送に修正
        avatar.file.seek(0, os.SEEK_END)
        a_file_size = avatar.file.tell()
        avatar.file.seek(0)
        a_mime = avatar.content_type or "image/jpeg"
        
        avatar_drive_id = await upload_file_to_drive(avatar.file, avatar.filename, a_mime, a_file_size)
        update_data["avatar_drive_id"] = avatar_drive_id

    await supabase.table("profiles").update(update_data).eq("id", user.id).execute()
    return {"message": "プロフィールを更新しました", "data": update_data}

@router.get("/api/channel/{channel_id}")
async def get_channel_info(channel_id: str, page: int = Query(1, ge=1)):
    supabase = await get_supabase()
    
    prof_res = await supabase.table("profiles").select("*").eq("id", channel_id).execute()
    if not prof_res.data:
        raise HTTPException(status_code=404, detail="チャンネルが見つかりません")
    profile = prof_res.data[0]

    sub_res = await supabase.table("subscriptions").select("*", count="exact").eq("channel_id", channel_id).execute()
    sub_count = sub_res.count if sub_res.count else 0

    limit = 24
    offset = (page - 1) * limit
    vid_res = await supabase.table("videos").select("*").eq("user_id", channel_id).eq("is_private", False).order("created_at", desc=True).range(offset, offset + limit - 1).execute()

    return {
        "profile": profile,
        "subscriber_count": sub_count,
        "videos": vid_res.data or []
    }

@router.get("/api/channel/{channel_id}/status")
async def check_subscribe_status(channel_id: str, authorization: str = Header(None)):
    if not authorization:
        return {"is_subscribed": False}
    try:
        user = await get_user_from_token(authorization)
        supabase = await get_supabase()
        res = await supabase.table("subscriptions").select("id").eq("subscriber_id", user.id).eq("channel_id", channel_id).execute()
        return {"is_subscribed": len(res.data) > 0}
    except:
        return {"is_subscribed": False}

@router.post("/api/channel/{channel_id}/subscribe")
async def toggle_subscribe(channel_id: str, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    if str(user.id) == channel_id:
        raise HTTPException(status_code=400, detail="自分自身は登録できません")

    supabase = await get_supabase()
    res = await supabase.table("subscriptions").select("*").eq("subscriber_id", user.id).eq("channel_id", channel_id).execute()
    
    if res.data:
        await supabase.table("subscriptions").delete().eq("subscriber_id", user.id).eq("channel_id", channel_id).execute()
        return {"message": "登録を解除しました", "is_subscribed": False}
    else:
        await supabase.table("subscriptions").insert({
            "subscriber_id": str(user.id),
            "channel_id": channel_id
        }).execute()
        return {"message": "チャンネル登録しました", "is_subscribed": True}
