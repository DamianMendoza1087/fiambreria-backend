from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, ForeignKey, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from pydantic import BaseModel
from typing import List, Optional
import datetime

SQLALCHEMY_DATABASE_URL = "sqlite:///./fiambreria.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class UserDB(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="vendedor")
    is_active = Column(Boolean, default=True)
    can_preventa = Column(Boolean, default=True)
    can_caja = Column(Boolean, default=False)
    can_stock = Column(Boolean, default=False)
    can_ingreso = Column(Boolean, default=False)

class ProductDB(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    category = Column(String, default="Fiambres")
    price_per_unit = Column(Float, nullable=False)
    unit_type = Column(String, default="unid")
    stock = Column(Float, default=0.0)
    barcode = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)

class PreSaleDB(Base):
    __tablename__ = "presales"
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    status = Column(String, default="PENDIENTE")
    total_amount = Column(Float, default=0.0)
    items = relationship("PreSaleItemDB", back_populates="presale", cascade="all, delete-orphan")

class PreSaleItemDB(Base):
    __tablename__ = "presale_items"
    id = Column(Integer, primary_key=True, index=True)
    presale_id = Column(Integer, ForeignKey("presales.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    product_name = Column(String)
    price_per_unit = Column(Float)
    unit_type = Column(String, default="unid")
    quantity = Column(Float)
    presale = relationship("PreSaleDB", back_populates="items")

class SaleDB(Base):
    __tablename__ = "sales"
    id = Column(Integer, primary_key=True, index=True)
    presale_id = Column(Integer, nullable=True)
    total_amount = Column(Float, nullable=False)
    amount_cash = Column(Float, default=0.0)
    amount_mp = Column(Float, default=0.0)
    payment_method = Column(String, default="Efectivo")

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Fiambrería POS API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    db = SessionLocal()
    admin = db.query(UserDB).filter(UserDB.email == "admin@fiambreria.com").first()
    if not admin:
        db.add(UserDB(
            name="Super Admin",
            email="admin@fiambreria.com",
            hashed_password="admin123",
            role="superadmin",
            is_active=True,
            can_preventa=True,
            can_caja=True,
            can_stock=True,
            can_ingreso=True
        ))
        db.commit()
    db.close()

init_db()

class PermissionsSchema(BaseModel):
    is_active: Optional[bool] = None
    can_preventa: Optional[bool] = None
    can_caja: Optional[bool] = None
    can_stock: Optional[bool] = None
    can_ingreso: Optional[bool] = None

class ItemSchema(BaseModel):
    product_id: int
    quantity: float

class PreSaleCreateSchema(BaseModel):
    items: List[ItemSchema]

class FinalizeSaleSchema(BaseModel):
    presale_id: Optional[int] = None
    items: List[ItemSchema]
    total_amount: float
    amount_cash: float
    amount_mp: float
    payment_method: str

class ProductCreateSchema(BaseModel):
    name: str
    category: Optional[str] = "Varios"
    price_per_unit: float
    unit_type: Optional[str] = "unid"
    stock: float
    barcode: Optional[str] = None
    is_active: Optional[bool] = True

@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.email == form_data.username).first()
    if not user or user.hashed_password != form_data.password:
        raise HTTPException(status_code=400, detail="Credenciales incorrectas")
    return {
        "access_token": f"token-{user.id}",
        "token_type": "bearer",
        "role": user.role,
        "is_active": user.is_active,
        "can_preventa": user.can_preventa,
        "can_caja": user.can_caja,
        "can_stock": user.can_stock,
        "can_ingreso": getattr(user, 'can_ingreso', True)
    }

@app.get("/users")
def get_users(db: Session = Depends(get_db)):
    return db.query(UserDB).all()

@app.post("/users")
def create_user(user_data: dict, db: Session = Depends(get_db)):
    new_u = UserDB(
        name=user_data["name"],
        email=user_data["email"],
        hashed_password=user_data["password"],
        role=user_data.get("role", "vendedor"),
        is_active=False,
        can_preventa=True,
        can_caja=False,
        can_stock=False,
        can_ingreso=False
    )
    db.add(new_u); db.commit(); db.refresh(new_u)
    return new_u

@app.patch("/users/{user_id}/permissions")
def update_permissions(user_id: int, p: PermissionsSchema, db: Session = Depends(get_db)):
    u = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if p.is_active is not None: u.is_active = p.is_active
    if p.can_preventa is not None: u.can_preventa = p.can_preventa
    if p.can_caja is not None: u.can_caja = p.can_caja
    if p.can_stock is not None: u.can_stock = p.can_stock
    if p.can_ingreso is not None: u.can_ingreso = p.can_ingreso
    db.commit(); db.refresh(u)
    return u

@app.get("/products")
def get_products(db: Session = Depends(get_db)):
    return db.query(ProductDB).filter(ProductDB.is_active == True).all()

@app.post("/products")
def create_product(prod: ProductCreateSchema, db: Session = Depends(get_db)):
    p = ProductDB(**prod.dict())
    db.add(p); db.commit(); db.refresh(p)
    return p

@app.put("/products/{product_id}")
def update_product(product_id: int, prod: ProductCreateSchema, db: Session = Depends(get_db)):
    p = db.query(ProductDB).filter(ProductDB.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    p.name = prod.name; p.category = prod.category; p.price_per_unit = prod.price_per_unit
    p.unit_type = prod.unit_type; p.stock = prod.stock; p.barcode = prod.barcode
    db.commit(); db.refresh(p)
    return p

@app.delete("/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    p = db.query(ProductDB).filter(ProductDB.id == product_id).first()
    if p: p.is_active = False; db.commit(); return {"status": "ok"}
    raise HTTPException(status_code=404, detail="No encontrado")

@app.post("/presales")
def create_presale(payload: PreSaleCreateSchema, db: Session = Depends(get_db)):
    total = 0.0
    presale = PreSaleDB(status="PENDIENTE")
    db.add(presale); db.flush()

    for item in payload.items:
        prod = db.query(ProductDB).filter(ProductDB.id == item.product_id).first()
        if prod:
            subtotal = prod.price_per_unit * item.quantity
            total += subtotal
            db.add(PreSaleItemDB(
                presale_id=presale.id,
                product_id=prod.id,
                product_name=prod.name,
                price_per_unit=prod.price_per_unit,
                unit_type=prod.unit_type,
                quantity=item.quantity
            ))
    
    presale.total_amount = total
    db.commit()
    return {"status": "success", "presale_id": presale.id, "total": total}

@app.get("/presales/pending")
def get_pending_presales(db: Session = Depends(get_db)):
    presales = db.query(PreSaleDB).filter(PreSaleDB.status == "PENDIENTE").all()
    result = []
    for ps in presales:
        items = [{"product_id": i.product_id, "name": i.product_name, "price_per_unit": i.price_per_unit, "unit_type": i.unit_type, "qty": i.quantity} for i in ps.items]
        result.append({"id": ps.id, "created_at": ps.created_at.strftime("%H:%M"), "total": ps.total_amount, "items": items})
    return result

@app.delete("/presales/{presale_id}")
def delete_presale(presale_id: int, db: Session = Depends(get_db)):
    ps = db.query(PreSaleDB).filter(PreSaleDB.id == presale_id).first()
    if ps:
        ps.status = "CANCELADA"
        db.commit()
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="No encontrada")

@app.post("/sales/finalize")
def finalize_sale(payload: FinalizeSaleSchema, db: Session = Depends(get_db)):
    sale = SaleDB(
        presale_id=payload.presale_id,
        total_amount=payload.total_amount,
        amount_cash=payload.amount_cash,
        amount_mp=payload.amount_mp,
        payment_method=payload.payment_method
    )
    db.add(sale)

    for item in payload.items:
        prod = db.query(ProductDB).filter(ProductDB.id == item.product_id).first()
        if prod:
            prod.stock = max(0.0, prod.stock - item.quantity)

    if payload.presale_id:
        ps = db.query(PreSaleDB).filter(PreSaleDB.id == payload.presale_id).first()
        if ps:
            ps.status = "COMPLETADA"

    db.commit()
    return {"status": "success", "message": "Venta procesada"}
