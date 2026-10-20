from fastapi import FastAPI, Depends, HTTPException, status, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, ForeignKey, DateTime, func, Text, inspect, text, or_, Numeric
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel
from typing import List, Optional
import datetime
from zoneinfo import ZoneInfo
from db_config import load_database_config, verify_database_after_init, database_health
import json
from decimal import Decimal
import fair_planning
from fair_planning import D, to_cents
import os
import auth_core

# Valida configuración/ruta y, en producción, que la base exista y sea válida
# (o que su creación esté autorizada) ANTES de cualquier create_all/init_db.
DB_CONFIG = load_database_config()
SQLALCHEMY_DATABASE_URL = DB_CONFIG.url
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {}
)
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
    can_carteleria = Column(Boolean, default=False)
    can_edit_records = Column(Boolean, default=False)  # Controla si el dueño/usuario puede editar/eliminar registros
    branch_id = Column(Integer, default=1, index=True)  # Sucursal de trabajo predeterminada
    # U17/U15 (Fase 2.2): egresos PROVEEDOR / RETIRO_DUENO. Solo tiene efecto para ENCARGADO;
    # Dueño o superior lo tienen por rol y Cajero/Vendedor nunca (ver auth_core.capability_allowed).
    can_cash_privileged_outflows = Column(Boolean, default=False)

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
    minimum_stock = Column(Float, nullable=True)
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
    visual_presentation = Column(String, nullable=True)
    product_image_uri = Column(String, nullable=True)
    lots = relationship("ProductLotDB", back_populates="product", cascade="all, delete-orphan")

class PromotionDB(Base):
    __tablename__ = "promotions"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    created_by = Column(String, nullable=True)
    items = relationship("PromotionItemDB", back_populates="promotion", cascade="all, delete-orphan")
    branch_configs = relationship("PromotionBranchDB", back_populates="promotion", cascade="all, delete-orphan")

class PromotionItemDB(Base):
    __tablename__ = "promotion_items"
    id = Column(Integer, primary_key=True, index=True)
    promotion_id = Column(Integer, ForeignKey("promotions.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    included_qty = Column(Float, nullable=False)
    unit_type = Column(String, default="unid")
    sort_order = Column(Integer, default=0)
    promotion = relationship("PromotionDB", back_populates="items")
    product = relationship("ProductDB")

class PromotionBranchDB(Base):
    __tablename__ = "promotion_branches"
    id = Column(Integer, primary_key=True, index=True)
    promotion_id = Column(Integer, ForeignKey("promotions.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False)
    promo_price = Column(Float, nullable=True)
    enabled = Column(Boolean, default=True)
    promotion = relationship("PromotionDB", back_populates="branch_configs")

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
    product = relationship("ProductDB")

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
    # Fase 2.1 (ensure_auth_schema las agrega en bases existentes). NULL en registros históricos:
    # el actor de esos registros fue declarado por la app y no está verificado.
    actor_user_id = Column(Integer, nullable=True)
    actor_source = Column(String, nullable=True)      # session | cli | system | legacy_client
    session_id = Column(Integer, nullable=True)

# ===== AUTENTICACIÓN (Fase 2.1) =====
# Las credenciales viven fuera de `users`: ninguna serialización de usuario puede incluirlas.

class UserCredentialDB(Base):
    __tablename__ = "user_credentials"
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True)
    password_hash = Column(String, nullable=False)          # pbkdf2_sha256$iter$sal$hash
    must_change_password = Column(Boolean, nullable=False, default=True)
    change_reason = Column(String, nullable=True)           # DEFAULT_PASSWORD | LEGACY_PLAINTEXT | TEMPORARY | INITIAL
    password_changed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class AuthSessionDB(Base):
    __tablename__ = "auth_sessions"
    id = Column(Integer, primary_key=True, index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)   # SHA-256; nunca el token
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    last_seen_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String, nullable=True)
    ip = Column(String, nullable=True)
    peer_ip = Column(String, nullable=True)
    forwarded_chain = Column(String, nullable=True)   # solo mientras el límite por IP esté en "observe"
    ip_source = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    client_version = Column(String, nullable=True)

class AuthLoginFailureDB(Base):
    __tablename__ = "auth_login_failures"
    id = Column(Integer, primary_key=True, index=True)
    email_key = Column(String, nullable=False, index=True)   # email en minúsculas (exista o no)
    ip = Column(String, nullable=True, index=True)
    peer_ip = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, index=True)

class UserBranchDB(Base):
    """Sucursales asignadas a cada usuario (Fase 2.2). MASTERADMIN/SUPERADMIN acceden a todas por rol."""
    __tablename__ = "user_branches"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False, index=True)

class SchemaMigrationDB(Base):
    __tablename__ = "schema_migrations"
    version = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    applied_at = Column(DateTime, default=datetime.datetime.utcnow)

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
    cash_received = Column(Float, nullable=True)
    change_amount = Column(Float, nullable=True)
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
    unit_type = Column(String, nullable=True)
    is_promotion = Column(Boolean, default=False)
    promotion_id = Column(Integer, nullable=True)
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

# ===== OBJETIVO DE JORNADA — FERIA (tablas nuevas; create_all solo las agrega) =====

# Importes de dinero de las tablas nuevas: Numeric con centavos (los porcentajes siguen en Float).
MONEY_COLUMN_TYPE = Numeric(18, 2, asdecimal=True)

class FairCostConfigDB(Base):
    __tablename__ = "fair_cost_configs"
    id = Column(Integer, primary_key=True, index=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    is_current = Column(Boolean, default=True, index=True)
    stall_cost_per_day = Column(MONEY_COLUMN_TYPE, nullable=False)
    staff_count = Column(Integer, nullable=False)
    staff_cost_per_person_day = Column(MONEY_COLUMN_TYPE, nullable=False)
    transport_outbound = Column(MONEY_COLUMN_TYPE, nullable=False)
    transport_return = Column(MONEY_COLUMN_TYPE, nullable=False)
    green_max_stock_pct = Column(Float, nullable=False)
    yellow_max_stock_pct = Column(Float, nullable=False)
    safety_margin_pct = Column(Float, nullable=False)
    incomplete_data_tolerance_pct = Column(Float, nullable=False)
    comparable_days_count = Column(Integer, nullable=False)
    minimum_comparable_days = Column(Integer, nullable=False)
    created_by = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

class DayPlanDB(Base):
    __tablename__ = "day_plans"
    id = Column(Integer, primary_key=True, index=True)
    branch_id = Column(Integer, ForeignKey("branches.id"), nullable=False, index=True)
    plan_type = Column(String, nullable=False)
    start_date = Column(String, nullable=False)   # ISO AAAA-MM-DD
    end_date = Column(String, nullable=False)
    days_count = Column(Integer, nullable=False)
    stall_total = Column(MONEY_COLUMN_TYPE, nullable=False)
    staff_total = Column(MONEY_COLUMN_TYPE, nullable=False)
    transport_outbound = Column(MONEY_COLUMN_TYPE, nullable=False)
    transport_return = Column(MONEY_COLUMN_TYPE, nullable=False)
    transport_total = Column(MONEY_COLUMN_TYPE, nullable=False)
    other_expenses = Column(MONEY_COLUMN_TYPE, default=Decimal("0.00"))
    debt_target = Column(MONEY_COLUMN_TYPE, default=Decimal("0.00"))
    profit_target = Column(MONEY_COLUMN_TYPE, default=Decimal("0.00"))
    capital_available = Column(MONEY_COLUMN_TYPE, default=Decimal("0.00"))
    manual_expected_sales = Column(MONEY_COLUMN_TYPE, nullable=True)
    config_id = Column(Integer, ForeignKey("fair_cost_configs.id"), nullable=True)
    config_version = Column(Integer, nullable=True)  # None = valores por defecto
    green_max_stock_pct = Column(Float, nullable=False)
    yellow_max_stock_pct = Column(Float, nullable=False)
    safety_margin_pct = Column(Float, nullable=False)
    incomplete_data_tolerance_pct = Column(Float, nullable=False)
    comparable_days_count = Column(Integer, nullable=False)
    minimum_comparable_days = Column(Integer, nullable=False)
    status = Column(String, default="BORRADOR", index=True)
    notes = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    updated_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)
    # Jornada en vivo (ensure_fair_live_schema las agrega en bases existentes). Fechas en UTC.
    opened_at = Column(DateTime, nullable=True)
    opened_by = Column(String, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    closed_by = Column(String, nullable=True)
    final_results_json = Column(Text, nullable=True)   # resultado congelado al cerrar
    final_calc_version = Column(Integer, nullable=True)

class DayPlanSnapshotDB(Base):
    __tablename__ = "day_plan_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("day_plans.id"), nullable=False, index=True)
    computed_at = Column(DateTime, default=datetime.datetime.utcnow)
    calc_version = Column(Integer, nullable=False)
    inputs_json = Column(Text, nullable=False)
    results_json = Column(Text, nullable=False)
    economic_verdict = Column(String, nullable=False)
    commercial_verdict = Column(String, nullable=False)
    computed_by = Column(String, nullable=True)

Base.metadata.create_all(bind=engine)

def ensure_v2_schema():
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    existing = {c["name"] for c in inspector.get_columns("products")}
    additions = {
        "requires_expiration": "BOOLEAN DEFAULT 0",
        "replenishment_policy": "VARCHAR DEFAULT 'MRP'",
        "brand": "VARCHAR",
        "visual_presentation": "VARCHAR",
        "product_image_uri": "VARCHAR"
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
    sale_additions={
        "unit_price": "FLOAT DEFAULT 0",
        "unit_cost": "FLOAT DEFAULT 0",
        "cogs": "FLOAT DEFAULT 0",
        "gross_profit": "FLOAT DEFAULT 0",
        "is_promotion": "BOOLEAN DEFAULT 0",
        "promotion_id": "INTEGER"
    }
    with engine.begin() as conn:
        for column_name, ddl in sale_additions.items():
            if column_name not in sale_cols:
                conn.execute(text(f"ALTER TABLE sale_items ADD COLUMN {column_name} {ddl}"))

ensure_v2_schema()

def ensure_multilocal_schema():
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)

    user_cols = {c["name"] for c in inspector.get_columns("users")}
    with engine.begin() as conn:
        if "branch_id" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN branch_id INTEGER DEFAULT 1"))
        if "can_carteleria" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN can_carteleria BOOLEAN DEFAULT 0"))
        conn.execute(text("UPDATE users SET branch_id=1 WHERE branch_id IS NULL"))

    bp_cols = {c["name"] for c in inspector.get_columns("branch_products")}
    with engine.begin() as conn:
        if "minimum_stock" not in bp_cols:
            conn.execute(text("ALTER TABLE branch_products ADD COLUMN minimum_stock FLOAT"))

    sale_cols = {c["name"] for c in inspector.get_columns("sales")}
    with engine.begin() as conn:
        if "cash_received" not in sale_cols:
            conn.execute(text("ALTER TABLE sales ADD COLUMN cash_received FLOAT"))
        if "change_amount" not in sale_cols:
            conn.execute(text("ALTER TABLE sales ADD COLUMN change_amount FLOAT"))

    sale_item_cols = {c["name"] for c in inspector.get_columns("sale_items")}
    with engine.begin() as conn:
        if "unit_type" not in sale_item_cols:
            conn.execute(text("ALTER TABLE sale_items ADD COLUMN unit_type VARCHAR"))

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

def ensure_fair_live_schema():
    """Jornada de Feria en vivo: columnas nuevas en day_plans y una sola jornada ABIERTA por sucursal.

    Idempotente: solo agrega lo que falta; no borra ni recrea tablas.
    """
    Base.metadata.create_all(bind=engine)
    plan_cols = {c["name"] for c in inspect(engine).get_columns("day_plans")}
    additions = {
        "opened_at": "DATETIME",
        "opened_by": "VARCHAR",
        "closed_at": "DATETIME",
        "closed_by": "VARCHAR",
        "final_results_json": "TEXT",
        "final_calc_version": "INTEGER",
    }
    with engine.begin() as conn:
        for column_name, ddl in additions.items():
            if column_name not in plan_cols:
                conn.execute(text(f"ALTER TABLE day_plans ADD COLUMN {column_name} {ddl}"))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_day_plans_one_open_per_branch "
            "ON day_plans (branch_id) WHERE status = 'ABIERTA'"
        ))

ensure_fair_live_schema()

AUTH_SCHEMA_VERSION = "2.1-auth"

def ensure_auth_schema():
    """Fase 2.1: tablas de autenticación y columnas de actor verificado.

    Idempotente y aditiva: crea lo que falta y no borra ni recrea nada. NO crea ni modifica
    credenciales de usuarios existentes: eso solo ocurre con `manage_users.py migrate-credentials`
    o cuando el propio usuario cambia su contraseña.
    """
    Base.metadata.create_all(bind=engine)
    audit_cols = {c["name"] for c in inspect(engine).get_columns("audit_events")}
    with engine.begin() as conn:
        for column_name, ddl in {"actor_user_id": "INTEGER", "actor_source": "VARCHAR",
                                 "session_id": "INTEGER"}.items():
            if column_name not in audit_cols:
                conn.execute(text(f"ALTER TABLE audit_events ADD COLUMN {column_name} {ddl}"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_auth_login_failures_email_created "
                          "ON auth_login_failures (email_key, created_at)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_auth_login_failures_ip_created "
                          "ON auth_login_failures (ip, created_at)"))
        if not conn.execute(text("SELECT 1 FROM schema_migrations WHERE version = :v"),
                            {"v": AUTH_SCHEMA_VERSION}).fetchone():
            conn.execute(text("INSERT INTO schema_migrations (version, name, applied_at) VALUES (:v, :n, :t)"),
                         {"v": AUTH_SCHEMA_VERSION, "n": "Tablas de autenticación (Fase 2.1)",
                          "t": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)})

ensure_auth_schema()

AUTHZ_SCHEMA_VERSION = "2.2-authz"

def ensure_authz_schema():
    """Fase 2.2: capacidad de egresos privilegiados y sucursales por usuario.

    Aditiva e idempotente: agrega la columna si falta, crea user_branches y asigna a cada usuario
    que todavía no tiene ninguna fila su sucursal actual (users.branch_id). No modifica credenciales,
    roles, permisos existentes ni la columna heredada de contraseñas.
    """
    Base.metadata.create_all(bind=engine)
    user_cols = {c["name"] for c in inspect(engine).get_columns("users")}
    with engine.begin() as conn:
        if "can_cash_privileged_outflows" not in user_cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN can_cash_privileged_outflows BOOLEAN DEFAULT 0"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_user_branches_user_branch "
                          "ON user_branches (user_id, branch_id)"))
        conn.execute(text(
            "INSERT INTO user_branches (user_id, branch_id) "
            "SELECT u.id, COALESCE(u.branch_id, 1) FROM users u "
            "WHERE NOT EXISTS (SELECT 1 FROM user_branches ub WHERE ub.user_id = u.id)"))
        if not conn.execute(text("SELECT 1 FROM schema_migrations WHERE version = :v"),
                            {"v": AUTHZ_SCHEMA_VERSION}).fetchone():
            conn.execute(text("INSERT INTO schema_migrations (version, name, applied_at) VALUES (:v, :n, :t)"),
                         {"v": AUTHZ_SCHEMA_VERSION, "n": "Autorización y sucursales por usuario (Fase 2.2)",
                          "t": datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)})

ensure_authz_schema()

# U19=A: sin documentación interactiva ni esquema OpenAPI en producción.
_DOCS_KWARGS = ({"docs_url": None, "redoc_url": None, "openapi_url": None}
                if DB_CONFIG.is_production else {})
app = FastAPI(title="Fiambrería POS, RRHH, MRP, KPIs & Permisos API", **_DOCS_KWARGS)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["X-Error-Code", "Retry-After"])

# Código de error legible por la app en el header X-Error-Code; el "detail" sigue siendo un texto
# para que las versiones anteriores de la app muestren un mensaje claro.
ERROR_RETIRE_REQUIRES_CONFIRMATION = "RETIRE_REQUIRES_CONFIRMATION"
ERROR_PRODUCT_RETIRED_IN_BRANCH = "PRODUCT_RETIRED_IN_BRANCH"
ERROR_BRANCH_PRICE_REQUIRED = "BRANCH_PRICE_REQUIRED_FOR_REACTIVATION"


def _coded_error(status_code: int, code: str, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail, headers={"X-Error-Code": code})

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

def ar_to_utc(dt_obj: datetime.datetime) -> datetime.datetime:
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=ARG_TZ)
    return dt_obj.astimezone(datetime.timezone.utc).replace(tzinfo=None)

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

def parse_arg_date_filter(date_str: Optional[str], is_end: bool = False) -> Optional[datetime.datetime]:
    if date_str is None or not str(date_str).strip():
        return None
    value = str(date_str).strip()
    dt_local = None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            dt_local = datetime.datetime.strptime(value, fmt)
            break
        except ValueError:
            pass
    if dt_local is None:
        raise HTTPException(status_code=422, detail="Fecha inválida. Usá DD/MM/AAAA.")
    
    dt_local = dt_local.replace(hour=0, minute=0, second=0, microsecond=0)
    if is_end:
        dt_local = dt_local + datetime.timedelta(days=1)
    return ar_to_utc(dt_local)

def format_iso_to_ddmmyyyy(iso_input) -> str:
    if not iso_input:
        return ""
    if isinstance(iso_input, (datetime.date, datetime.datetime)):
        return iso_input.strftime("%d/%m/%Y")
    
    iso_str = str(iso_input).strip()
    if not iso_str:
        return ""
    
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            dt = datetime.datetime.strptime(iso_str.split("+")[0].split("Z")[0], fmt)
            return dt.strftime("%d/%m/%Y")
        except ValueError:
            pass

    parts = iso_str.split('-')
    if len(parts) == 3 and len(parts[0]) == 4:
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    return iso_str

MASTER_ADMIN_EMAIL = "admin@fiambreria.com"

def init_db():
    """Semilla de la cuenta maestra, solo si no existe (nunca modifica una cuenta existente).

    - Producción: se crea SIN contraseña utilizable. Se inicializa con
      `python manage_users.py reset-password --email admin@fiambreria.com` en el Shell de Render.
    - Desarrollo/tests: contraseña admin123 guardada como hash y marcada para cambio obligatorio.
    En ningún caso se guarda una contraseña nueva en texto plano.
    """
    db = SessionLocal()
    try:
        admin = db.query(UserDB).filter(func.lower(UserDB.email) == MASTER_ADMIN_EMAIL).first()
        if admin:
            # Compatibilidad con bases antiguas: el login promovía esta cuenta por email. Solo si no
            # queda ningún MASTERADMIN activo se conserva esa promoción (invariante: al menos uno).
            has_master = db.query(UserDB).filter(UserDB.role == "masteradmin", UserDB.is_active == True).first()
            if not has_master and admin.role == "superadmin":
                admin.role = "masteradmin"
                db.commit()
            return
        admin = UserDB(
            name="Super Admin",
            email=MASTER_ADMIN_EMAIL,
            hashed_password=auth_core.unusable_legacy_value(),
            role="masteradmin",
            is_active=True,
            can_preventa=True, can_caja=True, can_stock=True, can_ingreso=True,
            can_alertas=True, can_rrhh=True, can_mrp=True, can_verificacion=True,
            can_kpis=True, can_carteleria=True, can_edit_records=True
        )
        db.add(admin)
        db.flush()
        if not DB_CONFIG.is_production:
            now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            db.add(UserCredentialDB(
                user_id=admin.id,
                password_hash=auth_core.hash_password(auth_core.DEFAULT_MASTER_PASSWORD),
                must_change_password=True, change_reason=auth_core.REASON_DEFAULT_PASSWORD,
                created_at=now, updated_at=now))
        db.commit()
    finally:
        db.close()

init_db()
verify_database_after_init(DB_CONFIG)


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
    can_carteleria: Optional[bool] = None
    can_edit_records: Optional[bool] = None
    can_cash_privileged_outflows: Optional[bool] = None
    branch_id: Optional[int] = None
    branch_ids: Optional[List[int]] = None

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
    cash_received: Optional[float] = None
    change_amount: Optional[float] = None
    is_promotion: Optional[bool] = False
    promotion_id: Optional[int] = None

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
    visual_presentation: Optional[str] = None
    product_image_uri: Optional[str] = None
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
    visual_presentation: Optional[str] = None
    product_image_uri: Optional[str] = None
    is_active: bool = True

class BranchProductConfigSchema(BaseModel):
    price_per_unit: Optional[float] = None
    minimum_stock: Optional[float] = None
    is_available: bool = True
    is_exclusive: bool = False

class PromotionItemInputSchema(BaseModel):
    product_id: int
    included_qty: float
    unit_type: str = "unid"
    sort_order: Optional[int] = 0

class PromotionBranchConfigSchema(BaseModel):
    branch_id: int
    promo_price: Optional[float] = None
    enabled: bool = True

class PromotionCreateSchema(BaseModel):
    name: str
    description: Optional[str] = None
    active: bool = True
    created_by: Optional[str] = "Anonimo"
    items: List[PromotionItemInputSchema]
    branch_configs: List[PromotionBranchConfigSchema]

class PromotionUpdateSchema(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None
    items: Optional[List[PromotionItemInputSchema]] = None
    branch_configs: Optional[List[PromotionBranchConfigSchema]] = None

class PromotionCalculateItemSchema(BaseModel):
    product_id: int
    actual_qty: float

class PromotionCalculateRequestSchema(BaseModel):
    items: List[PromotionCalculateItemSchema]

class IngressCreateSchema(BaseModel):
    product_id: int
    supplier: Optional[str] = None
    cost_price: float
    quantity: float
    lot_number: Optional[str] = None
    expiration_date: Optional[str] = None
    received_by: Optional[str] = "Anonimo"
    notes: Optional[str] = None
    # Un ingreso sobre un producto RETIRADO de la sucursal solo lo reactiva con confirmación explícita.
    reactivate_branch_product: Optional[bool] = False

class BranchProductActionSchema(BaseModel):
    actor: Optional[str] = None
    reason: Optional[str] = None

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

# =====================================================================
# AUTENTICACIÓN (Fase 2.1)
# Contraseñas con hash (auth_core), sesiones opacas revocables en la base, bloqueo por intentos
# fallidos persistente. En 2.1 los endpoints de negocio siguen sin exigir sesión: eso es 2.2.
# =====================================================================

ERROR_AUTH_REQUIRED = "AUTH_REQUIRED"
ERROR_SESSION_INVALID = "SESSION_INVALID"
ERROR_SESSION_EXPIRED = "SESSION_EXPIRED"
ERROR_SESSION_REVOKED = "SESSION_REVOKED"
ERROR_USER_DISABLED = "USER_DISABLED"
ERROR_PASSWORD_CHANGE_REQUIRED = "PASSWORD_CHANGE_REQUIRED"
ERROR_ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
ERROR_TOO_MANY_ATTEMPTS_IP = "TOO_MANY_ATTEMPTS_IP"
ERROR_PASSWORD_POLICY = "PASSWORD_POLICY"
ERROR_INVALID_CREDENTIALS = "INVALID_CREDENTIALS"

ACCOUNT_LOCK_THRESHOLD = 5          # fallos por cuenta (email) ...
ACCOUNT_LOCK_MINUTES = 15           # ... dentro de esta ventana bloquean la cuenta 15 minutos
IP_LOCK_THRESHOLD = 20              # fallos por IP ...
IP_LOCK_WINDOW_MINUTES = 15         # ... en esta ventana
LOGIN_FAILURE_RETENTION_HOURS = 24
LAST_SEEN_UPDATE_SECONDS = 60
IP_LIMIT_MODES = ("observe", "enforce")


def _utc_naive_now() -> datetime.datetime:
    """UTC sin zona horaria, el mismo formato que el resto de las fechas guardadas."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _auth_utcnow() -> datetime.datetime:
    """Hora actual para autenticación (los tests la reemplazan)."""
    return _utc_naive_now()


def auth_settings() -> dict:
    """Configuración de red del login, leída del entorno en cada uso.

    - TRUSTED_PROXY_HOPS: proxies confiables delante del backend (0 a 5). Por defecto 0:
      X-Forwarded-For se ignora y se usa la dirección de la conexión.
    - AUTH_IP_LIMIT_MODE: observe | enforce. Por defecto `observe` en producción (registra pero
      no bloquea por IP hasta verificar qué IP real llega detrás de Render) y `enforce` fuera de
      producción. El límite por cuenta siempre está activo.
    """
    try:
        hops = int(os.environ.get("TRUSTED_PROXY_HOPS", "0").strip() or 0)
    except ValueError:
        hops = 0
    if not 0 <= hops <= auth_core.MAX_TRUSTED_PROXY_HOPS:
        hops = 0
    mode = (os.environ.get("AUTH_IP_LIMIT_MODE") or "").strip().lower()
    if mode not in IP_LIMIT_MODES:
        mode = "observe" if DB_CONFIG.is_production else "enforce"
    return {"trusted_proxy_hops": hops, "ip_limit_mode": mode}


def _client_address(request: Request) -> auth_core.ClientAddress:
    peer = request.client.host if request.client else None
    return auth_core.resolve_client_address(peer, request.headers.get("x-forwarded-for"),
                                            auth_settings()["trusted_proxy_hops"])


def _auth_error(status_code: int, code: str, detail: str, extra_headers: Optional[dict] = None) -> HTTPException:
    headers = {"X-Error-Code": code}
    if status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    headers.update(extra_headers or {})
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


def _email_key(email: Optional[str]) -> str:
    return (email or "").strip().lower()[:254]


def _find_user_by_email(db: Session, email: str) -> Optional[UserDB]:
    clean = (email or "").strip()
    if not clean:
        return None
    exact = db.query(UserDB).filter(UserDB.email == clean).first()
    if exact:
        return exact
    return db.query(UserDB).filter(func.lower(UserDB.email) == clean.lower()).order_by(UserDB.id.asc()).first()


# --- Bloqueo por intentos fallidos (persistente en la base: sobrevive a reinicios) -------------

def _recent_failures(db: Session, now: datetime.datetime, minutes: int, **filters):
    q = db.query(AuthLoginFailureDB).filter(
        AuthLoginFailureDB.created_at > now - datetime.timedelta(minutes=minutes))
    for column, value in filters.items():
        q = q.filter(getattr(AuthLoginFailureDB, column) == value)
    return q


def _check_login_limits(db: Session, email_key: str, address: auth_core.ClientAddress,
                        now: datetime.datetime):
    """Lanza 429 si la IP (en modo enforce) o la cuenta están bloqueadas. No verifica la contraseña."""
    if auth_settings()["ip_limit_mode"] == "enforce" and address.ip:
        ip_failures = _recent_failures(db, now, IP_LOCK_WINDOW_MINUTES, ip=address.ip).count()
        if ip_failures >= IP_LOCK_THRESHOLD:
            raise _auth_error(429, ERROR_TOO_MANY_ATTEMPTS_IP,
                              "Demasiados intentos fallidos desde esta conexión. Probá de nuevo en unos minutos.",
                              {"Retry-After": str(IP_LOCK_WINDOW_MINUTES * 60)})
    recent = _recent_failures(db, now, ACCOUNT_LOCK_MINUTES, email_key=email_key) \
        .order_by(AuthLoginFailureDB.created_at.desc()).limit(ACCOUNT_LOCK_THRESHOLD).all()
    if len(recent) >= ACCOUNT_LOCK_THRESHOLD:
        unlock_at = recent[-1].created_at + datetime.timedelta(minutes=ACCOUNT_LOCK_MINUTES)
        seconds = max(1, int((unlock_at - now).total_seconds()))
        raise _auth_error(429, ERROR_ACCOUNT_LOCKED,
                          f"Cuenta bloqueada por intentos fallidos. Probá de nuevo en {max(1, (seconds + 59) // 60)} minutos.",
                          {"Retry-After": str(seconds)})


def _record_login_failure(db: Session, email_key: str, address: auth_core.ClientAddress,
                          now: datetime.datetime):
    db.add(AuthLoginFailureDB(email_key=email_key, ip=address.ip, peer_ip=address.peer, created_at=now))
    db.query(AuthLoginFailureDB).filter(
        AuthLoginFailureDB.created_at < now - datetime.timedelta(hours=LOGIN_FAILURE_RETENTION_HOURS)
    ).delete(synchronize_session=False)
    db.commit()


def _clear_login_failures(db: Session, email_key: str):
    db.query(AuthLoginFailureDB).filter(AuthLoginFailureDB.email_key == email_key) \
        .delete(synchronize_session=False)


# --- Verificación de contraseña ----------------------------------------------------------------

def _credential(db: Session, user_id: int) -> Optional[UserCredentialDB]:
    return db.query(UserCredentialDB).filter(UserCredentialDB.user_id == user_id).first()


def _verify_user_password(db: Session, user: Optional[UserDB], password: str):
    """(ok, credencial). Siempre cuesta un PBKDF2, exista o no el usuario.

    Usuarios con credencial: solo se usa el hash. Usuarios sin credencial (base previa a 2.1):
    se compara contra la contraseña heredada SIN escribir nada; la sesión exige cambio (U7=A).
    """
    if user is None:
        auth_core.dummy_verify(password)
        return False, None
    cred = _credential(db, user.id)
    if cred is not None:
        return auth_core.verify_password(password, cred.password_hash), cred
    auth_core.dummy_verify(password)
    return auth_core.verify_legacy_plaintext(password, user.hashed_password), None


def _password_change_state(user: UserDB, cred: Optional[UserCredentialDB]):
    if cred is None:
        return True, auth_core.legacy_change_reason(user.hashed_password)
    return bool(cred.must_change_password), (cred.change_reason if cred.must_change_password else None)


def set_user_password(db: Session, user: UserDB, new_password: str, must_change: bool,
                      reason: Optional[str], now: datetime.datetime) -> UserCredentialDB:
    """Guarda SOLO el hash. No toca users.hashed_password (U8=A: el texto heredado se borra después)."""
    cred = _credential(db, user.id)
    hashed = auth_core.hash_password(new_password)
    if cred is None:
        cred = UserCredentialDB(user_id=user.id, password_hash=hashed, created_at=now)
        db.add(cred)
    cred.password_hash = hashed
    cred.must_change_password = must_change
    cred.change_reason = reason if must_change else None
    cred.password_changed_at = None if must_change else now
    cred.updated_at = now
    return cred


# --- Sesiones ----------------------------------------------------------------------------------

def _session_effective_expiry(session: AuthSessionDB, user: UserDB) -> datetime.datetime:
    """El vencimiento se recalcula con el rol ACTUAL: degradar un admin acorta sus sesiones."""
    cap = session.created_at + datetime.timedelta(hours=auth_core.session_max_hours(user.role))
    return min(session.expires_at, cap)


def _create_session(db: Session, user: UserDB, request: Request, address: auth_core.ClientAddress,
                    now: datetime.datetime):
    token = auth_core.new_session_token()
    observing = auth_settings()["ip_limit_mode"] == "observe"
    session = AuthSessionDB(
        token_hash=auth_core.token_digest(token), user_id=user.id, created_at=now,
        expires_at=now + datetime.timedelta(hours=auth_core.session_max_hours(user.role)),
        last_seen_at=now, ip=address.ip, peer_ip=address.peer, ip_source=address.source,
        forwarded_chain=auth_core.chain_json(address.forwarded_chain) if observing else None,
        user_agent=(request.headers.get("user-agent") or "")[:200] or None,
        client_version=(request.headers.get("x-client-version") or "")[:20] or None,
    )
    db.add(session)
    db.flush()
    return token, session


def revoke_user_sessions(db: Session, user_id: int, reason: str, now: datetime.datetime,
                         except_session_id: Optional[int] = None) -> int:
    q = db.query(AuthSessionDB).filter(AuthSessionDB.user_id == user_id, AuthSessionDB.revoked_at == None)
    if except_session_id is not None:
        q = q.filter(AuthSessionDB.id != except_session_id)
    return q.update({"revoked_at": now, "revoked_reason": reason}, synchronize_session=False)


class AuthContext:
    """Identidad autenticada de un pedido. En 2.2 el actor de auditoría sale de acá."""

    def __init__(self, user: UserDB, session: AuthSessionDB, must_change_password: bool,
                 change_reason: Optional[str]):
        self.user = user
        self.session = session
        self.must_change_password = must_change_password
        self.change_reason = change_reason

    @property
    def actor_email(self) -> str:
        return self.user.email

    def audit_fields(self) -> dict:
        return {"actor": self.user.email, "actor_user_id": self.user.id,
                "actor_source": "session", "session_id": self.session.id}


def _bearer_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def _resolve_auth_context(request: Request, db: Session, allow_pending_password_change: bool) -> AuthContext:
    token = _bearer_token(request)
    if not token:
        raise _auth_error(401, ERROR_AUTH_REQUIRED, "Iniciá sesión para continuar.")
    session = db.query(AuthSessionDB).filter(AuthSessionDB.token_hash == auth_core.token_digest(token)).first()
    if session is None:
        raise _auth_error(401, ERROR_SESSION_INVALID, "La sesión no es válida. Iniciá sesión de nuevo.")
    if session.revoked_at is not None:
        raise _auth_error(401, ERROR_SESSION_REVOKED, "La sesión fue cerrada. Iniciá sesión de nuevo.")
    user = db.query(UserDB).filter(UserDB.id == session.user_id).first()
    if user is None or not user.is_active:
        raise _auth_error(401, ERROR_USER_DISABLED, "Tu usuario está deshabilitado.")
    now = _auth_utcnow()
    if now >= _session_effective_expiry(session, user):
        raise _auth_error(401, ERROR_SESSION_EXPIRED, "La sesión venció. Iniciá sesión de nuevo.")
    must_change, reason = _password_change_state(user, _credential(db, user.id))
    if must_change and not allow_pending_password_change:
        raise _auth_error(403, ERROR_PASSWORD_CHANGE_REQUIRED, "Tenés que cambiar tu contraseña antes de continuar.")
    if session.last_seen_at is None or (now - session.last_seen_at).total_seconds() >= LAST_SEEN_UPDATE_SECONDS:
        session.last_seen_at = now
        db.commit()
    return AuthContext(user, session, must_change, reason)


def require_session(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    """Sesión válida, usuario activo y sin cambio de contraseña pendiente (para 2.2)."""
    return _resolve_auth_context(request, db, allow_pending_password_change=False)


def require_session_allow_password_change(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    """Como require_session, pero admite el cambio de contraseña pendiente (/auth/*)."""
    return _resolve_auth_context(request, db, allow_pending_password_change=True)


# =====================================================================
# AUTORIZACIÓN (Fase 2.2)
# Cada ruta declara UNA política (Policy) como dependencia. Un test recorre app.routes y falla si
# alguna ruta no tiene política. Las reglas de rol, capacidad y sucursal viven solo acá y en auth_core.
# =====================================================================

ERROR_FORBIDDEN_ROLE = "FORBIDDEN_ROLE"
ERROR_FORBIDDEN_CAPABILITY = "FORBIDDEN_CAPABILITY"
ERROR_FORBIDDEN_BRANCH = "FORBIDDEN_BRANCH"
ERROR_FORBIDDEN_USER_ADMIN = "FORBIDDEN_USER_ADMIN"
ERROR_LAST_MASTERADMIN = "LAST_MASTERADMIN"
ERROR_DEFAULT_PASSWORD_RESET_REQUIRED = "DEFAULT_PASSWORD_RESET_REQUIRED"


def has_capability(user: UserDB, capability: str) -> bool:
    return auth_core.capability_allowed(user.role, capability, getattr(user, capability, False))


def allowed_branch_ids(db: Session, user: UserDB) -> List[int]:
    """U14=A: MASTERADMIN y SUPERADMIN, todas las sucursales activas; el resto, las asignadas."""
    active = [b.id for b in db.query(BranchDB).filter(BranchDB.is_active == True).order_by(BranchDB.id).all()]
    if (user.role or "").strip().lower() in auth_core.ADMIN_ROLES:
        return active
    assigned = {r.branch_id for r in db.query(UserBranchDB).filter(UserBranchDB.user_id == user.id).all()}
    if not assigned:
        assigned = {user.branch_id or 1}
    return [b for b in active if b in assigned]


# Compatibilidad con el nombre usado en 2.1.
_allowed_branch_ids = allowed_branch_ids


def require_branch_access(db: Session, ctx: "AuthContext", branch_id: int) -> int:
    """403 FORBIDDEN_BRANCH si la sucursal no está entre las del usuario autenticado.

    Para MASTERADMIN/SUPERADMIN una sucursal inexistente no se rechaza acá: el endpoint responde
    su propio 404/422 como hasta ahora.
    """
    if branch_id in allowed_branch_ids(db, ctx.user):
        return branch_id
    if (ctx.user.role or "") in auth_core.ADMIN_ROLES and not db.query(BranchDB).filter(BranchDB.id == branch_id).first():
        return branch_id
    raise _auth_error(403, ERROR_FORBIDDEN_BRANCH, "No tenés acceso a esa sucursal.")


class Policy:
    """Política de seguridad de una ruta.

    min_role: rol mínimo (jerarquía de auth_core.ROLE_LEVELS).
    caps: capacidades can_* (alcanza una). MASTERADMIN/SUPERADMIN siempre las tienen; con
          cap_alt_role, ese rol o superior tampoco las necesita.
    branch: de dónde sale la sucursal a validar: "query" (branch_id, con branch_default), "path"
            ({branch_id} en la URL) o "day_plan" (sucursal guardada del plan {plan_id}).
    """

    def __init__(self, label: str, *, public: bool = False, min_role: Optional[str] = None,
                 caps: tuple = (), cap_alt_role: Optional[str] = None, branch: Optional[str] = None,
                 branch_default: Optional[int] = None, allow_password_change: bool = False):
        assert branch in (None, "query", "path", "day_plan")
        self.label = label
        self.public = public
        self.min_role = min_role
        self.caps = tuple(caps)
        self.cap_alt_role = cap_alt_role
        self.branch = branch
        self.branch_default = branch_default
        self.allow_password_change = allow_password_change

    def describe(self) -> dict:
        return {"category": self.label, "min_role": self.min_role, "capabilities": list(self.caps),
                "capability_waived_from": self.cap_alt_role, "branch": self.branch,
                "public": self.public, "password_change_pending_allowed": self.allow_password_change}

    def _branch_id(self, request: Request, db: Session) -> Optional[int]:
        try:
            if self.branch == "query":
                raw = request.query_params.get("branch_id")
                return int(raw) if raw not in (None, "") else self.branch_default
            if self.branch == "path":
                return int(request.path_params["branch_id"])
            if self.branch == "day_plan":
                plan = db.query(DayPlanDB).filter(DayPlanDB.id == int(request.path_params["plan_id"])).first()
                return plan.branch_id if plan else None   # inexistente: el endpoint responde 404
        except (KeyError, ValueError, TypeError):
            raise HTTPException(status_code=422, detail="branch_id inválido")
        return None

    def __call__(self, request: Request, db: Session = Depends(get_db)) -> Optional["AuthContext"]:
        if self.public:
            return None
        ctx = _resolve_auth_context(request, db, self.allow_password_change)
        user = ctx.user
        level = auth_core.role_level(user.role)
        if self.min_role and level < auth_core.role_level(self.min_role):
            raise _auth_error(403, ERROR_FORBIDDEN_ROLE, "Tu rol no tiene acceso a esta operación.")
        if self.caps:
            waived = (user.role or "") in auth_core.ADMIN_ROLES or (
                self.cap_alt_role is not None and level >= auth_core.role_level(self.cap_alt_role))
            if not waived and not any(has_capability(user, c) for c in self.caps):
                raise _auth_error(403, ERROR_FORBIDDEN_CAPABILITY, "No tenés permiso para este módulo.")
        if self.branch:
            branch_id = self._branch_id(request, db)
            if branch_id is not None:
                require_branch_access(db, ctx, branch_id)
        # Identidad para el sellado de actor (before_flush) en esta misma sesión de base.
        db.info["auth_ctx"] = ctx
        return ctx


def _policy(label, **kw) -> Policy:
    return Policy(label, **kw)


# Políticas reutilizables (las de sucursal se crean por ruta con su origen y valor por defecto).
PUBLIC = _policy("PUBLIC", public=True)
AUTHENTICATED_PENDING_OK = _policy("AUTHENTICATED", allow_password_change=True)
SUPERADMIN_ONLY = _policy("SUPERADMIN", min_role="superadmin")
MASTERADMIN_ONLY = _policy("MASTERADMIN", min_role="masteradmin")


def employee(cap=None, alt=None, branch=None, default=1, caps=None) -> Policy:
    caps = tuple(caps or ((cap,) if cap else ()))
    label = "EMPLOYEE" + ("+" + "|".join(caps) if caps else "")
    return _policy(label, min_role="vendedor", caps=caps, cap_alt_role=alt, branch=branch,
                   branch_default=default if branch == "query" else None)


def cashier(cap="can_caja", alt=None, branch="query", default=1, caps=None) -> Policy:
    caps = tuple(caps or ((cap,) if cap else ()))
    return _policy("CASHIER+" + "|".join(caps), min_role="cajero", caps=caps, cap_alt_role=alt,
                   branch=branch, branch_default=default if branch == "query" else None)


def manager(cap=None, branch=None, default=1) -> Policy:
    caps = (cap,) if cap else ()
    return _policy("MANAGER" + ("+" + cap if cap else ""), min_role="encargado", caps=caps, branch=branch,
                   branch_default=default if branch == "query" else None)


def owner(branch=None, default=1) -> Policy:
    return _policy("OWNER", min_role="dueno", branch=branch,
                   branch_default=default if branch == "query" else None)


def route_policies() -> dict:
    """{(MÉTODO, ruta): Policy | None} para todas las rutas de la API (inventario y test)."""
    from fastapi.routing import APIRoute
    out = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        found = [d.call for d in route.dependant.dependencies if isinstance(d.call, Policy)]
        for method in sorted(route.methods):
            out[(method, route.path)] = found
    return out


# --- Actor de auditoría desde la sesión ---------------------------------------------------------
# Campos de actor que el cliente podía declarar. Con una sesión autenticada, antes de guardar se
# reemplazan por el email del usuario de la sesión: el valor enviado por la app no cuenta.
ACTOR_FIELDS = {
    "AuditEventDB": ("actor",), "StockMovementDB": ("actor",), "CashMovementDB": ("actor",),
    "SaleDB": ("sold_by",), "PreSaleDB": ("created_by",), "ProductIngressDB": ("received_by",),
    "CashSessionDB": ("opened_by", "closed_by"), "InventoryCountDB": ("reported_by",),
    "PromotionDB": ("created_by",), "PriceHistoryDB": ("changed_by",), "AlertStateDB": ("resolved_by",),
    "ProductDB": ("last_counted_by",), "DayPlanDB": ("created_by", "updated_by", "opened_by", "closed_by"),
    "FairCostConfigDB": ("created_by",), "DayPlanSnapshotDB": ("computed_by",),
}


def _stamp_actor(session, flush_context, instances):
    ctx = session.info.get("auth_ctx")
    if ctx is None:
        return
    from sqlalchemy import inspect as sa_inspect
    for obj, is_new in [(o, True) for o in session.new] + [(o, False) for o in session.dirty]:
        fields = ACTOR_FIELDS.get(type(obj).__name__)
        if not fields:
            continue
        state = sa_inspect(obj)
        for field in fields:
            value = getattr(obj, field, None)
            changed = state.attrs[field].history.has_changes()
            if (is_new and value is not None) or (not is_new and changed):
                setattr(obj, field, ctx.actor_email)
        if is_new and isinstance(obj, AuditEventDB):
            obj.actor = ctx.actor_email
            obj.actor_user_id = ctx.user.id
            obj.actor_source = "session"
            obj.session_id = ctx.session.id


from sqlalchemy import event as _sa_event  # noqa: E402
_sa_event.listen(SessionLocal, "before_flush", _stamp_actor)


# --- Endpoints ---------------------------------------------------------------------------------

# Pública: diagnóstico sin datos sensibles (no expone URL ni rutas).
@app.get("/health")
def health(ctx: AuthContext = Depends(PUBLIC)):
    return database_health(DB_CONFIG, engine)


@app.post("/login")
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db), ctx: AuthContext = Depends(PUBLIC)):
    now = _auth_utcnow()
    address = _client_address(request)
    email_key = _email_key(form_data.username)
    _check_login_limits(db, email_key, address, now)

    user = _find_user_by_email(db, form_data.username)
    ok, cred = _verify_user_password(db, user, form_data.password)
    if not ok:
        _record_login_failure(db, email_key, address, now)
        raise _auth_error(400, ERROR_INVALID_CREDENTIALS, "Credenciales incorrectas")
    if not user.is_active:
        raise _auth_error(403, ERROR_USER_DISABLED,
                          "Tu usuario no está habilitado. Pedile al administrador que lo habilite.")

    _clear_login_failures(db, email_key)
    if cred is not None and auth_core.needs_rehash(cred.password_hash):
        cred.password_hash = auth_core.hash_password(form_data.password)
        cred.updated_at = now
    must_change, reason = _password_change_state(user, cred)
    token, session = _create_session(db, user, request, address, now)
    db.commit()

    # Mismos campos que antes (la APK instalada los usa) + sesión real.
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": _session_effective_expiry(session, user).isoformat() + "Z",
        "must_change_password": must_change,
        "password_change_reason": reason,
        "allowed_branch_ids": _allowed_branch_ids(db, user),
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
        "can_carteleria": user.can_carteleria,
        "can_edit_records": user.can_edit_records,
        "branch_id": user.branch_id or 1
    }


@app.get("/auth/me")
def auth_me(ctx: AuthContext = Depends(AUTHENTICATED_PENDING_OK), db: Session = Depends(get_db)):
    return {
        "user": serialize_user(ctx.user),
        "must_change_password": ctx.must_change_password,
        "password_change_reason": ctx.change_reason,
        "allowed_branch_ids": _allowed_branch_ids(db, ctx.user),
        "session": {
            "created_at": ctx.session.created_at.isoformat() + "Z",
            "expires_at": _session_effective_expiry(ctx.session, ctx.user).isoformat() + "Z",
        },
    }


@app.post("/auth/logout")
def auth_logout(ctx: AuthContext = Depends(AUTHENTICATED_PENDING_OK), db: Session = Depends(get_db)):
    ctx.session.revoked_at = _auth_utcnow()
    ctx.session.revoked_reason = "LOGOUT"
    db.commit()
    return {"status": "success"}


class ChangePasswordSchema(BaseModel):
    current_password: str
    new_password: str


@app.post("/auth/change-password")
def auth_change_password(payload: ChangePasswordSchema, request: Request,
                         ctx: AuthContext = Depends(AUTHENTICATED_PENDING_OK),
                         db: Session = Depends(get_db)):
    now = _auth_utcnow()
    user = ctx.user
    # Una contraseña por defecto es pública (admin123 figura en el repositorio): conocerla no prueba
    # ser el dueño de la cuenta. Desde ese estado solo se sale con el reset seguro de Render Shell.
    if ctx.change_reason == auth_core.REASON_DEFAULT_PASSWORD:
        raise _auth_error(403, ERROR_DEFAULT_PASSWORD_RESET_REQUIRED,
                          "Esta cuenta usa la contraseña por defecto y no puede cambiarse desde la app. "
                          "Pedí al administrador del sistema un reset seguro.")
    email_key = _email_key(user.email)
    address = _client_address(request)
    _check_login_limits(db, email_key, address, now)
    ok, _ = _verify_user_password(db, user, payload.current_password)
    if not ok:
        _record_login_failure(db, email_key, address, now)
        raise _auth_error(400, ERROR_INVALID_CREDENTIALS, "La contraseña actual no es correcta.")
    violation = auth_core.check_password_policy(payload.new_password, user.email, payload.current_password)
    if violation:
        raise _auth_error(422, ERROR_PASSWORD_POLICY, violation.message, {"X-Policy-Code": violation.code})

    _clear_login_failures(db, email_key)
    set_user_password(db, user, payload.new_password, must_change=False, reason=None, now=now)
    revoked = revoke_user_sessions(db, user.id, "PASSWORD_CHANGED", now, except_session_id=ctx.session.id)
    db.add(AuditEventDB(entity_type="user", entity_id=user.id, field_name="password",
                        old_value=None, new_value=None, reason="Cambio de contraseña",
                        created_at=now, **ctx.audit_fields()))
    db.commit()
    return {"status": "success", "must_change_password": False, "other_sessions_revoked": revoked}


# =====================================================================
# USUARIOS (Fase 2.2): autorización real (reemplaza los bloqueos temporales de U1)
# U3=A: MASTERADMIN administra todo; SUPERADMIN administra hasta Dueño; Dueño y menores no
# administran usuarios. U4=A: nadie asigna un rol >= al propio (salvo MASTERADMIN) y nadie se
# modifica a sí mismo. U5=A: siempre queda al menos un MASTERADMIN activo.
# =====================================================================

ADMIN_LEVEL_ROLES = auth_core.ADMIN_ROLES

# Único formato de salida de un usuario. Cualquier campo nuevo de UserDB queda fuera hasta que se
# agregue acá explícitamente. Nunca incluye credenciales (viven en user_credentials).
USER_PUBLIC_FIELDS = (
    "id", "name", "email", "role", "is_active", "branch_id",
    "can_preventa", "can_caja", "can_stock", "can_ingreso", "can_alertas", "can_rrhh",
    "can_mrp", "can_verificacion", "can_kpis", "can_carteleria", "can_edit_records",
    "can_cash_privileged_outflows",
)


def serialize_user(u: UserDB) -> dict:
    return {field: getattr(u, field) for field in USER_PUBLIC_FIELDS}


def assigned_branch_ids(db: Session, user: UserDB) -> List[int]:
    rows = sorted({r.branch_id for r in db.query(UserBranchDB).filter(UserBranchDB.user_id == user.id).all()})
    return rows or [user.branch_id or 1]


def visible_user_ids(db: Session, ctx: AuthContext) -> Optional[set]:
    """None = todos (MASTERADMIN/SUPERADMIN). Si no, usuarios que comparten alguna sucursal."""
    if (ctx.user.role or "") in ADMIN_LEVEL_ROLES:
        return None
    mine = set(allowed_branch_ids(db, ctx.user))
    visible = {ctx.user.id}
    for user in db.query(UserDB).all():
        if (user.role or "") not in ADMIN_LEVEL_ROLES and mine & set(assigned_branch_ids(db, user)):
            visible.add(user.id)
    return visible


def _user_admin_error(detail: str) -> HTTPException:
    return _auth_error(403, ERROR_FORBIDDEN_USER_ADMIN, detail)


def _require_can_manage(ctx: AuthContext, target: UserDB):
    reason = auth_core.can_manage_user(ctx.user.role, ctx.user.id, target.role, target.id)
    if reason:
        raise _user_admin_error(reason)


def _require_role_assignment(ctx: AuthContext, target: Optional[UserDB], new_role: str):
    if (new_role or "").strip().lower() not in auth_core.ROLE_LEVELS:
        raise HTTPException(status_code=400, detail="Rol invalido")
    reason = auth_core.can_assign_role(ctx.user.role, ctx.user.id, target.id if target else None,
                                       target.role if target else None, new_role)
    if reason:
        raise _user_admin_error(reason)


def active_masteradmin_ids(db: Session) -> set:
    return {u.id for u in db.query(UserDB).filter(UserDB.role == "masteradmin", UserDB.is_active == True).all()}


def guard_last_masteradmin(db: Session, target: UserDB, role_after: Optional[str], active_after: bool):
    if (target.role or "") != "masteradmin":
        return
    if auth_core.violates_last_masteradmin(active_masteradmin_ids(db), target.id, role_after, active_after):
        raise _auth_error(409, ERROR_LAST_MASTERADMIN, "Debe quedar al menos un MASTERADMIN activo.")


def _validated_branch_ids(db: Session, branch_ids) -> List[int]:
    ids = sorted({int(b) for b in branch_ids})
    active = {b.id for b in db.query(BranchDB).filter(BranchDB.is_active == True).all()}
    if not ids or any(b not in active for b in ids):
        raise HTTPException(status_code=400, detail="Sucursal invalida")
    return ids


def _set_user_branches(db: Session, user: UserDB, branch_ids: List[int], default_branch: Optional[int] = None):
    db.query(UserBranchDB).filter(UserBranchDB.user_id == user.id).delete(synchronize_session=False)
    for branch_id in branch_ids:
        db.add(UserBranchDB(user_id=user.id, branch_id=branch_id))
    user.branch_id = default_branch if default_branch in branch_ids else branch_ids[0]


def _get_user_or_404(db: Session, user_id: int) -> UserDB:
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return user


def _audit_user_change(db: Session, user: UserDB, field: str, old, new, reason: str):
    # actor, actor_user_id, actor_source y session_id los completa el sellado desde la sesión.
    db.add(AuditEventDB(entity_type="user", entity_id=user.id, field_name=field,
                        old_value=None if old is None else str(old), new_value=None if new is None else str(new),
                        reason=reason))


@app.get("/users")
def get_users(db: Session = Depends(get_db), ctx: AuthContext = Depends(employee(caps=("can_rrhh",), alt="dueno"))):
    visible = visible_user_ids(db, ctx)
    return [serialize_user(u) for u in db.query(UserDB).order_by(UserDB.id).all()
            if visible is None or u.id in visible]

@app.post("/users")
def create_user(user_data: dict, db: Session = Depends(get_db), ctx: AuthContext = Depends(SUPERADMIN_ONLY)):
    name = str(user_data.get("name") or "").strip()
    email = str(user_data.get("email") or "").strip()
    password = user_data.get("password")
    if not name or not email or not isinstance(password, str) or not password:
        raise HTTPException(status_code=422, detail="Nombre, email y contraseña son obligatorios")

    role = user_data.get("role")
    role = "vendedor" if role is None or str(role).strip() == "" else str(role).strip().lower()
    _require_role_assignment(ctx, None, role)

    lowered = email.lower()
    if lowered == MASTER_ADMIN_EMAIL or db.query(UserDB).filter(func.lower(UserDB.email) == lowered).first():
        raise HTTPException(status_code=409, detail="Ya existe un usuario con ese email")

    # La contraseña inicial cumple la política y se guarda SOLO como hash; se cambia en el primer ingreso.
    violation = auth_core.check_password_policy(password, email)
    if violation:
        raise _auth_error(422, ERROR_PASSWORD_POLICY, violation.message, {"X-Policy-Code": violation.code})

    if user_data.get("branch_ids") is not None:
        branch_ids = _validated_branch_ids(db, user_data["branch_ids"])
    else:
        branch_ids = _validated_branch_ids(db, [user_data.get("branch_id") or 1])

    privileged_default = role in ("superadmin", "dueno")
    new_u = UserDB(
        name=name,
        email=email,
        hashed_password=auth_core.unusable_legacy_value(),
        role=role,
        is_active=False,
        can_preventa=True, can_caja=False, can_stock=False, can_ingreso=False,
        can_alertas=False, can_rrhh=False, can_mrp=False, can_verificacion=False, can_kpis=False,
        can_carteleria=bool(user_data.get("can_carteleria", privileged_default)),
        can_edit_records=privileged_default,
        can_cash_privileged_outflows=False,
    )
    db.add(new_u); db.flush()
    _set_user_branches(db, new_u, branch_ids, user_data.get("branch_id"))
    set_user_password(db, new_u, password, must_change=True, reason=auth_core.REASON_INITIAL, now=_auth_utcnow())
    _audit_user_change(db, new_u, "created", None, role, "Alta de usuario")
    db.commit(); db.refresh(new_u)
    return serialize_user(new_u)

@app.patch("/users/{user_id}/permissions")
def update_permissions(user_id: int, p: PermissionsSchema, db: Session = Depends(get_db), ctx: AuthContext = Depends(SUPERADMIN_ONLY)):
    u = _get_user_or_404(db, user_id)
    _require_can_manage(ctx, u)
    now = _auth_utcnow()

    if p.role is not None:
        new_role = p.role.strip().lower()
        _require_role_assignment(ctx, u, new_role)
        guard_last_masteradmin(db, u, new_role, bool(u.is_active) if p.is_active is None else p.is_active)
    if p.is_active is False:
        guard_last_masteradmin(db, u, (p.role or u.role), False)

    if p.role is not None and p.role.strip().lower() != u.role:
        _audit_user_change(db, u, "role", u.role, p.role.strip().lower(), "Cambio de rol")
        u.role = p.role.strip().lower()
    if p.is_active is not None and p.is_active != bool(u.is_active):
        _audit_user_change(db, u, "is_active", bool(u.is_active), p.is_active, "Cambio de estado")
        u.is_active = p.is_active
        if not p.is_active:
            revoke_user_sessions(db, u.id, "USER_DISABLED", now)
    for cap in auth_core.CAPABILITIES:
        value = getattr(p, cap, None)
        if value is not None and value != bool(getattr(u, cap)):
            _audit_user_change(db, u, cap, bool(getattr(u, cap)), value, "Cambio de permiso")
            setattr(u, cap, value)
    if p.branch_ids is not None or p.branch_id is not None:
        ids = _validated_branch_ids(db, p.branch_ids if p.branch_ids is not None else [p.branch_id])
        old = assigned_branch_ids(db, u)
        _set_user_branches(db, u, ids, p.branch_id)
        if old != ids:
            _audit_user_change(db, u, "branch_ids", old, ids, "Cambio de sucursales")
    db.commit(); db.refresh(u)
    return serialize_user(u)

@app.patch("/users/{user_id}/status")
def update_user_status(user_id: int, payload: UserStatusSchema, db: Session=Depends(get_db), ctx: AuthContext = Depends(SUPERADMIN_ONLY)):
    u = _get_user_or_404(db, user_id)
    _require_can_manage(ctx, u)
    if not payload.is_active:
        guard_last_masteradmin(db, u, u.role, False)
    if payload.is_active != bool(u.is_active):
        _audit_user_change(db, u, "is_active", bool(u.is_active), payload.is_active, "Cambio de estado")
    u.is_active = payload.is_active
    if not payload.is_active:
        revoke_user_sessions(db, u.id, "USER_DISABLED", _auth_utcnow())
    db.commit()
    return {"status":"success","user_id":u.id,"is_active":u.is_active}

@app.post("/users/{user_id}/reset-password")
def reset_user_password(user_id: int, response: Response, db: Session = Depends(get_db),
                        ctx: AuthContext = Depends(SUPERADMIN_ONLY)):
    """Contraseña temporal para un usuario administrado (U13=A).

    La contraseña temporal se devuelve UNA sola vez en esta respuesta (no se guarda en claro ni en
    logs), obliga a cambiarla en el próximo ingreso y cierra todas las sesiones del usuario.
    Para la propia cuenta se usa /auth/change-password; para el MASTERADMIN sin acceso, Render Shell.
    """
    u = _get_user_or_404(db, user_id)
    _require_can_manage(ctx, u)
    now = _auth_utcnow()
    temporary = auth_core.generate_temporary_password()
    set_user_password(db, u, temporary, must_change=True, reason=auth_core.REASON_TEMPORARY, now=now)
    revoked = revoke_user_sessions(db, u.id, "PASSWORD_RESET", now)
    _clear_login_failures(db, _email_key(u.email))
    _audit_user_change(db, u, "password", None, None, f"Contraseña temporal; sesiones cerradas: {revoked}")
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"user": serialize_user(u), "temporary_password": temporary, "must_change_password": True,
            "sessions_revoked": revoked}

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
def get_profitability(period: str="month", branch_id: int=1, db: Session=Depends(get_db), ctx: AuthContext = Depends(employee("can_kpis", alt="dueno", branch="query"))):
    start,end,label=_kpi_period(period)
    data=build_profitability(start,end,db,branch_id)
    return {"period":label,"from":start.isoformat(),"to":end.isoformat(),**data}

@app.get("/kpis/dashboard")
def get_kpis_dashboard(period: str="month", branch_id: int=1, db: Session=Depends(get_db), ctx: AuthContext = Depends(employee("can_kpis", alt="dueno", branch="query"))):
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

def _shift_target(db: Session, ctx: AuthContext, user_id: int) -> UserDB:
    """U16=A: cada uno maneja su propio turno; el de otro requiere Encargado o superior + can_rrhh
    y que el empleado sea visible (comparte sucursal)."""
    user=db.query(UserDB).filter(UserDB.id==user_id).first()
    if not user: raise HTTPException(status_code=404,detail="Empleado no encontrado")
    if user.id == ctx.user.id:
        return user
    if auth_core.role_level(ctx.user.role) < auth_core.role_level("encargado") or not has_capability(ctx.user, "can_rrhh"):
        raise _auth_error(403, ERROR_FORBIDDEN_ROLE, "Solo un Encargado o superior con permiso de RRHH maneja turnos de otros.")
    visible = visible_user_ids(db, ctx)
    if visible is not None and user.id not in visible:
        raise _auth_error(403, ERROR_FORBIDDEN_BRANCH, "Ese empleado no pertenece a tus sucursales.")
    return user


@app.post("/hr/shifts/start")
def start_shift(payload: ShiftStartSchema, db: Session=Depends(get_db), ctx: AuthContext = Depends(employee())):
    user=_shift_target(db, ctx, payload.user_id)
    if not user.is_active: raise HTTPException(status_code=403,detail="El usuario esta inhabilitado")
    current=db.query(WorkLogDB).filter(WorkLogDB.user_id==user.id,WorkLogDB.clock_out==None).order_by(WorkLogDB.id.desc()).first()
    if current: raise HTTPException(status_code=409,detail="El empleado ya tiene un turno abierto")
    # El rol trabajado no puede superar el rol real del empleado.
    role_worked = payload.role_worked or user.role
    if auth_core.role_level(role_worked) > auth_core.role_level(user.role):
        role_worked = user.role
    log=WorkLogDB(user_id=user.id,user_name=user.name,user_email=user.email,clock_in=datetime.datetime.utcnow(),role_worked=role_worked)
    db.add(log); db.commit(); db.refresh(log)
    return {"status":"success","shift_id":log.id,"user_id":user.id,"role_worked":log.role_worked}

@app.post("/hr/shifts/end")
def end_shift(payload: ShiftEndSchema, db: Session=Depends(get_db), ctx: AuthContext = Depends(employee())):
    _shift_target(db, ctx, payload.user_id)
    log=db.query(WorkLogDB).filter(WorkLogDB.user_id==payload.user_id,WorkLogDB.clock_out==None).order_by(WorkLogDB.id.desc()).first()
    if not log: raise HTTPException(status_code=404,detail="No hay turno abierto para ese empleado")
    now=datetime.datetime.utcnow(); log.clock_out=now
    log.hours_worked=round((now-log.clock_in).total_seconds()/3600.0,2)
    db.commit()
    return {"status":"success","shift_id":log.id,"hours_worked":log.hours_worked}

@app.get("/hr/shifts/active")
def active_shifts(db: Session=Depends(get_db), ctx: AuthContext = Depends(employee("can_rrhh", alt="dueno"))):
    visible = visible_user_ids(db, ctx)
    return [l for l in db.query(WorkLogDB).filter(WorkLogDB.clock_out==None).order_by(WorkLogDB.clock_in.asc()).all()
            if visible is None or l.user_id in visible]

@app.get("/hr/worklogs")
def get_worklogs(email: Optional[str]=None, days: Optional[int]=None, db: Session=Depends(get_db), ctx: AuthContext = Depends(employee("can_rrhh", alt="dueno"))):
    q=db.query(WorkLogDB)
    if email: q=q.filter(WorkLogDB.user_email==email)
    if days and days>0: q=q.filter(WorkLogDB.clock_in>=datetime.datetime.utcnow()-datetime.timedelta(days=days))
    visible = visible_user_ids(db, ctx)
    logs=[l for l in q.order_by(WorkLogDB.id.desc()).all() if visible is None or l.user_id in visible]
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

def _visible_emails(db: Session, ctx: AuthContext) -> Optional[set]:
    visible = visible_user_ids(db, ctx)
    if visible is None:
        return None
    return {u.email for u in db.query(UserDB).filter(UserDB.id.in_(visible)).all()}


@app.get("/hr/performance")
def get_performance(email: str, days: int=30, db: Session=Depends(get_db), ctx: AuthContext = Depends(employee("can_rrhh", alt="dueno"))):
    emails = _visible_emails(db, ctx)
    if emails is not None and email not in emails:
        raise _auth_error(403, ERROR_FORBIDDEN_BRANCH, "Ese empleado no pertenece a tus sucursales.")
    return get_employee_stats(email,days,db)

@app.get("/hr/compare")
def compare_employees(emails: str="", days: int=30, db: Session=Depends(get_db), email1: Optional[str]=None, email2: Optional[str]=None, ctx: AuthContext = Depends(employee("can_rrhh", alt="dueno"))):
    selected=[x.strip() for x in emails.split(",") if x.strip()]
    if not selected: selected=[x for x in (email1,email2) if x]
    if not selected: selected=[u.email for u in db.query(UserDB).order_by(UserDB.name.asc()).all()]
    visible = _visible_emails(db, ctx)
    unique=[]
    for email in selected:
        if email not in unique and (visible is None or email in visible): unique.append(email)
    return {"days":days,"count":len(unique),"employees":[get_employee_stats(email,days,db) for email in unique]}

def branch_product_view(product: ProductDB, branch_id: int, db: Session):
    config = db.query(BranchProductDB).filter(
        BranchProductDB.branch_id == branch_id,
        BranchProductDB.product_id == product.id
    ).first()

    if branch_id == 1 and not config:
        config = BranchProductDB(
            branch_id=1,
            product_id=product.id,
            price_per_unit=product.price_per_unit,
            minimum_stock=None,
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
    data["minimum_stock"] = config.minimum_stock
    data["branch_id"] = branch_id
    data["is_exclusive"] = bool(config.is_exclusive)
    return data

@app.get("/products")
def get_products(branch_id: int = 1, db: Session = Depends(get_db), ctx: AuthContext = Depends(employee(branch="query"))):
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
, ctx: AuthContext = Depends(employee())):
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
def get_product_by_barcode(barcode: str, branch_id: int = 1, db: Session = Depends(get_db), ctx: AuthContext = Depends(employee(branch="query"))):
    product = db.query(ProductDB).filter(ProductDB.barcode == barcode.strip()).first()
    if not product:
        raise HTTPException(status_code=404, detail="EAN no asociado a ningun producto")

    view = branch_product_view(product, branch_id, db)
    if not view:
        raise HTTPException(status_code=404, detail="Producto no disponible en esta sucursal")

    db.commit()
    return view

@app.get("/products/search")
def search_products(q: str = "", branch_id: int = 1, db: Session = Depends(get_db), ctx: AuthContext = Depends(employee(branch="query"))):
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
def get_branch_product_admin(branch_id: int, db: Session = Depends(get_db), ctx: AuthContext = Depends(employee("can_stock", alt="encargado", branch="path"))):
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

        if branch_id == 1 and not config:
            config = BranchProductDB(
                branch_id=1,
                product_id=product.id,
                price_per_unit=product.price_per_unit,
                minimum_stock=None,
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
            "visual_presentation": product.visual_presentation,
            "product_image_uri": product.product_image_uri,
            "base_price": product.price_per_unit,
            "branch_price": (
                config.price_per_unit
                if config and config.price_per_unit is not None
                else product.price_per_unit
            ),
            "minimum_stock": config.minimum_stock if config else None,
            "stock": get_branch_stock(db, product.id, branch_id),
            "is_available": bool(config.is_available) if config else False,
            "is_exclusive": bool(config.is_exclusive) if config else False,
            "branch_status": _branch_status(config),
            # Precio propio de la sucursal, sin respaldo al precio global (None si no está configurado).
            "branch_price_configured": config.price_per_unit if config else None,
            "branch_id": branch_id
        })

    db.commit()
    return result


BRANCH_STATUS_AVAILABLE = "DISPONIBLE"
BRANCH_STATUS_RETIRED = "RETIRADO"
BRANCH_STATUS_NOT_ENABLED = "NO_HABILITADO"


def _branch_status(config) -> str:
    """DISPONIBLE / RETIRADO (configurado y deshabilitado en la sucursal) / NO_HABILITADO (nunca configurado)."""
    if config is None:
        return BRANCH_STATUS_NOT_ENABLED
    return BRANCH_STATUS_AVAILABLE if config.is_available else BRANCH_STATUS_RETIRED


def _branch_stock_detail(db: Session, product_id: int, branch_id: int) -> dict:
    """Stock de la sucursal según sus partidas y su valor a costo histórico de cada partida.

    El valor es confiable solo si todas las partidas con saldo tienen costo > 0.
    """
    lots = db.query(ProductLotDB).filter(
        ProductLotDB.product_id == product_id,
        ProductLotDB.branch_id == branch_id,
        ProductLotDB.current_qty > 0
    ).all()
    qty = sum(float(l.current_qty or 0) for l in lots)
    value = sum(float(l.current_qty or 0) * float(l.cost_price or 0) for l in lots)
    reliable = all(float(l.cost_price or 0) > 0 for l in lots)
    return {
        "stock": round(qty, 6),
        "stock_value_at_cost": round(value, 2) if lots and reliable else None,
        "cost_reliable": reliable,
        "lots_with_stock": len(lots),
    }


def _get_active_branch_or_404(db: Session, branch_id: int) -> BranchDB:
    branch = db.query(BranchDB).filter(BranchDB.id == branch_id, BranchDB.is_active == True).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada")
    return branch


def _require_branch_price_for_reactivation(price: Optional[float], product_name: str, branch_name: str):
    """Regla única para TODOS los caminos RETIRADO -> DISPONIBLE (reactivar, ingreso, configuración).

    `price` es el precio PROPIO de la sucursal que quedaría vigente. Nunca se resuelve con
    products.price_per_unit. Se llama ANTES de modificar nada.
    """
    if price is None or price <= 0:
        raise _coded_error(
            409, ERROR_BRANCH_PRICE_REQUIRED,
            f"{product_name} no tiene un precio de venta mayor a 0 configurado para {branch_name}. "
            "Configurá primero el precio de esta sucursal (Stock > Administración por Sucursal) y después reactivalo."
        )


def _format_qty(qty: float, unit_type: Optional[str]) -> str:
    text_qty = f"{qty:.3f}".rstrip("0").rstrip(".")
    return f"{text_qty} {unit_type or 'unid'}"


@app.get("/branches/{branch_id}/products/{product_id}/retire-preview")
def preview_retire_branch_product(branch_id: int, product_id: int, db: Session = Depends(get_db), ctx: AuthContext = Depends(employee("can_stock", alt="encargado", branch="path"))):
    """Qué pasaría al retirar el producto de la sucursal (no modifica nada)."""
    branch = _get_active_branch_or_404(db, branch_id)
    product = db.query(ProductDB).filter(ProductDB.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    config = db.query(BranchProductDB).filter(
        BranchProductDB.branch_id == branch_id,
        BranchProductDB.product_id == product_id
    ).first()
    detail = _branch_stock_detail(db, product_id, branch_id)
    return {
        "branch_id": branch_id,
        "branch_name": branch.name,
        "product_id": product.id,
        "product_name": product.name,
        "unit_type": product.unit_type,
        "branch_status": _branch_status(config),
        "requires_confirmation": detail["stock"] > 0.000001,
        **detail,
    }

@app.put("/branches/{branch_id}/products/{product_id}")
def configure_branch_product(
    branch_id: int,
    product_id: int,
    payload: BranchProductConfigSchema,
    db: Session = Depends(get_db)
, ctx: AuthContext = Depends(owner(branch="path"))):
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
    if payload.minimum_stock is not None and payload.minimum_stock < 0:
        raise HTTPException(status_code=422, detail="El stock mínimo no puede ser negativo")

    config = db.query(BranchProductDB).filter(
        BranchProductDB.branch_id == branch_id,
        BranchProductDB.product_id == product_id
    ).first()

    # RETIRADO -> DISPONIBLE: misma regla que /reactivate e Ingresos, validada antes de modificar nada.
    # El precio que valida es el propio de la sucursal: el enviado en este PUT o, si no se envía,
    # el ya configurado en la sucursal. Nunca el precio global.
    reactivating = config is not None and not config.is_available and payload.is_available
    if reactivating:
        own_price = payload.price_per_unit if payload.price_per_unit is not None else config.price_per_unit
        _require_branch_price_for_reactivation(own_price, product.name, branch.name)

    if not config:
        config = BranchProductDB(
            branch_id=branch_id,
            product_id=product_id
        )
        db.add(config)

    if reactivating:
        # Conserva el precio propio validado arriba; no lo reemplaza por el global.
        if payload.price_per_unit is not None:
            config.price_per_unit = payload.price_per_unit
    else:
        # Comportamiento previo (sin cambios para disponibles, retirados que siguen retirados
        # y productos nunca configurados).
        config.price_per_unit = (
            payload.price_per_unit
            if payload.price_per_unit is not None
            else product.price_per_unit
        )
    if payload.minimum_stock is not None:
        config.minimum_stock = payload.minimum_stock
    config.is_available = payload.is_available
    config.is_exclusive = payload.is_exclusive
    if reactivating:
        db.add(AuditEventDB(
            entity_type="branch_product", entity_id=config.id,
            field_name=f"is_available_branch_{branch_id}_product_{product_id}",
            old_value="False", new_value="True", actor="Anonimo",
            reason="Reactivado desde configuración de sucursal (PUT)"
        ))

    db.commit()
    db.refresh(config)

    return {
        "status": "success",
        "branch_id": branch_id,
        "product_id": product_id,
        "price_per_unit": config.price_per_unit,
        "minimum_stock": config.minimum_stock,
        "is_available": config.is_available,
        "is_exclusive": config.is_exclusive,
        "stock": get_branch_stock(db, product_id, branch_id)
    }

@app.delete("/branches/{branch_id}/products/{product_id}")
def remove_product_from_branch(
    branch_id: int,
    product_id: int,
    confirm_stock: bool = False,
    actor: Optional[str] = None,
    reason: Optional[str] = None,
    db: Session = Depends(get_db)
, ctx: AuthContext = Depends(owner(branch="path"))):
    """Retirar de sucursal: solo is_available=false en ESA sucursal.

    No borra el producto maestro, ni partidas, ni movimientos, ni historial. Si la sucursal
    tiene stock del producto exige confirm_stock=true (el stock queda inmovilizado, no se vende).
    """
    branch = _get_active_branch_or_404(db, branch_id)
    config = db.query(BranchProductDB).filter(
        BranchProductDB.branch_id == branch_id,
        BranchProductDB.product_id == product_id
    ).first()

    if not config:
        raise HTTPException(
            status_code=404,
            detail="Producto no configurado en esta sucursal"
        )

    product = db.query(ProductDB).filter(ProductDB.id == product_id).first()
    detail = _branch_stock_detail(db, product_id, branch_id)

    if not config.is_available:
        return {
            "status": "success",
            "already_retired": True,
            "branch_id": branch_id,
            "product_id": product_id,
            "is_available": False,
            "branch_status": BRANCH_STATUS_RETIRED,
            **detail
        }

    if detail["stock"] > 0.000001 and not confirm_stock:
        name = product.name if product else f"Producto {product_id}"
        value_text = (f" (valor a costo ${detail['stock_value_at_cost']:.2f})"
                      if detail["stock_value_at_cost"] is not None else "")
        raise _coded_error(
            409, ERROR_RETIRE_REQUIRES_CONFIRMATION,
            f"{name} tiene {_format_qty(detail['stock'], product.unit_type if product else None)} en stock "
            f"en {branch.name}{value_text}. Si lo retirás, ese stock queda sin vender hasta reactivarlo. "
            "Confirmá para retirarlo."
        )

    config.is_available = False
    note = (reason or "").strip() or "Retirar de sucursal"
    if detail["stock"] > 0.000001:
        note += f" | stock al retirar: {detail['stock']}"
    db.add(AuditEventDB(
        entity_type="branch_product", entity_id=config.id,
        field_name=f"is_available_branch_{branch_id}_product_{product_id}",
        old_value="True", new_value="False",
        actor=(actor or "").strip() or "Anonimo", reason=note
    ))
    db.commit()

    return {
        "status": "success",
        "already_retired": False,
        "branch_id": branch_id,
        "product_id": product_id,
        "is_available": False,
        "branch_status": BRANCH_STATUS_RETIRED,
        **detail
    }


@app.post("/branches/{branch_id}/products/{product_id}/reactivate")
def reactivate_branch_product(
    branch_id: int,
    product_id: int,
    payload: Optional[BranchProductActionSchema] = None,
    db: Session = Depends(get_db)
, ctx: AuthContext = Depends(owner(branch="path"))):
    """Vuelve a habilitar en la sucursal un producto RETIRADO, con su precio de sucursal ya configurado."""
    branch = _get_active_branch_or_404(db, branch_id)
    product = db.query(ProductDB).filter(ProductDB.id == product_id, ProductDB.is_active == True).first()
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado o inactivo")
    config = db.query(BranchProductDB).filter(
        BranchProductDB.branch_id == branch_id,
        BranchProductDB.product_id == product_id
    ).first()
    if not config:
        raise HTTPException(status_code=404, detail="Producto no configurado en esta sucursal")

    if config.is_available:
        return {"status": "success", "already_available": True, "branch_id": branch_id,
                "product_id": product_id, "is_available": True, "branch_status": BRANCH_STATUS_AVAILABLE,
                "price_per_unit": config.price_per_unit}

    # El precio de venta debe ser explícito en la sucursal: no se reactiva con precio vacío o 0.
    _require_branch_price_for_reactivation(config.price_per_unit, product.name, branch.name)

    config.is_available = True
    actor = ((payload.actor if payload else None) or "").strip() or "Anonimo"
    reason = ((payload.reason if payload else None) or "").strip() or "Reactivar en sucursal"
    db.add(AuditEventDB(
        entity_type="branch_product", entity_id=config.id,
        field_name=f"is_available_branch_{branch_id}_product_{product_id}",
        old_value="False", new_value="True", actor=actor, reason=reason
    ))
    db.commit()
    return {"status": "success", "already_available": False, "branch_id": branch_id,
            "product_id": product_id, "is_available": True, "branch_status": BRANCH_STATUS_AVAILABLE,
            "price_per_unit": config.price_per_unit, "stock": get_branch_stock(db, product_id, branch_id)}

@app.post("/products/master")
def create_product_master(
    prod: ProductMasterSchema,
    branch_id: int = 1,
    db: Session = Depends(get_db)
, ctx: AuthContext = Depends(employee("can_ingreso", alt="dueno", branch="query"))):
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
        visual_presentation=prod.visual_presentation,
        product_image_uri=prod.product_image_uri,
        is_active=prod.is_active
    )

    db.add(x)
    db.flush()

    config = BranchProductDB(
        branch_id=branch_id,
        product_id=x.id,
        price_per_unit=prod.price_per_unit,
        minimum_stock=None,
        is_available=True,
        is_exclusive=(branch_id != 1)
    )

    db.add(config)

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
                minimum_stock=None,
                is_available=False,
                is_exclusive=False
            ))

    db.commit()
    db.refresh(x)

    return x

# ==========================================
# SECCIÓN: GESTOR DE PROMOCIONES
# ==========================================

@app.get("/promotions")
def get_promotions(branch_id: int = 1, db: Session = Depends(get_db), ctx: AuthContext = Depends(employee(branch="query"))):
    branch = db.query(BranchDB).filter(BranchDB.id == branch_id, BranchDB.is_active == True).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada")
    
    promos = db.query(PromotionDB).all()
    result = []
    for promo in promos:
        b_conf = db.query(PromotionBranchDB).filter(
            PromotionBranchDB.promotion_id == promo.id,
            PromotionBranchDB.branch_id == branch_id
        ).first()
        
        enabled = b_conf.enabled if b_conf else True
        promo_price = b_conf.promo_price if b_conf else 0.0

        items_list = []
        for item in promo.items:
            prod = item.product
            prod_config = db.query(BranchProductDB).filter(
                BranchProductDB.branch_id == branch_id,
                BranchProductDB.product_id == prod.id
            ).first()
            normal_price = prod_config.price_per_unit if (prod_config and prod_config.price_per_unit is not None) else prod.price_per_unit

            items_list.append({
                "product_id": prod.id,
                "product_name": prod.name,
                "barcode": prod.barcode,
                "included_qty": item.included_qty,
                "unit_type": item.unit_type,
                "regular_unit_price": normal_price
            })

        result.append({
            "id": promo.id,
            "name": promo.name,
            "description": promo.description,
            "active": promo.active,
            "branch_id": branch_id,
            "enabled": enabled,
            "promo_price": promo_price,
            "items": items_list
        })
    return result

@app.get("/promotions/{promotion_id}")
def get_promotion_detail(promotion_id: int, branch_id: int = 1, db: Session = Depends(get_db), ctx: AuthContext = Depends(employee(branch="query"))):
    promo = db.query(PromotionDB).filter(PromotionDB.id == promotion_id).first()
    if not promo:
        raise HTTPException(status_code=404, detail="Promoción no encontrada")
    
    b_conf = db.query(PromotionBranchDB).filter(
        PromotionBranchDB.promotion_id == promo.id,
        PromotionBranchDB.branch_id == branch_id
    ).first()

    items_list = []
    for item in promo.items:
        prod = item.product
        prod_config = db.query(BranchProductDB).filter(
            BranchProductDB.branch_id == branch_id,
            BranchProductDB.product_id == prod.id
        ).first()
        normal_price = prod_config.price_per_unit if (prod_config and prod_config.price_per_unit is not None) else prod.price_per_unit

        items_list.append({
            "product_id": prod.id,
            "product_name": prod.name,
            "barcode": prod.barcode,
            "included_qty": item.included_qty,
            "unit_type": item.unit_type,
            "regular_unit_price": normal_price
        })

    return {
        "id": promo.id,
        "name": promo.name,
        "description": promo.description,
        "active": promo.active,
        "branch_id": branch_id,
        "enabled": b_conf.enabled if b_conf else True,
        "promo_price": b_conf.promo_price if b_conf else 0.0,
        "items": items_list
    }

def _validate_promotion_branches(branch_configs: List[PromotionBranchConfigSchema]):
    any_enabled = False
    for b_conf in branch_configs:
        if b_conf.enabled:
            any_enabled = True
            if b_conf.promo_price is None or b_conf.promo_price <= 0:
                raise HTTPException(
                    status_code=422,
                    detail="La sucursal habilitada debe tener un precio promocional mayor a 0."
                )
    if not any_enabled:
        raise HTTPException(
            status_code=422,
            detail="La promoción debe estar habilitada al menos en una sucursal."
        )

@app.post("/promotions")
def create_promotion(payload: PromotionCreateSchema, db: Session = Depends(get_db), ctx: AuthContext = Depends(SUPERADMIN_ONLY)):
    _validate_promotion_branches(payload.branch_configs)

    promo = PromotionDB(
        name=payload.name.strip(),
        description=payload.description,
        active=payload.active,
        created_by=payload.created_by
    )
    db.add(promo)
    db.flush()

    for item in payload.items:
        prod = db.query(ProductDB).filter(ProductDB.id == item.product_id).first()
        if not prod:
            raise HTTPException(status_code=404, detail=f"Producto {item.product_id} no encontrado")
        if item.included_qty <= 0:
            raise HTTPException(status_code=422, detail="La cantidad incluida debe ser mayor a 0")
        
        p_item = PromotionItemDB(
            promotion_id=promo.id,
            product_id=prod.id,
            included_qty=item.included_qty,
            unit_type=item.unit_type,
            sort_order=item.sort_order or 0
        )
        db.add(p_item)

    for b_conf in payload.branch_configs:
        branch = db.query(BranchDB).filter(BranchDB.id == b_conf.branch_id).first()
        if not branch:
            raise HTTPException(status_code=404, detail=f"Sucursal {b_conf.branch_id} no encontrada")
        
        pb = PromotionBranchDB(
            promotion_id=promo.id,
            branch_id=b_conf.branch_id,
            promo_price=b_conf.promo_price if b_conf.enabled else (b_conf.promo_price if b_conf.promo_price is not None else 0.0),
            enabled=b_conf.enabled
        )
        db.add(pb)

    db.commit()
    db.refresh(promo)
    return {"status": "success", "promotion_id": promo.id}

@app.put("/promotions/{promotion_id}")
def update_promotion(promotion_id: int, payload: PromotionUpdateSchema, db: Session = Depends(get_db), ctx: AuthContext = Depends(SUPERADMIN_ONLY)):
    promo = db.query(PromotionDB).filter(PromotionDB.id == promotion_id).first()
    if not promo:
        raise HTTPException(status_code=404, detail="Promoción no encontrada")

    if payload.branch_configs is not None:
        _validate_promotion_branches(payload.branch_configs)

    if payload.name is not None:
        promo.name = payload.name.strip()
    if payload.description is not None:
        promo.description = payload.description
    if payload.active is not None:
        promo.active = payload.active

    if payload.items is not None:
        db.query(PromotionItemDB).filter(PromotionItemDB.promotion_id == promo.id).delete()
        for item in payload.items:
            prod = db.query(ProductDB).filter(ProductDB.id == item.product_id).first()
            if not prod:
                raise HTTPException(status_code=404, detail=f"Producto {item.product_id} no encontrado")
            p_item = PromotionItemDB(
                promotion_id=promo.id,
                product_id=prod.id,
                included_qty=item.included_qty,
                unit_type=item.unit_type,
                sort_order=item.sort_order or 0
            )
            db.add(p_item)

    if payload.branch_configs is not None:
        db.query(PromotionBranchDB).filter(PromotionBranchDB.promotion_id == promo.id).delete()
        for b_conf in payload.branch_configs:
            branch = db.query(BranchDB).filter(BranchDB.id == b_conf.branch_id).first()
            if not branch:
                raise HTTPException(status_code=404, detail=f"Sucursal {b_conf.branch_id} no encontrada")
            pb = PromotionBranchDB(
                promotion_id=promo.id,
                branch_id=b_conf.branch_id,
                promo_price=b_conf.promo_price if b_conf.enabled else (b_conf.promo_price if b_conf.promo_price is not None else 0.0),
                enabled=b_conf.enabled
            )
            db.add(pb)

    db.commit()
    db.refresh(promo)
    return {"status": "success", "promotion_id": promo.id}

@app.delete("/promotions/{promotion_id}")
def delete_promotion(promotion_id: int, db: Session = Depends(get_db), ctx: AuthContext = Depends(SUPERADMIN_ONLY)):
    promo = db.query(PromotionDB).filter(PromotionDB.id == promotion_id).first()
    if not promo:
        raise HTTPException(status_code=404, detail="Promoción no encontrada")
    
    promo.active = False
    db.commit()
    return {"status": "success", "message": "Promoción desactivada correctamente"}

def _calculate_promotion_breakdown(promotion_id: int, branch_id: int, items_input: List[ItemSchema], db: Session):
    promo = db.query(PromotionDB).filter(PromotionDB.id == promotion_id).first()
    if not promo or not promo.active:
        raise HTTPException(status_code=404, detail="Promoción no encontrada o inactiva")

    b_conf = db.query(PromotionBranchDB).filter(
        PromotionBranchDB.promotion_id == promo.id,
        PromotionBranchDB.branch_id == branch_id
    ).first()

    if b_conf and not b_conf.enabled:
        raise HTTPException(status_code=400, detail="La promoción no está habilitada en esta sucursal")

    promo_price = b_conf.promo_price if (b_conf and b_conf.promo_price is not None) else 0.0

    provided_items = {item.product_id: item.quantity for item in items_input}
    required_product_ids = {item.product_id for item in promo.items}

    if set(provided_items.keys()) != required_product_ids:
        raise HTTPException(status_code=422, detail="Los productos provistos no coinciden exactamente con los componentes de la promoción")

    breakdown = []
    total_extra = 0.0
    sum_peso_valor = 0.0
    regular_actual_total = 0.0

    temp_items = []
    for promo_item in promo.items:
        prod_id = promo_item.product_id
        actual_qty = provided_items.get(prod_id, 0.0)
        included_qty = promo_item.included_qty

        if actual_qty <= 0:
            raise HTTPException(status_code=422, detail="La cantidad real debe ser mayor a cero")

        prod_config = db.query(BranchProductDB).filter(
            BranchProductDB.branch_id == branch_id,
            BranchProductDB.product_id == prod_id,
            BranchProductDB.is_available == True
        ).first()
        if not prod_config:
            raise HTTPException(status_code=409, detail=f"El producto ID {prod_id} no está disponible en esta sucursal")

        regular_price = prod_config.price_per_unit if prod_config.price_per_unit is not None else promo_item.product.price_per_unit

        extra_qty = max(0.0, actual_qty - included_qty)
        extra_amount = round(extra_qty * regular_price, 2)
        total_extra += extra_amount

        peso_valor = included_qty * regular_price
        sum_peso_valor += peso_valor

        regular_actual_value = round(actual_qty * regular_price, 2)
        regular_actual_total += regular_actual_value

        temp_items.append({
            "promo_item": promo_item,
            "prod_config": prod_config,
            "actual_qty": actual_qty,
            "included_qty": included_qty,
            "extra_qty": extra_qty,
            "extra_amount": extra_amount,
            "regular_price": regular_price,
            "peso_valor": peso_valor,
            "regular_actual_value": regular_actual_value
        })

    expected_total = round(promo_price + total_extra, 2)
    regular_actual_total = round(regular_actual_total, 2)

    assigned_base_sum = 0.0
    processed = 0
    calculated_items = []

    for t in temp_items:
        processed += 1
        if sum_peso_valor > 0:
            base_asignada = round(promo_price * (t["peso_valor"] / sum_peso_valor), 2)
        else:
            base_asignada = round(promo_price / len(temp_items), 2)

        if processed == len(temp_items):
            diff_centavos = promo_price - (assigned_base_sum + base_asignada)
            base_asignada = round(base_asignada + diff_centavos, 2)

        assigned_base_sum += base_asignada
        revenue_item = round(base_asignada + t["extra_amount"], 2)

        calculated_items.append({
            "product_id": t["promo_item"].product_id,
            "product_name": t["promo_item"].product.name,
            "unit_type": t["promo_item"].unit_type,
            "actual_qty": t["actual_qty"],
            "regular_price": t["regular_price"],
            "extra_amount": t["extra_amount"],
            "base_asignada": base_asignada,
            "revenue_item": revenue_item,
            "regular_actual_value": t["regular_actual_value"]
        })

    return expected_total, calculated_items, regular_actual_total

@app.post("/promotions/{promotion_id}/calculate")
def calculate_promotion(promotion_id: int, branch_id: int = 1, payload: PromotionCalculateRequestSchema = None, db: Session = Depends(get_db), ctx: AuthContext = Depends(employee(caps=("can_caja", "can_preventa"), alt="dueno", branch="query"))):
    items_input = [ItemSchema(product_id=i.product_id, quantity=i.actual_qty) for i in (payload.items if payload else [])]
    expected_total, calculated_items, regular_actual_total = _calculate_promotion_breakdown(promotion_id, branch_id, items_input, db)
    
    promo = db.query(PromotionDB).filter(PromotionDB.id == promotion_id).first()
    b_conf = db.query(PromotionBranchDB).filter(PromotionBranchDB.promotion_id == promo.id, PromotionBranchDB.branch_id == branch_id).first()
    promo_price = b_conf.promo_price if (b_conf and b_conf.promo_price is not None) else 0.0
    total_extra = round(sum(i["extra_amount"] for i in calculated_items), 2)

    breakdown = []
    for c in calculated_items:
        breakdown.append({
            "product_id": c["product_id"],
            "product_name": c["product_name"],
            "actual_qty": c["actual_qty"],
            "unit_type": c["unit_type"],
            "regular_unit_price": c["regular_price"],
            "extra_amount": c["extra_amount"],
            "regular_actual_value": c["regular_actual_value"]
        })

    return {
        "promotion_id": promo.id,
        "promotion_name": promo.name,
        "branch_id": branch_id,
        "promo_price": promo_price,
        "regular_actual_total": regular_actual_total,
        "total_extra": total_extra,
        "final_total": expected_total,
        "items": breakdown
    }

# ==========================================
# FIN SECCIÓN PROMOCIONES
# ==========================================

@app.post("/ingresses")
def create_ingress(payload: IngressCreateSchema, branch_id: int = 1, db: Session = Depends(get_db), ctx: AuthContext = Depends(employee("can_ingreso", branch="query"))):
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
            minimum_stock=None,
            is_available=True,
            is_exclusive=False
        )
        db.add(config)
        db.flush()
    elif not config.is_available:
        # Producto RETIRADO de esta sucursal: nunca se reactiva en silencio.
        if not payload.reactivate_branch_product:
            raise _coded_error(
                409, ERROR_PRODUCT_RETIRED_IN_BRANCH,
                f"{product.name} está retirado de {branch.name}. Confirmá que querés reactivarlo "
                "para registrar este ingreso, o reactivalo desde Stock."
            )
        # Misma regla que /reactivate. Se valida antes de tocar disponibilidad, lotes, ingreso o
        # movimientos: si falla, no queda nada registrado.
        _require_branch_price_for_reactivation(config.price_per_unit, product.name, branch.name)
        config.is_available = True
        db.add(AuditEventDB(
            entity_type="branch_product", entity_id=config.id,
            field_name=f"is_available_branch_{branch_id}_product_{product.id}",
            old_value="False", new_value="True",
            actor=payload.received_by or "Anonimo",
            reason="Reactivado al registrar ingreso (confirmado)"
        ))

    ing,lot=register_ingress(product,payload.supplier,payload.cost_price,payload.quantity,payload.lot_number,payload.expiration_date,payload.received_by,payload.notes,db,branch_id)
    db.commit(); db.refresh(ing)
    return {"status":"success","ingress_id":ing.id,"lot_id":lot.id,"product_id":product.id,"stock":product.stock}

@app.get("/ingresses")
def get_ingresses(product_id: Optional[int]=None, branch_id: int = 1, db: Session=Depends(get_db), ctx: AuthContext = Depends(employee("can_ingreso", alt="dueno", branch="query"))):
    q=db.query(ProductIngressDB).filter(ProductIngressDB.branch_id==branch_id)
    if product_id is not None: q=q.filter(ProductIngressDB.product_id==product_id)
    return q.order_by(ProductIngressDB.id.desc()).limit(200).all()

@app.get("/ingresses/history")
def get_ingresses_history(
    branch_id: int = 1,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db)
, ctx: AuthContext = Depends(employee("can_ingreso", alt="dueno", branch="query"))):
    safe_limit = min(max(limit, 1), 500)
    query = db.query(ProductIngressDB).join(ProductDB, ProductIngressDB.product_id == ProductDB.id).filter(ProductIngressDB.branch_id == branch_id)

    if date_from:
        dt_from_utc = parse_arg_date_filter(date_from, is_end=False)
        query = query.filter(ProductIngressDB.received_at >= dt_from_utc)

    if date_to:
        dt_to_utc = parse_arg_date_filter(date_to, is_end=True)
        query = query.filter(ProductIngressDB.received_at < dt_to_utc)

    if search and search.strip():
        term = search.strip()
        query = query.filter(
            or_(
                ProductDB.name.ilike(f"%{term}%"),
                ProductDB.barcode.ilike(f"%{term}%"),
                ProductIngressDB.supplier.ilike(f"%{term}%"),
                ProductIngressDB.lot_number.ilike(f"%{term}%")
            )
        )

    ingresses = query.order_by(ProductIngressDB.received_at.desc(), ProductIngressDB.id.desc()).limit(safe_limit).all()

    result = []
    for ing in ingresses:
        prod = ing.product
        result.append({
            "ingress_id": ing.id,
            "received_at": utc_to_ar(ing.received_at).isoformat() if ing.received_at else None,
            "received_at_formatted": utc_to_ar(ing.received_at).strftime("%d/%m/%Y %H:%M hs") if ing.received_at else None,
            "product_id": ing.product_id,
            "product_name": prod.name if prod else "Desconocido",
            "barcode": prod.barcode if prod else None,
            "category": prod.category if prod else None,
            "quantity": ing.quantity,
            "unit_type": prod.unit_type if prod else "unid",
            "unit_cost": ing.cost_price,
            "supplier": ing.supplier,
            "lot_code": ing.lot_number,
            "expiration_date": format_iso_to_ddmmyyyy(ing.expiration_date),
            "branch_id": ing.branch_id
        })
    return result

@app.patch("/ingresses/{ingress_id}")
def correct_ingress(
    ingress_id: int,
    payload: IngressCorrectionSchema,
    branch_id: int = 1,
    db: Session=Depends(get_db)
, ctx: AuthContext = Depends(employee("can_edit_records", alt="dueno", branch="query"))):
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
def get_stock_movements(product_id: Optional[int]=None, branch_id: int = 1, db: Session=Depends(get_db), ctx: AuthContext = Depends(owner(branch="query"))):
    # Fase 2.2: solo los movimientos de la sucursal autorizada (antes mezclaba todas).
    q=db.query(StockMovementDB).filter(StockMovementDB.branch_id==branch_id)
    if product_id is not None: q=q.filter(StockMovementDB.product_id==product_id)
    return q.order_by(StockMovementDB.id.desc()).limit(500).all()

@app.post("/products")
def create_product(prod: ProductCreateSchema, db: Session = Depends(get_db), ctx: AuthContext = Depends(MASTERADMIN_ONLY)):
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
        if prod.visual_presentation is not None: existing.visual_presentation=prod.visual_presentation
        if prod.product_image_uri is not None: existing.product_image_uri=prod.product_image_uri
        target=existing
    else:
        target=ProductDB(name=prod.name.strip(),category=prod.category or "Varios",brand=prod.brand,cost_price=0,previous_cost_price=0,price_per_unit=prod.price_per_unit,supplier=prod.supplier,unit_type=prod.unit_type or "unid",stock=0,barcode=barcode,requires_expiration=bool(prod.requires_expiration),replenishment_policy=prod.replenishment_policy or "MRP",visual_presentation=prod.visual_presentation,product_image_uri=prod.product_image_uri,is_active=True)
        db.add(target); db.flush()
    if prod.stock>0: register_ingress(target,prod.supplier,prod.cost_price or 0,prod.stock,prod.lot_number,prod.expiration_date,prod.received_by,prod.notes,db)
    db.commit(); db.refresh(target); return target

@app.put("/products/{product_id}")
def update_product(product_id:int, prod:ProductCreateSchema, db:Session=Depends(get_db), ctx: AuthContext = Depends(employee("can_edit_records", alt="dueno"))):
    x=db.query(ProductDB).filter(ProductDB.id==product_id).first()
    if not x: raise HTTPException(status_code=404,detail="Producto no encontrado")
    barcode=normalize_barcode(prod.barcode)
    if barcode and db.query(ProductDB).filter(ProductDB.barcode==barcode,ProductDB.id!=product_id).first():
        raise HTTPException(status_code=409,detail="EAN ya asociado a otro producto")
    old_price=x.price_per_unit
    changes={
        "name":(x.name,prod.name.strip()),
        "category":(x.category,prod.category),
        "brand":(x.brand,prod.brand),
        "price_per_unit":(x.price_per_unit,prod.price_per_unit),
        "supplier":(x.supplier,prod.supplier),
        "unit_type":(x.unit_type,prod.unit_type),
        "barcode":(x.barcode,barcode),
        "requires_expiration":(x.requires_expiration,prod.requires_expiration if prod.requires_expiration is not None else x.requires_expiration),
        "replenishment_policy":(x.replenishment_policy,prod.replenishment_policy if prod.replenishment_policy is not None else x.replenishment_policy),
        "visual_presentation":(x.visual_presentation,prod.visual_presentation),
        "product_image_uri":(x.product_image_uri,prod.product_image_uri)
    }
    x.name=prod.name.strip(); x.category=prod.category; x.brand=prod.brand; x.price_per_unit=prod.price_per_unit
    x.supplier=prod.supplier; x.unit_type=prod.unit_type; x.barcode=barcode
    if prod.requires_expiration is not None: x.requires_expiration=prod.requires_expiration
    if prod.replenishment_policy is not None: x.replenishment_policy=prod.replenishment_policy
    if prod.visual_presentation is not None: x.visual_presentation=prod.visual_presentation
    if prod.product_image_uri is not None: x.product_image_uri=prod.product_image_uri
    x.is_active=prod.is_active if prod.is_active is not None else x.is_active
    actor=prod.received_by or "Anonimo"
    for field,(old,new) in changes.items():
        if old!=new: db.add(AuditEventDB(entity_type="product",entity_id=x.id,field_name=field,old_value=str(old),new_value=str(new),actor=actor,reason=prod.notes or "Edicion de producto"))
    if old_price!=x.price_per_unit: db.add(PriceHistoryDB(product_id=x.id,old_price=old_price,new_price=x.price_per_unit,changed_by=actor,reason=prod.notes or "Cambio de PVP"))
    db.commit(); db.refresh(x); return x

@app.get("/products/{product_id}/lots")
def get_product_lots(product_id:int,branch_id: int = 1,db:Session=Depends(get_db), ctx: AuthContext = Depends(employee(branch="query"))):
    lots=db.query(ProductLotDB).filter(ProductLotDB.product_id==product_id,ProductLotDB.branch_id==branch_id,ProductLotDB.current_qty>0).order_by(ProductLotDB.expiration_date.is_(None),ProductLotDB.expiration_date.asc(),ProductLotDB.created_at.asc()).all()
    return [{"id":l.id,"lot_number":l.lot_number,"supplier":l.supplier,"cost_price":l.cost_price,"initial_qty":l.initial_qty,"current_qty":l.current_qty,"expiration_date":format_iso_to_ddmmyyyy(l.expiration_date)} for l in lots]

@app.post("/products/{product_id}/audit")
def audit_product_stock(
    product_id: int,
    audit: AuditSchema,
    branch_id: int = 1,
    db: Session = Depends(get_db)
, ctx: AuthContext = Depends(employee("can_stock", branch="query"))):
    x = db.query(ProductDB).filter(ProductDB.id == product_id).first()

    if not x:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    if audit.counted_qty < 0:
        raise HTTPException(status_code=422, detail="Conteo negativo")

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
, ctx: AuthContext = Depends(employee("can_stock", alt="encargado", branch="query"))):
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
def delete_product(product_id:int,db:Session=Depends(get_db), ctx: AuthContext = Depends(SUPERADMIN_ONLY)):
    x=db.query(ProductDB).filter(ProductDB.id==product_id).first()
    if not x: raise HTTPException(status_code=404,detail="No encontrado")
    x.is_active=False; db.commit(); return {"status":"ok"}

@app.post("/presales")
def create_presale(payload: PreSaleCreateSchema, branch_id: int = 1, db: Session = Depends(get_db), ctx: AuthContext = Depends(employee("can_preventa", branch="query"))):
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
def get_pending_presales(branch_id: int = 1, db: Session = Depends(get_db), ctx: AuthContext = Depends(cashier())):
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
, ctx: AuthContext = Depends(cashier())):
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
def get_cash_status(branch_id: int = 1, db: Session = Depends(get_db), ctx: AuthContext = Depends(cashier())):
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
def open_cash(payload: CashOpenSchema, branch_id: int = 1, db: Session = Depends(get_db), ctx: AuthContext = Depends(cashier())):
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

def serialize_sale_ticket(sale: SaleDB):
    db = SessionLocal()
    try:
        items_res = []
        for item in sale.items:
            u_type = item.unit_type
            if not u_type and item.product_id:
                prod = db.query(ProductDB).filter(ProductDB.id == item.product_id).first()
                if prod:
                    u_type = prod.unit_type
            items_res.append({
                "id": item.id,
                "product_id": item.product_id,
                "product_name": item.product_name,
                "quantity": item.quantity,
                "unit_type": u_type,
                "unit_price": item.unit_price,
                "subtotal": round(item.quantity * item.unit_price, 2),
                "is_promotion": getattr(item, "is_promotion", False),
                "promotion_id": getattr(item, "promotion_id", None)
            })
        return {
            "id": sale.id,
            "branch_id": sale.branch_id,
            "presale_id": sale.presale_id,
            "session_id": sale.session_id,
            "created_at": utc_to_ar(sale.created_at).isoformat() if sale.created_at else None,
            "total_amount": sale.total_amount,
            "amount_cash": sale.amount_cash,
            "amount_mp": sale.amount_mp,
            "payment_method": sale.payment_method,
            "cash_received": sale.cash_received,
            "change_amount": sale.change_amount,
            "sold_by": sale.sold_by,
            "items": items_res
        }
    finally:
        db.close()

@app.post("/sales/finalize")
def finalize_sale(payload: FinalizeSaleSchema, branch_id: int = 1, db: Session = Depends(get_db), ctx: AuthContext = Depends(cashier())):
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

    sale=SaleDB(
        branch_id=branch_id,
        presale_id=payload.presale_id,
        session_id=active.id if active else None,
        total_amount=payload.total_amount,
        amount_cash=payload.amount_cash,
        amount_mp=payload.amount_mp,
        payment_method=payload.payment_method,
        sold_by=payload.sold_by,
        cash_received=payload.cash_received,
        change_amount=payload.change_amount
    )
    db.add(sale); db.flush()

    is_promo = bool(payload.is_promotion)
    calculated_promo_items = []

    if is_promo:
        if not payload.promotion_id:
            raise HTTPException(status_code=422, detail="promotion_id es obligatorio para ventas de promoción")
        expected_total, calculated_promo_items, _ = _calculate_promotion_breakdown(payload.promotion_id, branch_id, payload.items, db)
        if abs(payload.total_amount - expected_total) > 0.05:
            raise HTTPException(status_code=422, detail=f"El total de la venta (${payload.total_amount}) no coincide con el total esperado de la promoción (${expected_total})")

    products={}
    for item in payload.items:
        x=db.query(ProductDB).filter(ProductDB.id==item.product_id).first()
        if not x:
            raise HTTPException(status_code=404, detail=f"Producto {item.product_id} no encontrado")
        if item.quantity<=0 or get_branch_stock(db,x.id,branch_id)<item.quantity:
            raise HTTPException(status_code=409, detail=f"Stock insuficiente o cantidad invalida: {x.name}")
        products[item.product_id]=x

    if is_promo:
        promo_map = {ci["product_id"]: ci for ci in calculated_promo_items}
        for item in payload.items:
            x = products[item.product_id]
            cost = consume_stock_fefo(x, item.quantity, "SALE", payload.sold_by or "Anonimo", "Venta Promocion", db, "sale", sale.id, branch_id=branch_id)
            ci = promo_map.get(item.product_id)
            if not ci:
                raise HTTPException(status_code=422, detail=f"El producto {x.name} no pertenece a la promoción seleccionada")
            
            revenue = ci["revenue_item"]
            unit_price = round(revenue / item.quantity, 4) if item.quantity > 0 else 0.0

            db.add(SaleItemDB(
                sale_id=sale.id,
                product_id=x.id,
                product_name=x.name,
                quantity=item.quantity,
                unit_type=x.unit_type,
                unit_price=unit_price,
                unit_cost=cost / item.quantity if item.quantity > 0 else 0.0,
                cogs=cost,
                gross_profit=round(revenue - cost, 2),
                is_promotion=True,
                promotion_id=payload.promotion_id
            ))
    else:
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
            db.add(SaleItemDB(
                sale_id=sale.id,
                product_id=x.id,
                product_name=x.name,
                quantity=item.quantity,
                unit_type=x.unit_type,
                unit_price=unit_price,
                unit_cost=cost/item.quantity if item.quantity > 0 else 0,
                cogs=cost,
                gross_profit=round(revenue-cost, 2),
                is_promotion=False,
                promotion_id=None
            ))

    if presale:
        presale.status = "COMPLETADA"
    db.commit()
    db.refresh(sale)
    return {
        "status": "success",
        "sale_id": sale.id,
        "branch_id": sale.branch_id,
        "created_at": utc_to_ar(sale.created_at).isoformat() if sale.created_at else None,
        "total_amount": sale.total_amount,
        "amount_cash": sale.amount_cash,
        "amount_mp": sale.amount_mp,
        "payment_method": sale.payment_method,
        "cash_received": sale.cash_received,
        "change_amount": sale.change_amount,
        "sold_by": sale.sold_by
    }

@app.get("/sales/history")
def get_sales_history(
    branch_id: int = 1,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db)
, ctx: AuthContext = Depends(cashier(alt="dueno"))):
    safe_limit = min(max(limit, 1), 500)
    query = db.query(SaleDB).filter(SaleDB.branch_id == branch_id)

    if date_from:
        dt_from_utc = parse_arg_date_filter(date_from, is_end=False)
        query = query.filter(SaleDB.created_at >= dt_from_utc)

    if date_to:
        dt_to_utc = parse_arg_date_filter(date_to, is_end=True)
        query = query.filter(SaleDB.created_at < dt_to_utc)

    if search and search.strip():
        term = search.strip()
        subq = db.query(SaleItemDB.sale_id).filter(SaleItemDB.product_name.ilike(f"%{term}%")).subquery()
        if term.isdigit():
            sale_id_val = int(term)
            query = query.filter(
                or_(
                    SaleDB.id == sale_id_val,
                    SaleDB.sold_by.ilike(f"%{term}%"),
                    SaleDB.id.in_(subq)
                )
            )
        else:
            query = query.filter(
                or_(
                    SaleDB.sold_by.ilike(f"%{term}%"),
                    SaleDB.id.in_(subq)
                )
            )

    sales = query.order_by(SaleDB.created_at.desc(), SaleDB.id.desc()).limit(safe_limit).all()

    if not sales:
        return []

    sale_ids = [s.id for s in sales]
    item_counts_raw = db.query(SaleItemDB.sale_id, func.count(SaleItemDB.id)).filter(SaleItemDB.sale_id.in_(sale_ids)).group_by(SaleItemDB.sale_id).all()
    counts_map = {row[0]: row[1] for row in item_counts_raw}

    result = []
    for s in sales:
        result.append({
            "id": s.id,
            "branch_id": s.branch_id,
            "created_at": utc_to_ar(s.created_at).isoformat() if s.created_at else None,
            "total_amount": s.total_amount,
            "payment_method": s.payment_method,
            "amount_cash": s.amount_cash,
            "amount_mp": s.amount_mp,
            "cash_received": s.cash_received,
            "change_amount": s.change_amount,
            "sold_by": s.sold_by,
            "items_count": counts_map.get(s.id, 0)
        })
    return result

@app.get("/sales/{sale_id}")
def get_sale_detail(
    sale_id: int,
    branch_id: int = 1,
    db: Session = Depends(get_db)
, ctx: AuthContext = Depends(cashier(alt="dueno"))):
    sale = db.query(SaleDB).filter(
        SaleDB.id == sale_id,
        SaleDB.branch_id == branch_id
    ).first()

    if not sale:
        raise HTTPException(status_code=404, detail="Venta no encontrada en esta sucursal")

    return serialize_sale_ticket(sale)

def cash_category_key(category: Optional[str]) -> str:
    """Forma canónica para autorizar: mayúsculas, sin tildes, espacios y guiones como '_'."""
    import unicodedata
    raw = unicodedata.normalize("NFKD", (category or "").strip().upper())
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return "_".join(raw.replace("-", " ").split())


# U17: pagos a proveedor/mercadería y retiros del dueño (mismos alias que el KPI y Feria).
PRIVILEGED_OUTFLOW_CATEGORIES = frozenset({
    "PROVEEDOR", "PROVEEDORES", "PAGO_PROVEEDOR", "MERCADERIA", "COMPRA_MERCADERIA",
    "RETIRO_DUENO", "OWNER_WITHDRAWAL",
})


@app.post("/cash/movements")
def create_cash_movement(payload: CashMovementCreateSchema, branch_id: int = 1, db: Session = Depends(get_db), ctx: AuthContext = Depends(cashier())):
    if payload.amount<=0:
        raise HTTPException(status_code=422, detail="Monto invalido")
    movement=payload.movement_type.strip().upper()
    if movement not in ("IN","OUT"):
        raise HTTPException(status_code=422, detail="movement_type debe ser IN u OUT")
    # U17: el tipo de egreso se evalúa en backend con la categoría normalizada, no con lo que diga la app.
    if movement == "OUT" and cash_category_key(payload.category) in PRIVILEGED_OUTFLOW_CATEGORIES \
            and not has_capability(ctx.user, auth_core.PRIVILEGED_OUTFLOWS_CAPABILITY):
        raise _auth_error(403, ERROR_FORBIDDEN_CAPABILITY,
                          "Los pagos a proveedores y retiros del dueño requieren Dueño o un Encargado autorizado.")
    active=db.query(CashSessionDB).filter(CashSessionDB.is_open==True,CashSessionDB.branch_id==branch_id).first()
    row=CashMovementDB(branch_id=branch_id, session_id=active.id if active else None, movement_type=movement, category=payload.category.strip().upper(), amount=payload.amount, concept=payload.concept, actor=payload.actor, supplier=payload.supplier, employee_email=payload.employee_email, notes=payload.notes)
    db.add(row); db.commit(); db.refresh(row)
    return row

@app.get("/cash/movements")
def list_cash_movements(session_id: Optional[int]=None, branch_id: int = 1, db: Session=Depends(get_db), ctx: AuthContext = Depends(cashier())):
    q=db.query(CashMovementDB).filter(CashMovementDB.branch_id == branch_id)
    if session_id is not None:
        q=q.filter(CashMovementDB.session_id==session_id)
    return q.order_by(CashMovementDB.id.desc()).limit(500).all()

@app.post("/stock/losses")
def create_stock_loss(payload: StockLossSchema, branch_id: int = 1, db: Session=Depends(get_db), ctx: AuthContext = Depends(employee("can_stock", branch="query"))):
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
def close_cash(payload: CashCloseSchema, branch_id: int = 1, db: Session = Depends(get_db), ctx: AuthContext = Depends(cashier())):
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
, ctx: AuthContext = Depends(employee("can_verificacion", alt="dueno", branch="query"))):
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
def update_replenishment_policy(product_id: int, payload: ReplenishmentPolicySchema, db: Session = Depends(get_db), ctx: AuthContext = Depends(employee("can_mrp", alt="dueno"))):
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
, ctx: AuthContext = Depends(employee("can_mrp", alt="dueno", branch="query"))):
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
def update_alert_state(alert_key: str, payload: AlertActionSchema, db: Session = Depends(get_db), ctx: AuthContext = Depends(employee("can_alertas", alt="dueno"))):
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
def get_alert_history(limit: int = 200, db: Session = Depends(get_db), ctx: AuthContext = Depends(employee("can_alertas", alt="dueno"))):
    rows=db.query(AuditEventDB).filter(AuditEventDB.entity_type=="alert").order_by(AuditEventDB.created_at.desc()).limit(min(max(limit,1),500)).all()
    return [{"id":x.id,"alert_state_id":x.entity_id,"old_state":x.old_value,"new_state":x.new_value,"actor":x.actor,"note":x.reason,"created_at":x.created_at} for x in rows]

@app.get("/alerts")
def get_system_alerts(branch_id: int = 1, db: Session = Depends(get_db), ctx: AuthContext = Depends(employee("can_alertas", alt="dueno", branch="query"))):
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
        min_stock = config.minimum_stock

        if policy=="MRP":
            if branch_stock<=0:
                append_alert(alerts,f"stock-b{branch_id}-{product.id}-zero","STOCK","CRITICAL",f"Producto agotado: {product.name}",f"Stock 0 {product.unit_type}.",db,product.id)
            elif min_stock is not None and min_stock > 0 and branch_stock <= min_stock:
                append_alert(alerts,f"stock-b{branch_id}-{product.id}-low","STOCK","IMPORTANT",f"Stock bajo: {product.name}",f"Quedan {branch_stock} {product.unit_type}. Mínimo configurado: {min_stock} {product.unit_type}.",db,product.id)

        branch_price=config.price_per_unit if config.price_per_unit is not None else product.price_per_unit
        if (product.cost_price or 0)>0 and branch_price<=product.cost_price:
            signature=f"b{branch_id}-{round(product.cost_price,2)}-{round(branch_price,2)}"
            append_alert(alerts,f"price-{product.id}-{signature}","PRICE","CRITICAL",f"PVP debajo del costo: {product.name}",f"Costo {product.cost_price}; PVP {branch_price}.",db,product.id,actions=["SEEN","SNOOZED","DISMISSED"])

    order={"CRITICAL":0,"IMPORTANT":1,"INFO":2}
    return sorted(alerts,key=lambda x:order.get(x["level"],9))


# =====================================================================
# OBJETIVO DE JORNADA — FERIA ("¿Conviene abrir?")
# El cálculo vive en fair_planning.py; acá solo se leen datos y se persiste.
# =====================================================================

FAIR_BRANCH_ID = 2
DAY_PLAN_EDITABLE_STATUSES = ("BORRADOR", "PLANIFICADA")
DAY_PLAN_STATUSES = ("BORRADOR", "PLANIFICADA", "ABIERTA", "CERRADA", "CANCELADA")
FAIR_MONEY_CONFIG_FIELDS = ("stall_cost_per_day", "staff_cost_per_person_day", "transport_outbound", "transport_return")
FAIR_PCT_CONFIG_FIELDS = ("green_max_stock_pct", "yellow_max_stock_pct", "safety_margin_pct", "incomplete_data_tolerance_pct")

class FairConfigUpdateSchema(BaseModel):
    stall_cost_per_day: Optional[float] = None
    staff_count: Optional[int] = None
    staff_cost_per_person_day: Optional[float] = None
    transport_outbound: Optional[float] = None
    transport_return: Optional[float] = None
    green_max_stock_pct: Optional[float] = None
    yellow_max_stock_pct: Optional[float] = None
    safety_margin_pct: Optional[float] = None
    incomplete_data_tolerance_pct: Optional[float] = None
    comparable_days_count: Optional[int] = None
    minimum_comparable_days: Optional[int] = None
    updated_by: Optional[str] = None
    notes: Optional[str] = None

class DayPlanInputSchema(BaseModel):
    branch_id: int = FAIR_BRANCH_ID
    plan_type: str
    start_date: str
    end_date: Optional[str] = None
    days_count: Optional[int] = None
    stall_total: Optional[float] = None
    staff_total: Optional[float] = None
    transport_outbound: Optional[float] = None
    transport_return: Optional[float] = None
    other_expenses: float = 0.0
    debt_target: float = 0.0
    profit_target: float = 0.0
    capital_available: float = 0.0
    manual_expected_sales: Optional[float] = None
    status: Optional[str] = "BORRADOR"
    notes: Optional[str] = None
    created_by: Optional[str] = None

class DayPlanUpdateSchema(BaseModel):
    plan_type: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    days_count: Optional[int] = None
    stall_total: Optional[float] = None
    staff_total: Optional[float] = None
    transport_outbound: Optional[float] = None
    transport_return: Optional[float] = None
    other_expenses: Optional[float] = None
    debt_target: Optional[float] = None
    profit_target: Optional[float] = None
    capital_available: Optional[float] = None
    manual_expected_sales: Optional[float] = None
    clear_manual_expected_sales: bool = False
    notes: Optional[str] = None
    updated_by: Optional[str] = None

class DayPlanRecalculateSchema(BaseModel):
    actor: Optional[str] = None

class DayPlanStatusSchema(BaseModel):
    status: str
    actor: Optional[str] = None
    reason: Optional[str] = None


def _schema_dump(model: BaseModel) -> dict:
    # Compatible con Pydantic v1 y v2.
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def _require_fair_branch(branch_id: int, db: Session):
    if branch_id != FAIR_BRANCH_ID:
        raise HTTPException(status_code=422, detail="El Objetivo de Jornada solo está disponible para Feria Damyale (branch_id=2).")
    branch = db.query(BranchDB).filter(BranchDB.id == branch_id, BranchDB.is_active == True).first()
    if not branch:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada")


def _fair_config_row(db: Session, branch_id: int):
    return db.query(FairCostConfigDB).filter(
        FairCostConfigDB.branch_id == branch_id, FairCostConfigDB.is_current == True
    ).order_by(FairCostConfigDB.version.desc()).first()


def _fair_config_values(row) -> dict:
    if row is None:
        values = dict(fair_planning.DEFAULT_FAIR_CONFIG)
        values.update({"version": None, "source": "default", "config_id": None})
        return values
    values = {k: getattr(row, k) for k in fair_planning.DEFAULT_FAIR_CONFIG}
    for k in FAIR_MONEY_CONFIG_FIELDS + FAIR_PCT_CONFIG_FIELDS:
        values[k] = D(values[k])
    values.update({"version": row.version, "source": "saved", "config_id": row.id})
    return values


def _fair_config_out(branch_id: int, values: dict, row=None) -> dict:
    out = {"branch_id": branch_id, "version": values["version"], "source": values["source"]}
    for k in fair_planning.DEFAULT_FAIR_CONFIG:
        v = values[k]
        out[k] = float(v) if isinstance(v, fair_planning.Decimal) else v
    weekend = fair_planning.operating_costs({
        "stall_total": D(values["stall_cost_per_day"]) * 2,
        "staff_total": D(values["staff_cost_per_person_day"]) * values["staff_count"] * 2,
        "transport_outbound": values["transport_outbound"], "transport_return": values["transport_return"],
    })
    out["weekend_operating_cost"] = fair_planning.money(weekend["opex"])
    out["created_at"] = row.created_at.isoformat() if row is not None and row.created_at else None
    out["created_by"] = row.created_by if row is not None else None
    return out


def _validate_fair_config(values: dict):
    for k in FAIR_MONEY_CONFIG_FIELDS:
        v = D(values[k])
        if not v.is_finite() or v < 0:
            raise HTTPException(status_code=422, detail=f"{k} debe ser un importe mayor o igual a 0")
    if not isinstance(values["staff_count"], int) or not 0 <= values["staff_count"] <= 100:
        raise HTTPException(status_code=422, detail="staff_count debe ser un entero entre 0 y 100")
    for k in FAIR_PCT_CONFIG_FIELDS:
        v = D(values[k])
        if not v.is_finite() or v < 0 or v > 1:
            raise HTTPException(status_code=422, detail=f"{k} debe estar entre 0 y 1")
    if D(values["green_max_stock_pct"]) <= 0:
        raise HTTPException(status_code=422, detail="green_max_stock_pct debe ser mayor a 0")
    if D(values["green_max_stock_pct"]) >= D(values["yellow_max_stock_pct"]):
        raise HTTPException(status_code=422, detail="green_max_stock_pct debe ser menor que yellow_max_stock_pct")
    if not isinstance(values["comparable_days_count"], int) or not 1 <= values["comparable_days_count"] <= 52:
        raise HTTPException(status_code=422, detail="comparable_days_count debe estar entre 1 y 52")
    if not isinstance(values["minimum_comparable_days"], int) or not 1 <= values["minimum_comparable_days"] <= values["comparable_days_count"]:
        raise HTTPException(status_code=422, detail="minimum_comparable_days debe estar entre 1 y comparable_days_count")


@app.get("/branches/{branch_id}/fair-config")
def get_fair_config(branch_id: int, db: Session = Depends(get_db), ctx: AuthContext = Depends(owner(branch="path"))):
    _require_fair_branch(branch_id, db)
    row = _fair_config_row(db, branch_id)
    return _fair_config_out(branch_id, _fair_config_values(row), row)


@app.put("/branches/{branch_id}/fair-config")
def update_fair_config(branch_id: int, payload: FairConfigUpdateSchema, db: Session = Depends(get_db), ctx: AuthContext = Depends(owner(branch="path"))):
    _require_fair_branch(branch_id, db)
    current = _fair_config_values(_fair_config_row(db, branch_id))
    values = {k: current[k] for k in fair_planning.DEFAULT_FAIR_CONFIG}
    for k in fair_planning.DEFAULT_FAIR_CONFIG:
        v = getattr(payload, k)
        if v is not None:
            values[k] = v
    _validate_fair_config(values)
    last_version = db.query(func.max(FairCostConfigDB.version)).filter(FairCostConfigDB.branch_id == branch_id).scalar() or 0
    # Versionado: la configuración anterior queda guardada; los planes conservan su copia.
    db.query(FairCostConfigDB).filter(FairCostConfigDB.branch_id == branch_id, FairCostConfigDB.is_current == True).update({"is_current": False})
    row = FairCostConfigDB(
        branch_id=branch_id, version=last_version + 1, is_current=True,
        created_by=payload.updated_by, notes=payload.notes,
        **{k: (to_cents(values[k]) if k in FAIR_MONEY_CONFIG_FIELDS
               else float(D(values[k])) if k in FAIR_PCT_CONFIG_FIELDS else values[k])
           for k in fair_planning.DEFAULT_FAIR_CONFIG}
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _fair_config_out(branch_id, _fair_config_values(row), row)


def _parse_plan_date(value: Optional[str], field: str) -> Optional[datetime.date]:
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    raise HTTPException(status_code=422, detail=f"{field} inválida. Usá DD/MM/AAAA.")


def _money_input(value, field: str, required: bool = False):
    if value is None:
        if required:
            raise HTTPException(status_code=422, detail=f"{field} es obligatorio")
        return None
    v = D(value)
    if not v.is_finite() or v < 0:
        raise HTTPException(status_code=422, detail=f"{field} debe ser un importe mayor o igual a 0")
    return to_cents(v)


def _config_for_plan(db: Session, plan) -> dict:
    """Configuración con la que se creó el plan (nunca la actual)."""
    row = db.query(FairCostConfigDB).filter(FairCostConfigDB.id == plan.config_id).first() if plan.config_id else None
    return _fair_config_values(row)


def _resolve_plan_inputs(data: dict, config: dict) -> dict:
    """Normaliza y valida las entradas de un plan. data contiene valores ya existentes o nuevos."""
    plan_type = str(data.get("plan_type") or "").strip().upper()
    if plan_type not in fair_planning.PLAN_TYPES:
        raise HTTPException(status_code=422, detail=f"plan_type inválido. Opciones: {', '.join(fair_planning.PLAN_TYPES)}")
    start = _parse_plan_date(data.get("start_date"), "start_date")
    if start is None:
        raise HTTPException(status_code=422, detail="start_date es obligatoria")
    end = _parse_plan_date(data.get("end_date"), "end_date")

    if plan_type == "SABADO":
        if start.weekday() != 5:
            raise HTTPException(status_code=422, detail="Para SABADO la fecha debe ser un sábado")
        end, days = start, 1
    elif plan_type == "DOMINGO":
        if start.weekday() != 6:
            raise HTTPException(status_code=422, detail="Para DOMINGO la fecha debe ser un domingo")
        end, days = start, 1
    elif plan_type == "FIN_DE_SEMANA":
        if start.weekday() != 5:
            raise HTTPException(status_code=422, detail="FIN_DE_SEMANA debe comenzar un sábado")
        expected_end = start + datetime.timedelta(days=1)
        if end is not None and end != expected_end:
            raise HTTPException(status_code=422, detail="FIN_DE_SEMANA debe terminar el domingo siguiente")
        end, days = expected_end, 2
    elif plan_type in ("FERIADO", "EXTRAORDINARIA"):
        if end is not None and end != start:
            raise HTTPException(status_code=422, detail=f"{plan_type} es de un día; para varios días usá PERSONALIZADA")
        end, days = start, 1
    else:  # PERSONALIZADA
        if end is None:
            raise HTTPException(status_code=422, detail="PERSONALIZADA requiere end_date")
        if end < start:
            raise HTTPException(status_code=422, detail="end_date no puede ser anterior a start_date")
        span = (end - start).days + 1
        if span > 31:
            raise HTTPException(status_code=422, detail="Una jornada PERSONALIZADA no puede superar 31 días")
        days = data.get("days_count") or span
        if not isinstance(days, int) or not 1 <= days <= span:
            raise HTTPException(status_code=422, detail="days_count debe estar entre 1 y la cantidad de días del rango")

    stall = _money_input(data.get("stall_total"), "stall_total")
    staff = _money_input(data.get("staff_total"), "staff_total")
    if stall is None:
        stall = to_cents(D(config["stall_cost_per_day"]) * days)
    if staff is None:
        staff = to_cents(D(config["staff_cost_per_person_day"]) * config["staff_count"] * days)

    t_out = _money_input(data.get("transport_outbound"), "transport_outbound")
    t_ret = _money_input(data.get("transport_return"), "transport_return")
    if plan_type == "FIN_DE_SEMANA":
        t_out = to_cents(config["transport_outbound"]) if t_out is None else t_out
        t_ret = to_cents(config["transport_return"]) if t_ret is None else t_ret
    elif t_out is None or t_ret is None:
        # Para jornadas individuales el transporte se define al crear el plan (puede ser 0).
        raise HTTPException(status_code=422, detail="Indicá transport_outbound y transport_return para este tipo de jornada (pueden ser 0).")

    manual = _money_input(data.get("manual_expected_sales"), "manual_expected_sales")
    return {
        "plan_type": plan_type, "start_date": start, "end_date": end, "days_count": days,
        "stall_total": stall, "staff_total": staff,
        "transport_outbound": t_out, "transport_return": t_ret, "transport_total": t_out + t_ret,
        "other_expenses": _money_input(data.get("other_expenses") or 0, "other_expenses"),
        "debt_target": _money_input(data.get("debt_target") or 0, "debt_target"),
        "profit_target": _money_input(data.get("profit_target") or 0, "profit_target"),
        "capital_available": _money_input(data.get("capital_available") or 0, "capital_available"),
        "manual_expected_sales": manual,
    }


def _thresholds_from(source) -> dict:
    if isinstance(source, dict):
        return {k: source[k] for k in fair_planning.THRESHOLD_KEYS}
    return {k: getattr(source, k) for k in fair_planning.THRESHOLD_KEYS}


def _load_fair_data(db: Session, branch_id: int, before: datetime.date, comparable_days_count: int) -> dict:
    """Lee stock (lotes de la sucursal), precios de Feria, historial y costos de reposición."""
    lot_rows = db.query(ProductLotDB).filter(ProductLotDB.branch_id == branch_id, ProductLotDB.current_qty > 0).all()
    lots = [{"lot_id": l.id, "product_id": l.product_id, "qty": l.current_qty, "cost_price": l.cost_price} for l in lot_rows]
    product_ids = sorted({l["product_id"] for l in lots})
    products = {}
    if product_ids:
        prods = {p.id: p for p in db.query(ProductDB).filter(ProductDB.id.in_(product_ids)).all()}
        configs = {c.product_id: c for c in db.query(BranchProductDB).filter(
            BranchProductDB.branch_id == branch_id, BranchProductDB.product_id.in_(product_ids)).all()}
        for pid in product_ids:
            p, c = prods.get(pid), configs.get(pid)
            products[pid] = {
                "name": p.name if p else None, "unit_type": p.unit_type if p else None,
                "product_active": bool(p and p.is_active),
                "branch_available": bool(c and c.is_available),
                "branch_price": c.price_per_unit if c is not None else None,
                "base_price": p.price_per_unit if p else None,
            }

    cutoff_utc = ar_to_utc(datetime.datetime(before.year, before.month, before.day))
    sales = db.query(SaleDB).filter(SaleDB.branch_id == branch_id, SaleDB.created_at < cutoff_utc).all()
    daily = {}
    item_revenue_by_day = {}
    for sale in sales:
        day = utc_to_ar(sale.created_at).date()
        d = daily.setdefault(day, {"date": day, "revenue": D(0), "gross_profit": D(0)})
        d["revenue"] += D(sale.total_amount)
        per_day = item_revenue_by_day.setdefault(day, {})
        for item in sale.items:
            d["gross_profit"] += D(item.gross_profit)
            per_day[item.product_id] = per_day.get(item.product_id, D(0)) + D(item.unit_price) * D(item.quantity)
    mix_days = sorted(item_revenue_by_day, reverse=True)[:comparable_days_count]
    product_revenue = {}
    for day in mix_days:
        for pid, rev in item_revenue_by_day[day].items():
            product_revenue[pid] = product_revenue.get(pid, D(0)) + rev

    replacement_costs = {}
    if product_ids:
        ingresses = db.query(ProductIngressDB).filter(
            ProductIngressDB.branch_id == branch_id, ProductIngressDB.product_id.in_(product_ids),
            ProductIngressDB.cost_price > 0, ProductIngressDB.quantity > 0
        ).order_by(ProductIngressDB.received_at.desc(), ProductIngressDB.id.desc()).all()
        for ing in ingresses:
            replacement_costs.setdefault(ing.product_id, D(ing.cost_price))

    return {"lots": lots, "products": products, "daily_sales": list(daily.values()),
            "product_revenue": product_revenue, "history_days_count": len(mix_days),
            "replacement_costs": replacement_costs}


def _plan_calc_dict(resolved: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, datetime.date) else v) for k, v in resolved.items()}


def _compute_for(db: Session, branch_id: int, resolved: dict, thresholds: dict):
    data = _load_fair_data(db, branch_id, resolved["start_date"], int(thresholds["comparable_days_count"]))
    result = fair_planning.compute_day_plan(
        _plan_calc_dict(resolved), thresholds, data["lots"], data["products"], data["daily_sales"],
        data["product_revenue"], data["history_days_count"], data["replacement_costs"])
    inputs = {
        "branch_id": branch_id,
        "plan": {k: (float(v) if isinstance(v, fair_planning.Decimal) else v) for k, v in _plan_calc_dict(resolved).items()},
        "thresholds": {k: (float(v) if isinstance(v, fair_planning.Decimal) else v) for k, v in thresholds.items()},
        "data_sources": {
            "lots_considered": len(data["lots"]),
            "history_sale_days": len(data["daily_sales"]),
            "mix_history_days": data["history_days_count"],
            "replacement_costs": {str(k): float(v) for k, v in data["replacement_costs"].items()},
            "history_cutoff_ar_date": resolved["start_date"].isoformat(),
        },
    }
    return inputs, result


def _fmt_ddmmyyyy(iso: Optional[str]) -> Optional[str]:
    if not iso:
        return None
    return datetime.date.fromisoformat(iso).strftime("%d/%m/%Y")


def _plan_out(plan) -> dict:
    # El resultado final de la jornada se entrega por /day-plans/{id}/live, no en cada listado.
    out = {c.name: getattr(plan, c.name) for c in DayPlanDB.__table__.columns if c.name != "final_results_json"}
    for k, v in out.items():
        if isinstance(v, Decimal):
            out[k] = fair_planning.money(v)
    out["start_date_display"] = _fmt_ddmmyyyy(plan.start_date)
    out["end_date_display"] = _fmt_ddmmyyyy(plan.end_date)
    for k in ("created_at", "updated_at"):
        out[k] = out[k].isoformat() if out[k] else None
    for k in ("opened_at", "closed_at"):
        out[k] = utc_to_ar(out[k]).isoformat() if out[k] else None
    opex = D(plan.stall_total) + D(plan.staff_total) + D(plan.transport_total) + D(plan.other_expenses)
    out["operating_cost_total"] = fair_planning.money(opex)
    tg = fair_planning.targets(opex, D(plan.debt_target), D(plan.profit_target))
    out["objective_equilibrium"] = fair_planning.money(tg["g_eq"])
    out["objective_with_debt"] = fair_planning.money(tg["g_debt"])
    out["objective_total"] = fair_planning.money(tg["g_obj"])
    out["has_final_results"] = plan.final_results_json is not None
    out["can_open_today"] = _plan_can_open_on(plan, utc_to_ar(_fair_live_utc_now()).date())
    return out


def _snapshot_summary(s) -> dict:
    return {"id": s.id, "computed_at": s.computed_at.isoformat() if s.computed_at else None,
            "calc_version": s.calc_version, "economic_verdict": s.economic_verdict,
            "commercial_verdict": s.commercial_verdict, "computed_by": s.computed_by}


def _save_snapshot(db: Session, plan, inputs: dict, result: dict, actor: Optional[str]):
    snap = DayPlanSnapshotDB(
        plan_id=plan.id, calc_version=fair_planning.CALC_VERSION,
        inputs_json=json.dumps(inputs, ensure_ascii=False), results_json=json.dumps(result, ensure_ascii=False),
        economic_verdict=result["economic"]["verdict"], commercial_verdict=result["commercial"]["verdict"],
        computed_by=actor)
    db.add(snap)
    return snap


def _apply_resolved(plan, resolved: dict):
    for k, v in resolved.items():
        if isinstance(v, datetime.date):
            v = v.isoformat()
        elif isinstance(v, Decimal):
            v = to_cents(v)
        setattr(plan, k, v)


def _get_plan_or_404(db: Session, plan_id: int):
    plan = db.query(DayPlanDB).filter(DayPlanDB.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan de jornada no encontrado")
    return plan


def _plan_detail(db: Session, plan) -> dict:
    snaps = db.query(DayPlanSnapshotDB).filter(DayPlanSnapshotDB.plan_id == plan.id).order_by(DayPlanSnapshotDB.id.desc()).all()
    latest = snaps[0] if snaps else None
    return {
        "plan": _plan_out(plan),
        "latest_snapshot": ({**_snapshot_summary(latest), "results": json.loads(latest.results_json),
                             "inputs": json.loads(latest.inputs_json)} if latest else None),
        "snapshots": [_snapshot_summary(s) for s in snaps],
    }


@app.post("/day-plans/preview")
def preview_day_plan(payload: DayPlanInputSchema, db: Session = Depends(get_db), ctx: AuthContext = Depends(owner())):
    require_branch_access(db, ctx, payload.branch_id)
    _require_fair_branch(payload.branch_id, db)
    config = _fair_config_values(_fair_config_row(db, payload.branch_id))
    resolved = _resolve_plan_inputs(_schema_dump(payload), config)
    thresholds = _thresholds_from(config)
    inputs, result = _compute_for(db, payload.branch_id, resolved, thresholds)
    # Preview no persiste nada.
    return {"persisted": False, "config_version": config["version"], "inputs": inputs, "results": result}


@app.post("/day-plans")
def create_day_plan(payload: DayPlanInputSchema, db: Session = Depends(get_db), ctx: AuthContext = Depends(owner())):
    require_branch_access(db, ctx, payload.branch_id)
    _require_fair_branch(payload.branch_id, db)
    status_value = (payload.status or "BORRADOR").strip().upper()
    if status_value not in DAY_PLAN_EDITABLE_STATUSES:
        raise HTTPException(status_code=422, detail="Un plan nuevo solo puede crearse como BORRADOR o PLANIFICADA")
    config = _fair_config_values(_fair_config_row(db, payload.branch_id))
    resolved = _resolve_plan_inputs(_schema_dump(payload), config)
    thresholds = _thresholds_from(config)
    inputs, result = _compute_for(db, payload.branch_id, resolved, thresholds)

    now = datetime.datetime.utcnow()
    plan = DayPlanDB(branch_id=payload.branch_id, status=status_value, notes=payload.notes,
                     created_by=payload.created_by, updated_by=payload.created_by,
                     config_id=config["config_id"], config_version=config["version"],
                     created_at=now, updated_at=now,
                     **{k: (float(v) if isinstance(v, fair_planning.Decimal) else v) for k, v in thresholds.items()})
    _apply_resolved(plan, resolved)
    db.add(plan)
    db.flush()
    _save_snapshot(db, plan, inputs, result, payload.created_by)
    db.commit()
    db.refresh(plan)
    return _plan_detail(db, plan)


@app.get("/day-plans")
def list_day_plans(branch_id: int = FAIR_BRANCH_ID, status: Optional[str] = None, db: Session = Depends(get_db), ctx: AuthContext = Depends(owner(branch="query", default=FAIR_BRANCH_ID))):
    _require_fair_branch(branch_id, db)
    q = db.query(DayPlanDB).filter(DayPlanDB.branch_id == branch_id)
    if status:
        q = q.filter(DayPlanDB.status == status.strip().upper())
    plans = q.order_by(DayPlanDB.start_date.desc(), DayPlanDB.id.desc()).all()
    result = []
    for plan in plans:
        latest = db.query(DayPlanSnapshotDB).filter(DayPlanSnapshotDB.plan_id == plan.id).order_by(DayPlanSnapshotDB.id.desc()).first()
        item = _plan_out(plan)
        item["latest_snapshot"] = _snapshot_summary(latest) if latest else None
        if latest:
            item["latest_snapshot"]["headline"] = json.loads(latest.results_json).get("headline")
        item["session_summary"] = _session_summary(db, plan)
        result.append(item)
    return result


@app.get("/day-plans/open")
def get_open_day_plan(branch_id: int = FAIR_BRANCH_ID, db: Session = Depends(get_db), ctx: AuthContext = Depends(owner(branch="query", default=FAIR_BRANCH_ID))):
    """Jornada ABIERTA de la sucursal (como máximo una)."""
    _require_fair_branch(branch_id, db)
    plan = _open_fair_session(db, branch_id)
    return {"open": plan is not None, "plan": _plan_out(plan) if plan else None}


@app.get("/day-plans/{plan_id}")
def get_day_plan(plan_id: int, db: Session = Depends(get_db), ctx: AuthContext = Depends(owner(branch="day_plan"))):
    return _plan_detail(db, _get_plan_or_404(db, plan_id))


@app.put("/day-plans/{plan_id}")
def update_day_plan(plan_id: int, payload: DayPlanUpdateSchema, db: Session = Depends(get_db), ctx: AuthContext = Depends(owner(branch="day_plan"))):
    plan = _get_plan_or_404(db, plan_id)
    if plan.status not in DAY_PLAN_EDITABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"No se puede editar un plan en estado {plan.status}")
    changes = {k: v for k, v in _schema_dump(payload).items()
               if v is not None and k not in ("updated_by", "notes", "clear_manual_expected_sales")}
    new_type = (changes.get("plan_type") or plan.plan_type).strip().upper()
    type_changed = new_type != plan.plan_type
    dates_changed = any(k in changes for k in ("start_date", "end_date", "days_count")) or type_changed

    data = {
        "plan_type": plan.plan_type, "start_date": plan.start_date, "end_date": plan.end_date,
        "days_count": plan.days_count,
        # Si cambian tipo/fechas y no se informan, puesto y personal se recalculan con la config ORIGINAL del plan.
        "stall_total": None if dates_changed else plan.stall_total,
        "staff_total": None if dates_changed else plan.staff_total,
        # Si cambia el tipo de jornada, el transporte no se arrastra en silencio.
        "transport_outbound": None if type_changed else plan.transport_outbound,
        "transport_return": None if type_changed else plan.transport_return,
        "other_expenses": plan.other_expenses, "debt_target": plan.debt_target,
        "profit_target": plan.profit_target, "capital_available": plan.capital_available,
        "manual_expected_sales": None if payload.clear_manual_expected_sales else plan.manual_expected_sales,
    }
    if "start_date" in changes and "end_date" not in changes:
        data["end_date"] = None
    if dates_changed and "days_count" not in changes:
        data["days_count"] = None
    data.update(changes)

    config = _config_for_plan(db, plan)
    resolved = _resolve_plan_inputs(data, config)
    thresholds = _thresholds_from(plan)
    inputs, result = _compute_for(db, plan.branch_id, resolved, thresholds)
    _apply_resolved(plan, resolved)
    if payload.notes is not None:
        plan.notes = payload.notes
    plan.updated_by = payload.updated_by
    plan.updated_at = datetime.datetime.utcnow()
    _save_snapshot(db, plan, inputs, result, payload.updated_by)
    db.commit()
    db.refresh(plan)
    return _plan_detail(db, plan)


@app.post("/day-plans/{plan_id}/recalculate")
def recalculate_day_plan(plan_id: int, payload: Optional[DayPlanRecalculateSchema] = None, db: Session = Depends(get_db), ctx: AuthContext = Depends(owner(branch="day_plan"))):
    plan = _get_plan_or_404(db, plan_id)
    if plan.status not in DAY_PLAN_EDITABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"No se puede recalcular un plan en estado {plan.status}")
    resolved = _resolve_plan_inputs({
        "plan_type": plan.plan_type, "start_date": plan.start_date, "end_date": plan.end_date,
        "days_count": plan.days_count, "stall_total": plan.stall_total, "staff_total": plan.staff_total,
        "transport_outbound": plan.transport_outbound, "transport_return": plan.transport_return,
        "other_expenses": plan.other_expenses, "debt_target": plan.debt_target,
        "profit_target": plan.profit_target, "capital_available": plan.capital_available,
        "manual_expected_sales": plan.manual_expected_sales,
    }, _config_for_plan(db, plan))
    inputs, result = _compute_for(db, plan.branch_id, resolved, _thresholds_from(plan))
    _save_snapshot(db, plan, inputs, result, payload.actor if payload else None)
    db.commit()
    return _plan_detail(db, plan)


@app.patch("/day-plans/{plan_id}/status")
def change_day_plan_status(plan_id: int, payload: DayPlanStatusSchema, db: Session = Depends(get_db), ctx: AuthContext = Depends(owner(branch="day_plan"))):
    plan = _get_plan_or_404(db, plan_id)
    new_status = payload.status.strip().upper()
    if new_status not in DAY_PLAN_STATUSES:
        raise HTTPException(status_code=422, detail=f"Estado inválido. Opciones: {', '.join(DAY_PLAN_STATUSES)}")
    if new_status in ("ABIERTA", "CERRADA"):
        raise HTTPException(status_code=422, detail="Abrir/cerrar jornadas se habilita en la etapa Jornada en vivo")
    if plan.status not in DAY_PLAN_EDITABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"No se puede cambiar el estado de un plan {plan.status}")
    if new_status == plan.status:
        return _plan_detail(db, plan)
    if new_status == "CANCELADA":
        # Baja lógica: el plan y sus cálculos quedan guardados.
        stamp = ar_now().strftime("%d/%m/%Y %H:%M")
        note = f"[CANCELADA {stamp} por {ctx.actor_email}{': ' + payload.reason if payload.reason else ''}]"
        plan.notes = f"{plan.notes}\n{note}" if plan.notes else note
    plan.status = new_status
    plan.updated_by = payload.actor
    plan.updated_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(plan)
    return _plan_detail(db, plan)


# =====================================================================
# JORNADA DE FERIA EN VIVO
# Observa las ventas, gastos de caja y mermas reales de Feria dentro de la ventana
# opened_at..closed_at (o ahora). No crea ni modifica ventas, stock ni caja.
# El cálculo vive en fair_planning.compute_live_session.
# =====================================================================

FAIR_SESSION_ROLES = ("masteradmin", "superadmin", "dueno")

class DayPlanSessionActionSchema(BaseModel):
    actor: Optional[str] = None


def _fair_live_utc_now() -> datetime.datetime:
    """Hora actual en UTC naive, igual que sales.created_at (los tests la reemplazan)."""
    return datetime.datetime.utcnow()


def _plan_can_open_on(plan, today_ar: datetime.date) -> bool:
    if plan.status not in DAY_PLAN_EDITABLE_STATUSES:
        return False
    return datetime.date.fromisoformat(plan.start_date) <= today_ar <= datetime.date.fromisoformat(plan.end_date)


def _require_fair_session_actor(ctx: AuthContext) -> str:
    """Fase 2.2: quien abre o cierra es el usuario de la sesión (el `actor` del cuerpo se ignora).
    La política de la ruta ya exige Dueño o superior; esto lo vuelve a comprobar."""
    if (ctx.user.role or "") not in FAIR_SESSION_ROLES:
        raise _auth_error(403, ERROR_FORBIDDEN_ROLE, "Solo administración o dueño pueden iniciar o cerrar la jornada")
    return ctx.user.email


def _open_fair_session(db: Session, branch_id: int):
    return db.query(DayPlanDB).filter(DayPlanDB.branch_id == branch_id, DayPlanDB.status == "ABIERTA") \
        .order_by(DayPlanDB.id.desc()).first()


def _open_conflict_message(current) -> str:
    return (f"Ya hay una jornada en curso (plan #{current.id}, {_fmt_ddmmyyyy(current.start_date)}"
            f"{' - ' + _fmt_ddmmyyyy(current.end_date) if current.end_date != current.start_date else ''}). "
            "Usá «Ver jornada en curso».")


def _plan_economics(plan) -> dict:
    """Montos congelados del plan (los de la base; nunca la configuración actual)."""
    return {
        "plan_type": plan.plan_type, "start_date": plan.start_date, "end_date": plan.end_date,
        "days_count": plan.days_count,
        "stall_total": D(plan.stall_total), "staff_total": D(plan.staff_total),
        "transport_outbound": D(plan.transport_outbound), "transport_return": D(plan.transport_return),
        "other_expenses": D(plan.other_expenses), "debt_target": D(plan.debt_target),
        "profit_target": D(plan.profit_target), "capital_available": D(plan.capital_available),
    }


def _session_payload(db: Session, plan, until_utc: datetime.datetime, today_ar: Optional[datetime.date],
                     source: str) -> dict:
    since = plan.opened_at
    sales = db.query(SaleDB).filter(
        SaleDB.branch_id == plan.branch_id, SaleDB.created_at >= since, SaleDB.created_at <= until_utc
    ).order_by(SaleDB.created_at, SaleDB.id).all()
    sale_rows = [{
        "sale_id": s.id, "date": utc_to_ar(s.created_at).date(), "total_amount": s.total_amount,
        # cogs: costo histórico FEFO guardado al vender; nunca el costo actual del producto.
        "items": [{"revenue": D(i.unit_price) * D(i.quantity), "cogs": i.cogs,
                   "is_promotion": bool(i.is_promotion)} for i in s.items],
    } for s in sales]
    movements = db.query(CashMovementDB).filter(
        CashMovementDB.branch_id == plan.branch_id, CashMovementDB.created_at >= since,
        CashMovementDB.created_at <= until_utc
    ).all()
    cash_rows = [{"date": utc_to_ar(m.created_at).date(), "movement_type": m.movement_type,
                  "category": m.category, "amount": m.amount} for m in movements]
    losses = db.query(StockMovementDB).filter(
        StockMovementDB.branch_id == plan.branch_id, StockMovementDB.movement_type == "LOSS",
        StockMovementDB.created_at >= since, StockMovementDB.created_at <= until_utc
    ).all()
    loss_rows = [{"date": utc_to_ar(x.created_at).date(), "quantity": x.quantity, "unit_cost": x.unit_cost}
                 for x in losses]
    result = fair_planning.compute_live_session(
        _plan_economics(plan), sale_rows, cash_rows, loss_rows,
        utc_to_ar(since).date(), utc_to_ar(until_utc).date(), today_ar)
    if source == "FINAL_SNAPSHOT" and utc_to_ar(until_utc).date() > datetime.date.fromisoformat(plan.end_date):
        result["warnings"].append({"code": "CERRADA_FUERA_DE_PERIODO",
                                   "detail": "La jornada se cerró después del período planificado"})
    branch = db.query(BranchDB).filter(BranchDB.id == plan.branch_id).first()
    return {
        "source": source,
        "plan_id": plan.id,
        "branch_id": plan.branch_id,
        "branch_name": branch.name if branch else None,
        "plan_type": plan.plan_type,
        "start_date": plan.start_date, "end_date": plan.end_date,
        "start_date_display": _fmt_ddmmyyyy(plan.start_date), "end_date_display": _fmt_ddmmyyyy(plan.end_date),
        "days_count": plan.days_count,
        "multi_day": plan.start_date != plan.end_date,
        "status": plan.status,
        "opened_at": utc_to_ar(plan.opened_at).isoformat(), "opened_by": plan.opened_by,
        "closed_at": utc_to_ar(plan.closed_at).isoformat() if plan.closed_at else None,
        "closed_by": plan.closed_by,
        "computed_at": utc_to_ar(until_utc).isoformat(),
        "elapsed_minutes": int((until_utc - since).total_seconds() // 60),
        **result,
    }


def _session_summary(db: Session, plan) -> Optional[dict]:
    if plan.status == "CERRADA" and plan.final_results_json:
        data = json.loads(plan.final_results_json)
    elif plan.status == "ABIERTA" and plan.opened_at:
        now = _fair_live_utc_now()
        data = _session_payload(db, plan, now, utc_to_ar(now).date(), "LIVE")
    else:
        return None
    return {"source": data["source"], "revenue": data["result"]["revenue"],
            "gross_margin": data["result"]["gross_margin"], "operating_result": data["result"]["operating_result"],
            "indicator": data["indicator"]}


@app.post("/day-plans/{plan_id}/open")
def open_day_plan_session(plan_id: int, payload: DayPlanSessionActionSchema, db: Session = Depends(get_db), ctx: AuthContext = Depends(owner(branch="day_plan"))):
    actor = _require_fair_session_actor(ctx)
    plan = _get_plan_or_404(db, plan_id)
    _require_fair_branch(plan.branch_id, db)
    if plan.status == "ABIERTA":
        raise HTTPException(status_code=409, detail="La jornada ya está abierta")
    if plan.status == "CERRADA":
        raise HTTPException(status_code=409, detail="La jornada ya fue cerrada y no puede volver a abrirse")
    if plan.status == "CANCELADA":
        raise HTTPException(status_code=409, detail="Un plan cancelado no puede iniciarse")
    if plan.status not in DAY_PLAN_EDITABLE_STATUSES:
        raise HTTPException(status_code=409, detail=f"No se puede iniciar un plan en estado {plan.status}")
    now = _fair_live_utc_now()
    today = utc_to_ar(now).date()
    if not _plan_can_open_on(plan, today):
        raise HTTPException(status_code=409, detail=(
            f"Esta jornada solo puede iniciarse entre el {_fmt_ddmmyyyy(plan.start_date)} y el "
            f"{_fmt_ddmmyyyy(plan.end_date)} (hoy es {today.strftime('%d/%m/%Y')})."))
    current = _open_fair_session(db, plan.branch_id)
    if current:
        raise HTTPException(status_code=409, detail=_open_conflict_message(current))
    # Actualización condicional + índice único parcial: ante dos pedidos simultáneos solo uno abre.
    try:
        changed = db.query(DayPlanDB).filter(
            DayPlanDB.id == plan.id, DayPlanDB.status.in_(DAY_PLAN_EDITABLE_STATUSES)
        ).update({"status": "ABIERTA", "opened_at": now, "opened_by": actor,
                  "updated_by": actor, "updated_at": now}, synchronize_session=False)
        db.commit()
    except IntegrityError:
        db.rollback()
        current = _open_fair_session(db, plan.branch_id)
        raise HTTPException(status_code=409, detail=_open_conflict_message(current) if current
                            else "Ya hay una jornada en curso")
    if not changed:
        raise HTTPException(status_code=409, detail="El plan cambió de estado; actualizá la pantalla")
    db.refresh(plan)
    return _session_payload(db, plan, now, today, "LIVE")


@app.get("/day-plans/{plan_id}/live")
def get_day_plan_live(plan_id: int, db: Session = Depends(get_db), ctx: AuthContext = Depends(owner(branch="day_plan"))):
    plan = _get_plan_or_404(db, plan_id)
    if plan.status == "CERRADA":
        if not plan.final_results_json:
            raise HTTPException(status_code=409, detail="La jornada cerrada no tiene resultado final guardado")
        # Solo lectura: nunca se recalcula una jornada cerrada.
        return json.loads(plan.final_results_json)
    if plan.status != "ABIERTA" or not plan.opened_at:
        raise HTTPException(status_code=409, detail="La jornada todavía no fue iniciada")
    now = _fair_live_utc_now()
    return _session_payload(db, plan, now, utc_to_ar(now).date(), "LIVE")


@app.post("/day-plans/{plan_id}/close")
def close_day_plan_session(plan_id: int, payload: DayPlanSessionActionSchema, db: Session = Depends(get_db), ctx: AuthContext = Depends(owner(branch="day_plan"))):
    actor = _require_fair_session_actor(ctx)
    plan = _get_plan_or_404(db, plan_id)
    if plan.status == "CERRADA":
        raise HTTPException(status_code=409, detail="La jornada ya fue cerrada")
    if plan.status != "ABIERTA" or not plan.opened_at:
        raise HTTPException(status_code=409, detail="Solo puede cerrarse una jornada ABIERTA")
    now = _fair_live_utc_now()
    plan.status, plan.closed_at, plan.closed_by = "CERRADA", now, actor
    final = _session_payload(db, plan, now, None, "FINAL_SNAPSHOT")
    db.expire(plan)  # la escritura real es la actualización condicional de abajo
    changed = db.query(DayPlanDB).filter(DayPlanDB.id == plan_id, DayPlanDB.status == "ABIERTA").update({
        "status": "CERRADA", "closed_at": now, "closed_by": actor, "updated_by": actor, "updated_at": now,
        "final_results_json": json.dumps(final, ensure_ascii=False),
        "final_calc_version": fair_planning.LIVE_CALC_VERSION,
    }, synchronize_session=False)
    db.commit()
    if not changed:
        raise HTTPException(status_code=409, detail="La jornada ya fue cerrada")
    return final
