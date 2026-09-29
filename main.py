# Agregar en los esquemas de base de datos
class UserDB(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="vendedor") # superadmin, dueno, encargado, vendedor, cajero, auditor
    is_active = Column(Boolean, default=True)
    is_cashier_active = Column(Boolean, default=False)

# Endpoint para listar todos los usuarios (solo Superadmin/Dueño)
@app.get("/users")
def get_users(db: Session = Depends(get_db)):
    return db.query(UserDB).all()

# Endpoint para crear un nuevo usuario con Rol
@app.post("/users")
def create_user(user_data: dict, db: Session = Depends(get_db)):
    new_user = UserDB(
        name=user_data["name"],
        email=user_data["email"],
        hashed_password=user_data["password"], # En producción aplicar hash
        role=user_data["role"],
        is_active=True,
        is_cashier_active=False
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

# Endpoint para Activar al Cajero Único de Turno
@app.patch("/users/{user_id}/activate-cashier")
def set_active_cashier(user_id: int, db: Session = Depends(get_db)):
    # 1. Desactivar el turno a todos los usuarios
    db.query(UserDB).update({"is_cashier_active": False})
    # 2. Activar el turno solo al usuario seleccionado
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if user:
        user.is_cashier_active = True
        db.commit()
        return {"status": "success", "active_cashier": user.name}
    raise HTTPException(status_code=404, detail="Usuario no encontrado")
