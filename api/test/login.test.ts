import { beforeEach, describe, expect, it, vi } from "vitest";
import Fastify from "fastify";
import jwt from "jsonwebtoken";

const jwtSecret = "admin-api-test-secret";
process.env.API_AUTH_JWT_SECRET = jwtSecret;

const { mockPrisma, mockCompare } = vi.hoisted(() => ({
  mockCompare: vi.fn(),
  mockPrisma: {
    adminUser: {
      findUnique: vi.fn(),
    },
  },
}));

vi.mock("bcrypt", () => ({
  default: {
    compare: mockCompare,
  },
}));

vi.mock("../src/index.ts", () => ({
  prisma: mockPrisma,
}));

const { default: authRoutes } = await import("../src/routes/auth.ts");

describe("POST /login", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCompare.mockResolvedValue(true);
  });

  it.each(["ADMIN", "SUPER_ADMIN"] as const)("returns a usable %s JWT", async (role) => {
    mockPrisma.adminUser.findUnique.mockResolvedValue({
      id: 1,
      email: `${role.toLowerCase()}@example.com`,
      password: "hashed-password",
      role,
    });

    const app = Fastify();
    await app.register(authRoutes);
    const response = await app.inject({
      method: "POST",
      url: "/login",
      payload: {
        email: `${role.toLowerCase()}@example.com`,
        password: "password",
      },
    });

    expect(response.statusCode).toBe(200);
    const body = response.json();
    expect(body.user.role).toBe(role);
    expect(body.token).toEqual(expect.any(String));
    expect(jwt.verify(body.token, jwtSecret)).toMatchObject({
      id: 1,
      role,
    });
  });

  it("rejects invalid credentials", async () => {
    mockPrisma.adminUser.findUnique.mockResolvedValue(null);

    const app = Fastify();
    await app.register(authRoutes);
    const response = await app.inject({
      method: "POST",
      url: "/login",
      payload: { email: "missing@example.com", password: "password" },
    });

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({ message: "Invalid email or password." });
  });
});
