from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import admin, analytics, auth, billing, dashboard, insights, inventory, owner, purchasing, store_settings

app = FastAPI(title="Medical Store SaaS API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(owner.router)
app.include_router(store_settings.router)
app.include_router(dashboard.router)
app.include_router(insights.expiry_router)
app.include_router(insights.reorder_router)
app.include_router(inventory.router)
app.include_router(purchasing.supplier_router)
app.include_router(purchasing.purchase_router)
app.include_router(billing.router)
app.include_router(analytics.router)


@app.get("/health")
async def health():
    return {"status": "ok"}