from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
import models, database
from pydantic import BaseModel
from datetime import datetime

app = FastAPI(title="NYC Taxi Data API")

# Pydantic models for API
class TripBase(BaseModel):
    vendor_id: int = None
    tpep_pickup_datetime: datetime
    tpep_dropoff_datetime: datetime
    passenger_count: float = None
    trip_distance: float = None
    rate_code_id: float = None
    store_and_fwd_flag: str = None
    pu_location_id: int = None
    do_location_id: int = None
    payment_type: int = None
    fare_amount: float = None
    extra: float = None
    mta_tax: float = None
    tip_amount: float = None
    tolls_amount: float = None
    improvement_surcharge: float = None
    total_amount: float = None
    congestion_surcharge: float = None
    airport_fee: float = None

class TripCreate(TripBase):
    pass

class TripResponse(TripBase):
    id: int

    class Config:
        from_attributes = True

@app.on_event("startup")
def on_startup():
    # Wait for DB to be ready and create tables
    import time
    max_retries = 10
    while max_retries > 0:
        try:
            models.Base.metadata.create_all(bind=database.engine)
            print("Database tables created successfully.")
            break
        except Exception as e:
            print(f"Database not ready yet ({e}). Retrying in 5s...")
            time.sleep(5)
            max_retries -= 1

@app.get("/")
def read_root():
    return {"message": "NYC Taxi Data API is running"}

@app.post("/trips/", response_model=TripResponse)
def create_trip(trip: TripCreate, db: Session = Depends(database.get_db)):
    db_trip = models.Trip(**trip.model_dump())
    db.add(db_trip)
    db.commit()
    db.refresh(db_trip)
    return db_trip

@app.post("/trips/batch/", status_code=201)
def create_trips_batch(trips: List[TripCreate], db: Session = Depends(database.get_db)):
    db_trips = [models.Trip(**trip.model_dump()) for trip in trips]
    db.add_all(db_trips)
    db.commit()
    return {"message": f"Successfully ingested {len(trips)} trips"}

@app.get("/trips/", response_model=List[TripResponse])
def read_trips(skip: int = 0, limit: int = 100, db: Session = Depends(database.get_db)):
    trips = db.query(models.Trip).offset(skip).limit(limit).all()
    return trips

@app.get("/trips/all/", response_model=List[TripResponse])
def read_all_trips(db: Session = Depends(database.get_db)):
    # CAUTION: This could be very slow for large datasets
    trips = db.query(models.Trip).all()
    return trips
