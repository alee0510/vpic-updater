-- Run once against target-db on first container init. Creates the
-- read-only application role that grant_app_access() grants per-database
-- access to on every successful deployment. Matches the one-time manual
-- setup agreed for pg_hba.conf -- role creation happens once here, access
-- control happens automatically per-release via GRANT/REVOKE.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'vpic_user') THEN
        CREATE ROLE vpic_user LOGIN PASSWORD 'secret';
    END IF;
END
$$;