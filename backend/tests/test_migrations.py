"""Migrations 0009 and 0010 on a disposable database, the way production gets them: an existing
schema with data -> upgrade -> downgrade -> upgrade again. Nothing outside the new tables changes.

Its own database (`aichat_migrations_test`, same server as the test database), created and
dropped here, so the shared test database is never downgraded.
"""

import asyncio
import subprocess
import sys
import threading

import asyncpg

from tests.conftest import ROOT, TEST_DATABASE_URL, _asyncpg_dsn

URL = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/aichat_migrations_test"
DSN = _asyncpg_dsn(URL)
ADMIN = DSN.rsplit("/", 1)[0] + "/postgres"
BEFORE = "0008_learning_notifications"
GREETINGS = "0009_study_greetings"
FILE_TABLES = ("user_files", "user_file_blobs", "file_chunks", "message_attachments")


def run(coroutine):
    """In a thread of its own, so this event loop never touches pytest-asyncio's."""
    result = {}
    worker = threading.Thread(target=lambda: result.setdefault("value", asyncio.run(coroutine)))
    worker.start()
    worker.join()
    return result.get("value")


def alembic(*args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"url={URL}", *args], cwd=ROOT, check=True, capture_output=True
    )


async def _admin(sql: str) -> None:
    conn = await asyncpg.connect(ADMIN)
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


async def _query(sql: str, *args):
    conn = await asyncpg.connect(DSN)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def _snapshot(new_tables: tuple[str, ...] = ("study_greetings",)) -> dict[str, tuple]:
    """Every table but the new ones: its columns, and a digest of its rows."""
    conn = await asyncpg.connect(DSN)
    try:
        tables = [
            row["tablename"]
            for row in await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> ALL($1::text[])",
                list(new_tables),
            )
        ]
        snapshot = {}
        for table in tables:
            columns = await conn.fetch(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = $1 ORDER BY ordinal_position",
                table,
            )
            digest = await conn.fetchval(
                f"SELECT md5(coalesce(string_agg(t::text, ',' ORDER BY t::text), '')) FROM \"{table}\" t"
            )
            snapshot[table] = (tuple(tuple(column) for column in columns), digest)
        return snapshot
    finally:
        await conn.close()


def test_migration_0009_upgrades_downgrades_and_upgrades_again_without_touching_anything_else():
    run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))
    run(_admin('CREATE DATABASE "aichat_migrations_test"'))
    try:
        alembic("upgrade", BEFORE)
        # Existing data, as production has it.
        run(
            _query(
                "INSERT INTO users (email, password_hash, display_name) VALUES ($1, 'x', 'Existing student')",
                "existing@example.com",
            )
        )
        before = run(_snapshot())
        assert before["users"][1] != run(_query("SELECT md5('')"))[0][0]  # the row is really there

        alembic("upgrade", GREETINGS)
        rows = run(_query("SELECT text, active_from, retired_from FROM study_greetings ORDER BY id"))
        assert len(rows) == 12
        assert {str(row["active_from"]) for row in rows} == {"2026-09-28"}
        assert all(row["retired_from"] is None for row in rows)
        constraints = {
            row["conname"]: row["contype"]
            for row in run(
                _query(
                    "SELECT conname, contype::text AS contype FROM pg_constraint "
                    "WHERE conrelid = 'study_greetings'::regclass"
                )
            )
        }
        assert constraints == {
            "pk_study_greetings": "p",
            "uq_study_greetings_text": "u",
            "ck_study_greetings_text": "c",
            "ck_study_greetings_active_from_monday": "c",
            "ck_study_greetings_retired_from": "c",
        }
        after = run(_snapshot())
        assert {table: value for table, value in after.items() if table != "alembic_version"} == {
            table: value for table, value in before.items() if table != "alembic_version"
        }

        alembic("downgrade", BEFORE)
        assert run(_query("SELECT to_regclass('public.study_greetings') AS t"))[0]["t"] is None
        down = run(_snapshot())
        assert {t: v for t, v in down.items() if t != "alembic_version"} == {
            t: v for t, v in before.items() if t != "alembic_version"
        }

        alembic("upgrade", GREETINGS)
        assert len(run(_query("SELECT 1 FROM study_greetings"))) == 12
    finally:
        run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))


def test_migration_0010_upgrades_downgrades_and_upgrades_again_without_touching_anything_else():
    run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))
    run(_admin('CREATE DATABASE "aichat_migrations_test"'))
    try:
        alembic("upgrade", GREETINGS)
        run(
            _query(
                "INSERT INTO users (email, password_hash, display_name) VALUES ($1, 'x', 'Existing student')",
                "existing@example.com",
            )
        )
        before = run(_snapshot(FILE_TABLES))

        alembic("upgrade", "0010_user_files")
        for table in FILE_TABLES:
            assert run(_query("SELECT to_regclass($1) AS t", f"public.{table}"))[0]["t"] is not None, table
        column = run(
            _query(
                "SELECT format_type(atttypid, atttypmod) AS t FROM pg_attribute "
                "WHERE attrelid = 'file_chunks'::regclass AND attname = 'embedding'"
            )
        )[0]["t"]
        assert column == "vector(1536)"
        after = run(_snapshot(FILE_TABLES))
        assert {t: v for t, v in after.items() if t != "alembic_version"} == {
            t: v for t, v in before.items() if t != "alembic_version"
        }

        alembic("downgrade", GREETINGS)
        for table in FILE_TABLES:
            assert run(_query("SELECT to_regclass($1) AS t", f"public.{table}"))[0]["t"] is None, table
        down = run(_snapshot(FILE_TABLES))
        assert {t: v for t, v in down.items() if t != "alembic_version"} == {
            t: v for t, v in before.items() if t != "alembic_version"
        }

        alembic("upgrade", "head")
        assert run(_query("SELECT count(*) AS n FROM user_files"))[0]["n"] == 0
    finally:
        run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))


MEMORY = "0018_memory_items"


def test_migration_0018_adds_and_removes_memory_without_touching_existing_data():
    run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))
    run(_admin('CREATE DATABASE "aichat_migrations_test"'))
    try:
        alembic("upgrade", "0017_learning_engine")
        run(
            _query(
                "INSERT INTO users (email, password_hash, display_name) VALUES ($1, 'x', 'Existing student')",
                "memory-existing@example.com",
            )
        )
        before = run(_snapshot(("memory_items",)))

        alembic("upgrade", MEMORY)
        assert run(_query("SELECT to_regclass('public.memory_items') AS t"))[0]["t"] == "memory_items"
        vector_type = run(
            _query(
                "SELECT format_type(atttypid, atttypmod) AS t FROM pg_attribute "
                "WHERE attrelid = 'memory_items'::regclass AND attname = 'embedding'"
            )
        )[0]["t"]
        assert vector_type == "vector(1536)"
        constraints = {
            row["conname"]: row["contype"]
            for row in run(
                _query(
                    "SELECT conname, contype::text AS contype FROM pg_constraint "
                    "WHERE conrelid = 'memory_items'::regclass"
                )
            )
        }
        assert constraints == {
            "pk_memory_items": "p",
            "fk_memory_items_user_id_users": "f",
            "fk_memory_items_supersedes_id_memory_items": "f",
            "fk_memory_items_source_message_id_messages": "f",
            "ck_memory_items_kind": "c",
            "ck_memory_items_status": "c",
            "ck_memory_items_content_length": "c",
            "ck_memory_items_version": "c",
            "ck_memory_items_expires_after_create": "c",
            "uq_memory_items_supersedes_id": "u",
        }
        indexes = {
            row["indexname"] for row in run(_query("SELECT indexname FROM pg_indexes WHERE tablename = 'memory_items'"))
        }
        assert {
            "uq_memory_items_active_content",
            "ix_memory_items_user_status_updated",
            "ix_memory_items_user_kind_status",
        } <= indexes
        after = run(_snapshot(("memory_items",)))
        assert {t: v for t, v in after.items() if t != "alembic_version"} == {
            t: v for t, v in before.items() if t != "alembic_version"
        }

        alembic("downgrade", "0017_learning_engine")
        assert run(_query("SELECT to_regclass('public.memory_items') AS t"))[0]["t"] is None
        down = run(_snapshot(("memory_items",)))
        assert {t: v for t, v in down.items() if t != "alembic_version"} == {
            t: v for t, v in before.items() if t != "alembic_version"
        }

        alembic("upgrade", "head")
        assert run(_query("SELECT count(*) AS n FROM memory_items"))[0]["n"] == 0
    finally:
        run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))


MEMORY_FOUNDATION = "0019_memory_foundation"


def test_migration_0019_downgrade_refuses_populated_new_data_before_ddl():
    run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))
    run(_admin('CREATE DATABASE "aichat_migrations_test"'))
    try:
        alembic("upgrade", MEMORY_FOUNDATION)
        user_id = run(
            _query(
                "INSERT INTO users (email, password_hash, display_name) VALUES ($1, 'x', 'Memory owner') RETURNING id",
                "memory-0019@example.com",
            )
        )[0]["id"]
        run(
            _query(
                "INSERT INTO memory_deletion_markers (user_id, content_hmac, policy_version, reason) "
                "VALUES ($1, $2, 'v1', 'delete')",
                user_id,
                "a" * 64,
            )
        )
        process = subprocess.run(
            [sys.executable, "-m", "alembic", "-x", f"url={URL}", "downgrade", MEMORY],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert process.returncode != 0
        assert "downgrade refused" in process.stderr
        assert run(_query("SELECT to_regclass('public.memory_deletion_markers') AS t"))[0]["t"] == (
            "memory_deletion_markers"
        )
        assert run(_query("SELECT count(*) AS n FROM memory_deletion_markers"))[0]["n"] == 1
        assert run(_query("SELECT version_num FROM alembic_version"))[0]["version_num"] == MEMORY_FOUNDATION
    finally:
        run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))


def test_migration_0019_empty_downgrade_and_forward_preserve_legacy_memory():
    run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))
    run(_admin('CREATE DATABASE "aichat_migrations_test"'))
    try:
        alembic("upgrade", MEMORY)
        user_id = run(
            _query(
                "INSERT INTO users (email, password_hash, display_name) VALUES ($1, 'x', 'Legacy owner') RETURNING id",
                "memory-legacy@example.com",
            )
        )[0]["id"]
        run(
            _query(
                "INSERT INTO memory_items (user_id, kind, content, content_hash, embedding, embedding_model) "
                "VALUES ($1, 'learning_context', 'Legacy memory', $2, $3::vector, 'legacy-model')",
                user_id,
                "b" * 64,
                "[" + ",".join(["0"] * 1536) + "]",
            )
        )
        alembic("upgrade", MEMORY_FOUNDATION)
        row = run(
            _query(
                "SELECT content, origin, embedding_dimensions, embedding_version FROM memory_items WHERE user_id = $1",
                user_id,
            )
        )[0]
        assert tuple(row) == ("Legacy memory", "explicit", 1536, "v1")
        alembic("downgrade", MEMORY)
        legacy = run(_query("SELECT content, embedding_model FROM memory_items WHERE user_id = $1", user_id))[0]
        assert tuple(legacy) == ("Legacy memory", "legacy-model")
        assert run(_query("SELECT to_regclass('public.memory_proposals') AS t"))[0]["t"] is None
        alembic("upgrade", MEMORY_FOUNDATION)
        assert run(_query("SELECT count(*) AS n FROM memory_items WHERE user_id = $1", user_id))[0]["n"] == 1
    finally:
        run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))


ENGINEERING = "0020_engineering_profiles"
HYBRID_SEARCH = "0021_kb_hybrid_search"
PASSWORD_SESSION_REVOCATION = "0022_password_session_revocation"
KNOWLEDGE_LIBRARY = "0023_knowledge_library"


async def _refused(sql: str, *args) -> bool:
    """Whether a CHECK refuses the statement (run() cannot carry an exception out of its thread)."""
    try:
        await _query(sql, *args)
    except asyncpg.CheckViolationError:
        return True
    return False


def test_migration_0020_adds_and_removes_the_engineering_profile_without_touching_anything_else():
    run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))
    run(_admin('CREATE DATABASE "aichat_migrations_test"'))
    try:
        alembic("upgrade", MEMORY_FOUNDATION)
        user_id = run(
            _query(
                "INSERT INTO users (email, password_hash, display_name) VALUES ($1, 'x', 'Existing user') RETURNING id",
                "engineering-existing@example.com",
            )
        )[0]["id"]
        run(_query("INSERT INTO learner_profiles (user_id, grade) VALUES ($1, 'class_10')", user_id))
        before = run(_snapshot(("engineering_profiles",)))

        alembic("upgrade", ENGINEERING)
        constraints = {
            row["conname"]: row["contype"]
            for row in run(
                _query(
                    "SELECT conname, contype::text AS contype FROM pg_constraint "
                    "WHERE conrelid = 'engineering_profiles'::regclass"
                )
            )
        }
        assert constraints == {
            "pk_engineering_profiles": "p",
            "fk_engineering_profiles_user_id_users": "f",
            "ck_engineering_profiles_focus_areas": "c",
            "ck_engineering_profiles_platforms": "c",
        }
        run(_query("INSERT INTO engineering_profiles (user_id, role) VALUES ($1, 'soc_analyst')", user_id))
        row = run(_query("SELECT version, focus_areas, platforms, updated_at FROM engineering_profiles"))[0]
        assert (row["version"], row["focus_areas"], row["platforms"]) == (0, [], [])
        assert row["updated_at"] is not None
        assert run(_refused("UPDATE engineering_profiles SET platforms = $1", [f"p{n}" for n in range(13)]))
        assert run(_refused("UPDATE engineering_profiles SET focus_areas = $1", [f"f{n}" for n in range(9)]))
        assert not run(_refused("UPDATE engineering_profiles SET platforms = $1", [f"p{n}" for n in range(12)]))
        after = run(_snapshot(("engineering_profiles",)))
        assert {t: v for t, v in after.items() if t != "alembic_version"} == {
            t: v for t, v in before.items() if t != "alembic_version"
        }
        leaving = run(
            _query(
                "INSERT INTO users (email, password_hash, display_name) VALUES ($1, 'x', 'Leaving user') RETURNING id",
                "engineering-leaving@example.com",
            )
        )[0]["id"]
        run(_query("INSERT INTO engineering_profiles (user_id) VALUES ($1)", leaving))
        run(_query("DELETE FROM users WHERE id = $1", leaving))  # the profile goes with its user
        assert run(_query("SELECT count(*) AS n FROM engineering_profiles"))[0]["n"] == 1

        alembic("downgrade", MEMORY_FOUNDATION)
        assert run(_query("SELECT to_regclass('public.engineering_profiles') AS t"))[0]["t"] is None
        down = run(_snapshot(("engineering_profiles",)))
        assert {t: v for t, v in down.items() if t != "alembic_version"} == {
            t: v for t, v in before.items() if t != "alembic_version"
        }
        alembic("upgrade", ENGINEERING)
        assert run(_query("SELECT count(*) AS n FROM engineering_profiles"))[0]["n"] == 0
    finally:
        run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))


def test_migration_0021_adds_hybrid_search_without_rewriting_the_insert_contract():
    run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))
    run(_admin('CREATE DATABASE "aichat_migrations_test"'))
    try:
        alembic("upgrade", ENGINEERING)
        user_id = run(
            _query(
                "INSERT INTO users (email, password_hash, display_name) VALUES ($1, 'x', 'KB owner') RETURNING id",
                "kb-0021@example.com",
            )
        )[0]["id"]
        source_id = run(
            _query(
                "INSERT INTO knowledge_sources (name, owner_user_id) VALUES ('Private KB', $1) RETURNING id", user_id
            )
        )[0]["id"]
        document_id = run(
            _query(
                "INSERT INTO documents (source_id, uri, title, content_hash, ingest_signature) "
                "VALUES ($1, 'kb://existing', 'Existing', $2, $3) RETURNING id",
                source_id,
                "a" * 64,
                "b" * 64,
            )
        )[0]["id"]
        vector = "[" + ",".join(["0"] * 1536) + "]"
        existing_id = run(
            _query(
                "INSERT INTO document_chunks "
                "(document_id, source_id, ordinal, content, embedding, embedding_model) "
                "VALUES ($1, $2, 0, 'Configure OSPF area 0', $3::vector, 'legacy-model') RETURNING id",
                document_id,
                source_id,
                vector,
            )
        )[0]["id"]

        alembic("upgrade", HYBRID_SEARCH)
        column = run(
            _query(
                "SELECT format_type(atttypid, atttypmod) AS type, attgenerated, "
                "pg_get_expr(adbin, adrelid) AS expression "
                "FROM pg_attribute JOIN pg_attrdef ON adrelid = attrelid AND adnum = attnum "
                "WHERE attrelid = 'document_chunks'::regclass AND attname = 'content_tsv'"
            )
        )[0]
        assert column["type"] == "tsvector"
        assert column["attgenerated"] in ("s", b"s")
        assert column["expression"] == "to_tsvector('simple'::regconfig, content)"
        index = run(
            _query(
                "SELECT am.amname, pg_get_indexdef(i.indexrelid) AS definition "
                "FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "JOIN pg_am am ON am.oid = c.relam "
                "WHERE c.relname = 'ix_document_chunks_content_tsv_gin'"
            )
        )[0]
        assert index["amname"] == "gin"
        assert "USING gin (content_tsv)" in index["definition"]
        existing = run(
            _query("SELECT content, content_tsv::text AS tsv FROM document_chunks WHERE id = $1", existing_id)
        )[0]
        assert tuple(existing) == ("Configure OSPF area 0", "'0':4 'area':3 'configure':1 'ospf':2")

        inserted = run(
            _query(
                "INSERT INTO document_chunks "
                "(document_id, source_id, ordinal, content, embedding, embedding_model) "
                "VALUES ($1, $2, 1, 'BGP route policy', $3::vector, 'legacy-model') "
                "RETURNING content_tsv::text AS tsv",
                document_id,
                source_id,
                vector,
            )
        )[0]
        assert inserted["tsv"] == "'bgp':1 'policy':3 'route':2"

        alembic("downgrade", ENGINEERING)
        assert (
            run(
                _query(
                    "SELECT count(*) AS n FROM document_chunks "
                    "WHERE document_id = $1 AND content = 'Configure OSPF area 0'",
                    document_id,
                )
            )[0]["n"]
            == 1
        )
        assert (
            run(
                _query(
                    "SELECT count(*) AS n FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'document_chunks' AND column_name = 'content_tsv'"
                )
            )[0]["n"]
            == 0
        )
        assert run(_query("SELECT to_regclass('public.ix_document_chunks_content_tsv_gin') AS t"))[0]["t"] is None

        alembic("upgrade", HYBRID_SEARCH)
        assert run(_query("SELECT content_tsv::text AS tsv FROM document_chunks WHERE id = $1", existing_id))[0][
            "tsv"
        ] == ("'0':4 'area':3 'configure':1 'ospf':2")
        alembic("upgrade", "head")
        assert run(_query("SELECT version_num FROM alembic_version"))[0]["version_num"] == KNOWLEDGE_LIBRARY
    finally:
        run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))


def test_migration_0022_revokes_only_sessions_that_predate_a_real_password_change():
    run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))
    run(_admin('CREATE DATABASE "aichat_migrations_test"'))
    try:
        alembic("upgrade", HYBRID_SEARCH)
        credential_user = run(
            _query(
                "INSERT INTO users (email, password_hash, display_name) "
                "VALUES ('credential-0022@example.com', '$argon2id$old', 'Credential user') RETURNING id"
            )
        )[0]["id"]
        passwordless_user = run(
            _query(
                "INSERT INTO users (email, password_hash, display_name) "
                "VALUES ('passwordless-0022@example.com', NULL, 'Passwordless user') RETURNING id"
            )
        )[0]["id"]
        run(
            _query(
                "INSERT INTO auth_accounts (user_id, account_id, provider_id) VALUES ($1, 'google-0022', 'google')",
                credential_user,
            )
        )
        run(
            _query(
                "INSERT INTO auth_sessions (user_id, token, expires_at) VALUES "
                "($1, 'credential-old-a', now() + interval '1 day'), "
                "($1, 'credential-old-b', now() + interval '1 day'), "
                "($2, 'passwordless-current', now() + interval '1 day')",
                credential_user,
                passwordless_user,
            )
        )

        alembic("upgrade", PASSWORD_SESSION_REVOCATION)
        function = run(
            _query(
                "SELECT prosecdef FROM pg_proc WHERE oid = "
                "'auth_accounts_revoke_sessions_on_password_change()'::regprocedure"
            )
        )[0]
        assert function["prosecdef"] is False

        # OAuth account writes and the first credential insert are not password changes.
        run(_query("UPDATE auth_accounts SET access_token = 'ignored' WHERE provider_id = 'google'"))
        run(
            _query(
                "INSERT INTO auth_accounts (user_id, account_id, provider_id, password) "
                "VALUES ($1, $2, 'credential', '$argon2id$first')",
                passwordless_user,
                str(passwordless_user),
            )
        )
        assert run(_query("SELECT count(*) AS n FROM auth_sessions WHERE user_id = $1", passwordless_user))[0]["n"] == 1

        # A no-op password write changes no credential and keeps every session.
        run(
            _query(
                "UPDATE auth_accounts SET password = password, updated_at = clock_timestamp() "
                "WHERE user_id = $1 AND provider_id = 'credential'",
                credential_user,
            )
        )
        assert run(_query("SELECT count(*) AS n FROM auth_sessions WHERE user_id = $1", credential_user))[0]["n"] == 2

        # The password hash and all sessions disappear atomically if the statement commits.
        async def rolled_back_password_change() -> None:
            conn = await asyncpg.connect(DSN)
            try:
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE auth_accounts SET password = '$argon2id$rolled-back' "
                        "WHERE user_id = $1 AND provider_id = 'credential'",
                        credential_user,
                    )
                    assert (
                        await conn.fetchval("SELECT count(*) FROM auth_sessions WHERE user_id = $1", credential_user)
                        == 0
                    )
                    raise RuntimeError("force rollback")
            except RuntimeError as error:
                assert str(error) == "force rollback"
            finally:
                await conn.close()

        run(rolled_back_password_change())
        assert run(_query("SELECT count(*) AS n FROM auth_sessions WHERE user_id = $1", credential_user))[0]["n"] == 2
        assert (
            run(
                _query(
                    "SELECT password FROM auth_accounts WHERE user_id = $1 AND provider_id = 'credential'",
                    credential_user,
                )
            )[0]["password"]
            == "$argon2id$old"
        )

        run(
            _query(
                "UPDATE auth_accounts SET password = '$argon2id$new' WHERE user_id = $1 AND provider_id = 'credential'",
                credential_user,
            )
        )
        assert run(_query("SELECT count(*) AS n FROM auth_sessions WHERE user_id = $1", credential_user))[0]["n"] == 0
        assert run(_query("SELECT count(*) AS n FROM auth_sessions WHERE user_id = $1", passwordless_user))[0]["n"] == 1
        assert (
            run(_query("SELECT password_hash FROM users WHERE id = $1", credential_user))[0]["password_hash"]
            == "$argon2id$new"
        )

        # Better Auth creates the caller replacement after changing the hash; the trigger must not
        # delete sessions inserted after that update.
        run(
            _query(
                "INSERT INTO auth_sessions (user_id, token, expires_at) "
                "VALUES ($1, 'credential-replacement', now() + interval '1 day')",
                credential_user,
            )
        )
        assert run(_query("SELECT token FROM auth_sessions WHERE user_id = $1", credential_user))[0]["token"] == (
            "credential-replacement"
        )

        alembic("downgrade", HYBRID_SEARCH)
        assert (
            run(_query("SELECT to_regprocedure('auth_accounts_revoke_sessions_on_password_change()') AS function"))[0][
                "function"
            ]
            is None
        )
        run(
            _query(
                "UPDATE auth_accounts SET password = '$argon2id$post-downgrade' "
                "WHERE user_id = $1 AND provider_id = 'credential'",
                credential_user,
            )
        )
        assert run(_query("SELECT count(*) AS n FROM auth_sessions WHERE user_id = $1", credential_user))[0]["n"] == 1
        alembic("upgrade", PASSWORD_SESSION_REVOCATION)
        assert run(_query("SELECT version_num FROM alembic_version"))[0]["version_num"] == PASSWORD_SESSION_REVOCATION
    finally:
        run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))


AUTH_BEFORE = "0011_notification_cleared_at"
AUTH_AFTER = "0012_better_auth"


def test_migration_0012_keeps_every_account_and_both_releases_agree():
    """Better Auth's tables on the existing identity data: no user id changes, every password
    becomes the same hash in a credential account, and the mirrors keep 0011's code and 0012's in
    agreement in both directions (the rollback path). Its role, aichat_auth, is created without a
    login when missing; it stays in the cluster, inert."""
    run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))
    run(_admin('CREATE DATABASE "aichat_migrations_test"'))
    try:
        alembic("upgrade", AUTH_BEFORE)
        run(
            _query(
                "INSERT INTO users (email, password_hash, email_verified_at) VALUES "
                "('kept@example.com', '$argon2id$kept', now()), ('pending@example.com', '$argon2id$pending', NULL)"
            )
        )
        run(
            _query(
                "INSERT INTO auth_sessions (user_id, token_hash, expires_at) "
                "SELECT id, repeat('a', 64), now() + interval '1 day' FROM users WHERE email = 'kept@example.com'"
            )
        )
        ids = {row["email"]: row["id"] for row in run(_query("SELECT id, email FROM users"))}

        alembic("upgrade", AUTH_AFTER)
        users = {row["email"]: row for row in run(_query("SELECT * FROM users"))}
        assert {email: row["id"] for email, row in users.items()} == ids
        assert users["kept@example.com"]["email_verified"] and not users["pending@example.com"]["email_verified"]
        accounts = {
            row["user_id"]: row
            for row in run(_query("SELECT user_id, account_id, provider_id, password FROM auth_accounts"))
        }
        assert {accounts[ids[e]]["password"] for e in ids} == {"$argon2id$kept", "$argon2id$pending"}
        assert all(
            row["provider_id"] == "credential" and row["account_id"] == str(row["user_id"]) for row in accounts.values()
        )
        assert tuple(run(_query("SELECT token_hash, token FROM auth_sessions"))[0]) == ("a" * 64, None)

        # A Better Auth session: only the token is written; the trigger stores its SHA-256.
        run(
            _query(
                "INSERT INTO auth_sessions (user_id, token, expires_at) "
                "VALUES ($1, 'better-auth-token', now() + interval '1 day')",
                ids["kept@example.com"],
            )
        )
        hashed = run(_query("SELECT token_hash FROM auth_sessions WHERE token = 'better-auth-token'"))[0]["token_hash"]
        assert hashed == run(_query("SELECT encode(sha256('better-auth-token'::bytea), 'hex') AS h"))[0]["h"]

        # 0012's code changes a password (Better Auth): 0011's column follows, and the change is dated.
        run(_query("UPDATE auth_accounts SET password = '$argon2id$new' WHERE user_id = $1", ids["kept@example.com"]))
        kept = run(_query("SELECT password_hash, password_changed_at FROM users WHERE email = 'kept@example.com'"))[0]
        assert kept["password_hash"] == "$argon2id$new" and kept["password_changed_at"] is not None
        # 0011's code (after a rollback) changes one: the credential follows.
        run(_query("UPDATE users SET password_hash = '$argon2id$old-code' WHERE email = 'kept@example.com'"))
        assert run(_query("SELECT password FROM auth_accounts WHERE user_id = $1", ids["kept@example.com"]))[0][0] == (
            "$argon2id$old-code"
        )
        # 0011's code verifies an address and signs someone up: 0012's flag and credential follow.
        run(_query("UPDATE users SET email_verified_at = now() WHERE email = 'pending@example.com'"))
        assert run(_query("SELECT email_verified FROM users WHERE email = 'pending@example.com'"))[0][0] is True
        run(_query("INSERT INTO users (email, password_hash) VALUES ('old-code@example.com', '$argon2id$signup')"))
        assert (
            run(
                _query(
                    "SELECT a.password FROM auth_accounts a JOIN users u ON u.id = a.user_id "
                    "WHERE u.email = 'old-code@example.com' AND a.provider_id = 'credential'"
                )
            )[0][0]
            == "$argon2id$signup"
        )
        # Removing the credential (no password any more) clears 0011's column; deletion cascades.
        run(_query("DELETE FROM auth_accounts WHERE user_id = $1", ids["pending@example.com"]))
        assert run(_query("SELECT password_hash FROM users WHERE email = 'pending@example.com'"))[0][0] is None
        run(_query("DELETE FROM users WHERE email = 'kept@example.com'"))
        assert run(_query("SELECT count(*) FROM auth_accounts WHERE user_id = $1", ids["kept@example.com"]))[0][0] == 0

        # The web app's role reaches identity tables only.
        grants = {
            row["table_name"]
            for row in run(
                _query(
                    "SELECT DISTINCT table_name FROM information_schema.role_table_grants WHERE grantee = 'aichat_auth'"
                )
            )
        }
        assert grants == {"users", "auth_sessions", "auth_accounts", "auth_verifications", "auth_attempts"}

        alembic("downgrade", AUTH_BEFORE)
        assert run(_query("SELECT to_regclass('public.auth_accounts') AS t"))[0]["t"] is None
        # The account left without a password gets an unusable hash 0011's code refuses.
        assert run(_query("SELECT password_hash FROM users WHERE email = 'pending@example.com'"))[0][0] == "!"
        alembic("upgrade", AUTH_AFTER)
        assert run(_query("SELECT count(*) FROM auth_accounts"))[0][0] == 2
    finally:
        run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))


def test_migration_0013_two_step_sign_in_is_additive_and_rolls_back_cleanly():
    """0013 adds a column with a default and a table: every existing row is untouched (the release
    before it runs on this schema), the web app's role can reach the new table, and a downgrade
    leaves exactly the 0012 schema again."""
    run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))
    run(_admin('CREATE DATABASE "aichat_migrations_test"'))
    try:
        alembic("upgrade", AUTH_AFTER)
        run(
            _query(
                "INSERT INTO users (email, password_hash, email_verified_at) VALUES ('two@example.com', '$a', now())"
            )
        )
        before = run(_snapshot(new_tables=()))

        alembic("upgrade", "0013_two_factor")
        assert run(_query("SELECT two_factor_enabled FROM users"))[0][0] is False
        user_id = run(_query("SELECT id FROM users"))[0][0]
        run(_query("INSERT INTO auth_two_factors (user_id, secret, backup_codes) VALUES ($1, 's', 'b')", user_id))
        run(
            _query(
                "INSERT INTO user_notifications (user_id, kind, title, body, dedup_key) "
                "VALUES ($1, 'security_two_factor_on', 't', 'b', 'k')",
                user_id,
            )
        )
        grants = {
            row["privilege_type"]
            for row in run(
                _query(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE grantee = 'aichat_auth' AND table_name = 'auth_two_factors'"
                )
            )
        }
        assert grants == {"SELECT", "INSERT", "UPDATE", "DELETE"}

        alembic("downgrade", AUTH_AFTER)
        assert run(_query("SELECT to_regclass('public.auth_two_factors') AS t"))[0]["t"] is None
        assert run(_snapshot(new_tables=())) == before
        alembic("upgrade", "0013_two_factor")
        assert run(_query("SELECT count(*) FROM users"))[0][0] == 1
    finally:
        run(_admin('DROP DATABASE IF EXISTS "aichat_migrations_test" WITH (FORCE)'))
