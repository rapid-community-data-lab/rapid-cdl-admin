CREATE TYPE "AdminRole" AS ENUM (
    'ADMIN',
    'SUPER_ADMIN'
);

CREATE TABLE "admin_users" (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL,
    role "AdminRole" NOT NULL DEFAULT 'ADMIN',
    enabled BOOLEAN NOT NULL DEFAULT true,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL
);
