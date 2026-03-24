import logging
import os
import httpx
from contextlib import asynccontextmanager
from typing import List, Optional
from datetime import date
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload
from sqlalchemy import select, String, Integer, Date, ForeignKey, Boolean

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Setup Uploads ---
os.makedirs("uploads", exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./travel.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and not DATABASE_URL.startswith("postgresql+asyncpg://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class TripDB(Base):
    __tablename__ = "trips"
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(100))
    start_date: Mapped[date] = mapped_column(Date)
    total_days: Mapped[int] = mapped_column(Integer)
    places: Mapped[List["PlaceDB"]] = relationship(back_populates="trip", cascade="all, delete-orphan")

class PlaceDB(Base):
    __tablename__ = "places"
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    day: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(50), default="景點")
    cost: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    map_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    scheduled_time: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    trip: Mapped["TripDB"] = relationship(back_populates="places")
    checklists: Mapped[List["ChecklistItemDB"]] = relationship(back_populates="place", cascade="all, delete-orphan")

class ChecklistItemDB(Base):
    __tablename__ = "checklists"
    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    place_id: Mapped[int] = mapped_column(ForeignKey("places.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(100))
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    place: Mapped["PlaceDB"] = relationship(back_populates="checklists")

# --- Pydantic Schemas ---
class TripBase(BaseModel):
    title: str = Field(..., max_length=100)
    start_date: date
    total_days: int = Field(..., ge=1)

class TripCreate(TripBase): pass

class TripResponse(TripBase):
    id: int
    class Config: from_attributes = True

class ChecklistItemBase(BaseModel):
    title: str = Field(..., max_length=100)
    is_completed: bool = Field(default=False)

class ChecklistItemCreate(ChecklistItemBase): pass

class ChecklistItemResponse(ChecklistItemBase):
    id: int
    place_id: int
    class Config: from_attributes = True

class PlaceBase(BaseModel):
    day: int = Field(default=1)
    name: str = Field(..., max_length=100)
    category: str = Field(default="景點", max_length=50)
    cost: Optional[int] = Field(default=None)
    map_url: Optional[str] = Field(default=None)
    note: Optional[str] = Field(default=None)
    scheduled_time: Optional[str] = Field(default=None)
    image_url: Optional[str] = Field(default=None)

class PlaceCreate(PlaceBase): pass

class PlaceResponse(PlaceBase):
    id: int
    trip_id: int
    checklists: List[ChecklistItemResponse] = []
    class Config: from_attributes = True

# --- App Setup ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(title="Travel App API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
import os
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

# --- Routes: Trips ---
@app.get("/trips", response_model=List[TripResponse])
async def read_trips(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TripDB).order_by(TripDB.start_date.desc()))
    return result.scalars().all()

@app.post("/trips", response_model=TripResponse, status_code=201)
async def create_trip(trip: TripCreate, db: AsyncSession = Depends(get_db)):
    new_trip = TripDB(**trip.model_dump())
    db.add(new_trip)
    await db.commit()
    await db.refresh(new_trip)
    return new_trip

@app.delete("/trips/{trip_id}", status_code=204)
async def delete_trip(trip_id: int, db: AsyncSession = Depends(get_db)):
    trip = await db.get(TripDB, trip_id)
    if trip:
        await db.delete(trip)
        await db.commit()

# --- Routes: Places ---
@app.get("/trips/{trip_id}/places", response_model=List[PlaceResponse])
async def read_places(trip_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PlaceDB)
        .where(PlaceDB.trip_id == trip_id)
        .options(selectinload(PlaceDB.checklists))
        .order_by(PlaceDB.day.asc(), PlaceDB.scheduled_time.asc())
    )
    return result.scalars().unique().all()

@app.post("/trips/{trip_id}/places", response_model=PlaceResponse, status_code=201)
async def create_place(trip_id: int, place: PlaceCreate, db: AsyncSession = Depends(get_db)):
    new_place = PlaceDB(**place.model_dump(), trip_id=trip_id)
    db.add(new_place)
    await db.commit()
    result = await db.execute(select(PlaceDB).where(PlaceDB.id == new_place.id).options(selectinload(PlaceDB.checklists)))
    return result.scalars().first()

@app.put("/places/{place_id}", response_model=PlaceResponse)
async def update_place(place_id: int, place: PlaceCreate, db: AsyncSession = Depends(get_db)):
    db_place = await db.get(PlaceDB, place_id)
    if not db_place: raise HTTPException(status_code=404)
    db_place.day = place.day
    db_place.name = place.name
    db_place.category = place.category
    db_place.cost = place.cost
    db_place.map_url = place.map_url
    db_place.note = place.note
    db_place.scheduled_time = place.scheduled_time
    await db.commit()
    result = await db.execute(select(PlaceDB).where(PlaceDB.id == place_id).options(selectinload(PlaceDB.checklists)))
    return result.scalars().first()

@app.delete("/places/{place_id}", status_code=204)
async def delete_place(place_id: int, db: AsyncSession = Depends(get_db)):
    place = await db.get(PlaceDB, place_id)
    if place:
        await db.delete(place)
        await db.commit()

@app.post("/places/{place_id}/image")
async def upload_place_image(place_id: int, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    place = await db.get(PlaceDB, place_id)
    if not place: raise HTTPException(status_code=404)
    
    try:
        async with httpx.AsyncClient() as client:
            files = {'fileToUpload': (file.filename, file.file, file.content_type)}
            data = {'reqtype': 'fileupload'}
            response = await client.post("https://catbox.moe/user/api.php", data=data, files=files, timeout=30.0)
            response.raise_for_status()
            image_url = response.text.strip()
            place.image_url = image_url
            await db.commit()
            return {"image_url": image_url}
    except Exception as e:
        logger.error(f"Image upload failed: {e}")
        raise HTTPException(status_code=500, detail="Image upload failed")

# --- Routes: Checklists ---
@app.post("/places/{place_id}/checklists", response_model=ChecklistItemResponse, status_code=201)
async def create_checklist(place_id: int, item: ChecklistItemCreate, db: AsyncSession = Depends(get_db)):
    new_item = ChecklistItemDB(**item.model_dump(), place_id=place_id)
    db.add(new_item)
    await db.commit()
    await db.refresh(new_item)
    return new_item

@app.put("/checklists/{item_id}", response_model=ChecklistItemResponse)
async def update_checklist(item_id: int, item: ChecklistItemCreate, db: AsyncSession = Depends(get_db)):
    db_item = await db.get(ChecklistItemDB, item_id)
    if not db_item: raise HTTPException(status_code=404)
    db_item.title = item.title
    db_item.is_completed = item.is_completed
    await db.commit()
    await db.refresh(db_item)
    return db_item

@app.delete("/checklists/{item_id}", status_code=204)
async def delete_checklist(item_id: int, db: AsyncSession = Depends(get_db)):
    db_item = await db.get(ChecklistItemDB, item_id)
    if db_item:
        await db.delete(db_item)
        await db.commit()
