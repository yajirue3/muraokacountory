from typing import Optional, List
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from db import get_supabase
from main import get_user_from_token, is_king, enforce_rate_limit  # 既存関数をimport

router = APIRouter(prefix="/api/auctions", tags=["auctions"])

# --- Pydantic Models ---
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

# --- API Endpoints ---

@router.get("")
async def get_auctions(status_filter: str = "OPEN"):
    client = await get_supabase()
    # 実際はクエリビルダーで詳細にフィルタリング
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
    # 終了時刻を超えたオークションの確定処理。バッチタスクや画面表示フックで叩かれる想定。
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
    # 一般枠のエスクロー解除（落札者の受取確認）
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

@router.
