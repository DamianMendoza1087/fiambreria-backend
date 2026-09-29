from pydantic import BaseModel
from typing import Optional

# Esquemas para Productos
class ProductBase(BaseModel):
    name: str
    category: str
    price_per_unit: float
    unit_type: str = "kg"
    stock: float = 0.0
    is_active: bool = True

class ProductCreate(ProductBase):
    pass

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    price_per_unit: Optional[float] = None
    unit_type: Optional[str] = None
    stock: Optional[float] = None
    is_active: Optional[bool] = None

class ProductResponse(ProductBase):
    id: int

    class Config:
        from_attributes = True
