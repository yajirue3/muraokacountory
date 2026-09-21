from typing import Optional, List
import time
from collections import defaultdict
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from db import get_supabase

# ==========================================
# 循環インポート回避のための自己完結関数群
# ==========================================
async def get_user_from_token(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証トークンがありません")
    token = authorization.split(" ")[1]
    try:
        client = await get_supabase()
        user_res = await client.auth.get_user(token)
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

# オークションチャット専用の連投制限メモリ
_auction_post_history = defaultdict(list)
_auction_banned_until = {}

def enforce_rate_limit(user_id: str):
    now = time.time()
    if user_id in _auction_banned_until:
        if now < _auction_banned_until[user_id]:
            remain = int(_auction_banned_until[user_id] - now)
            raise HTTPException(status_code=429, detail=f"連投制限中です。残り {remain} 秒お待ちください。")
        else:
            del _auction_banned_until[user_id]

    history = _auction_post_history[user_id]
    history = [t for t in history if now - t <= 10]
    
    if len(history) >= 8:
        _auction_banned_until[user_id] = now + 60
        raise HTTPException(status_code=429, detail="スパム行為を検知したため、1分間投稿を禁止します。")
    
    if history and now - history[-1] < 1.0:
        raise HTTPException(status_code=429, detail="送信が早すぎます。1秒お待ちください。")
    
    history.append(now)
    _auction_post_history[user_id] = history

# ==========================================
# ルーターおよびPydanticモデル定義
# ==========================================
router = APIRouter(prefix="/api/auctions", tags=["auctions"])

class AuctionCreate(BaseModel):
    category: str = Field(..., pattern="^(ITEM|GENERAL)$")
    seller_wallet_id: str
    title: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    start_price: int = Field(..., gt=0)
    buyout_price: Optional[int] = Field(None, gt=0)
    end_at: datetime
    chat_setting: str = Field("OPEN", pattern="^(OPEN|RESTRICTED)$")
    # ITEM用
    inventory_id: Optional[int] = None
    quantity: int = Field(1, gt=0)

class AuctionBid(BaseModel):
    bidder_wallet_id: str
    target_item_account_id: Optional[str] = None
    bid_amount: int = Field(..., gt=0)

class AuctionCommentCreate(BaseModel):
    message: str = Field(..., min_length=1, max_length=200)

# ==========================================
# API エンドポイント
# ==========================================

@router.get("")
async def get_auctions(status_filter: str = "OPEN"):
    client = await get_supabase()
    res = await client.table("auctions").select("*").eq("status", status_filter).order("end_at", desc=False).execute()
    return res.data

@router.post("")
async def create_auction(data: AuctionCreate, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    
    # 即決価格が設定されている場合、開始価格を上回っているかバリデーション
    if data.buyout_price is not None and data.buyout_price <= data.start_price:
        raise HTTPException(status_code=400, detail="即決価格は開始価格より高く設定してください")

    try:
        res = await client.rpc("execute_auction_create", {
            "p_user_id": str(user.id),
            "p_category": data.category,
            "p_seller_wallet_id": data.seller_wallet_id,
            "p_title": data.title.strip(),
            "p_description": data.description.strip(),
            "p_quantity": data.quantity,
            "p_start_price": data.start_price,
            "p_buyout_price": data.buyout_price,
            "p_end_at": data.end_at.isoformat(),
            "p_chat_setting": data.chat_setting,
            "p_inventory_id": data.inventory_id
        }).execute()
        return {"message": "オークションの出品が完了しました！", "auction_id": res.data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

@router.post("/{auction_id}/bid")
async def place_bid(auction_id: int, data: AuctionBid, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    
    try:
        res = await client.rpc("execute_auction_bid", {
            "p_auction_id": auction_id,
            "p_user_id": str(user.id),
            "p_bidder_wallet_id": data.bidder_wallet_id,
            "p_target_item_account_id": data.target_item_account_id,
            "p_bid_amount": data.bid_amount
        }).execute()
        
        result = res.data
        if result.get("is_buyout"):
            return {"message": "即決価格で落札しました！🎉"}
        return {"message": f"{result['new_price']}G で入札（または増額）しました！"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

@router.post("/{auction_id}/settle")
async def settle_auction(auction_id: int):
    client = await get_supabase()
    try:
        res = await client.rpc("execute_auction_settle", {"p_auction_id": auction_id}).execute()
        return res.data
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

@router.post("/{auction_id}/cancel")
async def cancel_auction(auction_id: int, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    is_king_user = await is_king(user.id)
    
    try:
        res = await client.rpc("execute_auction_cancel", {
            "p_auction_id": auction_id,
            "p_user_id": str(user.id),
            "p_is_king": is_king_user
        }).execute()
        return res.data
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

@router.post("/{auction_id}/complete")
async def complete_general_auction(auction_id: int, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    client = await get_supabase()
    try:
        res = await client.rpc("execute_auction_complete_general", {
            "p_auction_id": auction_id,
            "p_user_id": str(user.id),
            "p_is_auto": False
        }).execute()
        return res.data
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(getattr(e, "message", e)))

# ==========================================
# チャット履歴取得 API（追加分）
# ==========================================
@router.get("/{auction_id}/comments")
async def get_auction_comments(auction_id: int):
    client = await get_supabase()
    res = await client.table("auction_comments").select("*").eq("auction_id", auction_id).order("created_at", desc=False).execute()
    return res.data

# ==========================================
# チャット投稿 API
# ==========================================
@router.post("/{auction_id}/comments")
async def post_auction_comment(auction_id: int, data: AuctionCommentCreate, authorization: str = Header(None)):
    user = await get_user_from_token(authorization)
    enforce_rate_limit(user.id)
    client = await get_supabase()
    
    # 参加資格チェック
    auction_res = await client.table("auctions").select("chat_setting, seller_user_id, status").eq("id", auction_id).execute()
    if not auction_res.data:
        raise HTTPException(status_code=404, detail="オークションが見つかりません")
    
    auction = auction_res.data[0]
    if auction["status"] != "OPEN":
        raise HTTPException(status_code=400, detail="終了したオークションでは発言できません")
        
    if auction["chat_setting"] == "RESTRICTED" and auction["seller_user_id"] != user.id:
        bids = await client.table("auction_bids").select("id").eq("auction_id", auction_id).eq("bidder_user_id", user.id).limit(1).execute()
        if not bids.data:
            raise HTTPException(status_code=403, detail="このオークションは入札者のみ発言可能な設定です")

    prof = await client.table("profiles").select("nickname, equipped_title").eq("id", user.id).execute()
    nickname = prof.data[0].get("nickname", "不明") if prof.data else "不明"
    title = prof.data[0].get("equipped_title", "鉱山労働奴隷") if prof.data else "鉱山労働奴隷"

    await client.table("auction_comments").insert({
        "auction_id": auction_id,
        "user_id": str(user.id),
        "nickname": nickname,
        "user_title": title,
        "message": data.message.strip()
    }).execute()
    
    return {"message": "送信しました"}
