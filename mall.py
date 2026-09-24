import os
import time
from collections import defaultdict
from typing import Optional, List
import httpx
from fastapi import APIRouter, HTTPException, Header, UploadFile, File, Form, Query
from pydantic import BaseModel, Field
from db import get_supabase

# ==========================================
# Google Drive 連携（環境変数直接取得）
# ==========================================
CLIENT_ID = os.environ.get("GDRIVE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GDRIVE_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("GDRIVE_REFRESH_TOKEN", "")
FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "")

http_client = httpx.AsyncClient(
    timeout=60.0,
    limits=httpx.Limits(max_keepalive_connections=50, max_connections=200)
)

async def get_gdrive_access_token() -> str:
    if not all([CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN]):
        raise HTTPException(status_code=500, detail="Google Driveの認証環境変数が未設定です")
    
    url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": REFRESH_TOKEN,
        "grant_type": "refresh_token"
    }
    res = await http_client.post(url, data=data)
    if res.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Googleトークン取得失敗: {res.status_code}")
    return res.json().get("access_token")

async def upload_banner_to_drive(file_obj, filename: str, mime_type: str, file_size: int) -> str:
    if file_size > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="看板画像は5MB以下にしてください")
    if not mime_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="画像ファイル(JPEG, PNG, WebP等)のみ許可されています")

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
        raise HTTPException(status_code=500, detail="アップロードセッション作成に失敗しました")
    
    session_url = init_res.headers.get("Location")
    chunk_size = 5 * 1024 * 1024  
    file_id = None
    start = 0
    file_obj.seek(0)
    
    while start < file_size:
        chunk = file_obj.read(chunk_size)
        if not chunk:
            break
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
            raise HTTPException(status_code=500, detail="看板画像データ転送エラー")
        start = end + 1

    if not file_id:
        raise HTTPException(status_code=500, detail="Google Drive ファイルID取得に失敗しました")

    perm_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions"
    await http_client.post(perm_url, headers={"Authorization": f"Bearer {token}"}, json={"role": "reader", "type": "anyone"})
    return file_id

# ==========================================
# 認証 & 連投防止（自己完結型）
# ==========================================
async def get_user_from_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証トークンがありません")
    token = authorization.split(" ")[1]
    try:
        client = await get_supabase()
        user_res = await client.auth.get_user(token)
        if not user_res.user:
            raise HTTPException(status_code=401, detail="ユーザーが見つかりません")
        return user_res.user
    except Exception:
        raise HTTPException(status_code=401, detail="無効なトークンです")

_mall_request_history = defaultdict(list)
_mall_banned_until = {}

def enforce_mall_rate_limit(user_id: str):
    now = time.time()
    if user_id in _mall_banned_until:
        if now < _mall_banned_until[user_id]:
            remain = int(_mall_banned_until[user_id] - now)
            raise HTTPException(status_code=429, detail=f"連続操作制限中です。残り {remain} 秒お待ちください。")
        else:
            del _mall_banned_until[user_id]

    history = _mall_request_history[user_id]
    history = [t for t in history if now - t <= 10]
    
    if len(history) >= 12:
        _mall_banned_until[user_id] = now + 30
        raise HTTPException(status_code=429, detail="スパム行為を検知しました。30秒間操作を制限します。")
    
    if history and now - history[-1] < 0.3:
        raise HTTPException(status_code=429, detail="送信が早すぎます。少しお待ちください。")
    
    history.append(now)
    _mall_request_history[user_id] = history

# ==========================================
# Pydantic モデル定義
# ==========================================
router = APIRouter(prefix="/api/mall", tags=["mall"])

class MallItemCreate(BaseModel):
    category: str = Field(..., pattern="^(ITEM|GENERAL)$")
    user_inventory_id: Optional[int] = None
    title: str = Field(..., min_length=1, max_length=60)
    description: str = Field("", max_length=500)
    secret_content: Optional[str] = Field(None, max_length=1000)
    price: int = Field(..., gt=0, le=100_000_000)
    stock_quantity: int = Field(0, ge=0, le=9999)
    is_unlimited: bool = False

class MallItemRestock(BaseModel):
    quantity: int = Field(..., gt=0, le=9999)
    source_inventory_id: Optional[int] = None

class MallItemWithdraw(BaseModel):
    quantity: int = Field(..., gt=0, le=9999)
    target_account_id: Optional[str] = None

class MallItemBuy(BaseModel):
    buyer_wallet_id: str
    quantity: int = Field(..., gt=0, le=9999)
    target_account_id: Optional[str] = None

# ==========================================
# 店舗 (Shop) 管理 API
# ==========================================

@router.get("/shops")
async def get_shops(page: int = Query(1, ge=1)):
    client = await get_supabase()
    limit = 20
    offset = (page - 1) * limit
    res = await client.table("mall_shops").select(
        "id, owner_user_id, shop_name, description, banner_drive_id, created_at, profiles:owner_user_id(nickname, equipped_title)"
    ).order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    return {"shops": res.data or []}

@router.get("/my-shop")
async def get_my_shop(authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    res = await client.table("mall_shops").select("*").eq("owner_user_id", user.id).execute()
    if not res.data:
        return {"shop": None, "items": []}
    
    shop = res.data[0]
    items_res = await client.table("mall_items").select(
        "*, items:item_master_id(name, base_price, description)"
    ).eq("shop_id", shop["id"]).order("created_at", desc=False).execute()
    return {"shop": shop, "items": items_res.data or []}

@router.get("/shops/{shop_id}")
async def get_shop_detail(shop_id: int):
    client = await get_supabase()
    shop_res = await client.table("mall_shops").select(
        "id, owner_user_id, shop_name, description, banner_drive_id, created_at, profiles:owner_user_id(nickname, equipped_title)"
    ).eq("id", shop_id).execute()
    
    if not shop_res.data:
        raise HTTPException(status_code=404, detail="指定された店舗は見つかりません")
    
    items_res = await client.table("mall_items").select(
        "id, shop_id, category, item_master_id, title, description, price, stock_quantity, is_unlimited, status, created_at, items:item_master_id(name, base_price)"
    ).eq("shop_id", shop_id).neq("status", "HIDDEN").order("created_at", desc=False).execute()
    
    return {"shop": shop_res.data[0], "items": items_res.data or []}

@router.post("/shops")
async def create_or_update_shop(
    wallet_id: str = Form(...),
    shop_name: str = Form(...),
    description: str = Form(""),
    banner: Optional[UploadFile] = File(None),
    authorization: str = Header(None)
):
    user = await get_user_from_token(authorization)
    enforce_mall_rate_limit(user.id)
    client = await get_supabase()
    
    clean_name = shop_name.strip()
    clean_desc = description.strip()
    if not clean_name or len(clean_name) > 30:
        raise HTTPException(status_code=400, detail="店舗名は1〜30文字で入力してください")
    if len(clean_desc) > 300:
        raise HTTPException(status_code=400, detail="説明文は300文字以内で入力してください")

    # 指定ウォレットの所有権を完全検証
    w_res = await client.table("wallets").select("wallet_id").eq("wallet_id", wallet_id).eq("user_id", user.id).execute()
    if not w_res.data:
        raise HTTPException(status_code=403, detail="指定されたレジ口座が存在しないか、所有権がありません")

    banner_drive_id = None
    if banner and banner.filename:
        try:
            banner.file.seek(0, os.SEEK_END)
            file_size = banner.file.tell()
            banner.file.seek(0)
            mime_type = banner.content_type or "image/jpeg"
            banner_drive_id = await upload_banner_to_drive(banner.file, banner.filename, mime_type, file_size)
        except HTTPException as he:
            raise he
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"看板画像のアップロードに失敗しました: {e}")

    exist_res = await client.table("mall_shops").select("id, banner_drive_id").eq("owner_user_id", user.id).execute()
    
    upsert_data = {
        "owner_user_id": str(user.id),
        "wallet_id": wallet_id,
        "shop_name": clean_name,
        "description": clean_desc,
        "updated_at": "now()"
    }
    
    if banner_drive_id:
        upsert_data["banner_drive_id"] = banner_drive_id
    elif exist_res.data:
        upsert_data["banner_drive_id"] = exist_res.data[0].get("banner_drive_id")

    res = await client.table("mall_shops").upsert(upsert_data, on_conflict="owner_user_id").execute()
    return {"message": "店舗の登録・更新が完了しました！", "shop": res.data[0]}

# ==========================================
# 商品出品・在庫操作 API
# ==========================================

@router.post("/items")
async def create_item(data: MallItemCreate, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    enforce_mall_rate_limit(user.id)
    client = await get_supabase()

    # カテゴリ整合性のゼロトラストチェック
    if data.category == "ITEM":
        if not data.user_inventory_id:
            raise HTTPException(status_code=400, detail="出品するインベントリアイテムを選択してください")
        if data.is_unlimited:
            raise HTTPException(status_code=400, detail="インベントリアイテムを無限在庫にすることはできません")
        if data.stock_quantity <= 0:
            raise HTTPException(status_code=400, detail="出品数は1個以上必要です")
    elif data.category == "GENERAL":
        if not data.is_unlimited and data.stock_quantity <= 0:
            raise HTTPException(status_code=400, detail="有限商品の場合、在庫数は1個以上必要です")

    shop_res = await client.table("mall_shops").select("id").eq("owner_user_id", user.id).execute()
    if not shop_res.data:
        raise HTTPException(status_code=400, detail="商品を出品する前に店舗を開設してください")
    shop_id = shop_res.data[0]["id"]

    try:
        res = await client.rpc("execute_mall_create_item", {
            "p_shop_id": shop_id,
            "p_user_id": str(user.id),
            "p_category": data.category,
            "p_user_inventory_id": data.user_inventory_id,
            "p_title": data.title.strip(),
            "p_description": data.description.strip(),
            "p_secret_content": data.secret_content.strip() if data.secret_content else None,
            "p_price": data.price,
            "p_stock_quantity": data.stock_quantity,
            "p_is_unlimited": data.is_unlimited
        }).execute()
        return {"message": "商品を出品しました！", "item_id": res.data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

@router.post("/items/{item_id}/restock")
async def restock_item(item_id: int, data: MallItemRestock, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    enforce_mall_rate_limit(user.id)
    client = await get_supabase()
    
    # 権限検証
    item_res = await client.table("mall_items").select("shop_id, mall_shops!inner(owner_user_id)").eq("id", item_id).execute()
    if not item_res.data or item_res.data[0]["mall_shops"]["owner_user_id"] != str(user.id):
        raise HTTPException(status_code=403, detail="この商品の在庫を補充する権限がありません")
        
    try:
        await client.rpc("execute_mall_restock_item", {
            "p_user_id": str(user.id),
            "p_item_id": item_id,
            "p_quantity": data.quantity,
            "p_source_inventory_id": data.source_inventory_id
        }).execute()
        return {"message": f"在庫を {data.quantity} 個補充しました！"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

@router.post("/items/{item_id}/withdraw")
async def withdraw_item(item_id: int, data: MallItemWithdraw, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    enforce_mall_rate_limit(user.id)
    client = await get_supabase()
    
    # 権限検証
    item_res = await client.table("mall_items").select("shop_id, mall_shops!inner(owner_user_id)").eq("id", item_id).execute()
    if not item_res.data or item_res.data[0]["mall_shops"]["owner_user_id"] != str(user.id):
        raise HTTPException(status_code=403, detail="この商品を引き戻す権限がありません")
        
    try:
        await client.rpc("execute_mall_withdraw_item", {
            "p_user_id": str(user.id),
            "p_item_id": item_id,
            "p_quantity": data.quantity,
            "p_target_account_id": data.target_account_id
        }).execute()
        return {"message": f"店頭から在庫を {data.quantity} 個手元に戻しました！"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

# ==========================================
# 購入決済 & 注文履歴 API
# ==========================================

@router.post("/items/{item_id}/buy")
async def buy_item(item_id: int, data: MallItemBuy, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    enforce_mall_rate_limit(user.id)
    client = await get_supabase()
    
    # 買い手のウォレット所有権をAPI層で確認
    w_res = await client.table("wallets").select("wallet_id").eq("wallet_id", data.buyer_wallet_id).eq("user_id", user.id).execute()
    if not w_res.data:
        raise HTTPException(status_code=403, detail="指定された支払口座が存在しないか、所有権がありません")

    try:
        res = await client.rpc("execute_mall_buy_item", {
            "p_buyer_user_id": str(user.id),
            "p_buyer_wallet_id": data.buyer_wallet_id,
            "p_item_id": item_id,
            "p_quantity": data.quantity,
            "p_target_account_id": data.target_account_id
        }).execute()
        
        result = res.data
        return {"message": f"合計 {result['total_price']}G で {data.quantity} 個購入しました！"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

@router.get("/my-purchases")
async def get_my_purchases(page: int = Query(1, ge=1), authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    limit = 20
    offset = (page - 1) * limit
    
    res = await client.table("mall_orders").select(
        "id, quantity, unit_price, total_price, target_account_id, created_at, mall_items(title, category, secret_content), mall_shops(shop_name)"
    ).eq("buyer_user_id", user.id).order("created_at", desc=True).range(offset, offset + limit - 1).execute()
    
    return {"orders": res.data or []}
