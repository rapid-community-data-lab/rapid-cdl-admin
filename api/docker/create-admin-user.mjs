import bcrypt from "bcrypt";
import pg from "pg";
import readline from "node:readline";

const ROLES = new Set(["INDEX_ADMIN", "ROLES_ADMIN"]);

function ask(question) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    rl.question(question, (answer) => {
      rl.close();
      resolve(answer.trim());
    });
  });
}

function askHidden(question) {
  if (!process.stdin.isTTY) {
    return ask(question);
  }
  process.stdout.write(question);
  return new Promise((resolve) => {
    let value = "";
    const finish = (code) => {
      process.stdin.setRawMode(false);
      process.stdin.pause();
      process.stdin.removeListener("data", onData);
      process.stdout.write("\n");
      if (code !== undefined) {
        process.exit(code);
      }
      resolve(value);
    };
    const onData = (chunk) => {
      for (const char of chunk) {
        if (char === "\r" || char === "\n" || char === "\u0004") {
          finish();
          return;
        }
        if (char === "\u0003") {
          finish(130);
          return;
        }
        if (char === "\u007f" || char === "\b") {
          value = value.slice(0, -1);
        } else {
          value += char;
        }
      }
    };
    process.stdin.setRawMode(true);
    process.stdin.setEncoding("utf8");
    process.stdin.resume();
    process.stdin.on("data", onData);
  });
}

function fail(message) {
  console.error(message);
  process.exit(1);
}

if (!process.env.DATABASE_URL) {
  fail("DATABASE_URL is not set.");
}

const email = process.env.ADMIN_EMAIL || (await ask("Email: "));
if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
  fail("A valid email address is required.");
}

const roleInput = process.env.ADMIN_ROLE || (await ask("Role (INDEX_ADMIN or ROLES_ADMIN) [ROLES_ADMIN]: "));
const role = (roleInput || "ROLES_ADMIN").toUpperCase();
if (!ROLES.has(role)) {
  fail("Role must be INDEX_ADMIN or ROLES_ADMIN.");
}

const password = process.env.ADMIN_PASSWORD || (await askHidden("Password (at least 12 characters): "));
if (password.length < 12) {
  fail("The password must have at least 12 characters.");
}

const hash = await bcrypt.hash(password, 10);
const client = new pg.Client({ connectionString: process.env.DATABASE_URL });

try {
  await client.connect();
  const result = await client.query(
    `INSERT INTO "admin_users" (email, password, role, enabled, "updatedAt")
     VALUES ($1, $2, $3::"AdminRole", true, CURRENT_TIMESTAMP)
     ON CONFLICT (email) DO UPDATE
       SET password = EXCLUDED.password, role = EXCLUDED.role, enabled = true, "updatedAt" = CURRENT_TIMESTAMP
     RETURNING (xmax = 0) AS created`,
    [email, hash, role],
  );
  console.log(result.rows[0].created ? `Account created with role ${role}.` : `Account updated with role ${role}.`);
} catch (error) {
  fail(`The account could not be saved: ${error.message}`);
} finally {
  await client.end().catch(() => {});
}
