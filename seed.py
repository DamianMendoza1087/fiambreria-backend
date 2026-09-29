from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import engine
import models
from seed import init_db

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

@app.get("/")
def home():
    return {"status": "ok", "message": "Backend de Fiambreria activo y listo con BD"}

@app.get("/health")
def health():
    return {"status": "healthy"}
