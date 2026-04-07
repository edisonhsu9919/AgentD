"""Skills catalog + install/uninstall router (§5.4, Phase F1).

Catalog (admin):
  GET  /api/skills           — list catalog skills
  GET  /api/skills/{id}      — get skill detail
  POST /api/skills           — create skill (DB + catalog dir)
  PUT  /api/skills/{id}      — update skill (DB + catalog dir)
  DELETE /api/skills/{id}    — soft-delete skill (DB + catalog dir)
  POST /api/skills/import-local — import a local skill package to catalog

User install/uninstall:
  GET  /api/skills/installed         — list user's installed skills
  POST /api/skills/{id}/install      — install skill to user's skills dir
  DELETE /api/skills/{id}/uninstall  — uninstall skill from user's skills dir
"""

import uuid

import os

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, require_admin
from auth.models import User
from core.database import get_db
from core.response import ok, ok_list
from skills import service as skill_svc
from skills.filesystem import (
    SkillImportError,
    get_skills_dir,
    import_package_to_catalog,
    install_skill_for_user,
    remove_skill_from_catalog,
    uninstall_skill_for_user,
    write_skill_to_catalog,
)
from skills.package import SkillPackageMeta, parse_frontmatter, validate_package
from skills.schemas import (
    SkillCreate,
    SkillDetailResponse,
    SkillImportLocal,
    SkillSummaryResponse,
    SkillUpdate,
)

router = APIRouter()


# ── Catalog (admin manages, any user reads) ────────────────────────────────


@router.get("")
async def list_skills(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all active skills in the catalog (without content full text)."""
    skills = await skill_svc.list_skills(db)
    data = [SkillSummaryResponse.model_validate(s).model_dump(mode="json") for s in skills]
    return ok_list(data, total=len(data))


@router.get("/installed")
async def list_installed_skills(
    current_user: User = Depends(get_current_user),
):
    """List skills installed in the current user's skills directory."""
    import os

    skills_dir = get_skills_dir(current_user.workspace)
    installed: list[dict] = []

    if not os.path.isdir(skills_dir):
        return ok_list(installed, total=0)

    for entry in sorted(os.listdir(skills_dir)):
        skill_path = os.path.join(skills_dir, entry)
        if not os.path.isdir(skill_path):
            continue
        skill_md = os.path.join(skill_path, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue
        try:
            with open(skill_md, "r", encoding="utf-8") as f:
                content = f.read()
            meta = parse_frontmatter(content)
            installed.append({
                "name": meta.get("name", entry),
                "description": meta.get("description", ""),
                "version": meta.get("version", "0.1.0"),
                "icon": meta.get("icon", ""),
                "tags": meta.get("tags", []),
                "dir_name": entry,
            })
        except Exception:
            continue

    return ok_list(installed, total=len(installed))


# ── Skill Square (Phase H3) ─────────────────────────────────────────────────


@router.get("/square")
async def square_list(
    q: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Skill Square homepage — aggregated cards by skill name.

    Supports optional ``q`` parameter for case-insensitive search across
    name, description, and tags.
    """
    from skills import square_service as sq_svc

    cards = await sq_svc.list_square_cards(db, current_user.id, q=q, user_root=current_user.workspace)
    return ok_list(cards, total=len(cards))


@router.get("/square/{skill_name}")
async def square_detail(
    skill_name: str,
    version: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Skill Square detail — single skill with version-resolved content.

    If ``version`` is not provided:
    - Uses the user's installed version (if any)
    - Otherwise uses the latest version
    """
    from fastapi import HTTPException as _HTTPException
    from skills import square_service as sq_svc

    detail = await sq_svc.get_square_detail(
        db, current_user.id, skill_name, version=version,
    )
    if not detail:
        raise _HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"Skill '{skill_name}' not found in catalog"},
        )
    return ok(detail)


@router.get("/square/{skill_name}/icon")
async def square_icon(
    skill_name: str,
    version: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Serve icon file for a skill from the catalog assets directory.

    Resolves version the same way as square_detail:
    installed version > latest version.
    Falls back to reading icon path from SKILL.md frontmatter if DB
    metadata_extra lacks the icon field (self-healing for stale records).
    """
    import os

    from fastapi.responses import FileResponse

    from skills.filesystem import get_catalog_dir, get_latest_version, read_catalog_skill_md
    from skills import user_skill_service as us_svc

    # Resolve version
    if not version:
        us = await us_svc.get_user_skill(db, current_user.id, skill_name)
        version = us.version if us else None
    if not version:
        version = get_latest_version(skill_name)
    if not version:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Skill not found"})

    # Find icon value — try DB first, then fall back to SKILL.md on disk
    icon_rel = ""
    skill = await skill_svc.get_skill_by_name_version(db, skill_name, version)
    if skill and skill.icon:
        icon_rel = skill.icon

    if not icon_rel:
        # Fallback: read icon from SKILL.md frontmatter on disk
        raw_md = read_catalog_skill_md(skill_name, version)
        if raw_md:
            fm = parse_frontmatter(raw_md)
            icon_rel = fm.get("icon", "")

    if not icon_rel:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "No icon"})

    # Only serve file-path icons (not emojis)
    if "/" not in icon_rel and "\\" not in icon_rel:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Icon is not a file"})

    catalog_dir = get_catalog_dir()
    version_dir = os.path.join(catalog_dir, skill_name, version)
    icon_abs = os.path.normpath(os.path.join(version_dir, icon_rel))

    # Path traversal guard
    if not icon_abs.startswith(os.path.normpath(version_dir)):
        raise HTTPException(status_code=400, detail={"code": "VALIDATION_ERROR", "message": "Invalid icon path"})

    if not os.path.isfile(icon_abs):
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Icon file not found"})

    return FileResponse(icon_abs)


@router.get("/{skill_id}")
async def get_skill(
    skill_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single skill with full content."""
    skill = await skill_svc.get_skill(db, skill_id)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Skill not found"},
        )
    return ok(SkillDetailResponse.model_validate(skill).model_dump(mode="json"))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_skill(
    body: SkillCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new skill (admin only). Writes to DB + versioned catalog dir."""
    existing = await skill_svc.get_skill_by_name_version(db, body.name, body.version)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CONFLICT",
                "message": f"Skill '{body.name}' version '{body.version}' already exists",
            },
        )
    # Merge icon into metadata_extra so it persists in JSONB
    me = dict(body.metadata_extra)
    if body.icon:
        me["icon"] = body.icon
    skill = await skill_svc.create_skill(
        db,
        name=body.name,
        description=body.description,
        content=body.content,
        tags=body.tags,
        created_by=admin.id,
        version=body.version,
        license=body.license,
        compatibility=body.compatibility,
        metadata_extra=me,
        source_type=body.source_type,
        source_path=body.source_path,
    )
    # Sync to versioned filesystem catalog
    meta = SkillPackageMeta(
        name=body.name,
        description=body.description,
        version=body.version,
        license=body.license,
        compatibility=body.compatibility,
        icon=body.icon,
        metadata=body.metadata_extra,
        tags=body.tags,
    )
    write_skill_to_catalog(meta, body.content)
    return ok(SkillDetailResponse.model_validate(skill).model_dump(mode="json"))


@router.put("/{skill_id}")
async def update_skill(
    skill_id: uuid.UUID,
    body: SkillUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing skill (admin only). Syncs to catalog dir."""
    skill = await skill_svc.get_skill(db, skill_id)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Skill not found"},
        )
    # Check name+version uniqueness if changing
    old_name = skill.name
    old_version = skill.version
    new_name = body.name or old_name
    new_version = body.version or old_version
    if (new_name, new_version) != (old_name, old_version):
        existing = await skill_svc.get_skill_by_name_version(db, new_name, new_version)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "CONFLICT",
                    "message": f"Skill '{new_name}' version '{new_version}' already exists",
                },
            )
    # Merge icon into metadata_extra if provided
    me = body.metadata_extra
    if body.icon is not None:
        me = dict(me if me is not None else (skill.metadata_extra or {}))
        me["icon"] = body.icon
    updated = await skill_svc.update_skill(
        db,
        skill_id,
        name=body.name,
        description=body.description,
        content=body.content,
        version=body.version,
        license=body.license,
        compatibility=body.compatibility,
        metadata_extra=me,
        tags=body.tags,
        is_active=body.is_active,
    )
    # Remove old catalog entry if name or version changed
    if (new_name, new_version) != (old_name, old_version):
        remove_skill_from_catalog(old_name, old_version)
    # Write updated skill to catalog
    meta = SkillPackageMeta(
        name=updated.name,
        description=updated.description,
        version=updated.version,
        license=updated.license,
        compatibility=updated.compatibility,
        icon=updated.icon,
        metadata=updated.metadata_extra or {},
        tags=updated.tags or [],
    )
    write_skill_to_catalog(meta, updated.content)
    return ok(SkillDetailResponse.model_validate(updated).model_dump(mode="json"))


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Global delete a skill (admin only).

    Phase 6: full global deletion — removes from:
    1. DB skills table (is_active=false)
    2. Catalog directory
    3. ALL users' installed skill directories
    4. ALL user_skills relationship records

    This prevents orphan skill states.
    """
    import shutil
    from sqlalchemy import delete as sa_delete, select as sa_select
    from auth.models import User as UserModel
    from skills.models import UserSkill

    skill = await skill_svc.get_skill(db, skill_id)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Skill not found"},
        )

    skill_name = skill.name

    # 1. Soft-delete in DB
    await skill_svc.delete_skill(db, skill_id)

    # 2. Remove from catalog
    remove_skill_from_catalog(skill_name, skill.version)

    # 3. Remove from ALL users' installed directories
    users = (await db.execute(sa_select(UserModel))).scalars().all()
    removed_installs = 0
    for user in users:
        skill_dir = os.path.join(user.workspace, "skills", skill_name)
        if os.path.isdir(skill_dir):
            shutil.rmtree(skill_dir)
            removed_installs += 1

    # 4. Remove ALL user_skills records for this skill name
    result = await db.execute(
        sa_delete(UserSkill).where(UserSkill.skill_name == skill_name)
    )
    removed_links = result.rowcount

    await db.commit()

    return ok({
        "deleted": True,
        "skill_name": skill_name,
        "removed_user_installs_count": removed_installs,
        "removed_user_links_count": removed_links,
    })


# ── Import local skill package ─────────────────────────────────────────────


@router.post("/import-local", status_code=status.HTTP_201_CREATED)
async def import_local_skill(
    body: SkillImportLocal,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Import a local skill package directory into the catalog (admin only).

    Validates the package, copies to versioned catalog, and creates a DB record.
    """
    result = validate_package(body.source_path)
    if not result.valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_PACKAGE",
                "message": "Skill package validation failed",
                "errors": result.errors,
            },
        )

    meta = result.meta
    # Check for duplicate — allow re-import over soft-deleted records
    existing = await skill_svc.get_skill_by_name_version(db, meta.name, meta.version)
    if existing and existing.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CONFLICT",
                "message": f"Skill '{meta.name}' version '{meta.version}' already exists in catalog",
            },
        )

    # Import to filesystem catalog (may build .venv — fail-fast on error)
    try:
        import_package_to_catalog(body.source_path, meta)
    except SkillImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "IMPORT_FAILED",
                "message": f"Skill import failed: {exc}",
            },
        )

    # Merge icon into metadata_extra for DB persistence
    me = dict(meta.metadata) if meta.metadata else {}
    if meta.icon:
        me["icon"] = meta.icon

    if existing and not existing.is_active:
        # Reactivate and update the soft-deleted record
        skill = await skill_svc.update_skill(
            db,
            existing.id,
            name=meta.name,
            description=meta.description,
            content=meta.body,
            tags=meta.tags,
            version=meta.version,
            license=meta.license,
            compatibility=meta.compatibility,
            metadata_extra=me,
            is_active=True,
            source_type="import_local",
            source_path=body.source_path,
        )
    else:
        # Create new DB record
        skill = await skill_svc.create_skill(
            db,
            name=meta.name,
            description=meta.description,
            content=meta.body,
            tags=meta.tags,
            created_by=admin.id,
            version=meta.version,
            license=meta.license,
            compatibility=meta.compatibility,
            metadata_extra=me,
            source_type="import_local",
            source_path=body.source_path,
        )

    return ok(SkillDetailResponse.model_validate(skill).model_dump(mode="json"))


# ── User install / uninstall ───────────────────────────────────────────────


def _ensure_catalog_from_db(skill) -> None:
    """Best-effort re-create catalog SKILL.md from DB content.

    This handles the case where a skill exists in DB but its catalog directory
    was lost or never created (e.g. created via test fixtures, DB restore, etc).

    **Limitation (Phase M4-B):** This can only restore SKILL.md from DB content.
    It cannot restore scripts/, requirements.txt, or .venv — those only exist
    when a skill was imported via ``import-local``. After self-heal, the caller
    must still rely on ``install_skill_for_user()``'s completeness check.
    For import-local skills with scripts/.venv, re-importing is the correct
    repair path, not self-heal.
    """
    meta = SkillPackageMeta(
        name=skill.name,
        description=skill.description,
        version=skill.version,
        license=getattr(skill, "license", "") or "",
        compatibility=getattr(skill, "compatibility", "") or "",
        icon=skill.icon,
        metadata=skill.metadata_extra or {},
        tags=skill.tags or [],
    )
    write_skill_to_catalog(meta, skill.content)


@router.post("/{skill_id}/install")
async def install_skill(
    skill_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Install a skill from catalog to user's skills directory.

    If the catalog directory is missing but the DB has the skill content,
    auto-syncs the catalog before installing (self-healing for DB/FS drift).
    """
    skill = await skill_svc.get_skill(db, skill_id)
    if not skill or not skill.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Skill not found in catalog"},
        )
    try:
        install_skill_for_user(current_user.workspace, skill.name, skill.version)
    except FileNotFoundError:
        # Self-heal: regenerate catalog directory from DB content
        _ensure_catalog_from_db(skill)
        try:
            install_skill_for_user(current_user.workspace, skill.name, skill.version)
        except FileNotFoundError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": "Skill not found in catalog directory"},
            )
    # Sync to user_skills table (Phase H1)
    from skills import user_skill_service as us_svc
    await us_svc.upsert_user_skill(db, current_user.id, skill.name, skill.version)
    return ok({"installed": True, "skill_name": skill.name, "version": skill.version})


@router.delete("/{skill_id}/uninstall")
async def uninstall_skill(
    skill_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Uninstall a skill from user's skills directory."""
    skill = await skill_svc.get_skill(db, skill_id)
    if not skill:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Skill not found"},
        )
    removed = uninstall_skill_for_user(current_user.workspace, skill.name)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Skill not installed"},
        )
    # Sync to user_skills table (Phase H1)
    from skills import user_skill_service as us_svc
    await us_svc.remove_user_skill(db, current_user.id, skill.name)
    return ok({"uninstalled": True, "skill_name": skill.name})
