import pg from "pg";

const { Client } = pg;
const connectionString = process.env.DATABASE_URL;

if (!connectionString) {
  throw new Error("DATABASE_URL is required for admin API schema setup");
}

const client = new Client({ connectionString });

try {
  await client.connect();
  await client.query(`
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_type WHERE typname = 'AdminRole'
      ) THEN
        CREATE TYPE "AdminRole" AS ENUM ('ADMIN', 'SUPER_ADMIN');
      END IF;
    END
    $$;

    CREATE TABLE IF NOT EXISTS "admin_users" (
      "id" SERIAL NOT NULL,
      "email" TEXT NOT NULL,
      "password" TEXT NOT NULL,
      "role" "AdminRole" NOT NULL DEFAULT 'ADMIN',
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
