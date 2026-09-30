from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, ForeignKey, DateTime, func
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
    role = Column(String, default="vendedor")  # 'superadmin', 'dueno', 'vendedor', 'encargado'
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
    can_edit_records = Column(Boolean, default=False)  # Permiso especial para modificar/borrar registros (aplicable a Dueño)

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
            can_preventa=True,
            can_caja=True,
            can_stock=True,
            can_ingreso=True,
            can_alertas=True,
            can_rrhh=True,
            can_mrp=True,
            can_verificacion=True,
            can_kpis=True,
            can_edit_records=True
        ))
        db.commit()
    db.close()

init_db()

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
        can_preventa=True,
        can_caja=False,
        can_stock=False,
        can_ingreso=False,
        can_alertas=False,
        can_rrhh=False,
        can_mrp=False,
        can_verificacion=False,
        can_kpis=False,
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

# NUEVO ENDPOINT DE KPIS Y REPORTES GENERALES
@app.get("/kpis/dashboard")
def get_kpis_dashboard(db: Session = Depends(get_db)):
    now = datetime.datetime.utcnow()
    month_start = datetime.datetime(now.year, now.month, 1)
    
    # Calcular mes anterior
    first_this_month = month_start
    last_month_last_day = first_this_month - datetime.timedelta(days=1)
    prev_month_start = datetime.datetime(last_month_last_day.year, last_month_last_day.month, 1)

    sales_this_month = db.query(SaleDB).filter(SaleDB.created_at >= month_start).all()
    sales_prev_month = db.query(SaleDB).filter(SaleDB.created_at >= prev_month_start, SaleDB.created_at < month_start).all()

    rev_this_month = sum(s.total_amount for s in sales_this_month)
    rev_prev_month = sum(s.total_amount for s in sales_prev_month)

    # Variación porcentual
    if rev_prev_month > 0:
        pct_growth = round(((rev_this_month - rev_prev_month) / rev_prev_month) * 100.0, 1)
    else:
        pct_growth = 100.0 if rev_this_month > 0 else 0.0

    # Top 10 Productos Más Vendidos (30 días)
    thirty_days_ago = now - datetime.timedelta(days=30)
    top_products_query = db.query(
        SaleItemDB.product_name,
        func.sum(SaleItemDB.quantity).label("total_qty")
    ).join(SaleDB).filter(SaleDB.created_at >= thirty_days_ago).group_by(SaleItemDB.product_name).order_by(func.sum(SaleItemDB.quantity).desc()).limit(10).all()

    top_products = [{"name": row[0], "total_qty": round(row[1], 2)} for row in top_products_query]

    # Top 10 Empleados por Asistencia y Puntualidad
    top_staff_query = db.query(
        WorkLogDB.user_name,
        func.count(WorkLogDB.id).label("shifts_count"),
        func.sum(WorkLogDB.hours_worked).label("total_hours")
    ).filter(WorkLogDB.clock_in >= thirty_days_ago).group_by(WorkLogDB.user_name).order_by(func.sum(WorkLogDB.hours_worked).desc()).limit(10).all()

    top_staff = [{"name": row[0], "shifts": row[1], "hours": round(row[2] or 0.0, 1)} for row in top_staff_query]

    # Conteo de Alertas
    alerts_endpoint_data = get_system_alerts(db)
    critical_alerts_count = sum(1 for a in alerts_endpoint_data if a["level"] == "CRITICAL")
    warning_alerts_count = sum(1 for a in alerts_endpoint_data if a["level"] in ["WARNING", "HIGH"])

    # Generación de Diagnóstico Ejecutivo Escrito
    if pct_growth >= 0 and critical_alerts_count == 0:
        health_status = "EXCELENTE"
        summary_text = f"🟢 ¡Vamos por muy buen camino! Las ventas crecieron un {pct_growth}% respecto al mes anterior. No hay alertas críticas registradas en inventarios ni cajas."
    elif pct_growth >= 0 and critical_alerts_count > 0:
        health_status = "ATENCION"
        summary_text = f"⚠️ El nivel de ventas viene bien (+{pct_growth}%), pero tenés {critical_alerts_count} alerta(s) crítica(s) pendientes de revisión (vencimientos de lotes o faltantes de stock)."
    else:
        health_status = "ALERTA"
        summary_text = f"🔴 Las ventas cayeron un {abs(pct_growth)}% en comparación con el mes anterior y tenés {critical_alerts_count} alerta(s) crítica(s) por resolver."

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

# Los demás endpoints (products, sales, cash, mrp, alerts) permanecen intactos
