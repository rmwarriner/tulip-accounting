"""Tests for the right-to-erasure flow (H-2 + H-3, #235).

Covers:
* ``DELETE /v1/users/{user_id}`` — admin-only, last-admin guard,
  schema cascade (sessions + MFA codes), audit redaction.
* ``POST /v1/households/me/erase-request`` — issues a fresh token.
* ``DELETE /v1/households/me`` — two-step confirmation, schema cascade,
  attachment ciphertext unlink on disk.
* ``AttachmentRepository.delete()`` — refcount + blob unlink.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from _problem_details import assert_problem
from tulip_storage.models import (
    Attachment,
    AuditLog,
    Household,
    PendingHouseholdErasure,
    User,
    UserRole,
)
from tulip_storage.repositories import AttachmentRepository

REG_PASSWORD = "correct horse battery staple"


@pytest.fixture
def registered(client: TestClient) -> dict[str, str]:
    body = {
        "email": "alice@example.com",
        "password": REG_PASSWORD,
        "display_name": "Alice",
        "household_name": "Smith",
    }
    r = client.post("/v1/auth/register", json=body)
    assert r.status_code == 201, r.text
    return body


def _access_token(client: TestClient, email: str) -> str:
    r = client.post("/v1/auth/login", json={"email": email, "password": REG_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _seed_second_admin(session_maker: sessionmaker[Session], household_id, *, email: str) -> User:
    """Insert a second admin user directly so we can test the last-admin guard."""
    from uuid import uuid4

    from tulip_api.auth.passwords import hash_password

    with session_maker() as s:
        u = User(
            household_id=household_id,
            id=uuid4(),
            email=email,
            password_hash=hash_password(REG_PASSWORD),
            display_name=email.split("@")[0].title(),
            role=UserRole.ADMIN,
        )
        s.add(u)
        s.commit()
        return u


class TestDeleteUser:
    def test_admin_can_delete_a_member(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        # Promote Alice to admin (she already is via register), seed a member.
        from uuid import uuid4

        from tulip_api.auth.passwords import hash_password

        with session_maker() as s:
            h = s.execute(select(Household)).scalar_one()
            member = User(
                household_id=h.id,
                id=uuid4(),
                email="bob@example.com",
                password_hash=hash_password(REG_PASSWORD),
                display_name="Bob",
                role=UserRole.MEMBER,
            )
            s.add(member)
            s.commit()
            member_id = member.id

        access = _access_token(client, registered["email"])
        r = client.delete(
            f"/v1/users/{member_id}",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert r.status_code == 204, r.text

        with session_maker() as s:
            assert s.get(User, (h.id, member_id)) is None
            actions = [
                row.action
                for row in s.execute(select(AuditLog).order_by(AuditLog.occurred_at)).scalars()
            ]
        assert "user.deleted" in actions

    def test_refuses_to_delete_last_admin(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        with session_maker() as s:
            u = s.execute(select(User)).scalar_one()
            user_id = u.id

        access = _access_token(client, registered["email"])
        r = client.delete(
            f"/v1/users/{user_id}",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert_problem(r, code="user.last_admin", status=409)

    def test_can_delete_one_of_two_admins(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        with session_maker() as s:
            h = s.execute(select(Household)).scalar_one()
            household_id = h.id
        second = _seed_second_admin(session_maker, household_id, email="carol@example.com")

        access = _access_token(client, registered["email"])
        r = client.delete(
            f"/v1/users/{second.id}",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert r.status_code == 204, r.text

    def test_redacts_historic_audit_pii(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        # Seed a second admin who registers, then delete them; their
        # registration audit row (which has email in after_snapshot)
        # must have its JSON blobs nulled.
        with session_maker() as s:
            h = s.execute(select(Household)).scalar_one()
            household_id = h.id
        second = _seed_second_admin(session_maker, household_id, email="carol@example.com")

        with session_maker() as s:
            # Mimic a register-style audit row for carol so we have PII to scrub.
            from uuid import uuid4

            from tulip_storage.repositories import AuditLogWriter

            AuditLogWriter(s, household_id).write(
                action="register",
                actor_kind="user",
                actor_user_id=second.id,
                entity_type="user",
                entity_id=second.id,
                after={"email": "carol@example.com", "role": "admin"},
                request_id=uuid4(),
            )
            s.commit()

        access = _access_token(client, registered["email"])
        r = client.delete(
            f"/v1/users/{second.id}",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert r.status_code == 204

        with session_maker() as s:
            rows = (
                s.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == second.id, AuditLog.action == "register"
                    )
                )
                .scalars()
                .all()
            )
        # The register row still exists (we keep the pseudonymous user id
        # under Art. 17(3)(e)) but its PII payload is nulled.
        assert rows
        assert all(r.after_snapshot is None and r.before_snapshot is None for r in rows)

    def test_member_cannot_delete_anyone(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        with session_maker() as s:
            u = s.execute(select(User)).scalar_one()
            u.role = UserRole.MEMBER
            s.commit()

        access = _access_token(client, registered["email"])
        from uuid import uuid4

        r = client.delete(
            f"/v1/users/{uuid4()}",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert_problem(r, code="auth.forbidden", status=403)


class TestEraseHousehold:
    def test_two_step_flow_deletes_household_and_files(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
        settings,
    ):
        # Seed an attachment so we can prove the file is unlinked.
        with session_maker() as s:
            h = s.execute(select(Household)).scalar_one()
            household_id = h.id
            repo = AttachmentRepository(
                s,
                household_id,
                master_key=settings.master_key,
                attachment_root=settings.attachment_root,
            )
            att = repo.create(
                filename="statement.pdf",
                content_type="application/pdf",
                raw_bytes=b"hello tulip",
            )
            s.commit()
            content_hash = att.content_hash

        blob = settings.attachment_root / content_hash
        assert blob.exists()

        access = _access_token(client, registered["email"])
        r1 = client.post(
            "/v1/households/me/erase-request",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert r1.status_code == 200, r1.text
        token = r1.json()["token"]

        r2 = client.delete(
            "/v1/households/me",
            headers={
                "Authorization": f"Bearer {access}",
                "X-Erasure-Token": token,
            },
        )
        assert r2.status_code == 204, r2.text

        # Household gone.
        with session_maker() as s:
            assert s.get(Household, household_id) is None
            assert s.get(PendingHouseholdErasure, household_id) is None
            # Cascade purged attachments.
            attachments = s.execute(select(Attachment)).scalars().all()
            assert attachments == []

        # Ciphertext gone from disk.
        assert not blob.exists()

    def test_delete_without_token_refused(
        self,
        client: TestClient,
        registered: dict[str, str],
    ):
        access = _access_token(client, registered["email"])
        r = client.delete(
            "/v1/households/me",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert_problem(r, code="household.erasure_not_requested", status=409)

    def test_delete_with_wrong_token_refused(
        self,
        client: TestClient,
        registered: dict[str, str],
    ):
        access = _access_token(client, registered["email"])
        client.post(
            "/v1/households/me/erase-request",
            headers={"Authorization": f"Bearer {access}"},
        )
        r = client.delete(
            "/v1/households/me",
            headers={
                "Authorization": f"Bearer {access}",
                "X-Erasure-Token": "wrong-token",
            },
        )
        assert_problem(r, code="household.erasure_token_invalid", status=401)

    def test_member_cannot_initiate_or_confirm(
        self,
        client: TestClient,
        registered: dict[str, str],
        session_maker: sessionmaker[Session],
    ):
        with session_maker() as s:
            u = s.execute(select(User)).scalar_one()
            u.role = UserRole.MEMBER
            s.commit()

        access = _access_token(client, registered["email"])
        r1 = client.post(
            "/v1/households/me/erase-request",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert_problem(r1, code="auth.forbidden", status=403)

        r2 = client.delete(
            "/v1/households/me",
            headers={
                "Authorization": f"Bearer {access}",
                "X-Erasure-Token": "irrelevant",
            },
        )
        assert_problem(r2, code="auth.forbidden", status=403)


class TestAttachmentDelete:
    def test_delete_unlinks_blob_when_refcount_zero(
        self,
        session_maker: sessionmaker[Session],
        settings,
    ):
        # Seed a household + one attachment row, then delete; the
        # ciphertext must disappear from disk.
        from uuid import uuid4

        with session_maker() as s:
            h = Household(id=uuid4(), name="X", base_currency="USD")
            s.add(h)
            s.commit()
            repo = AttachmentRepository(
                s,
                h.id,
                master_key=settings.master_key,
                attachment_root=settings.attachment_root,
            )
            att = repo.create(
                filename="t.pdf",
                content_type="application/pdf",
                raw_bytes=b"abc",
            )
            s.commit()
            attachment_id = att.id
            content_hash = att.content_hash

        blob = settings.attachment_root / content_hash
        assert blob.exists()

        with session_maker() as s:
            repo = AttachmentRepository(
                s,
                h.id,
                master_key=settings.master_key,
                attachment_root=settings.attachment_root,
            )
            ok = repo.delete(attachment_id)
            assert ok is True
            s.commit()

        assert not blob.exists()


class TestAttachmentGc:
    def test_run_gc_unlinks_orphans_only(
        self,
        session_maker: sessionmaker[Session],
        settings,
        tmp_path: Path,
    ):
        from uuid import uuid4

        from tulip_storage.runner.handlers import run_attachment_gc

        attachment_root: Path = settings.attachment_root
        attachment_root.mkdir(parents=True, exist_ok=True)

        with session_maker() as s:
            h = Household(id=uuid4(), name="X", base_currency="USD")
            s.add(h)
            s.commit()
            repo = AttachmentRepository(
                s,
                h.id,
                master_key=settings.master_key,
                attachment_root=attachment_root,
            )
            kept = repo.create(
                filename="kept.pdf",
                content_type="application/pdf",
                raw_bytes=b"kept",
            )
            s.commit()
            kept_hash = kept.content_hash

        # Drop a fake orphan blob with a sha256-shaped name.
        orphan_hash = "0" * 64
        orphan = attachment_root / orphan_hash
        orphan.write_bytes(b"orphan")

        # Files older than min_age_seconds get swept; back-date both.
        import os

        old = 1_000_000.0
        os.utime(orphan, (old, old))
        os.utime(attachment_root / kept_hash, (old, old))

        deleted = run_attachment_gc(
            session_maker,
            attachment_root,
            now_seconds=old + 7200.0,  # 2 hours later → over min_age
            min_age_seconds=3600,
        )
        assert deleted == 1
        assert not orphan.exists()
        assert (attachment_root / kept_hash).exists()
