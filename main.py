from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, ForeignKey, DateTime, func, Text, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from pydantic import BaseModel
from typing import List, Optional
import datetime
from zoneinfo import ZoneInfo

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
    role = Column(String, default="vendedor")  # 'masteradmin', 'superadmin', 'dueno', 'vendedor'
    is_active = Column(Boolean, default=False)
    can_preventa = Column(Boolean, default=True)
    can_caja = Column(Boolean, default=False)
    can_stock = Column(Boolean, default=False)
    can_ingreso = Column(Boolean, default=False)
    can_alertas = Column(Boolean, default=False)
    can_rrhh = Column(Boolean, default=False)
    can_mrp = Column(Boolean, default=False)
    can_verificacion = Column(Boolean, default=False)
    can_kpis = Column(Boolean, default=False)
    can_edit_records = Column(Boolean, default=False)  # Controla si el dueño/usuario puede editar/eliminar registros
    branch_id = Column(Integer, default=1, index=True)  # Sucursal de trabajo predeterminada

class BranchDB(Base):
    __tablename__ = "branches"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    code = Column(String, nullable=False, unique=True, index=True)
    is_active = Column(Boolean, default=True)


class BranchProductDB(Base):
    __tablename__ = "branch_products"
    id = Column(Integer, primary_key=True, index=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    price_per_unit = Column(Float, nullable=True)
    is_available = Column(Boolean, default=True)
    is_exclusive = Column(Boolean, default=False)


class WorkLogDB(Base):
    __tablename__ = "work_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    user_name = Column(String)
    user_email = Column(String)
    clock_in = Column(DateTime, default=datetime.datetime.utcnow)
    clock_out = Column(DateTime, nullable=True)
    hours_worked = Column(Float, default=0.0)
    role_worked = Column(String, nullable=True)

class ProductDB(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    category = Column(String, default="Fiambres")
    cost_price = Column(Float, default=0.0)
    previous_cost_price = Column(Float, default=0.0)
    price_per_unit = Column(Float, nullable=False)
    supplier = Column(String, nullable=True)
    unit_type = Column(String, default="unid")
    stock = Column(Float, default=0.0)
    last_counted_qty = Column(Float, nullable=True)
    last_counted_by = Column(String, nullable=True)
    barcode = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    brand = Column(String, nullable=True)
    requires_expiration = Column(Boolean, default=False)
    replenishment_policy = Column(String, default="MRP")
    lots = relationship("ProductLotDB", back_populates="product", cascade="all, delete-orphan")

class ProductLotDB(Base):
    __tablename__ = "product_lots"
    branch_id = Column(Integer, default=1, index=True)
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"))
    lot_number = Column(String, nullable=True)
    supplier = Column(String, nullable=True)
    cost_price = Column(Float, default=0.0)
    initial_qty = Column(Float, default=0.0)
    current_qty = Column(Float, default=0.0)
    expiration_date = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    product = relationship("ProductDB", back_populates="lots")

class ProductIngressDB(Base):
    __tablename__ = "product_ingresses"
    branch_id = Column(Integer, default=1, index=True)
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    supplier = Column(String, nullable=True)
    cost_price = Column(Float, default=0.0)
    quantity = Column(Float, default=0.0)
    lot_number = Column(String, nullable=True)
    expiration_date = Column(String, nullable=True)
    received_at = Column(DateTime, default=datetime.datetime.utcnow)
    received_by = Column(String, nullable=True)
    notes = Column(Text, nullable=True)

class StockMovementDB(Base):
    __tablename__ = "stock_movements"
    branch_id = Column(Integer, default=1, index=True)
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    lot_id = Column(Integer, ForeignKey("product_lots.id"), nullable=True)
    movement_type = Column(String, nullable=False)
    quantity = Column(Float, nullable=False)
    unit_cost = Column(Float, default=0.0)
    reason = Column(String, nullable=True)
    actor = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    reference_type = Column(String, nullable=True)
    reference_id = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)

class AuditEventDB(Base):
    __tablename__ = "audit_events"
    id = Column(Integer, primary_key=True, index=True)
    entity_type = Column(String, nullable=False, index=True)
    entity_id = Column(Integer, nullable=True, index=True)
    field_name = Column(String, nullable=True)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    actor = Column(String, nullable=True)
    reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class PriceHistoryDB(Base):
    __tablename__ = "price_history"
    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    old_price = Column(Float, nullable=True)
    new_price = Column(Float, nullable=False)
    changed_by = Column(String, nullable=True)
    reason = Column(String, nullable=True)
    changed_at = Column(DateTime, default=datetime.datetime.utcnow)

class CashMovementDB(Base):
    __tablename__ = "cash_movements"
    branch_id = Column(Integer, default=1, index=True)
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("cash_sessions.id"), nullable=True, index=True)
    movement_type = Column(String, nullable=False)
    category = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    concept = Column(String, nullable=False)
    actor = Column(String, nullable=True)
    supplier = Column(String, nullable=True)
    employee_email = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class AlertStateDB(Base):
    __tablename__ = "alert_states"
    id = Column(Integer, primary_key=True, index=True)
    alert_key = Column(String, unique=True, nullable=False, index=True)
    status = Column(String, default="NEW")
    snoozed_until = Column(DateTime, nullable=True)
    resolved_by = Column(String, nullable=True)
    resolution_note = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class PreSaleDB(Base):
    __tablename__ = "presales"
    branch_id = Column(Integer, default=1, index=True)
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    status = Column(String, default="PENDIENTE")
    total_amount = Column(Float, default=0.0)
    created_by = Column(String, nullable=True)
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
    branch_id = Column(Integer, default=1, index=True)
    id = Column(Integer, primary_key=True, index=True)
    presale_id = Column(Integer, nullable=True)
    session_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    total_amount = Column(Float, nullable=False)
    amount_cash = Column(Float, default=0.0)
    amount_mp = Column(Float, default=0.0)
    payment_method = Column(String, default="Efectivo")
    sold_by = Column(String, nullable=True)
    items = relationship("SaleItemDB", back_populates="sale", cascade="all, delete-orphan")

class SaleItemDB(Base):
    __tablename__ = "sale_items"
    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"))
    product_id = Column(Integer, ForeignKey("products.id"))
    product_name = Column(String, nullable=True)
    quantity = Column(Float, default=0.0)
    unit_price = Column(Float, default=0.0)
    unit_cost = Column(Float, default=0.0)
    cogs = Column(Float, default=0.0)
    gross_profit = Column(Float, default=0.0)
    sale = relationship("SaleDB", back_populates="items")

class CashSessionDB(Base):
    __tablename__ = "cash_sessions"
    branch_id = Column(Integer, default=1, index=True)
    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, nullable=False)
    opened_at = Column(DateTime, default=datetime.datetime.utcnow)
    opened_by = Column(String, nullable=False)
    initial_amount = Column(Float, default=0.0)
    is_open = Column(Boolean, default=True)
    closed_at = Column(DateTime, nullable=True)
    closed_by = Column(String, nullable=True)
    reported_cash = Column(Float, nullable=True)
    expected_cash = Column(Float, nullable=True)
    total_mp = Column(Float, nullable=True)
    difference = Column(Float, nullable=True)
    status_message = Column(String, nullable=True)

class InventoryCountDB(Base):
    __tablename__ = "inventory_counts"
    id = Column(Integer, primary_key=True, index=True)
    branch_id = Column(Integer, index=True, default=1)
    product_id = Column(Integer, ForeignKey("products.id"), index=True, nullable=False)
    system_qty = Column(Float, default=0.0)
    counted_qty = Column(Float, default=0.0)
    difference = Column(Float, default=0.0)
    reported_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

Base.metadata.create_all(bind=engine)

def ensure_v2_schema():
    # Migracion aditiva V2 para SQLite. Conserva los datos existentes.
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    existing = {c["name"] for c in inspector.get_columns("products")}
    additions = {
        "requires_expiration": "BOOLEAN DEFAULT 0",
        "replenishment_policy": "VARCHAR DEFAULT 'MRP'",
        "brand": "VARCHAR",
    }
    with engine.begin() as conn:
        for column_name, ddl in additions.items():
            if column_name not in existing:
                conn.execute(text(f"ALTER TABLE products ADD COLUMN {column_name} {ddl}"))

    work_cols={c["name"] for c in inspector.get_columns("work_logs")}
    if "role_worked" not in work_cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE work_logs ADD COLUMN role_worked VARCHAR"))

    sale_cols={c["name"] for c in inspector.get_columns("sale_items")}
    sale_additions={"unit_price":"FLOAT DEFAULT 0","unit_cost":"FLOAT DEFAULT 0","cogs":"FLOAT DEFAULT 0","gross_profit":"FLOAT DEFAULT 0"}
    with engine.begin() as conn:
        for column_name, ddl in sale_additions.items():
            if column_name not in sale_cols:
                conn.execute(text(f"ALTER TABLE sale_items ADD COLUMN {column_name} {ddl}"))

ensure_v2_schema()



def ensure_multilocal_schema():
    Base.metadata.create_all(bind=engine)

    user_cols = {c["name"] for c in inspect(engine).get_columns("users")}

    with engine.begin() as conn:
        if "branch_id" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN branch_id INTEGER DEFAULT 1"))

        conn.execute(text("UPDATE users SET branch_id=1 WHERE branch_id IS NULL"))

    tables = [
        "product_lots",
        "product_ingresses",
        "stock_movements",
        "cash_movements",
        "presales",
        "sales",
        "cash_sessions",
    ]

    with engine.begin() as conn:
        for table_name in tables:
            cols = {
                c["name"]
                for c in inspect(engine).get_columns(table_name)
            }

            if "branch_id" not in cols:
                conn.execute(text(
                    f"ALTER TABLE {table_name} "
                    "ADD COLUMN branch_id INTEGER DEFAULT 1"
                ))

            conn.execute(text(
                f"UPDATE {table_name} "
                "SET branch_id=1 WHERE branch_id IS NULL"
            ))

        if not conn.execute(
            text("SELECT id FROM branches WHERE id=1")
        ).fetchone():
            conn.execute(text(
                "INSERT INTO branches "
                "(id,name,code,is_active) VALUES "
                "(1,'Fiambrería Local','FIAMBRERIA',1)"
            ))

        if not conn.execute(
            text("SELECT id FROM branches WHERE id=2")
        ).fetchone():
            conn.execute(text(
                "INSERT INTO branches "
                "(id,name,code,is_active) VALUES "
                "(2,'Feria Damyale','FERIA',1)"
            ))


ensure_multilocal_schema()


app = FastAPI(title="Fiambrería POS, RRHH, MRP, KPIs & Permisos API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

ARG_TZ=ZoneInfo("America/Argentina/Buenos_Aires")

def ar_now():
    return datetime.datetime.now(ARG_TZ)

def utc_to_ar(value):
    if not value: return None
    aware=value.replace(tzinfo=datetime.timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(ARG_TZ)

def parse_date_to_iso(date_str: Optional[str]) -> Optional[str]:
    if date_str is None or not str(date_str).strip():
        return None
    value = str(date_str).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise HTTPException(status_code=422, detail="Fecha de vencimiento invalida. Usa DD/MM/AAAA o AAAA-MM-DD.")

def format_iso_to_ddmmyyyy(iso_str: str) -> str:
    if not iso_str:
        return ""
    parts = iso_str.split('-')
    if len(parts) == 3 and len(parts[0]) == 4:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return iso_str

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
            can_preventa=True, can_caja=True, can_stock=True, can_ingreso=True,
            can_alertas=True, can_rrhh=True, can_mrp=True, can_verificacion=True,
            can_kpis=True, can_edit_records=True
        ))
        db.commit()
    db.close()

init_db()

class AuditSchema(BaseModel):
    counted_qty: float
    reported_by: str

class PermissionsSchema(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None
    can_preventa: Optional[bool] = None
    can_caja: Optional[bool] = None
    can_stock: Optional[bool] = None
    can_ingreso: Optional[bool] = None
    can_alertas: Optional[bool] = None
    can_rrhh: Optional[bool] = None
    can_mrp: Optional[bool] = None
    can_verificacion: Optional[bool] = None
    can_kpis: Optional[bool] = None
    can_edit_records: Optional[bool] = None
    branch_id: Optional[int] = None

class ItemSchema(BaseModel):
    product_id: int
    quantity: float

class PreSaleCreateSchema(BaseModel):
    items: List[ItemSchema]
    created_by: Optional[str] = "Anonimo"

class FinalizeSaleSchema(BaseModel):
    presale_id: Optional[int] = None
    items: List[ItemSchema]
    total_amount: float
    amount_cash: float
    amount_mp: float
    payment_method: str
    sold_by: Optional[str] = "Anonimo"

class ProductCreateSchema(BaseModel):
    name: str
    category: Optional[str] = "Varios"
    cost_price: Optional[float] = 0.0
    price_per_unit: float
    supplier: Optional[str] = None
    unit_type: Optional[str] = "unid"
    stock: float = 0.0
    barcode: Optional[str] = None
    is_active: Optional[bool] = True
    expiration_date: Optional[str] = None
    lot_number: Optional[str] = None
    brand: Optional[str] = None
    requires_expiration: Optional[bool] = None
    replenishment_policy: Optional[str] = None
    received_by: Optional[str] = "Anonimo"
    notes: Optional[str] = None

class ProductMasterSchema(BaseModel):
    name: str
    category: Optional[str] = "Varios"
    brand: Optional[str] = None
    barcode: Optional[str] = None
    price_per_unit: float
    unit_type: Optional[str] = "unid"
    requires_expiration: bool = False
    replenishment_policy: Optional[str] = "MRP"
    is_active: bool = True

class BranchProductConfigSchema(BaseModel):
    price_per_unit: Optional[float] = None
    is_available: bool = True
    is_exclusive: bool = False


class IngressCreateSchema(BaseModel):
    product_id: int
    supplier: Optional[str] = None
    cost_price: float
    quantity: float
    lot_number: Optional[str] = None
    expiration_date: Optional[str] = None
    received_by: Optional[str] = "Anonimo"
    notes: Optional[str] = None

class CashOpenSchema(BaseModel):
    initial_amount: float
    opened_by: str

class CashCloseSchema(BaseModel):
    reported_cash: float
    closed_by: str
    attempt_number: int

class CashMovementCreateSchema(BaseModel):
    movement_type: str = "OUT"
    category: str
    amount: float
    concept: str
    actor: Optional[str] = "Anonimo"
    supplier: Optional[str] = None
    employee_email: Optional[str] = None
    notes: Optional[str] = None

class StockLossSchema(BaseModel):
    product_id: int
    quantity: float
    reason: str
    actor: Optional[str] = "Anonimo"
    lot_id: Optional[int] = None
    notes: Optional[str] = None

class AlertActionSchema(BaseModel):
    action: str
    actor: Optional[str] = "Anonimo"
    snooze_hours: Optional[int] = 24
    note: Optional[str] = None

class ReplenishmentPolicySchema(BaseModel):
    policy: str
    actor: Optional[str] = "Anonimo"
    reason: Optional[str] = None

class ShiftStartSchema(BaseModel):
    user_id: int
    role_worked: Optional[str] = None

class ShiftEndSchema(BaseModel):
    user_id: int

class UserStatusSchema(BaseModel):
    is_active: bool

class IngressCorrectionSchema(BaseModel):
    supplier: Optional[str] = None
    cost_price: Optional[float] = None
    quantity: Optional[float] = None
    lot_number: Optional[str] = None
    expiration_date: Optional[str] = None
    actor: str
    reason: str

@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.email == form_data.username).first()
    if not user or user.hashed_password != form_data.password:
        raise HTTPException(status_code=400, detail="Credenciales incorrectas")

    # Cuenta maestra protegida del sistema
    if user.email.lower() == "admin@fiambreria.com":
        changed = False
        if user.role != "masteradmin":
            user.role = "masteradmin"
            changed = True
        if not user.is_active:
            user.is_active = True
            changed = True
        if not user.can_edit_records:
            user.can_edit_records = True
            changed = True
        if changed:
            db.commit()
            db.refresh(user)

    return {
        "access_token": f"token-{user.id}",
        "token_type": "bearer",
        "role": user.role,
        "is_active": user.is_active,
        "can_preventa": user.can_preventa,
        "can_caja": user.can_caja,
        "can_stock": user.can_stock,
        "can_ingreso": user.can_ingreso,
        "can_alertas": user.can_alertas,
        "can_rrhh": user.can_rrhh,
        "can_mrp": user.can_mrp,
        "can_verificacion": user.can_verificacion,
        "can_kpis": user.can_kpis,
        "can_edit_records": user.can_edit_records,
        "branch_id": user.branch_id or 1
    }

@app.get("/users")
def get_users(db: Session = Depends(get_db)):
    return db.query(UserDB).all()

@app.post("/users")
def create_user(user_data: dict, db: Session = Depends(get_db)):
    role = user_data.get("role", "vendedor")
    new_u = UserDB(
        name=user_data["name"],
        email=user_data["email"],
        hashed_password=user_data["password"],
        role=role,
        is_active=False,
        can_preventa=True, can_caja=False, can_stock=False, can_ingreso=False,
        can_alertas=False, can_rrhh=False, can_mrp=False, can_verificacion=False, can_kpis=False,
        can_edit_records=(role in ["superadmin", "dueno"]),
        branch_id=int(user_data.get("branch_id", 1) or 1)
    )
    db.add(new_u); db.commit(); db.refresh(new_u)
    return new_u

@app.patch("/users/{user_id}/permissions")
def update_permissions(user_id: int, p: PermissionsSchema, db: Session = Depends(get_db)):
    u=db.query(UserDB).filter(UserDB.id==user_id).first()
    if not u: raise HTTPException(status_code=404,detail="Usuario no encontrado")

    # Admin Maestro: cuenta reservada e indegradable
    if u.email.lower() == "admin@fiambreria.com":
        if p.role is not None and p.role != "masteradmin":
            raise HTTPException(status_code=403, detail="El Admin Maestro no puede ser degradado")
        if p.is_active is False:
            raise HTTPException(status_code=403, detail="El Admin Maestro no puede ser deshabilitado")
        u.role = "masteradmin"
        u.is_active = True
        u.can_edit_records = True

    if p.role is not None and u.email.lower() != "admin@fiambreria.com":
        if p.role not in ("superadmin", "dueno", "vendedor"):
            raise HTTPException(status_code=400, detail="Rol invalido")
        u.role=p.role
        if p.role=="superadmin": u.can_edit_records=True
    if p.is_active is not None: u.is_active=p.is_active
    if p.can_preventa is not None: u.can_preventa=p.can_preventa
    if p.can_caja is not None: u.can_caja=p.can_caja
    if p.can_stock is not None: u.can_stock=p.can_stock
    if p.can_ingreso is not None: u.can_ingreso=p.can_ingreso
    if p.can_alertas is not None: u.can_alertas=p.can_alertas
    if p.can_rrhh is not None: u.can_rrhh=p.can_rrhh
    if p.can_mrp is not None: u.can_mrp=p.can_mrp
    if p.can_verificacion is not None: u.can_verificacion=p.can_verificacion
    if p.can_kpis is not None: u.can_kpis=p.can_kpis
    if p.can_edit_records is not None: u.can_edit_records=p.can_edit_records
    if p.branch_id is not None:
        if p.branch_id not in (1, 2):
            raise HTTPException(status_code=400, detail="Sucursal invalida")
        u.branch_id=p.branch_id
    db.commit(); db.refresh(u); return u

@app.patch("/users/{user_id}/status")
def update_user_status(user_id: int, payload: UserStatusSchema, db: Session=Depends(get_db)):
    u=db.query(UserDB).filter(UserDB.id==user_id).first()
    if not u: raise HTTPException(status_code=404,detail="Usuario no encontrado")
    if u.email.lower() == "admin@fiambreria.com" and not payload.is_active:
        raise HTTPException(status_code=403, detail="El Admin Maestro no puede ser deshabilitado")
    u.is_active=payload.is_active
    db.commit()
    return {"status":"success","user_id":u.id,"is_active":u.is_active}

def _kpi_period(period: str):
    now=datetime.datetime.utcnow()
    p=(period or "month").lower()
    if p in ("week","weekly","semana","semanal"):
        start=now-datetime.timedelta(days=7); label="week"
    elif p in ("30d","30days","30dias"):
        start=now-datetime.timedelta(days=30); label="30d"
    else:
        start=datetime.datetime(now.year,now.month,1); label="month"
    return start,now,label

def _economic_cash_category(category: str):
    c=(category or "").strip().upper()
    if c in {"PROVEEDOR","PROVEEDORES","PAGO_PROVEEDOR","MERCADERIA","COMPRA_MERCADERIA","RETIRO_DUENO","RETIRO_DUEÑO","OWNER_WITHDRAWAL"}:
        return None
    if c in {"EMPLEADO","EMPLEADOS","PAGO_EMPLEADO","SUELDO","SUELDOS","SALARIO"}:
        return "employee"
    if c in {"SERVICIO","SERVICIOS","OPERATIVO","OPERATIVOS","GASTO_OPERATIVO","COMPRA_MENOR","CAJA_CHICA"}:
        return "operating"
    return "other"

def build_profitability(start: datetime.datetime, end: datetime.datetime, db: Session, branch_id: int = 1):
    sales=db.query(SaleDB).filter(
        SaleDB.created_at>=start,
        SaleDB.created_at<end,
        SaleDB.branch_id==branch_id
    ).all()
    sale_ids=[x.id for x in sales]
    items=db.query(SaleItemDB).filter(SaleItemDB.sale_id.in_(sale_ids)).all() if sale_ids else []
    revenue=round(sum(float(x.total_amount or 0) for x in sales),2)
    cogs=round(sum(float(x.cogs or 0) for x in items),2)
    gross=round(revenue-cogs,2)

    losses=db.query(StockMovementDB).filter(
        StockMovementDB.movement_type=="LOSS",
        StockMovementDB.created_at>=start,
        StockMovementDB.created_at<end,
        StockMovementDB.branch_id==branch_id
    ).all()
    loss_cost=round(sum(abs(float(x.quantity or 0))*float(x.unit_cost or 0) for x in losses),2)

    movements=db.query(CashMovementDB).filter(
        CashMovementDB.created_at>=start,
        CashMovementDB.created_at<end,
        CashMovementDB.branch_id==branch_id
    ).all()
    cash_in=round(sum(float(x.amount or 0) for x in movements if (x.movement_type or "").upper()=="IN"),2)
    cash_out=round(sum(float(x.amount or 0) for x in movements if (x.movement_type or "").upper()=="OUT"),2)
    operating=employee=other=0.0
    supplier_payments=owner_withdrawals=0.0
    for x in movements:
        if (x.movement_type or "").upper()!="OUT": continue
        cat=(x.category or "").strip().upper()
        amount=float(x.amount or 0)
        if cat in {"PROVEEDOR","PROVEEDORES","PAGO_PROVEEDOR","MERCADERIA","COMPRA_MERCADERIA"}:
            supplier_payments+=amount; continue
        if cat in {"RETIRO_DUENO","RETIRO_DUEÑO","OWNER_WITHDRAWAL"}:
            owner_withdrawals+=amount; continue
        bucket=_economic_cash_category(cat)
        if bucket=="employee": employee+=amount
        elif bucket=="operating": operating+=amount
        else: other+=amount
    operating=round(operating,2); employee=round(employee,2); other=round(other,2)
    economic_expenses=round(loss_cost+operating+employee+other,2)
    net=round(gross-economic_expenses,2)
    return {
        "sales_count":len(sales),"revenue":revenue,"cogs":cogs,"gross_profit":gross,
        "gross_margin_pct":round((gross/revenue*100) if revenue else 0,2),
        "losses_at_cost":loss_cost,"operating_expenses":operating,"employee_expenses":employee,
        "other_expenses":other,"economic_expenses":economic_expenses,"net_profit":net,
        "net_margin_pct":round((net/revenue*100) if revenue else 0,2),
        "cash_flow":{"sales_cash":round(sum(float(x.amount_cash or 0) for x in sales),2),
                     "sales_mp":round(sum(float(x.amount_mp or 0) for x in sales),2),
                     "other_cash_in":cash_in,"cash_out":cash_out,
                     "supplier_payments":round(supplier_payments,2),
                     "owner_withdrawals":round(owner_withdrawals,2)}
    }

@app.get("/kpis/profitability")
def get_profitability(period: str="month", branch_id: int=1, db: Session=Depends(get_db)):
    start,end,label=_kpi_period(period)
    data=build_profitability(start,end,db,branch_id)
    return {"period":label,"from":start.isoformat(),"to":end.isoformat(),**data}

@app.get("/kpis/dashboard")
def get_kpis_dashboard(period: str="month", branch_id: int=1, db: Session=Depends(get_db)):
    start,end,label=_kpi_period(period)
    data=build_profitability(start,end,db,branch_id)
    top=db.query(SaleItemDB.product_name,func.sum(SaleItemDB.quantity).label("qty")).join(SaleDB).filter(
        SaleDB.created_at>=start,
        SaleDB.created_at<end,
        SaleDB.branch_id==branch_id
    ).group_by(SaleItemDB.product_name).order_by(func.sum(SaleItemDB.quantity).desc()).limit(10).all()
    alerts=get_system_alerts(branch_id=branch_id, db=db)
    critical=sum(1 for a in alerts if a.get("level")=="CRITICAL")
    important=sum(1 for a in alerts if a.get("level") in ("IMPORTANT","WARNING","HIGH"))
    return {
        "period":label,"from":start.isoformat(),"to":end.isoformat(),
        "health_status":"ATENCION" if critical else "OK",
        "summary_text":f"Resultado neto ${data['net_profit']:.2f}. Alertas criticas pendientes: {critical}.",
        "revenue_this_month":data["revenue"],"critical_alerts_count":critical,
        "warning_alerts_count":important,
        "top_products":[{"name":x[0] or "Producto","total_qty":round(float(x[1] or 0),2)} for x in top],
        **data
    }

@app.post("/hr/shifts/start")
def start_shift(payload: ShiftStartSchema, db: Session=Depends(get_db)):
    user=db.query(UserDB).filter(UserDB.id==payload.user_id).first()
    if not user: raise HTTPException(status_code=404,detail="Empleado no encontrado")
    if not user.is_active: raise HTTPException(status_code=403,detail="El usuario esta inhabilitado")
    current=db.query(WorkLogDB).filter(WorkLogDB.user_id==user.id,WorkLogDB.clock_out==None).order_by(WorkLogDB.id.desc()).first()
    if current: raise HTTPException(status_code=409,detail="El empleado ya tiene un turno abierto")
    log=WorkLogDB(user_id=user.id,user_name=user.name,user_email=user.email,clock_in=datetime.datetime.utcnow(),role_worked=payload.role_worked or user.role)
    db.add(log); db.commit(); db.refresh(log)
    return {"status":"success","shift_id":log.id,"user_id":user.id,"role_worked":log.role_worked}

@app.post("/hr/shifts/end")
def end_shift(payload: ShiftEndSchema, db: Session=Depends(get_db)):
    log=db.query(WorkLogDB).filter(WorkLogDB.user_id==payload.user_id,WorkLogDB.clock_out==None).order_by(WorkLogDB.id.desc()).first()
    if not log: raise HTTPException(status_code=404,detail="No hay turno abierto para ese empleado")
    now=datetime.datetime.utcnow(); log.clock_out=now
    log.hours_worked=round((now-log.clock_in).total_seconds()/3600.0,2)
    db.commit()
    return {"status":"success","shift_id":log.id,"hours_worked":log.hours_worked}

@app.get("/hr/shifts/active")
def active_shifts(db: Session=Depends(get_db)):
    return db.query(WorkLogDB).filter(WorkLogDB.clock_out==None).order_by(WorkLogDB.clock_in.asc()).all()

@app.get("/hr/worklogs")
def get_worklogs(email: Optional[str]=None, days: Optional[int]=None, db: Session=Depends(get_db)):
    q=db.query(WorkLogDB)
    if email: q=q.filter(WorkLogDB.user_email==email)
    if days and days>0: q=q.filter(WorkLogDB.clock_in>=datetime.datetime.utcnow()-datetime.timedelta(days=days))
    logs=q.order_by(WorkLogDB.id.desc()).all()
    return [{"id":l.id,"user_id":l.user_id,"user_name":l.user_name,"user_email":l.user_email,"role_worked":l.role_worked,"date":l.clock_in.strftime("%Y-%m-%d"),"clock_in":l.clock_in.strftime("%H:%M hs"),"clock_out":l.clock_out.strftime("%H:%M hs") if l.clock_out else "En jornada","hours_worked":l.hours_worked} for l in logs]

def get_employee_stats(email: str, days: int, db: Session):
    since=datetime.datetime.utcnow()-datetime.timedelta(days=max(1,days))
    user=db.query(UserDB).filter(UserDB.email==email).first()
    sales=db.query(SaleDB).filter(SaleDB.sold_by==email,SaleDB.created_at>=since).all()
    revenue=sum(x.total_amount for x in sales)
    work=db.query(WorkLogDB).filter(WorkLogDB.user_email==email,WorkLogDB.clock_in>=since).all()
    now=datetime.datetime.utcnow(); hours=0.0; roles={}
    for w in work:
        end=w.clock_out or now
        hours+=(w.hours_worked if w.clock_out else (end-w.clock_in).total_seconds()/3600.0)
        role=w.role_worked or (user.role if user else "sin_rol")
        roles[role]=roles.get(role,0)+1
    cash_ops=db.query(CashMovementDB).filter(CashMovementDB.actor==email,CashMovementDB.created_at>=since).count()
    presales=db.query(PreSaleDB).filter(PreSaleDB.created_by==email,PreSaleDB.created_at>=since).count()
    losses=db.query(StockMovementDB).filter(StockMovementDB.actor==email,StockMovementDB.movement_type=="LOSS",StockMovementDB.created_at>=since).count()
    return {"email":email,"name":user.name if user else email,"account_enabled":user.is_active if user else None,"current_role":user.role if user else None,"roles_worked":roles,"days":days,"shifts_count":len(work),"total_hours":round(hours,2),"sales_count":len(sales),"total_revenue":round(revenue,2),"ticket_avg":round(revenue/max(1,len(sales)),2),"presales_count":presales,"cash_operations":cash_ops,"stock_loss_operations":losses}

@app.get("/hr/performance")
def get_performance(email: str, days: int=30, db: Session=Depends(get_db)):
    return get_employee_stats(email,days,db)

@app.get("/hr/compare")
def compare_employees(emails: str="", days: int=30, db: Session=Depends(get_db), email1: Optional[str]=None, email2: Optional[str]=None):
    selected=[x.strip() for x in emails.split(",") if x.strip()]
    if not selected: selected=[x for x in (email1,email2) if x]
    if not selected: selected=[u.email for u in db.query(UserDB).order_by(UserDB.name.asc()).all()]
    unique=[]
    for email in selected:
        if email not in unique: unique.append(email)
    return {"days":days,"count":len(unique),"employees":[get_employee_stats(email,days,db) for email in unique]}

def branch_product_view(product: ProductDB, branch_id: int, db: Session):
    config = db.query(BranchProductDB).filter(
        BranchProductDB.branch_id == branch_id,
        BranchProductDB.product_id == product.id
    ).first()

    # Compatibilidad histórica: todos los productos existentes pertenecen
    # inicialmente a Fiambrería Local.
    if branch_id == 1 and not config:
        config = BranchProductDB(
            branch_id=1,
            product_id=product.id,
            price_per_unit=product.price_per_unit,
            is_available=product.is_active,
            is_exclusive=False
        )
        db.add(config)
        db.flush()

    if not config or not config.is_available:
        return None

    data = {c.name: getattr(product, c.name) for c in ProductDB.__table__.columns}
    data["stock"] = get_branch_stock(db, product.id, branch_id)
    data["price_per_unit"] = (
        config.price_per_unit
        if config.price_per_unit is not None
        else product.price_per_unit
    )
    data["branch_id"] = branch_id
    data["is_exclusive"] = bool(config.is_exclusive)
    return data


@app.get("/products")
def get_products(branch_id: int = 1, db: Session = Depends(get_db)):
    products = db.query(ProductDB).filter(ProductDB.is_active == True).order_by(ProductDB.name.asc()).all()
    result = []
    for product in products:
        view = branch_product_view(product, branch_id, db)
        if view:
            result.append(view)
    db.commit()
    return result


@app.get("/products/master/by-barcode/{barcode}")
def get_master_product_by_barcode(
    barcode: str,
    db: Session = Depends(get_db)
):
    clean = barcode.strip()

    product = db.query(ProductDB).filter(
        ProductDB.barcode == clean,
        ProductDB.is_active == True
    ).first()

    if not product:
        raise HTTPException(
            status_code=404,
            detail="EAN no asociado a ningun producto"
        )

    return {
        c.name: getattr(product, c.name)
        for c in ProductDB.__table__.columns
    }


@app.get("/products/by-barcode/{barcode}")
def get_product_by_barcode(barcode: str, branch_id: int = 1, db: Session = Depends(get_db)):
    product = db.query(ProductDB).filter(ProductDB.barcode == barcode.strip()).first()
    if not product:
        raise HTTPException(status_code=404, detail="EAN no asociado a ningun producto")

    view = branch_product_view(product, branch_id, db)
    if not view:
        raise HTTPException(status_code=404, detail="Producto no disponible en esta sucursal")

    db.commit()
    return view


@app.get("/products/search")
def search_products(q: str = "", branch_id: int = 1, db: Session = Depends(get_db)):
    query = db.query(ProductDB).filter(ProductDB.is_active == True)

    if q.strip():
        query = query.filter(ProductDB.name.ilike(f"%{q.strip()}%"))

    result = []
    for product in query.order_by(ProductDB.name.asc()).limit(100).all():
        view = branch_product_view(product, branch_id, db)
        if view:
            result.append(view)
        if len(result) >= 50:
            break

    db.commit()
    return result

def normalize_barcode(value: Optional[str]) -> Optional[str]:
    clean = str(value).strip() if value is not None else ""
    return clean or None

def register_ingress(product: ProductDB, supplier: Optional[str], cost_price: float, quantity: float,
                     lot_number: Optional[str], expiration_date: Optional[str], received_by: Optional[str],
                     notes: Optional[str], db: Session, branch_id: int = 1):
    if quantity <= 0: raise HTTPException(status_code=422, detail="La cantidad debe ser mayor a cero")
    if cost_price < 0: raise HTTPException(status_code=422, detail="El costo no puede ser negativo")
    iso_exp = parse_date_to_iso(expiration_date)
    if product.requires_expiration and not iso_exp:
        raise HTTPException(status_code=422, detail="Este producto requiere vencimiento")
    product.previous_cost_price = product.cost_price or 0.0
    product.cost_price = cost_price
    product.supplier = supplier or product.supplier
    product.stock = (product.stock or 0.0) + quantity
    product.is_active = True
    lot=ProductLotDB(branch_id=branch_id,product_id=product.id,lot_number=lot_number or f"LOTE-{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}",supplier=supplier,cost_price=cost_price,initial_qty=quantity,current_qty=quantity,expiration_date=iso_exp)
    db.add(lot); db.flush()
    ing=ProductIngressDB(branch_id=branch_id,product_id=product.id,supplier=supplier,cost_price=cost_price,quantity=quantity,lot_number=lot.lot_number,expiration_date=iso_exp,received_by=received_by,notes=notes)
    db.add(ing); db.flush()
    db.add(StockMovementDB(branch_id=branch_id,product_id=product.id,lot_id=lot.id,movement_type="INGRESS",quantity=quantity,unit_cost=cost_price,reason="Ingreso de mercaderia",actor=received_by,reference_type="product_ingress",reference_id=ing.id,notes=notes))
    return ing,lot

@app.get("/branches/{branch_id}/products")
def get_branch_product_admin(branch_id: int, db: Session = Depends(get_db)):
    branch = db.query(BranchDB).filter(
        BranchDB.id == branch_id,
        BranchDB.is_active == True
    ).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada")

    products = db.query(ProductDB).filter(
        ProductDB.is_active == True
    ).order_by(ProductDB.name.asc()).all()

    result = []

    for product in products:
        config = db.query(BranchProductDB).filter(
            BranchProductDB.branch_id == branch_id,
            BranchProductDB.product_id == product.id
        ).first()

        # La sucursal principal conserva compatibilidad con catálogo histórico.
        if branch_id == 1 and not config:
            config = BranchProductDB(
                branch_id=1,
                product_id=product.id,
                price_per_unit=product.price_per_unit,
                is_available=True,
                is_exclusive=False
            )
            db.add(config)
            db.flush()

        result.append({
            "product_id": product.id,
            "name": product.name,
            "category": product.category,
            "brand": product.brand,
            "barcode": product.barcode,
            "unit_type": product.unit_type,
            "base_price": product.price_per_unit,
            "branch_price": (
                config.price_per_unit
                if config and config.price_per_unit is not None
                else product.price_per_unit
            ),
            "stock": get_branch_stock(db, product.id, branch_id),
            "is_available": bool(config.is_available) if config else False,
            "is_exclusive": bool(config.is_exclusive) if config else False,
            "branch_id": branch_id
        })

    db.commit()
    return result


@app.put("/branches/{branch_id}/products/{product_id}")
def configure_branch_product(
    branch_id: int,
    product_id: int,
    payload: BranchProductConfigSchema,
    db: Session = Depends(get_db)
):
    branch = db.query(BranchDB).filter(
        BranchDB.id == branch_id,
        BranchDB.is_active == True
    ).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada")

    product = db.query(ProductDB).filter(
        ProductDB.id == product_id,
        ProductDB.is_active == True
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    if payload.price_per_unit is not None and payload.price_per_unit < 0:
        raise HTTPException(status_code=422, detail="El precio no puede ser negativo")

    config = db.query(BranchProductDB).filter(
        BranchProductDB.branch_id == branch_id,
        BranchProductDB.product_id == product_id
    ).first()

    if not config:
        config = BranchProductDB(
            branch_id=branch_id,
            product_id=product_id
        )
        db.add(config)

    config.price_per_unit = (
        payload.price_per_unit
        if payload.price_per_unit is not None
        else product.price_per_unit
    )
    config.is_available = payload.is_available
    config.is_exclusive = payload.is_exclusive

    db.commit()
    db.refresh(config)

    return {
        "status": "success",
        "branch_id": branch_id,
        "product_id": product_id,
        "price_per_unit": config.price_per_unit,
        "is_available": config.is_available,
        "is_exclusive": config.is_exclusive,
        "stock": get_branch_stock(db, product_id, branch_id)
    }


@app.delete("/branches/{branch_id}/products/{product_id}")
def remove_product_from_branch(
    branch_id: int,
    product_id: int,
    db: Session = Depends(get_db)
):
    config = db.query(BranchProductDB).filter(
        BranchProductDB.branch_id == branch_id,
        BranchProductDB.product_id == product_id
    ).first()

    if not config:
        raise HTTPException(
            status_code=404,
            detail="Producto no configurado en esta sucursal"
        )

    # No se elimina el producto maestro ni su historial.
    config.is_available = False

    db.commit()

    return {
        "status": "success",
        "branch_id": branch_id,
        "product_id": product_id,
        "is_available": False
    }


@app.post("/products/master")
def create_product_master(
    prod: ProductMasterSchema,
    branch_id: int = 1,
    db: Session = Depends(get_db)
):
    barcode = normalize_barcode(prod.barcode)

    if barcode and db.query(ProductDB).filter(
        ProductDB.barcode == barcode
    ).first():
        raise HTTPException(status_code=409, detail="Ese EAN ya existe")

    if db.query(ProductDB).filter(
        func.lower(ProductDB.name) == prod.name.strip().lower()
    ).first():
        raise HTTPException(
            status_code=409,
            detail="Ya existe un producto con ese nombre"
        )

    branch = db.query(BranchDB).filter(
        BranchDB.id == branch_id,
        BranchDB.is_active == True
    ).first()

    if not branch:
        raise HTTPException(
            status_code=404,
            detail="Sucursal no encontrada"
        )

    x = ProductDB(
        name=prod.name.strip(),
        category=prod.category or "Varios",
        brand=prod.brand,
        barcode=barcode,
        price_per_unit=prod.price_per_unit,
        unit_type=prod.unit_type or "unid",
        stock=0,
        cost_price=0,
        previous_cost_price=0,
        requires_expiration=prod.requires_expiration,
        replenishment_policy=prod.replenishment_policy or "MRP",
        is_active=prod.is_active
    )

    db.add(x)
    db.flush()

    config = BranchProductDB(
        branch_id=branch_id,
        product_id=x.id,
        price_per_unit=prod.price_per_unit,
        is_available=True,
        is_exclusive=(branch_id != 1)
    )

    db.add(config)

    # Un producto creado desde Feria nace exclusivo de Feria.
    # Dejamos una configuración explícita deshabilitada en Fiambrería
    # para impedir que la compatibilidad histórica lo habilite sola.
    if branch_id != 1:
        main_config = db.query(BranchProductDB).filter(
            BranchProductDB.branch_id == 1,
            BranchProductDB.product_id == x.id
        ).first()

        if not main_config:
            db.add(BranchProductDB(
                branch_id=1,
                product_id=x.id,
                price_per_unit=prod.price_per_unit,
                is_available=False,
                is_exclusive=False
            ))

    db.commit()
    db.refresh(x)

    return x

@app.post("/ingresses")
def create_ingress(payload: IngressCreateSchema, branch_id: int = 1, db: Session = Depends(get_db)):
    product=db.query(ProductDB).filter(ProductDB.id==payload.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    branch = db.query(BranchDB).filter(
        BranchDB.id == branch_id,
        BranchDB.is_active == True
    ).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada")

    config = db.query(BranchProductDB).filter(
        BranchProductDB.branch_id == branch_id,
        BranchProductDB.product_id == product.id
    ).first()

    if not config:
        config = BranchProductDB(
            branch_id=branch_id,
            product_id=product.id,
            price_per_unit=product.price_per_unit,
            is_available=True,
            is_exclusive=False
        )
        db.add(config)
        db.flush()
    elif not config.is_available:
        config.is_available = True

    ing,lot=register_ingress(product,payload.supplier,payload.cost_price,payload.quantity,payload.lot_number,payload.expiration_date,payload.received_by,payload.notes,db,branch_id)
    db.commit(); db.refresh(ing)
    return {"status":"success","ingress_id":ing.id,"lot_id":lot.id,"product_id":product.id,"stock":product.stock}

@app.get("/ingresses")
def get_ingresses(product_id: Optional[int]=None, branch_id: int = 1, db: Session=Depends(get_db)):
    q=db.query(ProductIngressDB).filter(ProductIngressDB.branch_id==branch_id)
    if product_id is not None: q=q.filter(ProductIngressDB.product_id==product_id)
    return q.order_by(ProductIngressDB.id.desc()).limit(200).all()

@app.patch("/ingresses/{ingress_id}")
def correct_ingress(
    ingress_id: int,
    payload: IngressCorrectionSchema,
    branch_id: int = 1,
    db: Session=Depends(get_db)
):
    if not payload.reason.strip():
        raise HTTPException(status_code=422,detail="El motivo de la correccion es obligatorio")

    ing=db.query(ProductIngressDB).filter(
        ProductIngressDB.id==ingress_id,
        ProductIngressDB.branch_id==branch_id
    ).first()
    if not ing: raise HTTPException(status_code=404,detail="Ingreso no encontrado")
    product=db.query(ProductDB).filter(ProductDB.id==ing.product_id).first()
    movement=db.query(StockMovementDB).filter(
        StockMovementDB.reference_type=="product_ingress",
        StockMovementDB.reference_id==ing.id,
        StockMovementDB.branch_id==branch_id
    ).order_by(StockMovementDB.id.asc()).first()

    lot=db.query(ProductLotDB).filter(
        ProductLotDB.id==movement.lot_id,
        ProductLotDB.branch_id==branch_id
    ).first() if movement and movement.lot_id else None
    if not product or not lot: raise HTTPException(status_code=409,detail="El ingreso no tiene lote trazable y no puede corregirse automaticamente")
    changes=[]
    def audit(field,old,new):
        if old!=new:
            changes.append((field,old,new))
            db.add(AuditEventDB(entity_type="product_ingress",entity_id=ing.id,field_name=field,old_value=str(old),new_value=str(new),actor=payload.actor,reason=payload.reason))
    if payload.quantity is not None:
        if payload.quantity<=0: raise HTTPException(status_code=422,detail="La cantidad debe ser mayor a cero")
        diff=payload.quantity-ing.quantity
        if lot.current_qty + diff < -0.000001:
            raise HTTPException(status_code=409, detail="No se puede reducir el ingreso: parte de ese lote ya fue consumido")
        audit("quantity",ing.quantity,payload.quantity)
        if abs(diff)>0.000001:
            lot.initial_qty+=diff; lot.current_qty+=diff; product.stock=(product.stock or 0)+diff
            db.add(StockMovementDB(branch_id=ing.branch_id,product_id=product.id,lot_id=lot.id,movement_type="ADJUSTMENT",quantity=diff,unit_cost=lot.cost_price or 0,reason=f"Correccion ingreso #{ing.id}",actor=payload.actor,reference_type="product_ingress_correction",reference_id=ing.id,notes=payload.reason))
            ing.quantity=payload.quantity
    if payload.cost_price is not None:
        if payload.cost_price<0: raise HTTPException(status_code=422,detail="El costo no puede ser negativo")
        audit("cost_price",ing.cost_price,payload.cost_price); ing.cost_price=payload.cost_price; lot.cost_price=payload.cost_price
        if movement: movement.unit_cost=payload.cost_price
        latest=db.query(ProductIngressDB).filter(
            ProductIngressDB.product_id==product.id,
            ProductIngressDB.branch_id==ing.branch_id
        ).order_by(ProductIngressDB.received_at.desc(),ProductIngressDB.id.desc()).first()
        if latest and latest.id==ing.id: product.previous_cost_price=product.cost_price or 0; product.cost_price=payload.cost_price
    if payload.supplier is not None: audit("supplier",ing.supplier,payload.supplier); ing.supplier=payload.supplier; lot.supplier=payload.supplier
    if payload.lot_number is not None: audit("lot_number",ing.lot_number,payload.lot_number); ing.lot_number=payload.lot_number; lot.lot_number=payload.lot_number
    if payload.expiration_date is not None:
        new_exp=parse_date_to_iso(payload.expiration_date)
        if product.requires_expiration and not new_exp: raise HTTPException(status_code=422,detail="Este producto requiere vencimiento")
        audit("expiration_date",ing.expiration_date,new_exp); ing.expiration_date=new_exp; lot.expiration_date=new_exp
    if not changes: return {"status":"no_changes","ingress_id":ing.id}
    db.commit()
    return {"status":"success","ingress_id":ing.id,"product_id":product.id,"lot_id":lot.id,"stock":product.stock,"changes":[{"field":f,"old":o,"new":n} for f,o,n in changes]}

@app.get("/stock/movements")
def get_stock_movements(product_id: Optional[int]=None, db: Session=Depends(get_db)):
    q=db.query(StockMovementDB)
    if product_id is not None: q=q.filter(StockMovementDB.product_id==product_id)
    return q.order_by(StockMovementDB.id.desc()).limit(500).all()

@app.post("/products")
def create_product(prod: ProductCreateSchema, db: Session = Depends(get_db)):
    barcode=normalize_barcode(prod.barcode)
    existing=db.query(ProductDB).filter(ProductDB.barcode==barcode).first() if barcode else None
    if not existing: existing=db.query(ProductDB).filter(func.lower(ProductDB.name)==prod.name.strip().lower()).first()
    if existing:
        if barcode and existing.barcode and existing.barcode!=barcode: raise HTTPException(status_code=409,detail="Nombre coincide con producto de otro EAN")
        if barcode and not existing.barcode:
            other=db.query(ProductDB).filter(ProductDB.barcode==barcode,ProductDB.id!=existing.id).first()
            if other: raise HTTPException(status_code=409,detail="EAN ya asociado")
            existing.barcode=barcode
        existing.name=prod.name.strip(); existing.category=prod.category or existing.category
        existing.brand=prod.brand if prod.brand is not None else existing.brand
        existing.price_per_unit=prod.price_per_unit; existing.unit_type=prod.unit_type or existing.unit_type
        if prod.requires_expiration is not None: existing.requires_expiration=prod.requires_expiration
        if prod.replenishment_policy is not None: existing.replenishment_policy=prod.replenishment_policy
        target=existing
    else:
        target=ProductDB(name=prod.name.strip(),category=prod.category or "Varios",brand=prod.brand,cost_price=0,previous_cost_price=0,price_per_unit=prod.price_per_unit,supplier=prod.supplier,unit_type=prod.unit_type or "unid",stock=0,barcode=barcode,requires_expiration=bool(prod.requires_expiration),replenishment_policy=prod.replenishment_policy or "MRP",is_active=True)
        db.add(target); db.flush()
    if prod.stock>0: register_ingress(target,prod.supplier,prod.cost_price or 0,prod.stock,prod.lot_number,prod.expiration_date,prod.received_by,prod.notes,db)
    db.commit(); db.refresh(target); return target

@app.put("/products/{product_id}")
def update_product(product_id:int, prod:ProductCreateSchema, db:Session=Depends(get_db)):
    x=db.query(ProductDB).filter(ProductDB.id==product_id).first()
    if not x: raise HTTPException(status_code=404,detail="Producto no encontrado")
    barcode=normalize_barcode(prod.barcode)
    if barcode and db.query(ProductDB).filter(ProductDB.barcode==barcode,ProductDB.id!=product_id).first():
        raise HTTPException(status_code=409,detail="EAN ya asociado a otro producto")
    old_price=x.price_per_unit
    changes={"name":(x.name,prod.name.strip()),"category":(x.category,prod.category),"brand":(x.brand,prod.brand),"price_per_unit":(x.price_per_unit,prod.price_per_unit),"supplier":(x.supplier,prod.supplier),"unit_type":(x.unit_type,prod.unit_type),"barcode":(x.barcode,barcode),"requires_expiration":(x.requires_expiration,prod.requires_expiration if prod.requires_expiration is not None else x.requires_expiration),"replenishment_policy":(x.replenishment_policy,prod.replenishment_policy if prod.replenishment_policy is not None else x.replenishment_policy)}
    x.name=prod.name.strip(); x.category=prod.category; x.brand=prod.brand; x.price_per_unit=prod.price_per_unit
    x.supplier=prod.supplier; x.unit_type=prod.unit_type; x.barcode=barcode
    if prod.requires_expiration is not None: x.requires_expiration=prod.requires_expiration
    if prod.replenishment_policy is not None: x.replenishment_policy=prod.replenishment_policy
    x.is_active=prod.is_active if prod.is_active is not None else x.is_active
    actor=prod.received_by or "Anonimo"
    for field,(old,new) in changes.items():
        if old!=new: db.add(AuditEventDB(entity_type="product",entity_id=x.id,field_name=field,old_value=str(old),new_value=str(new),actor=actor,reason=prod.notes or "Edicion de producto"))
    if old_price!=x.price_per_unit: db.add(PriceHistoryDB(product_id=x.id,old_price=old_price,new_price=x.price_per_unit,changed_by=actor,reason=prod.notes or "Cambio de PVP"))
    db.commit(); db.refresh(x); return x

@app.get("/products/{product_id}/lots")
def get_product_lots(product_id:int,branch_id: int = 1,db:Session=Depends(get_db)):
    lots=db.query(ProductLotDB).filter(ProductLotDB.product_id==product_id,ProductLotDB.branch_id==branch_id,ProductLotDB.current_qty>0).order_by(ProductLotDB.expiration_date.is_(None),ProductLotDB.expiration_date.asc(),ProductLotDB.created_at.asc()).all()
    return [{"id":l.id,"lot_number":l.lot_number,"supplier":l.supplier,"cost_price":l.cost_price,"initial_qty":l.initial_qty,"current_qty":l.current_qty,"expiration_date":format_iso_to_ddmmyyyy(l.expiration_date)} for l in lots]

@app.post("/products/{product_id}/audit")
def audit_product_stock(
    product_id: int,
    audit: AuditSchema,
    branch_id: int = 1,
    db: Session = Depends(get_db)
):
    x = db.query(ProductDB).filter(ProductDB.id == product_id).first()

    if not x:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    if audit.counted_qty < 0:
        raise HTTPException(status_code=422, detail="Conteo negativo")

    # El stock real se calcula exclusivamente con los lotes de esta sucursal.
    old = get_branch_stock(db, product_id, branch_id)
    diff = audit.counted_qty - old

    x.last_counted_qty = audit.counted_qty
    x.last_counted_by = audit.reported_by

    if diff > 0.000001:
        lot = ProductLotDB(
            branch_id=branch_id,
            product_id=x.id,
            lot_number=f"AJUSTE-{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            supplier=x.supplier,
            cost_price=x.cost_price or 0,
            initial_qty=diff,
            current_qty=diff,
            expiration_date=None
        )

        db.add(lot)
        db.flush()

        db.add(StockMovementDB(
            branch_id=branch_id,
            product_id=x.id,
            lot_id=lot.id,
            movement_type="ADJUSTMENT",
            quantity=diff,
            unit_cost=x.cost_price or 0,
            reason="Sobrante por conteo fisico",
            actor=audit.reported_by
        ))

    elif diff < -0.000001:
        consume_stock_fefo(
            x,
            -diff,
            "ADJUSTMENT",
            audit.reported_by,
            "Faltante por conteo fisico",
            db,
            branch_id=branch_id
        )

    # Crear siempre un registro InventoryCountDB, incluso si difference == 0
    count_record = InventoryCountDB(
        branch_id=branch_id,
        product_id=x.id,
        system_qty=old,
        counted_qty=audit.counted_qty,
        difference=round(diff, 6),
        reported_by=audit.reported_by
    )
    db.add(count_record)

    if abs(diff) > 0.000001:
        db.add(AuditEventDB(
            entity_type="product",
            entity_id=x.id,
            field_name=f"stock_branch_{branch_id}",
            old_value=str(old),
            new_value=str(audit.counted_qty),
            actor=audit.reported_by,
            reason="Conteo fisico"
        ))

    db.commit()
    db.refresh(count_record)

    branch_stock = get_branch_stock(db, product_id, branch_id)

    return {
        "status": "ok",
        "branch_id": branch_id,
        "system_qty": round(old, 6),
        "counted_qty": round(audit.counted_qty, 6),
        "difference": round(diff, 6),
        "stock": round(branch_stock, 6),
        "lots_stock": round(branch_stock, 6),
        "reported_by": count_record.reported_by,
        "counted_at": count_record.created_at.isoformat()
    }


@app.get("/products/{product_id}/audit/latest")
def get_latest_product_audit(
    product_id: int,
    branch_id: int = 1,
    db: Session = Depends(get_db)
):
    latest = db.query(InventoryCountDB).filter(
        InventoryCountDB.product_id == product_id,
        InventoryCountDB.branch_id == branch_id
    ).order_by(
        InventoryCountDB.created_at.desc(),
        InventoryCountDB.id.desc()
    ).first()

    if not latest:
        return {
            "has_count": False,
            "branch_id": branch_id,
            "product_id": product_id
        }

    return {
        "has_count": True,
        "branch_id": latest.branch_id,
        "product_id": latest.product_id,
        "system_qty": latest.system_qty,
        "counted_qty": latest.counted_qty,
        "difference": latest.difference,
        "reported_by": latest.reported_by,
        "created_at": latest.created_at.isoformat()
    }


@app.delete("/products/{product_id}")
def delete_product(product_id:int,db:Session=Depends(get_db)):
    x=db.query(ProductDB).filter(ProductDB.id==product_id).first()
    if not x: raise HTTPException(status_code=404,detail="No encontrado")
    x.is_active=False; db.commit(); return {"status":"ok"}

@app.post("/presales")
def create_presale(payload: PreSaleCreateSchema, branch_id: int = 1, db: Session = Depends(get_db)):
    total = 0.0
    presale = PreSaleDB(branch_id=branch_id, status="PENDIENTE", created_by=payload.created_by)
    db.add(presale); db.flush()

    for item in payload.items:
        prod = db.query(ProductDB).filter(ProductDB.id == item.product_id).first()
        if not prod:
            raise HTTPException(status_code=404, detail=f"Producto {item.product_id} no encontrado")

        config = db.query(BranchProductDB).filter(
            BranchProductDB.branch_id == branch_id,
            BranchProductDB.product_id == prod.id,
            BranchProductDB.is_available == True
        ).first()

        if not config:
            raise HTTPException(status_code=409, detail=f"{prod.name} no esta habilitado en esta sucursal")

        if get_branch_stock(db, prod.id, branch_id) < item.quantity:
            raise HTTPException(status_code=409, detail=f"Stock insuficiente: {prod.name}")

        unit_price = config.price_per_unit if config.price_per_unit is not None else prod.price_per_unit
        subtotal = unit_price * item.quantity
        total += subtotal

        db.add(PreSaleItemDB(
            presale_id=presale.id,
            product_id=prod.id,
            product_name=prod.name,
            price_per_unit=unit_price,
            unit_type=prod.unit_type,
            quantity=item.quantity
        ))

    presale.total_amount = total
    db.commit()
    return {"status": "success", "presale_id": presale.id, "total": total}


@app.get("/presales/pending")
def get_pending_presales(branch_id: int = 1, db: Session = Depends(get_db)):
    presales = db.query(PreSaleDB).filter(
        PreSaleDB.status == "PENDIENTE",
        PreSaleDB.branch_id == branch_id
    ).all()
    result = []
    for ps in presales:
        items = [{"product_id": i.product_id, "name": i.product_name, "price_per_unit": i.price_per_unit, "unit_type": i.unit_type, "qty": i.quantity} for i in ps.items]
        result.append({"id": ps.id, "created_at": utc_to_ar(ps.created_at).strftime("%H:%M"), "total": ps.total_amount, "items": items})
    return result

@app.delete("/presales/{presale_id}")
def delete_presale(
    presale_id: int,
    branch_id: int = 1,
    db: Session = Depends(get_db)
):
    ps = db.query(PreSaleDB).filter(
        PreSaleDB.id == presale_id,
        PreSaleDB.branch_id == branch_id
    ).first()

    if not ps:
        raise HTTPException(
            status_code=404,
            detail="Preventa no encontrada en esta sucursal"
        )

    ps.status = "CANCELADA"
    db.commit()
    return {"status": "ok"}

def consume_stock_fefo(product: ProductDB, quantity: float, movement_type: str, actor: str, reason: str, db: Session, reference_type: Optional[str]=None, reference_id: Optional[int]=None, preferred_lot_id: Optional[int]=None, branch_id: int = 1):
    if quantity <= 0:
        raise HTTPException(status_code=422, detail="La cantidad debe ser mayor a cero")
    branch_stock = get_branch_stock(db, product.id, branch_id)
    if branch_stock + 0.000001 < quantity:
        raise HTTPException(status_code=409, detail=f"Stock insuficiente de {product.name} en esta sucursal")
    remaining=quantity
    total_cost=0.0
    q=db.query(ProductLotDB).filter(ProductLotDB.product_id==product.id, ProductLotDB.branch_id==branch_id, ProductLotDB.current_qty>0)
    lots=q.order_by(ProductLotDB.expiration_date.is_(None), ProductLotDB.expiration_date.asc(), ProductLotDB.created_at.asc()).all()
    if preferred_lot_id is not None:
        lots=sorted(lots, key=lambda x: 0 if x.id==preferred_lot_id else 1)
    for lot in lots:
        if remaining <= 0:
            break
        take=min(lot.current_qty, remaining)
        lot.current_qty-=take
        remaining-=take
        uc=lot.cost_price or product.cost_price or 0.0
        total_cost+=take*uc
        db.add(StockMovementDB(branch_id=branch_id, product_id=product.id, lot_id=lot.id, movement_type=movement_type, quantity=-take, unit_cost=uc, reason=reason, actor=actor, reference_type=reference_type, reference_id=reference_id))
    if remaining > 0.000001:
        uc=product.cost_price or 0.0
        total_cost+=remaining*uc
        db.add(StockMovementDB(branch_id=branch_id, product_id=product.id, movement_type=movement_type, quantity=-remaining, unit_cost=uc, reason=reason, actor=actor, reference_type=reference_type, reference_id=reference_id))
    product.stock=(product.stock or 0)-quantity
    return total_cost


def get_branch_stock(db: Session, product_id: int, branch_id: int = 1) -> float:
    value = db.query(func.coalesce(func.sum(ProductLotDB.current_qty), 0.0)).filter(
        ProductLotDB.product_id == product_id,
        ProductLotDB.branch_id == branch_id
    ).scalar()
    return float(value or 0.0)


@app.get("/cash/status")
def get_cash_status(branch_id: int = 1, db: Session = Depends(get_db)):
    active_session = db.query(CashSessionDB).filter(CashSessionDB.is_open == True, CashSessionDB.branch_id == branch_id).first()
    if not active_session:
        return {"is_open": False}
    return {
        "is_open": True,
        "session_id": active_session.id,
        "opened_by": active_session.opened_by,
        "opened_at": utc_to_ar(active_session.opened_at).strftime("%H:%M hs"),
        "initial_amount": active_session.initial_amount
    }

@app.post("/cash/open")
def open_cash(payload: CashOpenSchema, branch_id: int = 1, db: Session = Depends(get_db)):
    active = db.query(CashSessionDB).filter(CashSessionDB.is_open == True, CashSessionDB.branch_id == branch_id).first()
    if active:
        raise HTTPException(status_code=400, detail="La caja ya se encuentra abierta")

    today_str = ar_now().date().strftime("%Y-%m-%d")
    session = CashSessionDB(branch_id=branch_id,
        date=today_str,
        opened_by=payload.opened_by,
        initial_amount=payload.initial_amount,
        is_open=True
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return {"status": "ok", "session_id": session.id}

@app.post("/sales/finalize")
def finalize_sale(payload: FinalizeSaleSchema, branch_id: int = 1, db: Session = Depends(get_db)):
    active=db.query(CashSessionDB).filter(CashSessionDB.is_open==True,CashSessionDB.branch_id==branch_id).first()

    presale = None
    if payload.presale_id:
        presale = db.query(PreSaleDB).filter(
            PreSaleDB.id == payload.presale_id,
            PreSaleDB.branch_id == branch_id,
            PreSaleDB.status == "PENDIENTE"
        ).first()

        if not presale:
            raise HTTPException(
                status_code=409,
                detail="La preventa no pertenece a esta sucursal o ya fue procesada"
            )

    sale=SaleDB(branch_id=branch_id,presale_id=payload.presale_id, session_id=active.id if active else None, total_amount=payload.total_amount, amount_cash=payload.amount_cash, amount_mp=payload.amount_mp, payment_method=payload.payment_method, sold_by=payload.sold_by)
    db.add(sale); db.flush()
    products={}
    for item in payload.items:
        x=db.query(ProductDB).filter(ProductDB.id==item.product_id).first()
        if not x:
            raise HTTPException(status_code=404, detail=f"Producto {item.product_id} no encontrado")
        if item.quantity<=0 or get_branch_stock(db,x.id,branch_id)<item.quantity:
            raise HTTPException(status_code=409, detail=f"Stock insuficiente o cantidad invalida: {x.name}")
        products[item.product_id]=x
    for item in payload.items:
        x=products[item.product_id]
        cost=consume_stock_fefo(x, item.quantity, "SALE", payload.sold_by or "Anonimo", "Venta", db, "sale", sale.id, branch_id=branch_id)
        branch_config=db.query(BranchProductDB).filter(
            BranchProductDB.branch_id==branch_id,
            BranchProductDB.product_id==x.id,
            BranchProductDB.is_available==True
        ).first()
        if not branch_config:
            raise HTTPException(status_code=409, detail=f"{x.name} no esta habilitado en esta sucursal")

        unit_price=branch_config.price_per_unit if branch_config.price_per_unit is not None else (x.price_per_unit or 0)

        if presale:
            psi=db.query(PreSaleItemDB).filter(
                PreSaleItemDB.presale_id==presale.id,
                PreSaleItemDB.product_id==x.id
            ).first()
            if psi:
                unit_price=psi.price_per_unit or unit_price
        revenue=unit_price*item.quantity
        db.add(SaleItemDB(sale_id=sale.id, product_id=x.id, product_name=x.name, quantity=item.quantity, unit_price=unit_price, unit_cost=cost/item.quantity, cogs=cost, gross_profit=revenue-cost))
    if presale:
        presale.status = "COMPLETADA"
    db.commit()
    return {"status":"success","message":"Venta procesada","sale_id":sale.id}

@app.post("/cash/movements")
def create_cash_movement(payload: CashMovementCreateSchema, branch_id: int = 1, db: Session = Depends(get_db)):
    if payload.amount<=0:
        raise HTTPException(status_code=422, detail="Monto invalido")
    movement=payload.movement_type.upper()
    if movement not in ("IN","OUT"):
        raise HTTPException(status_code=422, detail="movement_type debe ser IN u OUT")
    active=db.query(CashSessionDB).filter(CashSessionDB.is_open==True,CashSessionDB.branch_id==branch_id).first()
    row=CashMovementDB(branch_id=branch_id, session_id=active.id if active else None, movement_type=movement, category=payload.category.upper(), amount=payload.amount, concept=payload.concept, actor=payload.actor, supplier=payload.supplier, employee_email=payload.employee_email, notes=payload.notes)
    db.add(row); db.commit(); db.refresh(row)
    return row

@app.get("/cash/movements")
def list_cash_movements(session_id: Optional[int]=None, branch_id: int = 1, db: Session=Depends(get_db)):
    q=db.query(CashMovementDB).filter(CashMovementDB.branch_id == branch_id)
    if session_id is not None:
        q=q.filter(CashMovementDB.session_id==session_id)
    return q.order_by(CashMovementDB.id.desc()).limit(500).all()

@app.post("/stock/losses")
def create_stock_loss(payload: StockLossSchema, branch_id: int = 1, db: Session=Depends(get_db)):
    allowed={"VENCIMIENTO","ROTURA","DIFERENCIA_INVENTARIO","CONSUMO_INTERNO","MERMA_CORTE","OTRO"}
    reason=payload.reason.upper().strip()
    if reason not in allowed:
        raise HTTPException(status_code=422, detail="Motivo de merma invalido")
    x=db.query(ProductDB).filter(ProductDB.id==payload.product_id).first()
    if not x:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    cost=consume_stock_fefo(x, payload.quantity, "LOSS", payload.actor or "Anonimo", reason, db, "stock_loss", None, payload.lot_id, branch_id=branch_id)
    db.add(AuditEventDB(entity_type="stock_loss", entity_id=x.id, field_name="stock", old_value=None, new_value=str(-payload.quantity), actor=payload.actor, reason=reason))
    db.commit()
    return {"status":"success","product_id":x.id,"quantity":payload.quantity,"economic_loss":round(cost,2),"reason":reason,"stock":x.stock}

@app.post("/cash/close")
def close_cash(payload: CashCloseSchema, branch_id: int = 1, db: Session = Depends(get_db)):
    session = db.query(CashSessionDB).filter(CashSessionDB.is_open == True, CashSessionDB.branch_id == branch_id).first()
    if not session:
        raise HTTPException(status_code=400, detail="No hay una caja abierta para cerrar")

    sales = db.query(SaleDB).filter(SaleDB.session_id == session.id).all()
    total_cash_sales = sum(s.amount_cash for s in sales)
    total_mp_sales = sum(s.amount_mp for s in sales)

    movements=db.query(CashMovementDB).filter(CashMovementDB.session_id==session.id).all()
    cash_in=sum(m.amount for m in movements if m.movement_type=="IN")
    cash_out=sum(m.amount for m in movements if m.movement_type=="OUT")
    expected_cash = session.initial_amount + total_cash_sales + cash_in - cash_out
    diff = payload.reported_cash - expected_cash

    if payload.attempt_number == 1 and abs(diff) > 0.01:
        return {
            "status": "mismatch_first_attempt",
            "message": "⚠️ Monto incorrecto. Por favor volvé a contar el dinero e ingresá la cifra nuevamente."
        }

    session.is_open = False
    session.closed_at = datetime.datetime.utcnow()
    session.closed_by = payload.closed_by
    session.reported_cash = payload.reported_cash
    session.expected_cash = expected_cash
    session.total_mp = total_mp_sales
    session.difference = diff

    if abs(diff) <= 0.01:
        session.status_message = "Objetivo logrado satisfactoriamente"
    else:
        session.status_message = f"Cerrado con diferencia de ${diff:.2f}"

    db.commit()
    return {
        "status": "success",
        "message": session.status_message,
        "is_correct": abs(diff) <= 0.01
    }

@app.get("/cash/audits")
def get_cash_audits(
    date: Optional[str] = None,
    branch_id: int = 1,
    db: Session = Depends(get_db)
):
    query = db.query(CashSessionDB).filter(
        CashSessionDB.branch_id == branch_id
    )
    if date:
        query = query.filter(CashSessionDB.date == date)

    sessions = query.order_by(CashSessionDB.id.desc()).all()
    result = []
    for s in sessions:
        sales = db.query(SaleDB).filter(SaleDB.session_id == s.id).all()
        result.append({
            "id": s.id,
            "date": s.date,
            "opened_at": utc_to_ar(s.opened_at).strftime("%H:%M hs"),
            "opened_by": s.opened_by,
            "initial_amount": s.initial_amount,
            "is_open": s.is_open,
            "closed_at": utc_to_ar(s.closed_at).strftime("%H:%M hs") if s.closed_at else "En curso",
            "closed_by": s.closed_by or "N/A",
            "reported_cash": s.reported_cash,
            "expected_cash": s.expected_cash,
            "total_mp": s.total_mp,
            "difference": s.difference,
            "status_message": s.status_message,
            "total_sales_count": len(sales)
        })
    return result

@app.patch("/products/{product_id}/replenishment-policy")
def update_replenishment_policy(product_id: int, payload: ReplenishmentPolicySchema, db: Session = Depends(get_db)):
    allowed={"MRP","MANUAL","PAUSED","DISCONTINUED"}
    policy=payload.policy.upper().strip()
    if policy not in allowed:
        raise HTTPException(status_code=422, detail="Politica invalida: MRP, MANUAL, PAUSED o DISCONTINUED")
    product=db.query(ProductDB).filter(ProductDB.id==product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    old=product.replenishment_policy or "MRP"
    product.replenishment_policy=policy
    if policy=="DISCONTINUED":
        product.is_active=False
    db.add(AuditEventDB(entity_type="product",entity_id=product.id,field_name="replenishment_policy",old_value=old,new_value=policy,actor=payload.actor,reason=payload.reason or "Cambio de politica de reposicion"))
    db.commit()
    return {"status":"success","product_id":product.id,"policy":policy}

@app.get("/mrp/suggestions")
def get_mrp_suggestions(
    days: int = 7,
    target_days: int = 3,
    branch_id: int = 1,
    db: Session = Depends(get_db)
):
    since = datetime.datetime.utcnow() - datetime.timedelta(days=days)

    sales = db.query(SaleDB).filter(
        SaleDB.created_at >= since,
        SaleDB.branch_id == branch_id
    ).all()

    sales_by_prod = {}
    for sale in sales:
        for item in sale.items:
            sales_by_prod[item.product_id] = (
                sales_by_prod.get(item.product_id, 0.0) + item.quantity
            )

    suggestions = []

    products = db.query(ProductDB).filter(
        ProductDB.is_active == True
    ).all()

    for product in products:
        config = db.query(BranchProductDB).filter(
            BranchProductDB.branch_id == branch_id,
            BranchProductDB.product_id == product.id,
            BranchProductDB.is_available == True
        ).first()

        if not config:
            continue

        policy = (product.replenishment_policy or "MRP").upper()
        if policy != "MRP":
            continue

        total_sold = sales_by_prod.get(product.id, 0.0)
        if total_sold <= 0:
            continue

        current_stock = get_branch_stock(db, product.id, branch_id)
        daily = total_sold / max(1, days)
        target = daily * target_days
        buy = max(0.0, target - current_stock)

        status = "OK"
        if current_stock <= 0:
            status = "AGOTADO"
        elif current_stock < daily:
            status = "CRITICO"
        elif buy > 0:
            status = "REPOSICION_RECOMENDADA"

        suggestions.append({
            "product_id": product.id,
            "product_name": product.name,
            "supplier": product.supplier or "Sin Proveedor",
            "current_stock": round(current_stock, 2),
            "unit_type": product.unit_type,
            "total_sold_period": round(total_sold, 2),
            "daily_demand": round(daily, 2),
            "target_days": target_days,
            "suggested_buy": round(buy, 2),
            "estimated_cost": round(
                buy * (product.cost_price or 0), 2
            ),
            "status": status,
            "replenishment_policy": policy
        })

    return sorted(
        suggestions,
        key=lambda x: x["suggested_buy"],
        reverse=True
    )

def alert_is_visible(alert_key: str, db: Session):
    state=db.query(AlertStateDB).filter(AlertStateDB.alert_key==alert_key).first()
    if not state:
        return True,"NEW"
    status=(state.status or "NEW").upper()
    if status in ("RESOLVED","DISMISSED"):
        return False,status
    if status=="SNOOZED" and state.snoozed_until and state.snoozed_until>datetime.datetime.utcnow():
        return False,status
    if status=="SNOOZED" and (not state.snoozed_until or state.snoozed_until<=datetime.datetime.utcnow()):
        state.status="NEW"
        db.commit()
        return True,"NEW"
    return True,status

def append_alert(alerts, key, alert_type, level, title, detail, db, product_id=None, lot_id=None, actions=None):
    visible,state=alert_is_visible(key,db)
    if visible:
        alerts.append({"id":key,"type":alert_type,"level":level,"severity":level,"title":title,"detail":detail,"state":state,"product_id":product_id,"lot_id":lot_id,"actions":actions or ["SEEN","SNOOZED","RESOLVED","DISMISSED"]})

@app.post("/alerts/{alert_key}/action")
def update_alert_state(alert_key: str, payload: AlertActionSchema, db: Session = Depends(get_db)):
    action=payload.action.upper().strip()
    mapping={"VIEW":"SEEN","SEEN":"SEEN","POSTPONE":"SNOOZED","SNOOZE":"SNOOZED","SNOOZED":"SNOOZED","RESOLVE":"RESOLVED","RESOLVED":"RESOLVED","DISMISS":"DISMISSED","DISMISSED":"DISMISSED","NEW":"NEW"}
    if action not in mapping:
        raise HTTPException(status_code=422,detail="Accion de alerta invalida")
    status=mapping[action]
    state=db.query(AlertStateDB).filter(AlertStateDB.alert_key==alert_key).first()
    if not state:
        state=AlertStateDB(alert_key=alert_key)
        db.add(state); db.flush()
    old_status=state.status or "NEW"
    state.status=status
    state.updated_at=datetime.datetime.utcnow()
    state.resolved_by=payload.actor
    state.resolution_note=payload.note
    state.snoozed_until=(datetime.datetime.utcnow()+datetime.timedelta(hours=max(1,payload.snooze_hours or 24))) if status=="SNOOZED" else None
    db.add(AuditEventDB(entity_type="alert",entity_id=state.id,field_name="status",old_value=old_status,new_value=status,actor=payload.actor,reason=payload.note or f"Alerta {status}"))
    db.commit(); db.refresh(state)
    return {"status":"success","alert_key":alert_key,"state":state.status,"snoozed_until":state.snoozed_until}

@app.get("/alerts/history")
def get_alert_history(limit: int = 200, db: Session = Depends(get_db)):
    rows=db.query(AuditEventDB).filter(AuditEventDB.entity_type=="alert").order_by(AuditEventDB.created_at.desc()).limit(min(max(limit,1),500)).all()
    return [{"id":x.id,"alert_state_id":x.entity_id,"old_state":x.old_value,"new_state":x.new_value,"actor":x.actor,"note":x.reason,"created_at":x.created_at} for x in rows]

@app.get("/alerts")
def get_system_alerts(branch_id: int = 1, db: Session = Depends(get_db)):
    alerts=[]
    today=datetime.date.today()
    active_lots=db.query(ProductLotDB).join(ProductDB).filter(
        ProductDB.is_active==True,
        ProductLotDB.branch_id==branch_id,
        ProductLotDB.current_qty>0,
        ProductLotDB.expiration_date!=None
    ).all()
    for lot in active_lots:
        try:
            exp=datetime.datetime.strptime(lot.expiration_date,"%Y-%m-%d").date()
            days=(exp-today).days
            display=format_iso_to_ddmmyyyy(lot.expiration_date)
            if days<0:
                key=f"exp-{lot.id}-{lot.expiration_date}"
                append_alert(alerts,key,"EXPIRATION","CRITICAL",f"Lote vencido: {lot.product.name}",f"Quedan {lot.current_qty} {lot.product.unit_type}. Vencio {display}.",db,lot.product_id,lot.id,["SEEN","SNOOZED","DISMISSED"])
            elif days<=30:
                level="IMPORTANT" if days<=7 else "INFO"
                key=f"exp-{lot.id}-{lot.expiration_date}"
                append_alert(alerts,key,"EXPIRATION",level,f"Vencimiento proximo ({days} dias): {lot.product.name}",f"Lote {lot.current_qty} {lot.product.unit_type}; vence {display}.",db,lot.product_id,lot.id,["SEEN","SNOOZED","DISMISSED"])
        except (ValueError,TypeError):
            continue

    products=db.query(ProductDB).filter(ProductDB.is_active==True).all()
    for product in products:
        config=db.query(BranchProductDB).filter(
            BranchProductDB.branch_id==branch_id,
            BranchProductDB.product_id==product.id,
            BranchProductDB.is_available==True
        ).first()

        if not config:
            continue

        branch_stock=get_branch_stock(db,product.id,branch_id)
        policy=(product.replenishment_policy or "MRP").upper()

        if policy=="MRP":
            if branch_stock<=0:
                append_alert(alerts,f"stock-b{branch_id}-{product.id}-zero","STOCK","CRITICAL",f"Producto agotado: {product.name}",f"Stock 0 {product.unit_type}.",db,product.id)
            elif branch_stock<=5:
                append_alert(alerts,f"stock-b{branch_id}-{product.id}-low-{round(branch_stock,3)}","STOCK","IMPORTANT",f"Stock bajo: {product.name}",f"Quedan {branch_stock} {product.unit_type}.",db,product.id)

        branch_price=config.price_per_unit if config.price_per_unit is not None else product.price_per_unit
        if (product.cost_price or 0)>0 and branch_price<=product.cost_price:
            signature=f"b{branch_id}-{round(product.cost_price,2)}-{round(branch_price,2)}"
            append_alert(alerts,f"price-{product.id}-{signature}","PRICE","CRITICAL",f"PVP debajo del costo: {product.name}",f"Costo {product.cost_price}; PVP {branch_price}.",db,product.id,actions=["SEEN","SNOOZED","DISMISSED"])

    order={"CRITICAL":0,"IMPORTANT":1,"INFO":2}
    return sorted(alerts,key=lambda x:order.get(x["level"],9))
