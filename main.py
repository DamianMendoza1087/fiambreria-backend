from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel
from typing import List, Optional

Base = declarative_base()

# Modelo de Usuario en BD
class UserDB(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="vendedor") # superadmin, dueno, encargado, vendedor, cajero, auditor
    is_active = Column(Boolean, default=True)
    is_cashier_active = Column(Boolean, default=False) # Solo uno puede estar True a la vez

# Esqueva Pydantic para creación de usuario
class UserCreate(BaseModel):
    name: str
    email: str
    password: str
    role: str

class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    is_cashier_active: bool

    class Config:
        orm_mode = True

# Endpoint para crear usuario (Solo administrado por Superadmin / Dueño)
# Lógica para Habilitar Cajero Único
@app.patch("/users/{user_id}/activate-cashier", response_model=UserResponse)
def set_active_cashier(user_id: int, db: Session = Depends(get_db)):
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    # Desactivar a todos los demás cajeros activos
    db.query(UserDB).update({UserDB.is_cashier_active: False})
    
    # Activar al usuario seleccionado
    user.is_cashier_active = True
    user.is_active = True
    db.commit()
    db.refresh(user)
    return user
