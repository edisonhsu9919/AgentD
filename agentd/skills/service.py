import uuid
from typing import Any, Optional

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from skills.models import Skill


async def create_skill(
    db: AsyncSession,
    name: str,
    description: str,
    content: str,
    tags: list[str] | None = None,
    created_by: uuid.UUID | None = None,
    version: str = "0.1.0",
    license: str = "",
    compatibility: str = "",
    metadata_extra: dict[str, Any] | None = None,
    source_type: str = "manual",
    source_path: str | None = None,
) -> Skill:
    skill = Skill(
        id=uuid.uuid4(),
        name=name,
        description=description,
        content=content,
        tags=tags or [],
        created_by=created_by,
        version=version,
        license=license,
        compatibility=compatibility,
        metadata_extra=metadata_extra or {},
        source_type=source_type,
        source_path=source_path,
    )
    db.add(skill)
    await db.flush()
    return skill


async def list_skills(
    db: AsyncSession,
    include_inactive: bool = False,
) -> list[Skill]:
    """Return all skills, ordered by name then version. Excludes inactive by default."""
    q = select(Skill).order_by(Skill.name, Skill.version)
    if not include_inactive:
        q = q.where(Skill.is_active == True)  # noqa: E712
    rows = (await db.execute(q)).scalars().all()
    return list(rows)


async def get_skill(
    db: AsyncSession, skill_id: uuid.UUID
) -> Optional[Skill]:
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    return result.scalar_one_or_none()


async def get_skill_by_name(
    db: AsyncSession, name: str
) -> Optional[Skill]:
    """Return the first active skill with this name (any version)."""
    result = await db.execute(
        select(Skill)
        .where(and_(Skill.name == name, Skill.is_active == True))  # noqa: E712
        .order_by(Skill.version.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_skill_by_name_version(
    db: AsyncSession, name: str, version: str
) -> Optional[Skill]:
    """Return the exact (name, version) skill."""
    result = await db.execute(
        select(Skill).where(
            and_(Skill.name == name, Skill.version == version)
        )
    )
    return result.scalar_one_or_none()


async def update_skill(
    db: AsyncSession,
    skill_id: uuid.UUID,
    **kwargs,
) -> Optional[Skill]:
    """Update a skill's fields. Only provided (non-None) kwargs are applied."""
    values = {k: v for k, v in kwargs.items() if v is not None}
    if not values:
        return await get_skill(db, skill_id)

    await db.execute(
        update(Skill).where(Skill.id == skill_id).values(**values)
    )
    await db.flush()
    return await get_skill(db, skill_id)


async def delete_skill(db: AsyncSession, skill_id: uuid.UUID) -> bool:
    """Soft-delete: set is_active=False. Returns True if skill existed."""
    skill = await get_skill(db, skill_id)
    if not skill:
        return False
    await db.execute(
        update(Skill).where(Skill.id == skill_id).values(is_active=False)
    )
    return True
