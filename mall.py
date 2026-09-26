import os
import time
import traceback
from collections import defaultdict
from typing import Optional, List
import httpx
from fastapi import APIRouter, HTTPException, Header, UploadFile, File, Form, Query
from pydantic import BaseModel, Field
from db import get_supabase

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
        print(f"[ERROR Google Drive Auth] status: {res.status_code}, body: {res.text}")
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
        print(f"[ERROR Drive Init Session] {init_res.status_code} - {init_res.text}")
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
            print(f"[ERROR Drive Chunk Upload] {upload_res.status_code} - {upload_res.text}")
            raise HTTPException(status_code=500, detail="看板画像データ転送エラー")
        start = end + 1

    if not file_id:
        raise HTTPException(status_code=500, detail="Google Drive ファイルID取得に失敗しました")

    perm_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions"
    await http_client.post(perm_url, headers={"Authorization": f"Bearer {token}"}, json={"role": "reader", "type": "anyone"})
    return file_id

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
    except HTTPException:
        raise
    except Exception as e:
        print(f"[AUTH ERROR] Token verification failed: {e}")
        traceback.print_exc()
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
    
    if len(history) >= 15:
        _mall_banned_until[user_id] = now + 30
        raise HTTPException(status_code=429, detail="スパム行為を検知しました。30秒間操作を制限します。")
    
    if history and now - history[-1] < 0.2:
        raise HTTPException(status_code=429, detail="送信が早すぎます。少しお待ちください。")
    
    history.append(now)
    _mall_request_history[user_id] = history

router = APIRouter(prefix="/api/mall", tags=["mall"])

class MallItemCreate(BaseModel):
    category: str = Field(..., pattern="^(ITEM|GENERAL|PHYSICAL)$")
    user_inventory_id: Optional[int] = None
    title: str = Field(..., min_length=1, max_length=60)
    description: str = Field("", max_length=500)
    secret_content: Optional[str] = Field(None, max_length=1000)
    price: int = Field(..., gt=0, le=100_000_000)
    stock_quantity: int = Field(0, ge=0, le=9999)
    is_unlimited: bool = False
    secret_lines: List[str] = []

class MallItemRestock(BaseModel):
    quantity: int = Field(0, ge=0, le=9999)
    source_inventory_id: Optional[int] = None
    secret_lines: List[str] = []

class MallItemWithdraw(BaseModel):
    quantity: int = Field(..., gt=0, le=9999)
    target_account_id: Optional[str] = None

class MallItemBuy(BaseModel):
    buyer_wallet_id: str
    quantity: int = Field(..., gt=0, le=9999)
    target_account_id: Optional[str] = None

@router.get("/shops")
async def get_shops(page: int = Query(1, ge=1)):
    try:
        client = await get_supabase()
        limit = 20
        offset = (page - 1) * limit
        res = await client.table("mall_shops").select("*").order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        shops = res.data or []
        if shops:
            owner_ids = list({s["owner_user_id"] for s in shops if s.get("owner_user_id")})
            if owner_ids:
                prof_res = await client.table("profiles").select("id, nickname, equipped_title").in_("id", owner_ids).execute()
                prof_map = {p["id"]: p for p in (prof_res.data or [])}
                for s in shops:
                    s["profiles"] = prof_map.get(s["owner_user_id"], {"nickname": "不明", "equipped_title": "奴隷"})
        return {"shops": shops}
    except Exception as e:
        print("[MALL ERROR /shops]:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"店舗一覧の取得に失敗しました: {str(e)}")

@router.get("/my-shop")
async def get_my_shop(authorization: str = Header(None)):
    try:
        user = await get_user_from_token(authorization)
        client = await get_supabase()
        res = await client.table("mall_shops").select("*").eq("owner_user_id", user.id).execute()
        if not res.data:
            return {"shop": None, "items": []}
        shop = res.data[0]
        items_res = await client.table("mall_items").select("*").eq("shop_id", shop["id"]).order("created_at", desc=False).execute()
        return {"shop": shop, "items": items_res.data or []}
    except HTTPException:
        raise
    except Exception as e:
        print("[MALL ERROR /my-shop]:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"マイショップ情報の取得に失敗しました: {str(e)}")

@router.get("/shops/{shop_id}")
async def get_shop_detail(shop_id: int):
    try:
        client = await get_supabase()
        shop_res = await client.table("mall_shops").select("*").eq("id", shop_id).execute()
        if not shop_res.data:
            raise HTTPException(status_code=404, detail="指定された店舗は見つかりません")
        shop = shop_res.data[0]
        prof_res = await client.table("profiles").select("nickname, equipped_title").eq("id", shop["owner_user_id"]).execute()
        shop["profiles"] = prof_res.data[0] if prof_res.data else {"nickname": "不明", "equipped_title": "奴隷"}
        items_res = await client.table("mall_items").select(
            "id, shop_id, category, item_master_id, title, description, price, stock_quantity, is_unlimited, status, created_at"
        ).eq("shop_id", shop_id).neq("status", "HIDDEN").order("created_at", desc=False).execute()
        return {"shop": shop, "items": items_res.data or []}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[MALL ERROR /shops/{shop_id}]:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"店舗詳細の取得に失敗しました: {str(e)}")

@router.post("/shops")
async def create_or_update_shop(
    wallet_id: str = Form(...),
    shop_name: str = Form(...),
    description: str = Form(""),
    banner: Optional[UploadFile] = File(None),
    authorization: str = Header(None)
):
    try:
        user = await get_user_from_token(authorization)
        enforce_mall_rate_limit(user.id)
        client = await get_supabase()
        clean_name = shop_name.strip()
        clean_desc = description.strip()
        if not clean_name or len(clean_name) > 30:
            raise HTTPException(status_code=400, detail="店舗名は1〜30文字で入力してください")
        if len(clean_desc) > 300:
            raise HTTPException(status_code=400, detail="説明文は300文字以内で入力してください")

        w_res = await client.table("wallets").select("wallet_id").eq("wallet_id", wallet_id).eq("user_id", user.id).execute()
        if not w_res.data:
            raise HTTPException(status_code=403, detail="指定されたレジ口座が存在しないか、所有権がありません")

        banner_drive_id = None
        if banner and banner.filename:
            banner.file.seek(0, os.SEEK_END)
            file_size = banner.file.tell()
            banner.file.seek(0)
            mime_type = banner.content_type or "image/jpeg"
            banner_drive_id = await upload_banner_to_drive(banner.file, banner.filename, mime_type, file_size)

        exist_res = await client.table("mall_shops").select("id, banner_drive_id").eq("owner_user_id", user.id).execute()
        upsert_data = {
            "owner_user_id": str(user.id),
            "wallet_id": wallet_id,
            "shop_name": clean_name,
            "description": clean_desc,
        }
        if banner_drive_id:
            upsert_data["banner_drive_id"] = banner_drive_id
        elif exist_res.data:
            upsert_data["banner_drive_id"] = exist_res.data[0].get("banner_drive_id")

        res = await client.table("mall_shops").upsert(upsert_data, on_conflict="owner_user_id").execute()
        return {"message": "店舗の登録・更新が完了しました！", "shop": res.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        print("[MALL ERROR /shops POST]:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"店舗保存エラー: {str(e)}")

@router.post("/items")
async def create_item(data: MallItemCreate, authorization: str = Header(None)):
    try:
        user = await get_user_from_token(authorization)
        enforce_mall_rate_limit(user.id)
        client = await get_supabase()

        clean_lines = [line.strip() for line in data.secret_lines if line.strip()]

        if data.category == "ITEM":
            if not data.user_inventory_id:
                raise HTTPException(status_code=400, detail="出品するインベントリアイテムを選択してください")
            if data.stock_quantity <= 0:
                raise HTTPException(status_code=400, detail="出品数は1個以上必要です")
        elif data.category == "GENERAL":
            if not data.is_unlimited and len(clean_lines) == 0:
                raise HTTPException(status_code=400, detail="有限商品には個別データ（シリアル等）を1行以上入力してください")

        shop_res = await client.table("mall_shops").select("id").eq("owner_user_id", user.id).execute()
        if not shop_res.data:
            raise HTTPException(status_code=400, detail="商品を出品する前に店舗を開設してください")
        shop_id = shop_res.data[0]["id"]

        res = await client.rpc("execute_mall_create_item", {
            "p_shop_id": shop_id,
            "p_user_id": str(user.id),
            "p_category": data.category,
            "p_user_inventory_id": data.user_inventory_id,
            "p_title": data.title.strip(),
            "p_description": data.description.strip(),
            "p_secret_content": data.secret_content.strip() if data.secret_content else None,
            "p_price": data.price,
            "p_stock_quantity": data.stock_quantity if data.category == "ITEM" else len(clean_lines),
            "p_is_unlimited": data.is_unlimited,
            "p_secret_lines": clean_lines
        }).execute()
        return {"message": "商品を出品しました！", "item_id": res.data}
    except HTTPException:
        raise
    except Exception as e:
        print("[MALL ERROR /items POST]:")
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

@router.post("/items/{item_id}/restock")
async def restock_item(item_id: int, data: MallItemRestock, authorization: str = Header(None)):
    try:
        user = await get_user_from_token(authorization)
        enforce_mall_rate_limit(user.id)
        client = await get_supabase()

        clean_lines = [line.strip() for line in data.secret_lines if line.strip()]

        await client.rpc("execute_mall_restock_item", {
            "p_user_id": str(user.id),
            "p_item_id": item_id,
            "p_quantity": data.quantity,
            "p_source_inventory_id": data.source_inventory_id,
            "p_secret_lines": clean_lines
        }).execute()
        return {"message": "在庫を補充しました！"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[MALL ERROR /items/{item_id}/restock]:")
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

@router.post("/items/{item_id}/withdraw")
async def withdraw_item(item_id: int, data: MallItemWithdraw, authorization: str = Header(None)):
    try:
        user = await get_user_from_token(authorization)
        enforce_mall_rate_limit(user.id)
        client = await get_supabase()
        await client.rpc("execute_mall_withdraw_item", {
            "p_user_id": str(user.id),
            "p_item_id": item_id,
            "p_quantity": data.quantity,
            "p_target_account_id": data.target_account_id
        }).execute()
        return {"message": f"店頭から在庫を {data.quantity} 個手元に戻しました！"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[MALL ERROR /items/{item_id}/withdraw]:")
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

@router.post("/items/{item_id}/buy")
async def buy_item(item_id: int, data: MallItemBuy, authorization: str = Header(None)):
    try:
        user = await get_user_from_token(authorization)
        enforce_mall_rate_limit(user.id)
        client = await get_supabase()
        
        w_res = await client.table("wallets").select("wallet_id").eq("wallet_id", data.buyer_wallet_id).eq("user_id", user.id).execute()
        if not w_res.data:
            raise HTTPException(status_code=403, detail="指定された支払口座が存在しないか、所有権がありません")

        res = await client.rpc("execute_mall_buy_item", {
            "p_buyer_user_id": str(user.id),
            "p_buyer_wallet_id": data.buyer_wallet_id,
            "p_item_id": item_id,
            "p_quantity": data.quantity,
            "p_target_account_id": data.target_account_id
        }).execute()
        
        result = res.data
        return {"message": f"合計 {result['total_price']}G で {data.quantity} 個購入しました！"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[MALL ERROR /items/{item_id}/buy]:")
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

# 買い手用：購入履歴
@router.get("/my-purchases")
async def get_my_purchases(page: int = Query(1, ge=1), authorization: str = Header(None)):
    try:
        user = await get_user_from_token(authorization)
        client = await get_supabase()
        limit = 20
        offset = (page - 1) * limit
        
        res = await client.table("mall_orders").select(
            "id, shop_id, item_id, quantity, unit_price, total_price, distributed_content, created_at"
        ).eq("buyer_user_id", user.id).order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        
        orders = res.data or []
        if orders:
            item_ids = list({o["item_id"] for o in orders if o.get("item_id")})
            shop_ids = list({o["shop_id"] for o in orders if o.get("shop_id")})
            
            items_map = {}
            if item_ids:
                it_res = await client.table("mall_items").select("id, title, category").in_("id", item_ids).execute()
                items_map = {x["id"]: x for x in (it_res.data or [])}
                
            shops_map = {}
            if shop_ids:
                sh_res = await client.table("mall_shops").select("id, shop_name").in_("id", shop_ids).execute()
                shops_map = {x["id"]: x for x in (sh_res.data or [])}
                
            for o in orders:
                o["mall_items"] = items_map.get(o.get("item_id"), {"title": "商品", "category": "ITEM"})
                o["mall_shops"] = shops_map.get(o.get("shop_id"), {"shop_name": "店舗"})
                
        return {"orders": orders}
    except HTTPException:
        raise
    except Exception as e:
        print("[MALL ERROR /my-purchases]:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"購入履歴の取得に失敗しました: {str(e)}")

# 店主用：自店舗の販売履歴
@router.get("/my-sales")
async def get_my_sales(page: int = Query(1, ge=1), authorization: str = Header(None)):
    try:
        user = await get_user_from_token(authorization)
        client = await get_supabase()
        
        shop_res = await client.table("mall_shops").select("id").eq("owner_user_id", user.id).execute()
        if not shop_res.data:
            return {"sales": []}
        shop_id = shop_res.data[0]["id"]

        limit = 20
        offset = (page - 1) * limit
        res = await client.table("mall_orders").select(
            "id, item_id, buyer_user_id, quantity, unit_price, total_price, distributed_content, created_at"
        ).eq("shop_id", shop_id).order("created_at", desc=True).range(offset, offset + limit - 1).execute()

        sales = res.data or []
        if sales:
            item_ids = list({s["item_id"] for s in sales if s.get("item_id")})
            buyer_ids = list({s["buyer_user_id"] for s in sales if s.get("buyer_user_id")})

            items_map = {}
            if item_ids:
                it_res = await client.table("mall_items").select("id, title, category").in_("id", item_ids).execute()
                items_map = {x["id"]: x for x in (it_res.data or [])}

            buyers_map = {}
            if buyer_ids:
                prof_res = await client.table("profiles").select("id, nickname, equipped_title").in_("id", buyer_ids).execute()
                buyers_map = {p["id"]: p for p in (prof_res.data or [])}

            for s in sales:
                s["mall_items"] = items_map.get(s.get("item_id"), {"title": "商品", "category": "ITEM"})
                s["buyer"] = buyers_map.get(s.get("buyer_user_id"), {"nickname": "不明", "equipped_title": "奴隷"})

        return {"sales": sales}
    except HTTPException:
        raise
    except Exception as e:
        print("[MALL ERROR /my-sales]:")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"販売履歴の取得に失敗しました: {str(e)}")


class MallItemCancel(BaseModel):
    target_account_id: Optional[str] = None

@router.post("/items/{item_id}/cancel")
async def cancel_item(item_id: int, data: Optional[MallItemCancel] = None, authorization: str = Header(None)):
    try:
        user = await get_user_from_token(authorization)
        enforce_mall_rate_limit(user.id)
        client = await get_supabase()

        target_acc_id = data.target_account_id if data else None

        res = await client.rpc("execute_mall_cancel_item", {
            "p_user_id": str(user.id),
            "p_item_id": item_id,
            "p_target_account_id": target_acc_id
        }).execute()

        result = res.data or {}
        ret_qty = result.get("returned_quantity", 0)
        msg = f"商品の出品を取り消しました。（残在庫 {ret_qty} 個を手元に戻しました）" if ret_qty > 0 else "商品の出品を取り消しました。"
        return {"message": msg, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[MALL ERROR /items/{item_id}/cancel]:")
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

# ========================================================
# エスクロー物品売買用 追加エンドポイント
# ========================================================

class MallOrderShip(BaseModel):
    tracking_note: Optional[str] = Field(None, max_length=200)

class MallOrderRefund(BaseModel):
    buyer_refund_wallet_id: str


# 1. 店主用：発送・受渡連絡
@router.post("/orders/{order_id}/ship")
async def ship_order(order_id: int, data: Optional[MallOrderShip] = None, authorization: str = Header(None)):
    try:
        user = await get_user_from_token(authorization)
        enforce_mall_rate_limit(user.id)
        client = await get_supabase()

        note = data.tracking_note.strip() if (data and data.tracking_note) else None

        res = await client.rpc("execute_mall_ship_order", {
            "p_seller_user_id": str(user.id),
            "p_order_id": order_id,
            "p_tracking_note": note
        }).execute()

        return {"message": "発送・受渡連絡を完了しました！", "data": res.data}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[MALL ERROR /orders/{order_id}/ship]:")
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))


# 2. 買い手用：受取確認＆売上着金（エスクロー解除）
@router.post("/orders/{order_id}/complete")
async def complete_order(order_id: int, authorization: str = Header(None)):
    try:
        user = await get_user_from_token(authorization)
        enforce_mall_rate_limit(user.id)
        client = await get_supabase()

        res = await client.rpc("execute_mall_complete_order", {
            "p_buyer_user_id": str(user.id),
            "p_order_id": order_id
        }).execute()

        result = res.data or {}
        return {"message": f"受取を確定し、店主に {result.get('released_price', 0)}G が支払われました！", "data": result}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[MALL ERROR /orders/{order_id}/complete]:")
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))


# 3. 双方用：取引キャンセル＆返金
@router.post("/orders/{order_id}/refund")
async def refund_order(order_id: int, data: MallOrderRefund, authorization: str = Header(None)):
    try:
        user = await get_user_from_token(authorization)
        enforce_mall_rate_limit(user.id)
        client = await get_supabase()

        res = await client.rpc("execute_mall_refund_order", {
            "p_user_id": str(user.id),
            "p_order_id": order_id,
            "p_buyer_refund_wallet_id": data.buyer_refund_wallet_id
        }).execute()

        result = res.data or {}
        return {"message": f"注文をキャンセルし、購入者に {result.get('refunded_price', 0)}G 返金しました。", "data": result}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[MALL ERROR /orders/{order_id}/refund]:")
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))
