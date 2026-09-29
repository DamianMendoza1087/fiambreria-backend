from typing import List
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from database import engine, get_db
import models
import schemas
from seed import init_db
from auth import verify_password, create_access_token, get_current_user

# Crear tablas e inicializar datos base
models.Base.metadata.create_all(bind=engine)
init_db()

app = FastAPI(title="Fiambreria Backend API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- RUTAS DE AUTENTICACIÓN Y SISTEMA ---

@app.get("/")
def home():
    return {"status": "ok", "message": "Backend de Fiambreria activo y listo con BD"}

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Correo o contraseña incorrectos"
        )
    
    access_token = create_access_token(data={"sub": user.email, "role": user.role})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role
        }
    }

@app.get("/me")
def read_users_me(current_user: models.User = Depends(get_current_user)):
    return {
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "is_active": current_user.is_active
    }

# --- RUTAS DE PRODUCTOS ---

@app.get("/products", response_model=List[schemas.ProductResponse])
def get_products(db: Session = Depends(get_db)):
    """Obtener todos los productos activos"""
    return db.query(models.Product).filter(models.Product.is_active == True).all()

@app.post("/products", response_model=schemas.ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(
    product: schemas.ProductCreate, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Crear un producto nuevo (requiere estar autenticado)"""
    new_product = models.Product(**product.model_dump())
    db.add(new_product)
    db.commit()
    db.refresh(new_product)
    return new_product

@app.put("/products/{product_id}", response_model=schemas.ProductResponse)
def update_product(
    product_id: int, 
    product_data: schemas.ProductUpdate, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Actualizar un producto existente (requiere estar autenticado)"""
    product_query = db.query(models.Product).filter(models.Product.id == product_id)
    existing_product = product_query.first()
    
    if not existing_product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
        
    update_data = product_data.model_dump(exclude_unset=True)
    product_query.update(update_data)
    db.commit()
    db.refresh(existing_product)
    return existing_product

@app.delete("/products/{product_id}")
def delete_product(
    product_id: int, 
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user)
):
    """Desactivar/eliminar producto (requiere estar autenticado)"""
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
        
    product.is_active = False
    db.commit()
    return {"message": f"Producto '{product.name}' desactivado con éxito"}
