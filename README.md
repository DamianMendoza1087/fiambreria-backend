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

## Objetivo de Jornada — Feria ("¿Conviene abrir?")

Módulo para Feria Damyale (`branch_id=2`). El cálculo está en `fair_planning.py`
(Decimal, sin acceso a base); `main.py` lee los datos y persiste.

Tablas nuevas (solo se agregan con `create_all`): `fair_cost_configs` (versionada),
`day_plans` (cancelar es baja lógica) y `day_plan_snapshots` (`calc_version = 2`).

Endpoints:

- `GET/PUT /branches/2/fair-config`
- `POST /day-plans/preview` (no guarda), `POST /day-plans` (plan + snapshot)
- `GET /day-plans?branch_id=2`, `GET /day-plans/{id}`
- `PUT /day-plans/{id}` y `POST /day-plans/{id}/recalculate` (agregan snapshots)
- `PATCH /day-plans/{id}/status` (BORRADOR, PLANIFICADA, CANCELADA)

El stock sale solo de los lotes de la sucursal (`product_lots.current_qty` y el costo
del lote). Los lotes con costo <= 0 y los productos sin precio válido de Feria se
excluyen y se informan como datos incompletos.
