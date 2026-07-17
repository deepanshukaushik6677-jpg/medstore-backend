import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..dependencies import get_tenant_store_id, require_roles
from ..models import BillType, GstNumberingMode, Store
from ..schemas.store_settings import StoreSettingsResponse, StoreSettingsUpdateRequest

router = APIRouter(
    prefix="/owner/store-settings", tags=["owner"], dependencies=[Depends(require_roles("owner"))]
)


def _serialize(store: Store) -> StoreSettingsResponse:
    return StoreSettingsResponse(
        id=str(store.id),
        name=store.name,
        address=store.address,
        gstin=store.gstin,
        drug_license_no=store.drug_license_no,
        status=store.status.value,
        default_bill_type=store.default_bill_type.value,
        gst_numbering_mode=store.gst_numbering_mode.value,
        gst_invoice_prefix=store.gst_invoice_prefix,
    )


@router.get("", response_model=StoreSettingsResponse)
async def get_store_settings(store_id: uuid.UUID = Depends(get_tenant_store_id), db: AsyncSession = Depends(get_db)):
    store = await db.get(Store, store_id)
    return _serialize(store)


@router.patch("", response_model=StoreSettingsResponse)
async def update_store_settings(
    payload: StoreSettingsUpdateRequest,
    store_id: uuid.UUID = Depends(get_tenant_store_id),
    db: AsyncSession = Depends(get_db),
):
    store = await db.get(Store, store_id)
    data = payload.model_dump(exclude_unset=True)

    if "default_bill_type" in data:
        try:
            data["default_bill_type"] = BillType(data["default_bill_type"])
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "default_bill_type must be 'simple' or 'gst'")

    if "gst_numbering_mode" in data:
        try:
            data["gst_numbering_mode"] = GstNumberingMode(data["gst_numbering_mode"])
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "gst_numbering_mode must be 'fy_reset' or 'continuous'")

    for field, value in data.items():
        setattr(store, field, value)
    await db.commit()
    await db.refresh(store)
    return _serialize(store)
