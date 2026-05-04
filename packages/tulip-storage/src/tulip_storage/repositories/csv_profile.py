"""CsvProfileRepository — CRUD for per-household CSV column-mapping profiles."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select

from tulip_storage.models import CsvProfile

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class CsvProfileRepository:
    """Persists CSV profiles within one household."""

    def __init__(self, session: Session, household_id: UUID) -> None:
        """Bind the repository to a session and tenant scope."""
        self._session = session
        self._household_id = household_id

    def get(self, profile_id: UUID) -> CsvProfile | None:
        """Return a profile by id, or None."""
        return self._session.execute(
            select(CsvProfile).where(
                CsvProfile.household_id == self._household_id,
                CsvProfile.id == profile_id,
            )
        ).scalar_one_or_none()

    def get_by_name(self, name: str) -> CsvProfile | None:
        """Return the profile with the given name, or None."""
        return self._session.execute(
            select(CsvProfile).where(
                CsvProfile.household_id == self._household_id,
                CsvProfile.name == name,
            )
        ).scalar_one_or_none()

    def list_all(self) -> list[CsvProfile]:
        """Return all profiles in this household, ordered by name."""
        return list(
            self._session.execute(
                select(CsvProfile)
                .where(CsvProfile.household_id == self._household_id)
                .order_by(CsvProfile.name)
            )
            .scalars()
            .all()
        )

    def create(
        self,
        *,
        name: str,
        yaml_body: str,
        created_by_user_id: UUID | None = None,
    ) -> CsvProfile:
        """Insert a new CSV profile."""
        now = datetime.now(tz=UTC)
        profile = CsvProfile(
            household_id=self._household_id,
            id=uuid4(),
            name=name,
            yaml_body=yaml_body,
            created_by_user_id=created_by_user_id,
            created_at=now,
            updated_at=now,
        )
        self._session.add(profile)
        self._session.flush()
        return profile

    def update_yaml(self, profile_id: UUID, yaml_body: str) -> CsvProfile:
        """Update the YAML body of an existing profile."""
        profile = self.get(profile_id)
        if profile is None:
            raise LookupError(
                f"csv_profile {profile_id} not found in household {self._household_id}"
            )
        profile.yaml_body = yaml_body
        profile.updated_at = datetime.now(tz=UTC)
        self._session.flush()
        return profile

    def delete(self, profile_id: UUID) -> None:
        """Hard-delete a CSV profile."""
        profile = self.get(profile_id)
        if profile is None:
            raise LookupError(
                f"csv_profile {profile_id} not found in household {self._household_id}"
            )
        self._session.delete(profile)
        self._session.flush()
