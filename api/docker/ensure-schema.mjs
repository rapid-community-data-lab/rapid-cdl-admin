import pg from "pg";

const { Client } = pg;
const connectionString = process.env.DATABASE_URL;

if (!connectionString) {
  throw new Error("DATABASE_URL is required for admin API schema setup");
}

const client = new Client({ connectionString });

try {
  await client.connect();
  // TODO: Convert it to Prisma ORM in the future
  await client.query(`
    DO $$
    DECLARE
      has_old_roles BOOLEAN;
      has_new_roles BOOLEAN;
      has_admin_users BOOLEAN;
    BEGIN
      SELECT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = 'AdminRole' AND e.enumlabel = 'ADMIN'
      ) INTO has_old_roles;

      SELECT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = 'AdminRole' AND e.enumlabel = 'INDEX_ADMIN'
      ) INTO has_new_roles;

      SELECT to_regclass('public.admin_users') IS NOT NULL INTO has_admin_users;

      -- Migrate the previous enum values admin and superadmin to 
      -- the new enum values INDEX_ADMIN and ROLES_ADMIN once,
      -- so existing admin_users rows remain usable after the role rename.
      IF has_old_roles AND NOT has_new_roles THEN
        IF has_admin_users THEN
          ALTER TABLE "admin_users" ALTER COLUMN "role" DROP DEFAULT;
        END IF;

        ALTER TYPE "AdminRole" RENAME TO "AdminRole_old";
        CREATE TYPE "AdminRole" AS ENUM ('INDEX_ADMIN', 'ROLES_ADMIN');

        IF has_admin_users THEN
          ALTER TABLE "admin_users"
            ALTER COLUMN "role" TYPE "AdminRole"
            USING CASE "role"::text
              WHEN 'SUPER_ADMIN' THEN 'ROLES_ADMIN'
              ELSE 'INDEX_ADMIN'
            END::"AdminRole";
          ALTER TABLE "admin_users"
            ALTER COLUMN "role" SET DEFAULT 'INDEX_ADMIN';
        END IF;

        DROP TYPE "AdminRole_old";
      END IF;
    END
    $$;

    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_type WHERE typname = 'AdminRole'
      ) THEN
        CREATE TYPE "AdminRole" AS ENUM ('INDEX_ADMIN', 'ROLES_ADMIN');
      END IF;
    END
    $$;

    CREATE TABLE IF NOT EXISTS "admin_users" (
      "id" SERIAL NOT NULL,
      "email" TEXT NOT NULL,
      "password" TEXT NOT NULL,
      "role" "AdminRole" NOT NULL DEFAULT 'INDEX_ADMIN',
      "enabled" BOOLEAN NOT NULL DEFAULT true,
      "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
      "updatedAt" TIMESTAMP(3) NOT NULL,
      CONSTRAINT "admin_users_pkey" PRIMARY KEY ("id")
    );

    CREATE UNIQUE INDEX IF NOT EXISTS "admin_users_email_key"
      ON "admin_users"("email");
  `);
  console.log("[entrypoint] Admin API database schema is ready.");
} finally {
  await client.end();
}
