# fiambreria-backend

## Render Persistent SQLite

En producción la base SQLite vive en el Persistent Disk de Render.

Configuración del servicio en Render:

- Disk → Mount Path: `/var/data`
- Environment:
  - `APP_ENV=production`
  - `DATABASE_URL=sqlite:////var/data/fiambreria.db` (cuatro barras: ruta absoluta)

Con `APP_ENV=production` el backend valida al arrancar que `DATABASE_URL` sea SQLite
absoluta dentro de `/var/data`, que `/var/data` exista y sea escribible, y que la base
exista, no esté vacía, se pueda abrir y tenga las tablas, el usuario y las sucursales
iniciales. Si algo falla, **no arranca** (error explícito en los logs) en lugar de
crear una base vacía.

`ALLOW_DB_INITIALIZATION=true` se usa **únicamente en el primer arranque de un disco
vacío**, para crear la base nueva. Hay que **eliminar esa variable inmediatamente
después** de ese primer arranque exitoso.

Diagnóstico: `GET /health` (no expone la URL ni rutas).

Desarrollo local: sin `APP_ENV=production` y sin `DATABASE_URL` se usa
`sqlite:///./fiambreria.db`, como siempre.

Tests: `pip install -r requirements-dev.txt` y `python -m pytest tests`.
