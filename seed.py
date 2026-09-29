import hashlib
from database import SessionLocal, engine
import models

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def init_db():
    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    admin_user = db.query(models.User).filter(models.User.email == "admin@fiambreria.com").first()
    
    if not admin_user:
        hashed_pw = hash_password("admin123")
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
