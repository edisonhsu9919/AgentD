import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core.config import settings

# ── Instance identity (generated once per process) ────────────────────────────
_INSTANCE_ID = uuid.uuid4().hex[:12]
_STARTED_AT: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    global _STARTED_AT
    _STARTED_AT = datetime.now(timezone.utc).isoformat()
    print(f"[startup] Instance {_INSTANCE_ID} (PID={os.getpid()}) starting at {_STARTED_AT}")

    await _check_schema_version()
    await _log_runtime_model()
    await _seed_admin()

    # Start PG LISTEN/NOTIFY bridge for cross-process SSE (Phase C)
    from core.event_bridge import listener as event_bridge_listener
    await event_bridge_listener.start()

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    await event_bridge_listener.stop()
    from core.database import engine
    await engine.dispose()


EXPECTED_SCHEMA_VERSION = "010"


async def _check_schema_version() -> None:
    """Check that the database schema is up to date with the latest migration.

    Queries the Alembic version table and compares against the expected version.
    Prints a clear warning if the schema is behind, preventing confusing 500 errors.
    """
    from sqlalchemy import text
    from core.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            row = result.first()
            if row is None:
                print(
                    "\n"
                    "  ╔══════════════════════════════════════════════════════════════╗\n"
                    "  ║  WARNING: No Alembic version found in database.             ║\n"
                    "  ║  Run: cd agentd && .venv/bin/python -m alembic upgrade head  ║\n"
                    "  ╚══════════════════════════════════════════════════════════════╝\n"
                )
                return
            current = row[0]
            if current != EXPECTED_SCHEMA_VERSION:
                print(
                    "\n"
                    "  ╔══════════════════════════════════════════════════════════════╗\n"
                    f"  ║  WARNING: DB schema is at version {current!r},              ║\n"
                    f"  ║  but the application expects version {EXPECTED_SCHEMA_VERSION!r}.            ║\n"
                    "  ║  Run: cd agentd && .venv/bin/python -m alembic upgrade head  ║\n"
                    "  ╚══════════════════════════════════════════════════════════════╝\n"
                )
            else:
                print(f"[startup] DB schema version: {current} (up to date)")
    except Exception as e:
        print(f"[startup] Could not check DB schema version: {e}")
        print("  Hint: Run 'cd agentd && .venv/bin/python -m alembic upgrade head' to initialize the database")


async def _log_runtime_model() -> None:
    """Log the current runtime model configuration source at startup."""
    from core.database import AsyncSessionLocal
    from model_config.service import resolve_active_model_config

    try:
        async with AsyncSessionLocal() as db:
            resolved = await resolve_active_model_config(db)
            print(
                f"[startup] Model source: {resolved.source} | "
                f"name={resolved.name} | model_id={resolved.model_id} | "
                f"base_url={resolved.base_url}"
            )
    except Exception as e:
        print(f"[startup] Could not resolve runtime model: {e}")


async def _seed_admin() -> None:
    """Create a default admin user on startup when SEED_ADMIN_* env vars are set."""
    if not (settings.seed_admin_username and settings.seed_admin_password):
        return
    from sqlalchemy import select
    from auth.models import User
    from auth import service as auth_svc
    from workspace.manager import create_workspace, ensure_user_root
    from core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).limit(1))
        if result.scalar_one_or_none() is not None:
            return  # users already exist
        user = await auth_svc.create_user(
            db,
            username=settings.seed_admin_username,
            password=settings.seed_admin_password,
            role="admin",
            workspace="",  # fill in after flush
        )
        user.workspace = create_workspace(str(user.id))
        ensure_user_root(user.workspace)
        await db.commit()
        print(f"[seed] Admin user '{user.username}' created (id={user.id})")


app = FastAPI(
    title=settings.app_title,
    version=settings.app_version,
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global exception handlers ─────────────────────────────────────────────────

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail:
        error = detail
    else:
        error = {"code": "INTERNAL_ERROR", "message": str(detail), "details": {}}
    return JSONResponse(status_code=exc.status_code, content={"error": error})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": exc.errors(),
            }
        },
    )


# ── Routers ───────────────────────────────────────────────────────────────────
from auth.router import router as auth_router          # Phase 1
app.include_router(auth_router, prefix="/api/auth", tags=["auth"])

# Phase 2
from session.router import router as session_router
app.include_router(session_router, prefix="/api/sessions", tags=["sessions"])

# Phase 5
from permission.router import router as permission_router
app.include_router(permission_router, prefix="/api/permissions", tags=["permissions"])

# Phase 6
from skills.router import router as skills_router
app.include_router(skills_router, prefix="/api/skills", tags=["skills"])

# Phase 6.7 — Session-scoped workspace API
from workspace.router import router as workspace_router
app.include_router(workspace_router, prefix="/api/sessions", tags=["workspace"])

# Phase C.5 — Admin user management
from admin.router import router as admin_router
app.include_router(admin_router, prefix="/api/admin/users", tags=["admin"])

# Phase I2 — Admin model config management
from model_config.router import router as model_config_router, runtime_router as model_runtime_router
app.include_router(model_config_router, prefix="/api/admin/model-configs", tags=["admin-models"])
app.include_router(model_runtime_router, prefix="/api/admin/runtime", tags=["admin-runtime"])


# ── Health check (Phase I4 — readiness-level) ────────────────────────────────
@app.get("/health", tags=["health"])
async def health():
    from sqlalchemy import text
    from core.database import AsyncSessionLocal
    from model_config.service import resolve_active_model_config
    from model_config.schemas import _mask_api_key

    schema_version = None
    schema_ok = False
    db_reachable = False
    degraded_reasons: list[str] = []

    # ── Schema check ──────────────────────────────────────────────────────
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            row = result.first()
            db_reachable = True
            if row:
                schema_version = row[0]
                schema_ok = schema_version == EXPECTED_SCHEMA_VERSION
            if not schema_ok:
                degraded_reasons.append("schema_mismatch")
    except Exception:
        degraded_reasons.append("db_unreachable")

    # ── Runtime model check ───────────────────────────────────────────────
    runtime_model_source = None
    runtime_model = None
    try:
        async with AsyncSessionLocal() as db:
            resolved = await resolve_active_model_config(db)
            runtime_model_source = resolved.source
            runtime_model = {
                "name": resolved.name,
                "provider_type": "openai_compatible",
                "model_id": resolved.model_id,
                "base_url_masked": resolved.base_url,
            }
    except Exception:
        if "db_unreachable" not in degraded_reasons:
            degraded_reasons.append("model_config_unresolved")

    # ── Readiness ─────────────────────────────────────────────────────────
    ready = len(degraded_reasons) == 0

    if ready:
        status = "ok"
    elif degraded_reasons == ["schema_mismatch"]:
        status = "degraded"
    else:
        status = "degraded"

    return {
        "status": status,
        "ready": ready,
        "degraded_reason": degraded_reasons[0] if degraded_reasons else None,
        "version": settings.app_version,
        "schema_version": schema_version,
        "schema_expected": EXPECTED_SCHEMA_VERSION,
        "schema_ok": schema_ok,
        "runtime_model_source": runtime_model_source,
        "runtime_model": runtime_model,
        "instance_id": _INSTANCE_ID,
        "started_at": _STARTED_AT,
        "pid": os.getpid(),
    }
