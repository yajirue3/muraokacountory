import os
from typing import Optional, Any
from fastapi import APIRouter, Depends, HTTPException, Header, status
from pydantic import BaseModel
from supabase import AsyncClient
from db import get_supabase

router = APIRouter(prefix="/api", tags=["inventory"])

class ItemCreateUpdate(BaseModel):
    item_id: str
    name: str
    description: Optional[str] = ""
    base_price: int

class ItemAccountCreate(BaseModel):
    account_name: str

class SellItemRequest(BaseModel):
    inventory_id: int
    quantity: int
    wallet_id: str

class MoveItemRequest(BaseModel):
    inventory_id: int
    quantity: int
    target_account_id: str

# ▼ 修正: target_email を target_account_id に変更
class TransferItemRequest(BaseModel):
    inventory_id: int
    quantity: int
    target_account_id: str


async def get_current_user_from_header(authorization: str = Header(None)) -> Any:
    supabase = await get_supabase()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証トークンがありません")
    token = authorization.split(" ")[1]
    try:
        user_res = await supabase.auth.get_user(token)
        if not user_res.user:
            raise HTTPException(status_code=401, detail="無効なトークンです")
        return user_res.user
    except Exception:
        raise HTTPException(status_code=401, detail="トークンの検証に失敗しました")

async def verify_king_user(authorization: str = Header(None)) -> Any:
    supabase = await get_supabase()
    user = await get_current_user_from_header(authorization)
    try:
        res = await supabase.table("profiles").select("role").eq("id", user.id).execute()
        if not res.data or res.data[0].get("role") != "king":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="国王専用の操作です")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="権限確認処理でエラーが発生しました")
    return user


@router.get("/admin/items")
async def get_admin_items(current_user: Any = Depends(verify_king_user)):
    supabase = await get_supabase()
    res = await supabase.table("items").select("*").order("created_at").execute()
    return res.data

@router.post("/admin/items")
async def upsert_admin_item(item: ItemCreateUpdate, current_user: Any = Depends(verify_king_user)):
    supabase = await get_supabase()
    data = {
        "item_id": item.item_id,
        "name": item.name,
        "description": item.description,
        "base_price": item.base_price
    }
    res = await supabase.table("items").upsert(data).execute()
    return {"message": "アイテム情報を更新しました", "data": res.data}

@router.delete("/admin/items/{item_id}")
async def delete_admin_item(item_id: str, current_user: Any = Depends(verify_king_user)):
    supabase = await get_supabase()
    await supabase.table("items").delete().eq("item_id", item_id).execute()
    return {"message": f"アイテム({item_id})を削除しました"}


@router.get("/inventory")
async def get_user_inventory(current_user: Any = Depends(get_current_user_from_header)):
    supabase = await get_supabase()
    user_id = current_user.id

    is_king = False
    prof_res = await supabase.table("profiles").select("role").eq("id", user_id).execute()
    if prof_res.data and prof_res.data[0].get("role") == "king":
        is_king = True

    acc_res = await supabase.table("item_accounts") \
        .select("account_id, account_name, created_at, user_inventories(id, quantity, item_id, updated_at, items(name, description, base_price))") \
        .eq("user_id", user_id) \
        .order("created_at") \
        .execute()

    return {
        "current_user_id": user_id,
        "is_king": is_king,
        "accounts": acc_res.data
    }

@router.post("/item-accounts")
async def create_item_account(req: ItemAccountCreate, current_user: Any = Depends(get_current_user_from_header)):
    supabase = await get_supabase()
    if not req.account_name.strip():
        raise HTTPException(status_code=400, detail="口座名を入力してください")

    res = await supabase.table("item_accounts").insert({
        "user_id": current_user.id,
        "account_name": req.account_name.strip()
    }).execute()
    return {"message": f"新しいアイテム口座「{req.account_name}」を開設しました。"}

@router.post("/sell-item")
async def sell_item(req: SellItemRequest, current_user: Any = Depends(get_current_user_from_header)):
    supabase = await get_supabase()
    user_id = current_user.id

    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="個数は1以上を指定してください")

    inv_res = await supabase.table("user_inventories") \
        .select("*, items(base_price, name), item_accounts!inner(user_id)") \
        .eq("id", req.inventory_id) \
        .execute()

    if not inv_res.data or inv_res.data[0]["quantity"] < req.quantity:
        raise HTTPException(status_code=400, detail="アイテムが見つからないか、個数が不足しています")
    if inv_res.data[0]["item_accounts"]["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="他人のアイテムは売却できません")

    inventory_item = inv_res.data[0]
    unit_price = inventory_item["items"]["base_price"]
    total_earned = unit_price * req.quantity

    wallet_res = await supabase.table("wallets").select("*").eq("wallet_id", req.wallet_id).eq("user_id", user_id).execute()
    if not wallet_res.data:
        raise HTTPException(status_code=404, detail="指定されたGold口座が存在しないか所有権がありません")

    wallet = wallet_res.data[0]

    new_qty = inventory_item["quantity"] - req.quantity
    if new_qty > 0:
        await supabase.table("user_inventories").update({"quantity": new_qty}).eq("id", req.inventory_id).execute()
    else:
        await supabase.table("user_inventories").delete().eq("id", req.inventory_id).execute()

    new_balance = wallet["balance"] + total_earned
    await supabase.table("wallets").update({"balance": new_balance}).eq("id", wallet["id"]).execute()

    return {"message": f"「{inventory_item['items']['name']}」を{req.quantity}個売却し、{total_earned}G を口座へ入金しました。"}

@router.post("/move-item")
async def move_item(req: MoveItemRequest, current_user: Any = Depends(get_current_user_from_header)):
    supabase = await get_supabase()
    user_id = current_user.id

    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="個数は1以上を指定してください")

    src_inv = await supabase.table("user_inventories").select("*, item_accounts!inner(user_id), items(name)").eq("id", req.inventory_id).execute()
    if not src_inv.data or src_inv.data[0]["quantity"] < req.quantity or src_inv.data[0]["item_accounts"]["user_id"] != user_id:
        raise HTTPException(status_code=400, detail="不正な操作か、個数が不足しています")

    target_acc = await supabase.table("item_accounts").select("account_name").eq("account_id", req.target_account_id).eq("user_id", user_id).execute()
    if not target_acc.data:
        raise HTTPException(status_code=404, detail="移動先のアイテム口座が見つかりません")

    src_item = src_inv.data[0]
    item_id = src_item["item_id"]
    dest_inv = await supabase.table("user_inventories").select("*").eq("account_id", req.target_account_id).eq("item_id", item_id).execute()

    new_qty = src_item["quantity"] - req.quantity
    if new_qty > 0:
        await supabase.table("user_inventories").update({"quantity": new_qty}).eq("id", req.inventory_id).execute()
    else:
        await supabase.table("user_inventories").delete().eq("id", req.inventory_id).execute()

    if dest_inv.data:
        await supabase.table("user_inventories").update({"quantity": dest_inv.data[0]["quantity"] + req.quantity}).eq("id", dest_inv.data[0]["id"]).execute()
    else:
        await supabase.table("user_inventories").insert({"account_id": req.target_account_id, "item_id": item_id, "quantity": req.quantity}).execute()

    return {"message": f"「{src_item['items']['name']}」を口座「{target_acc.data[0]['account_name']}」へ移動しました。"}


# ▼ 修正: ID指定での完全譲渡ロジック
@router.post("/transfer-item")
async def transfer_item(req: TransferItemRequest, current_user: Any = Depends(get_current_user_from_header)):
    supabase = await get_supabase()
    sender_id = current_user.id

    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="個数は1以上を指定してください")

    # 1. 差出人の確認
    sender_inv = await supabase.table("user_inventories") \
        .select("*, item_accounts!inner(user_id), items(name)") \
        .eq("id", req.inventory_id) \
        .execute()

    if not sender_inv.data or sender_inv.data[0]["quantity"] < req.quantity or sender_inv.data[0]["item_accounts"]["user_id"] != sender_id:
        raise HTTPException(status_code=400, detail="アイテムを十分に所持していません")

    # 2. 譲渡先口座（ID）の存在確認
    target_acc = await supabase.table("item_accounts").select("account_id, user_id").eq("account_id", req.target_account_id).execute()
    if not target_acc.data:
        raise HTTPException(status_code=404, detail="指定されたアイテム口座IDが存在しません")
    if target_acc.data[0]["user_id"] == sender_id:
        raise HTTPException(status_code=400, detail="自分自身への譲渡は「口座間移動」を使用してください")

    sender_item = sender_inv.data[0]
    
    # 3. 差出人減算
    new_qty = sender_item["quantity"] - req.quantity
    if new_qty > 0:
        await supabase.table("user_inventories").update({"quantity": new_qty}).eq("id", req.inventory_id).execute()
    else:
        await supabase.table("user_inventories").delete().eq("id", req.inventory_id).execute()

    # 4. 受取人加算
    target_inv = await supabase.table("user_inventories").select("*").eq("account_id", req.target_account_id).eq("item_id", sender_item["item_id"]).execute()
    if target_inv.data:
        await supabase.table("user_inventories").update({"quantity": target_inv.data[0]["quantity"] + req.quantity}).eq("id", target_inv.data[0]["id"]).execute()
    else:
        await supabase.table("user_inventories").insert({"account_id": req.target_account_id, "item_id": sender_item["item_id"], "quantity": req.quantity}).execute()

    return {"message": f"「{sender_item['items']['name']}」を {req.quantity} 個譲渡しました。"}

# ▼ 管理者用: 所持数を直接変更・削除するAPI
class AdminUpdateInventoryRequest(BaseModel):
    inventory_id: int
    quantity: int

@router.put("/admin/inventories/{inventory_id}")
async def admin_update_inventory(inventory_id: int, req: AdminUpdateInventoryRequest, current_user: Any = Depends(verify_king_user)):
    supabase = await get_supabase()
    if req.quantity <= 0:
        await supabase.table("user_inventories").delete().eq("id", inventory_id).execute()
        return {"message": "アイテムをインベントリから削除しました"}
    
    await supabase.table("user_inventories").update({
        "quantity": req.quantity,
        "updated_at": "now()"
    }).eq("id", inventory_id).execute()
    return {"message": f"所持数を {req.quantity} 個に更新しました"}

@router.delete("/admin/inventories/{inventory_id}")
async def admin_delete_inventory(inventory_id: int, current_user: Any = Depends(verify_king_user)):
    supabase = await get_supabase()
    await supabase.table("user_inventories").delete().eq("id", inventory_id).execute()
    return {"message": "インベントリからアイテムを削除しました"}
