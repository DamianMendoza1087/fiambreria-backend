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
- `POST /day-plans/{id}/open`: solo BORRADOR/PLANIFICADA y con la fecha argentina de hoy
  dentro de `start_date..end_date`. Una sola ABIERTA por sucursal (API + índice).
- `GET /day-plans/{id}/live`: ABIERTA calcula en vivo; CERRADA devuelve `final_results_json`
  tal cual (nunca recalcula).
- `POST /day-plans/{id}/close`: solo ABIERTA; guarda el resultado final congelado.

Desde la Fase 2.2 quien abre o cierra es el usuario de la sesión (masteradmin, superadmin o dueno con
acceso a la sucursal del plan); un `actor` enviado en el cuerpo se ignora.
No hay apertura ni cierre automáticos; si sigue abierta después de `end_date` se advierte.

## Fase 1 — Retirar / Reactivar por sucursal, backup y diagnóstico

Sin cambios de esquema (no agrega tablas ni columnas).

Antes de desplegar, desde el Shell del servicio en Render:

    python backup_db.py --label pre-fase1   # copia verificada en /var/data/backups/ (nunca sobrescribe ni borra)
    python diagnose_db.py                   # SOLO LECTURA: duplicados, stock retirado, ventas inconsistentes, etc.

Ninguno de los dos importa `main.py`: no crean ni modifican tablas. `diagnose_db.py` abre la base con
`mode=ro` y nunca muestra contraseñas (solo informa si la cuenta maestra usa la clave por defecto).

Endpoints:

- `GET /branches/{b}/products` agrega `branch_status`: `DISPONIBLE`, `RETIRADO` o `NO_HABILITADO`.
- `GET /branches/{b}/products/{p}/retire-preview`: stock de la sucursal, valor a costo (si todas las
  partidas con saldo tienen costo) y si hace falta confirmar. No modifica nada.
- `DELETE /branches/{b}/products/{p}?confirm_stock=true&actor=...&reason=...`: retira solo en esa
  sucursal (`is_available=false`). No borra producto maestro, partidas ni historial. Con stock y sin
  `confirm_stock=true` responde 409 con header `X-Error-Code: RETIRE_REQUIRES_CONFIRMATION`.
- `POST /branches/{b}/products/{p}/reactivate` `{actor, reason}`: exige precio de sucursal > 0
  (409 `X-Error-Code: BRANCH_PRICE_REQUIRED_FOR_REACTIVATION`; nunca usa el precio global).
- `POST /ingresses`: si el producto está RETIRADO en la sucursal responde 409
  (`X-Error-Code: PRODUCT_RETIRED_IN_BRANCH`) salvo `reactivate_branch_product: true`.
  Con esa confirmación aplica la MISMA regla de precio que `/reactivate`: sin precio de sucursal > 0
  responde `BRANCH_PRICE_REQUIRED_FOR_REACTIVATION` y no registra ingreso, partida, movimiento ni
  cambio de disponibilidad. El listado de administración agrega `branch_price_configured`.
- `PUT /branches/{b}/products/{p}`: si pasa un producto RETIRADO a `is_available: true` aplica la misma
  regla (precio enviado en el PUT o, si no se envía, el propio ya configurado; nunca el global). Si se
  rechaza no cambia nada. Cambios que mantienen el producto retirado siguen permitidos.

Regla única en backend para todos los caminos RETIRADO -> DISPONIBLE (`/reactivate`, Ingresos y `PUT`):
precio propio de la sucursal > 0, validado por `_require_branch_price_for_reactivation`.

Retirar, reactivar y la reactivación desde un ingreso quedan en `audit_events`
(`entity_type = 'branch_product'`). El `actor` todavía lo informa la app: es trazabilidad, no
autenticación (eso llega en la fase de autenticación).

## Parche de seguridad U1 — usuarios (previo a Fase 2.1)

Histórico: en la Fase 2.2 los bloqueos temporales de este parche se reemplazaron por autorización real
(ver más abajo). Lo que sigue vigente es la lista blanca de campos. Texto original:

- `GET /users`, `POST /users` y `PATCH /users/{id}/permissions` devuelven solo campos no sensibles
  (`serialize_user`, lista blanca `USER_PUBLIC_FIELDS`). Nunca devuelven contraseñas.
- `POST /users` solo crea usuarios con rol `vendedor` (inactivos, como antes). Cualquier otro rol responde 403
  `X-Error-Code: ADMIN_OPERATION_BLOCKED_UNTIL_AUTH`. Emails duplicados (sin distinguir mayúsculas) y variantes
  del email maestro responden 409.
- `PATCH /users/{id}/permissions` no asigna roles `masteradmin` ni `superadmin` y no modifica usuarios con esos
  roles; `PATCH /users/{id}/status` no habilita ni deshabilita a esos usuarios (403, mismo código).
- Cambios entre `dueno` y `vendedor`, permisos `can_*`, sucursal y habilitar/inhabilitar empleados siguen
  funcionando como hasta ahora.
- `diagnose_db.py` informa cuentas administrativas, variantes del email maestro y roles desconocidos (sin
  contraseñas) para revisar si la escalada anónima previa fue utilizada.

## Fase 2.1 — Autenticación (credenciales, sesiones, bloqueo, recuperación)

En 2.1 los endpoints de negocio siguen sin exigir sesión (eso es 2.2, más abajo). Lo nuevo:

- **Contraseñas**: PBKDF2-SHA256, 600.000 iteraciones, sal de 16 bytes (`auth_core.py`, biblioteca
  estándar). Se guardan en la tabla `user_credentials`, separada de `users`: ninguna respuesta de
  usuarios puede incluirlas. Las contraseñas nuevas nunca se guardan en texto plano (`users.hashed_password`
  recibe un valor aleatorio inutilizable).
- **Política**: 10 a 128 caracteres; se rechazan admin123 y variantes, el email, contraseñas comunes y
  repetir la actual. Sin reglas de mayúscula/número/símbolo.
- **Login** (`POST /login`, mismo formulario y mismos campos de antes) devuelve además un token real,
  `expires_at`, `must_change_password`, `password_change_reason` y `allowed_branch_ids`.
- **Sesiones** (`auth_sessions`): token opaco; en la base solo su SHA-256. Vencen a las 12 h (empleados)
  o 7 días (MASTERADMIN/SUPERADMIN); el límite se recalcula con el rol actual. Rol, permisos y estado se leen
  de la base en cada pedido. Deshabilitar un usuario y cambiar la contraseña revocan sesiones.
- **Endpoints**: `GET /auth/me`, `POST /auth/logout`, `POST /auth/change-password`. Con cambio de contraseña
  pendiente, la dependencia `require_session` (que usarán los endpoints en 2.2) responde 403
  `PASSWORD_CHANGE_REQUIRED`.
- **Usuarios sin credencial** (bases previas a 2.1): el login compara contra la contraseña heredada sin
  escribir nada y exige cambio. Arrancar el servicio **no crea ni modifica credenciales**.
- **Bloqueo** (`auth_login_failures`, persistente): 5 fallos por email en 15 min bloquean 15 min (siempre).
  20 fallos por IP en 15 min bloquean solo con `AUTH_IP_LIMIT_MODE=enforce`.
  - `TRUSTED_PROXY_HOPS` (0 por defecto): cantidad de proxies confiables que agregan a `X-Forwarded-For`.
    Con 0 se ignora esa cabecera. Con N se toma la entrada N-ésima desde el final; lo que está a su izquierda
    lo pudo inventar el cliente.
  - `AUTH_IP_LIMIT_MODE`: `observe` por defecto en producción (registra la IP vista y la cadena
    `X-Forwarded-For` de cada sesión, sin bloquear), `enforce` fuera de producción.
  - Verificación en producción: después del primer login, `python diagnose_db.py` → `auth_network.recent_sessions`
    muestra `ip`, `peer_ip` y `forwarded_chain`. Comparar con la IP pública del teléfono, fijar
    `TRUSTED_PROXY_HOPS` y recién entonces `AUTH_IP_LIMIT_MODE=enforce`.
- **Producción**: sin `/docs`, `/redoc` ni `/openapi.json`. La semilla de una base nueva crea la cuenta maestra
  sin contraseña utilizable (no se recrea admin123).
- **Render Shell** (`manage_users.py`, hacer backup antes):
  - `python manage_users.py reset-password --email admin@fiambreria.com` — clave temporal mostrada una sola vez,
    cambio obligatorio, sesiones cerradas, bloqueo limpiado. `--activate` reactiva un MASTERADMIN deshabilitado.
  - `python manage_users.py unlock --email …` — quita el bloqueo por intentos.
  - `python manage_users.py migrate-credentials [--dry-run]` — convierte en hash las contraseñas heredadas
    (idempotente; no borra el texto heredado).
  - `python manage_users.py list-admins` — sin secretos.
- La columna heredada `users.hashed_password` no se borra todavía (U8=A).

## Fase 2.2 — Autorización estricta

Todos los endpoints exigen sesión salvo `GET /health` y `POST /login`. No existe modo legacy, bypass
anónimo ni interruptor de emergencia: una APK sin token recibe 401 en todo lo demás.

- **Una política por ruta** (`Policy` en `main.py`, reglas en `auth_core.py`): rol mínimo, capacidades
  `can_*` y origen de la sucursal (`branch_id` del query, `{branch_id}` de la URL, la sucursal guardada del
  plan o la del cuerpo). `main.route_policies()` lista la matriz y `tests/test_authz.py` falla si aparece una
  ruta sin política o con más de una.
- **Jerarquía**: masteradmin > superadmin > dueno > encargado > cajero > vendedor (Empleado de mostrador).
  MASTERADMIN y SUPERADMIN tienen todas las capacidades y todas las sucursales activas; el resto, solo sus
  sucursales asignadas (`user_branches`, tabla aditiva sembrada con la sucursal actual de cada usuario).
- **Códigos** (`X-Error-Code`): `AUTH_REQUIRED`/`SESSION_*` (401), `PASSWORD_CHANGE_REQUIRED`,
  `FORBIDDEN_ROLE`, `FORBIDDEN_CAPABILITY`, `FORBIDDEN_BRANCH`, `FORBIDDEN_USER_ADMIN` (403),
  `LAST_MASTERADMIN` (409), `DEFAULT_PASSWORD_RESET_REQUIRED` (403).
- **Actor**: los campos de actor (`actor`, `sold_by`, `opened_by`, `created_by`, …) se completan con el email
  de la sesión antes de guardar (evento `before_flush`); los `audit_events` nuevos llevan además
  `actor_user_id`, `actor_source=session` y `session_id`. Lo que mande la app en esos campos se ignora.
- **Usuarios**: SUPERADMIN o superior administra; MASTERADMIN administra a todos salvo a sí mismo; SUPERADMIN
  hasta Dueño (nunca crea, promueve ni modifica MASTERADMIN/SUPERADMIN); nadie cambia su propio rol ni estado;
  siempre queda al menos un MASTERADMIN activo. `POST /users/{id}/reset-password` devuelve una contraseña
  temporal una sola vez (`Cache-Control: no-store`), obliga a cambiarla y cierra las sesiones del usuario.
  `PATCH /users/{id}/permissions` acepta `branch_ids` (lista) o `branch_id` (reemplaza por esa sola).
- **Egresos privilegiados (U17)**: `can_cash_privileged_outflows` (apagado por defecto). Pagos a proveedor /
  mercadería y retiros del dueño (`POST /cash/movements` OUT, categoría normalizada sin tildes ni espacios)
  requieren Dueño o superior, o Encargado con la capacidad. Cajero y Empleado nunca, aunque esté guardada.
  Las mermas (`POST /stock/losses`) requieren `can_stock`.
- **Turnos (U16)**: cada uno abre y cierra su propio turno; el de otro requiere Encargado o superior con
  `can_rrhh` y que el empleado comparta sucursal. El rol trabajado no supera el rol real.
- **Feria**: plan, jornada en vivo, apertura y cierre requieren Dueño o superior con acceso a la sucursal.
- **admin123**: una credencial `DEFAULT_PASSWORD` puede iniciar sesión solo para ver `/auth/me` y cerrar
  sesión; `/auth/change-password` responde 403 `DEFAULT_PASSWORD_RESET_REQUIRED`. La salida es un reset
  (Render Shell o un administrador), que deja una clave temporal de un uso.
- **Migración** (`ensure_authz_schema`, aditiva e idempotente, versión `2.2-authz`): columna
  `users.can_cash_privileged_outflows` (0) y tabla `user_branches`. No toca credenciales ni la columna heredada.
