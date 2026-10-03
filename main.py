from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, ForeignKey, DateTime, func, Text, inspect, text
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
    role = Column(String, default="vendedor")  # 'superadmin', 'dueno', 'vendedor'
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

class WorkLogDB(Base):
    __tablename__ = "work_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    user_name = Column(String)
    user_email = Column(String)
    clock_in = Column(DateTime, default=datetime.datetime.utcnow)
    clock_out = Column(DateTime, nullable=True)
    hours_worked = Column(Float, default=0.0)

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
    lots = relationship("ProductLotDB", back_populates="product", cascade="all, delete-orphan")

class ProductLotDB(Base):
    __tablename__ = "product_lots"
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
    sale = relationship("SaleDB", back_populates="items")

class CashSessionDB(Base):
    __tablename__ = "cash_sessions"
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

ensure_v2_schema()


app = FastAPI(title="Fiambrería POS, RRHH, MRP, KPIs & Permisos API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def parse_date_to_iso(date_str: str) -> Optional[str]:
    if not date_str:
        return None
    clean_str = date_str.strip().replace('/', '-')
    parts = clean_str.split('-')
    if len(parts) == 3:
        if len(parts[0]) == 2 and len(parts[2]) == 4:
            return f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
        elif len(parts[0]) == 4 and len(parts[2]) == 2:
            return f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"
    return clean_str

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
    stock: float
    barcode: Optional[str] = None
    is_active: Optional[bool] = True
    expiration_date: Optional[str] = None
    lot_number: Optional[str] = None

class CashOpenSchema(BaseModel):
    initial_amount: float
    opened_by: str

class CashCloseSchema(BaseModel):
    reported_cash: float
    closed_by: str
    attempt_number: int

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
        "can_ingreso": user.can_ingreso,
        "can_alertas": user.can_alertas,
        "can_rrhh": user.can_rrhh,
        "can_mrp": user.can_mrp,
        "can_verificacion": user.can_verificacion,
        "can_kpis": user.can_kpis,
        "can_edit_records": user.can_edit_records
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
        can_edit_records=(role in ["superadmin", "dueno"])
    )
    db.add(new_u); db.commit(); db.refresh(new_u)
    return new_u

@app.patch("/users/{user_id}/permissions")
def update_permissions(user_id: int, p: PermissionsSchema, db: Session = Depends(get_db)):
    u = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    if p.role is not None:
        u.role = p.role
        if p.role == "superadmin":
            u.can_edit_records = True

    if p.is_active is not None and p.is_active != u.is_active:
        u.is_active = p.is_active
        now = datetime.datetime.utcnow()
        if p.is_active:
            db.add(WorkLogDB(user_id=u.id, user_name=u.name, user_email=u.email, clock_in=now))
        else:
            log = db.query(WorkLogDB).filter(WorkLogDB.user_id == u.id, WorkLogDB.clock_out == None).order_by(WorkLogDB.id.desc()).first()
            if log:
                log.clock_out = now
                diff_seconds = (now - log.clock_in).total_seconds()
                log.hours_worked = round(diff_seconds / 3600.0, 2)

    if p.can_preventa is not None: u.can_preventa = p.can_preventa
    if p.can_caja is not None: u.can_caja = p.can_caja
    if p.can_stock is not None: u.can_stock = p.can_stock
    if p.can_ingreso is not None: u.can_ingreso = p.can_ingreso
    if p.can_alertas is not None: u.can_alertas = p.can_alertas
    if p.can_rrhh is not None: u.can_rrhh = p.can_rrhh
    if p.can_mrp is not None: u.can_mrp = p.can_mrp
    if p.can_verificacion is not None: u.can_verificacion = p.can_verificacion
    if p.can_kpis is not None: u.can_kpis = p.can_kpis
    if p.can_edit_records is not None: u.can_edit_records = p.can_edit_records
    
    db.commit(); db.refresh(u)
    return u

@app.get("/kpis/dashboard")
def get_kpis_dashboard(db: Session = Depends(get_db)):
    now = datetime.datetime.utcnow()
    month_start = datetime.datetime(now.year, now.month, 1)
    
    first_this_month = month_start
    last_month_last_day = first_this_month - datetime.timedelta(days=1)
    prev_month_start = datetime.datetime(last_month_last_day.year, last_month_last_day.month, 1)

    sales_this_month = db.query(SaleDB).filter(SaleDB.created_at >= month_start).all()
    sales_prev_month = db.query(SaleDB).filter(SaleDB.created_at >= prev_month_start, SaleDB.created_at < month_start).all()

    rev_this_month = sum(s.total_amount for s in sales_this_month)
    rev_prev_month = sum(s.total_amount for s in sales_prev_month)

    if rev_prev_month > 0:
        pct_growth = round(((rev_this_month - rev_prev_month) / rev_prev_month) * 100.0, 1)
    else:
        pct_growth = 100.0 if rev_this_month > 0 else 0.0

    thirty_days_ago = now - datetime.timedelta(days=30)
    top_products_query = db.query(
        SaleItemDB.product_name,
        func.sum(SaleItemDB.quantity).label("total_qty")
    ).join(SaleDB).filter(SaleDB.created_at >= thirty_days_ago).group_by(SaleItemDB.product_name).order_by(func.sum(SaleItemDB.quantity).desc()).limit(10).all()

    top_products = [{"name": row[0] or "Producto", "total_qty": round(row[1], 2)} for row in top_products_query]

    top_staff_query = db.query(
        WorkLogDB.user_name,
        func.count(WorkLogDB.id).label("shifts_count"),
        func.sum(WorkLogDB.hours_worked).label("total_hours")
    ).filter(WorkLogDB.clock_in >= thirty_days_ago).group_by(WorkLogDB.user_name).order_by(func.sum(WorkLogDB.hours_worked).desc()).limit(10).all()

    top_staff = [{"name": row[0], "shifts": row[1], "hours": round(row[2] or 0.0, 1)} for row in top_staff_query]

    alerts_endpoint_data = get_system_alerts(db)
    critical_alerts_count = sum(1 for a in alerts_endpoint_data if a["level"] == "CRITICAL")
    warning_alerts_count = sum(1 for a in alerts_endpoint_data if a["level"] in ["WARNING", "HIGH"])

    if pct_growth >= 0 and critical_alerts_count == 0:
        health_status = "EXCELENTE"
        summary_text = f"🟢 ¡Vamos bien! Las ventas crecieron un {pct_growth}% respecto al mes anterior y no tenés alertas críticas pendientes."
    elif pct_growth >= 0 and critical_alerts_count > 0:
        health_status = "ATENCION"
        summary_text = f"⚠️ Las ventas crecieron (+{pct_growth}%), pero hay {critical_alerts_count} alerta(s) crítica(s) por revisar (revisar sección Alertas)."
    else:
        health_status = "ALERTA"
        summary_text = f"🔴 Las ventas cayeron un {abs(pct_growth)}% respecto al mes anterior y hay {critical_alerts_count} alerta(s) crítica(s)."

    return {
        "health_status": health_status,
        "summary_text": summary_text,
        "revenue_this_month": round(rev_this_month, 2),
        "revenue_prev_month": round(rev_prev_month, 2),
        "pct_growth": pct_growth,
        "critical_alerts_count": critical_alerts_count,
        "warning_alerts_count": warning_alerts_count,
        "top_products": top_products,
        "top_staff": top_staff
    }

@app.get("/hr/worklogs")
def get_worklogs(db: Session = Depends(get_db)):
    logs = db.query(WorkLogDB).order_by(WorkLogDB.id.desc()).all()
    result = []
    for l in logs:
        result.append({
            "id": l.id,
            "user_name": l.user_name,
            "user_email": l.user_email,
            "date": l.clock_in.strftime("%Y-%m-%d"),
            "clock_in": l.clock_in.strftime("%H:%M hs"),
            "clock_out": l.clock_out.strftime("%H:%M hs") if l.clock_out else "En jornada",
            "hours_worked": l.hours_worked
        })
    return result

def get_employee_stats(email: str, days: int, db: Session):
    since_date = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    sales = db.query(SaleDB).filter(SaleDB.sold_by == email, SaleDB.created_at >= since_date).all()
    
    total_revenue = sum(s.total_amount for s in sales)
    sales_count = len(sales)
    ticket_avg = round(total_revenue / max(1, sales_count), 2)
    
    cash_sessions = db.query(CashSessionDB).filter(CashSessionDB.closed_by == email, CashSessionDB.closed_at >= since_date).all()
    cash_shifts_count = len(cash_sessions)
    total_cash_diff = round(sum(cs.difference or 0.0 for cs in cash_sessions), 2)

    work_logs = db.query(WorkLogDB).filter(WorkLogDB.user_email == email, WorkLogDB.clock_in >= since_date).all()
    total_hours = round(sum(w.hours_worked for w in work_logs), 2)

    return {
        "email": email,
        "sales_count": sales_count,
        "total_revenue": round(total_revenue, 2),
        "ticket_avg": ticket_avg,
        "cash_shifts_count": cash_shifts_count,
        "total_cash_diff": total_cash_diff,
        "total_hours": total_hours
    }

@app.get("/hr/performance")
def get_performance(email: str, days: int = 30, db: Session = Depends(get_db)):
    return get_employee_stats(email, days, db)

@app.get("/hr/compare")
def compare_employees(email1: str, email2: str, days: int = 30, db: Session = Depends(get_db)):
    emp1 = db.query(UserDB).filter(UserDB.email == email1).first()
    emp2 = db.query(UserDB).filter(UserDB.email == email2).first()

    stats1 = get_employee_stats(email1, days, db)
    stats2 = get_employee_stats(email2, days, db)

    stats1["name"] = emp1.name if emp1 else email1
    stats2["name"] = emp2.name if emp2 else email2

    diagnosis = []
    if stats1["total_revenue"] > stats2["total_revenue"]:
        diff = round(stats1["total_revenue"] - stats2["total_revenue"], 2)
        diagnosis.append(f"• {stats1['name']} generó ${diff} más en ventas que {stats2['name']}.")
    elif stats2["total_revenue"] > stats1["total_revenue"]:
        diff = round(stats2["total_revenue"] - stats1["total_revenue"], 2)
        diagnosis.append(f"• {stats2['name']} generó ${diff} más en ventas que {stats1['name']}.")
    else:
        diagnosis.append("• Ambos registraron igual nivel de facturación.")

    return {
        "days": days,
        "emp1": stats1,
        "emp2": stats2,
        "diagnosis": diagnosis
    }

@app.get("/products")
def get_products(db: Session = Depends(get_db)):
    return db.query(ProductDB).all()

@app.post("/products")
def create_product(prod: ProductCreateSchema, db: Session = Depends(get_db)):
    existing = None
    if prod.barcode:
        existing = db.query(ProductDB).filter(ProductDB.barcode == prod.barcode).first()
    if not existing:
        existing = db.query(ProductDB).filter(ProductDB.name == prod.name).first()

    iso_expiration = parse_date_to_iso(prod.expiration_date)

    if existing:
        existing.previous_cost_price = existing.cost_price or 0.0
        existing.stock += prod.stock
        existing.cost_price = prod.cost_price or existing.cost_price
        existing.supplier = prod.supplier or existing.supplier
        existing.price_per_unit = prod.price_per_unit or existing.price_per_unit
        existing.is_active = True
        p_target = existing
    else:
        p_target = ProductDB(
            name=prod.name,
            category=prod.category,
            cost_price=prod.cost_price or 0.0,
            previous_cost_price=prod.cost_price or 0.0,
            price_per_unit=prod.price_per_unit,
            supplier=prod.supplier,
            unit_type=prod.unit_type,
            stock=prod.stock,
            barcode=prod.barcode,
            is_active=True
        )
        db.add(p_target); db.flush()

    if iso_expiration:
        lot = ProductLotDB(
            product_id=p_target.id,
            lot_number=prod.lot_number or f"LOTE-{datetime.date.today().strftime('%Y%m%d')}",
            supplier=prod.supplier,
            cost_price=prod.cost_price or 0.0,
            initial_qty=prod.stock,
            current_qty=prod.stock,
            expiration_date=iso_expiration
        )
        db.add(lot)

    db.commit(); db.refresh(p_target)
    return p_target

@app.put("/products/{product_id}")
def update_product(product_id: int, prod: ProductCreateSchema, db: Session = Depends(get_db)):
    p = db.query(ProductDB).filter(ProductDB.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    
    if prod.cost_price and prod.cost_price != p.cost_price:
        p.previous_cost_price = p.cost_price
        p.cost_price = prod.cost_price

    p.name = prod.name
    p.category = prod.category
    p.price_per_unit = prod.price_per_unit
    p.supplier = prod.supplier
    p.unit_type = prod.unit_type
    p.stock = prod.stock
    p.barcode = prod.barcode
    p.is_active = prod.is_active if prod.is_active is not None else p.is_active
    
    db.commit(); db.refresh(p)
    return p

@app.get("/products/{product_id}/lots")
def get_product_lots(product_id: int, db: Session = Depends(get_db)):
    lots = db.query(ProductLotDB).filter(ProductLotDB.product_id == product_id, ProductLotDB.current_qty > 0).order_by(ProductLotDB.expiration_date.asc()).all()
    res = []
    for l in lots:
        res.append({
            "id": l.id,
            "lot_number": l.lot_number,
            "supplier": l.supplier,
            "current_qty": l.current_qty,
            "expiration_date": format_iso_to_ddmmyyyy(l.expiration_date)
        })
    return res

@app.post("/products/{product_id}/audit")
def audit_product_stock(product_id: int, audit: AuditSchema, db: Session = Depends(get_db)):
    p = db.query(ProductDB).filter(ProductDB.id == product_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    p.last_counted_qty = audit.counted_qty
    p.last_counted_by = audit.reported_by
    db.commit()
    return {"status": "ok", "message": "Conteo físico guardado"}

@app.delete("/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    p = db.query(ProductDB).filter(ProductDB.id == product_id).first()
    if p: p.is_active = False; db.commit(); return {"status": "ok"}
    raise HTTPException(status_code=404, detail="No encontrado")

@app.post("/presales")
def create_presale(payload: PreSaleCreateSchema, db: Session = Depends(get_db)):
    total = 0.0
    presale = PreSaleDB(status="PENDIENTE", created_by=payload.created_by)
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

@app.get("/cash/status")
def get_cash_status(db: Session = Depends(get_db)):
    active_session = db.query(CashSessionDB).filter(CashSessionDB.is_open == True).first()
    if not active_session:
        return {"is_open": False}
    return {
        "is_open": True,
        "session_id": active_session.id,
        "opened_by": active_session.opened_by,
        "opened_at": active_session.opened_at.strftime("%H:%M hs"),
        "initial_amount": active_session.initial_amount
    }

@app.post("/cash/open")
def open_cash(payload: CashOpenSchema, db: Session = Depends(get_db)):
    active = db.query(CashSessionDB).filter(CashSessionDB.is_open == True).first()
    if active:
        raise HTTPException(status_code=400, detail="La caja ya se encuentra abierta")
    
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    session = CashSessionDB(
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
def finalize_sale(payload: FinalizeSaleSchema, db: Session = Depends(get_db)):
    active_session = db.query(CashSessionDB).filter(CashSessionDB.is_open == True).first()
    session_id = active_session.id if active_session else None

    sale = SaleDB(
        presale_id=payload.presale_id,
        session_id=session_id,
        total_amount=payload.total_amount,
        amount_cash=payload.amount_cash,
        amount_mp=payload.amount_mp,
        payment_method=payload.payment_method,
        sold_by=payload.sold_by
    )
    db.add(sale)
    db.flush()

    for item in payload.items:
        prod = db.query(ProductDB).filter(ProductDB.id == item.product_id).first()
        if prod:
            prod.stock = max(0.0, prod.stock - item.quantity)
            
            remaining_to_discount = item.quantity
            lots = db.query(ProductLotDB).filter(
                ProductLotDB.product_id == prod.id,
                ProductLotDB.current_qty > 0
            ).order_by(ProductLotDB.expiration_date.asc()).all()

            for lot in lots:
                if remaining_to_discount <= 0:
                    break
                if lot.current_qty >= remaining_to_discount:
                    lot.current_qty -= remaining_to_discount
                    remaining_to_discount = 0
                else:
                    remaining_to_discount -= lot.current_qty
                    lot.current_qty = 0

            db.add(SaleItemDB(sale_id=sale.id, product_id=prod.id, product_name=prod.name, quantity=item.quantity))

    if payload.presale_id:
        ps = db.query(PreSaleDB).filter(PreSaleDB.id == payload.presale_id).first()
        if ps:
            ps.status = "COMPLETADA"

    db.commit()
    return {"status": "success", "message": "Venta procesada"}

@app.post("/cash/close")
def close_cash(payload: CashCloseSchema, db: Session = Depends(get_db)):
    session = db.query(CashSessionDB).filter(CashSessionDB.is_open == True).first()
    if not session:
        raise HTTPException(status_code=400, detail="No hay una caja abierta para cerrar")

    sales = db.query(SaleDB).filter(SaleDB.session_id == session.id).all()
    total_cash_sales = sum(s.amount_cash for s in sales)
    total_mp_sales = sum(s.amount_mp for s in sales)
    
    expected_cash = session.initial_amount + total_cash_sales
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
def get_cash_audits(date: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(CashSessionDB)
    if date:
        query = query.filter(CashSessionDB.date == date)
    
    sessions = query.order_by(CashSessionDB.id.desc()).all()
    result = []
    for s in sessions:
        sales = db.query(SaleDB).filter(SaleDB.session_id == s.id).all()
        result.append({
            "id": s.id,
            "date": s.date,
            "opened_at": s.opened_at.strftime("%H:%M hs"),
            "opened_by": s.opened_by,
            "initial_amount": s.initial_amount,
            "is_open": s.is_open,
            "closed_at": s.closed_at.strftime("%H:%M hs") if s.closed_at else "En curso",
            "closed_by": s.closed_by or "N/A",
            "reported_cash": s.reported_cash,
            "expected_cash": s.expected_cash,
            "total_mp": s.total_mp,
            "difference": s.difference,
            "status_message": s.status_message,
            "total_sales_count": len(sales)
        })
    return result

@app.get("/mrp/suggestions")
def get_mrp_suggestions(days: int = 7, target_days: int = 3, db: Session = Depends(get_db)):
    since_date = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    sales = db.query(SaleDB).filter(SaleDB.created_at >= since_date).all()
    
    sales_by_prod = {}
    for s in sales:
        for item in s.items:
            sales_by_prod[item.product_id] = sales_by_prod.get(item.product_id, 0.0) + item.quantity

    products = db.query(ProductDB).filter(ProductDB.is_active == True).all()
    suggestions = []

    for p in products:
        total_sold = sales_by_prod.get(p.id, 0.0)
        daily_demand = total_sold / max(1, days)
        
        if total_sold <= 0:
            continue

        stock_target = daily_demand * target_days
        suggested_buy = max(0.0, stock_target - p.stock)

        status = "OK"
        if p.stock <= 0:
            status = "AGOTADO"
        elif p.stock < (daily_demand * 1):
            status = "CRÍTICO"
        elif suggested_buy > 0:
            status = "REPOSICIÓN RECOMENDADA"

        suggestions.append({
            "product_id": p.id,
            "product_name": p.name,
            "supplier": p.supplier or "Sin Proveedor",
            "current_stock": p.stock,
            "unit_type": p.unit_type,
            "total_sold_period": round(total_sold, 2),
            "daily_demand": round(daily_demand, 2),
            "target_days": target_days,
            "suggested_buy": round(suggested_buy, 2),
            "estimated_cost": round(suggested_buy * (p.cost_price or 0.0), 2),
            "status": status
        })

    return sorted(suggestions, key=lambda x: x["suggested_buy"], reverse=True)

@app.get("/alerts")
def get_system_alerts(db: Session = Depends(get_db)):
    alerts = []
    today = datetime.date.today()

    active_lots = db.query(ProductLotDB).join(ProductDB).filter(
        ProductDB.is_active == True,
        ProductLotDB.current_qty > 0,
        ProductLotDB.expiration_date != None
    ).all()

    for lot in active_lots:
        try:
            exp_date = datetime.datetime.strptime(lot.expiration_date, "%Y-%m-%d").date()
            days_left = (exp_date - today).days
            display_date = format_iso_to_ddmmyyyy(lot.expiration_date)

            if days_left < 0:
                alerts.append({
                    "id": f"exp-{lot.id}", "type": "EXPIRATION", "level": "CRITICAL",
                    "title": f"🚨 Lote Vencido: {lot.product.name}",
                    "detail": f"Quedan {lot.current_qty} {lot.product.unit_type} vencidas el {display_date}. Proveedor: {lot.supplier or 'N/A'}"
                })
            elif days_left <= 30:
                alerts.append({
                    "id": f"exp-{lot.id}", "type": "EXPIRATION", "level": "WARNING",
                    "title": f"⏳ Vencimiento Próximo ({days_left} días): {lot.product.name}",
                    "detail": f"Lote de {lot.current_qty} {lot.product.unit_type} vence el {display_date}. Proveedor: {lot.supplier or 'N/A'}"
                })
        except Exception:
            pass

    active_products = db.query(ProductDB).filter(ProductDB.is_active == True).all()
    for p in active_products:
        if p.stock <= 0:
            alerts.append({
                "id": f"stock-{p.id}", "type": "STOCK", "level": "CRITICAL",
                "title": f"🔴 Producto Agotado: {p.name}", "detail": f"Stock en 0 {p.unit_type}."
            })
        elif p.stock <= 5:
            alerts.append({
                "id": f"stock-{p.id}", "type": "STOCK", "level": "WARNING",
                "title": f"⚠️ Stock Bajo: {p.name}", "detail": f"Quedan únicamente {p.stock} {p.unit_type}."
            })

    for p in active_products:
        if p.cost_price > 0:
            if p.price_per_unit <= p.cost_price:
                alerts.append({
                    "id": f"price-{p.id}", "type": "PRICE", "level": "CRITICAL",
                    "title": f"💸 Precio de Venta Bajo / Pérdida: {p.name}",
                    "detail": f"Costo actual (${p.cost_price}) >= PVP (${p.price_per_unit})."
                })
            elif p.previous_cost_price > 0 and p.cost_price > p.previous_cost_price:
                old_margin = ((p.price_per_unit - p.previous_cost_price) / p.price_per_unit) * 100.0
                new_margin = ((p.price_per_unit - p.cost_price) / p.price_per_unit) * 100.0
                if new_margin < (old_margin - 5.0):
                    alerts.append({
                        "id": f"price-increase-{p.id}", "type": "PRICE", "level": "HIGH",
                        "title": f"⚠️ Aumento de Costo no Trasladado: {p.name}",
                        "detail": f"Costo subió de ${p.previous_cost_price} a ${p.cost_price}. Margen bajó de {old_margin:.1f}% a {new_margin:.1f}%."
                    })

    return alerts
