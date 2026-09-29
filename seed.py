from database import SessionLocal, engine
import models
from passlib.context import CryptContext

# Configuración para encriptar la contraseña
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def init_db():
    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    # Verificamos si ya existe un usuario admin
    admin_user = db.query(models.User).filter(models.User.email == "admin@fiambreria.com").first()
    
    if not admin_user:
        hashed_pw = pwd_context.hash("admin123")
        super_admin = models.User(
            email="admin@fiambreria.com",
            hashed_password=hashed_pw,
            full_name="Super Admin",
            role="superadmin",
            is_active=True
        )
        db.add(super_admin)
        db.commit()
        print("--> Usuario SuperAdmin creado exitosamente.")
    else:
        print("--> El usuario SuperAdmin ya existe.")
    
    db.close()

if __name__ == "__main__":
    init_db()
