from sqlalchemy import Column, Integer, String, Float, Boolean
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    full_name = Column(String, nullable=True)
    role = Column(String, default="admin") # roles: superadmin, admin, empleado
    is_active = Column(Boolean, default=True)

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    category = Column(String, index=True, nullable=False) # ej: Fiambres, Quesos, Bebidas, Almacén
    price_per_unit = Column(Float, nullable=False)        # precio por kg o por unidad
    unit_type = Column(String, default="kg")              # "kg" o "unidad"
    stock = Column(Float, default=0.0)                    # stock disponible
    is_active = Column(Boolean, default=True)
