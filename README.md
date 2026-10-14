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

## Jornada de Feria en vivo

Seguimiento durante y después de la Feria (`branch_id=2`) a partir de un Day Plan. No
crea ni modifica ventas, stock ni caja: solo **observa** lo que ya registró la app.
El cálculo está en `fair_planning.compute_live_session` (Decimal, sin base).

Ventana de la jornada: `opened_at <= created_at <= closed_at` (o ahora si sigue abierta),
todo en UTC; la agrupación por día (HOY / días) usa la fecha de America/Argentina/Buenos_Aires.

Resultado operativo real:

    ventas (sales.total_amount, lo realmente cobrado; en promos el precio promo)
  - costo de mercadería vendida (sale_items.cogs: costo histórico FEFO de la venta)
  = margen bruto
  - costos operativos planificados (puesto + personal + transporte + otros del plan, congelados)
  - gastos extraordinarios reales (cash_movements OUT: OPERATIVO, OPERATIVOS, GASTO_OPERATIVO,
    SERVICIOS, SERVICIO, COMPRA_MENOR, OTRO, OTROS, CAJA_CHICA; los alias históricos
    OPERATIVOS/GASTO_OPERATIVO = OPERATIVO, SERVICIO = SERVICIOS, OTROS = OTRO)
  - mermas reales a costo (stock_movements LOSS: |cantidad| x unit_cost histórico)
  = resultado operativo real

EMPLEADO se muestra solo como informativo (el personal ya está en el plan); PROVEEDOR
(mercadería, ya entra por COGS) y RETIRO_DUENO se excluyen. Una categoría desconocida **no
se resta**: queda listada como movimiento no clasificado, con su monto y la advertencia
`CATEGORIA_GASTO_NO_RECONOCIDA`, sin modificar el resultado hasta que exista una regla explícita.
Deuda y ganancia deseada son objetivos: nunca se restan como gasto.

Esquema (`ensure_fair_live_schema`, idempotente, sin borrar ni recrear tablas):
columnas `opened_at`, `opened_by`, `closed_at`, `closed_by`, `final_results_json`,
`final_calc_version` en `day_plans`, e índice único parcial
`ux_day_plans_one_open_per_branch ON day_plans(branch_id) WHERE status='ABIERTA'`.

Endpoints:

- `GET /day-plans/open?branch_id=2`: jornada en curso (o `open: false`).
- `POST /day-plans/{id}/open` `{actor}`: solo BORRADOR/PLANIFICADA y con la fecha argentina de hoy
  dentro de `start_date..end_date`. Una sola ABIERTA por sucursal (API + índice).
- `GET /day-plans/{id}/live`: ABIERTA calcula en vivo; CERRADA devuelve `final_results_json`
  tal cual (nunca recalcula).
- `POST /day-plans/{id}/close` `{actor}`: solo ABIERTA; guarda el resultado final congelado.

`actor` debe ser un usuario activo con rol masteradmin, superadmin o dueno. **Es una
validación de coherencia, no autenticación**: el backend todavía no verifica tokens.
No hay apertura ni cierre automáticos; si sigue abierta después de `end_date` se advierte.
