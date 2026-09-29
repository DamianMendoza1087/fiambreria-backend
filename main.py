from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
from typing import List, Optional

# --- CONFIGURACIÓN BASE DE DATOS ---
SQLALCHEMY_DATABASE_URL = "sqlite:///./fiambreria.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- MODELOS SQLALCHEMY ---
class UserDB(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="vendedor") # superadmin, dueno, encargado, vendedor, cajero, auditor
    is_active = Column(Boolean, default=True)
    is_cashier_active = Column(Boolean, default=False)

class ProductDB(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    category = Column(String, default="Fiambres")
    price_per_unit = Column(Float, nullable=False)
    unit_type = Column(String, default="kg")
    stock = Column(Float, default=0.0)
    barcode = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)

class SaleDB(Base):
    __tablename__ = "sales"
    id = Column(Integer, primary_key=True, index=True)
    total_amount = Column(Float, nullable=False)
    payment_method = Column(String, default="Efectivo") # Efectivo / Mercado Pago
    user_id = Column(Integer, nullable=True)

Base.metadata.create_all(bind=engine)

# --- INICIALIZACIÓN FASTAPI ---
app = FastAPI(title="Fiambrería POS API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dependencia DB
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Crear usuario Admin por defecto si no existe
def init_db():
    db = SessionLocal()
    admin = db.query(UserDB).filter(UserDB.email == "admin@fiambreria.com").first()
    if not admin:
        default_admin = UserDB(
            name="Super Admin",
            email="admin@fiambreria.com",
            hashed_password="admin123",
            role="superadmin",
            is_active=True,
            is_cashier_active=True
        )
        db.add(default_admin)
        db.commit()
    db.close()

init_db()

# --- ESQUEMAS PYDANTIC ---
class ProductCreate(BaseModel):
    name: str
    category: Optional[str] = "Fiambres"
    price_per_unit: float
    unit_type: Optional[str] = "kg"
    stock: float
    barcode: Optional[str] = None
    is_active: Optional[bool] = True

class UserCreate(BaseModel):
    name: str
    email: str
    password: str
    role: str

class SaleItem(BaseModel):
    product_id: int
    quantity: float

class SaleCreate(BaseModel):
    total_amount: float
    payment_method: str
    items: List[SaleItem]

# --- ENDPOINTS AUTH Y USUARIOS ---
@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.email == form_data.username).first()
    if not user or user.hashed_password != form_data.password:
        raise HTTPException(status_code=400, detail="Credenciales incorrectas")
    return {"access_token": f"token-{user.id}", "token_type": "bearer", "role": user.role}

@app.get("/users")
def get_users(db: Session = Depends(get_db)):
    return db.query(UserDB).all()

@app.post("/users")
def create_user(user_data: UserCreate, db: Session = Depends(get_db)):
    new_user = UserDB(
        name=user_data.name,
        email=user_data.email,
        hashed_password=user_data.password,
        role=user_data.role,
        is_active=True,
        is_cashier_active=False
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@app.patch("/users/{user_id}/activate-cashier")
def set_active_cashier(user_id: int, db: Session = Depends(get_db)):
    db.query(UserDB).update({"is_cashier_active": False})
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if user:
        user.is_cashier_active = True
        db.commit()
        return {"status": "success", "active_cashier": user.name}
    raise HTTPException(status_code=404, detail="Usuario no encontrado")

# --- ENDPOINTS PRODUCTOS ---
@app.get("/products")
def get_products(db: Session = Depends(get_db)):
    return db.query(ProductDB).filter(ProductDB.is_active == True).all()

@app.post("/products")
def create_product(product: ProductCreate, db: Session = Depends(get_db)):
    db_product = ProductDB(**product.dict())
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    return db_product

@app.delete("/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(ProductDB).filter(ProductDB.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    product.is_active = False
    db.commit()
    return {"status": "success"}

# --- ENDPOINT VENTAS ---
@app.post("/sales")
def create_sale(sale_data: SaleCreate, db: Session = Depends(get_db)):
    new_sale = SaleDB(total_amount=sale_data.total_amount, payment_method=sale_data.payment_method)
    db.add(new_sale)
    
    for item in sale_data.items:
        prod = db.query(ProductDB).filter(ProductDB.id == item.product_id).first()
        if prod:
            prod.stock -= item.quantity
            
    db.commit()
    return {"status": "success", "message": "Venta registrada y stock descontado"}
