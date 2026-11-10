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
      findMany: vi.fn(),
      count: vi.fn(),
      update: vi.fn(),
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

  it.each(["INDEX_ADMIN", "ROLES_ADMIN"] as const)("returns a usable %s JWT", async (role) => {
    mockPrisma.adminUser.findUnique.mockResolvedValue({
      id: 1,
      email: `${role.toLowerCase()}@example.com`,
      password: "hashed-password",
      role,
      enabled: true,
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

  it("rejects disabled accounts", async () => {
    mockPrisma.adminUser.findUnique.mockResolvedValue({
      id: 1,
      email: "disabled@example.com",
      password: "hashed-password",
      role: "INDEX_ADMIN",
      enabled: false,
    });

    const app = Fastify();
    await app.register(authRoutes);
    const response = await app.inject({
      method: "POST",
      url: "/login",
      payload: {
        email: "disabled@example.com",
        password: "password",
      },
    });

    expect(response.statusCode).toBe(401);
    expect(mockCompare).not.toHaveBeenCalled();
  });

  it("lists admins for a roles admin", async () => {
    mockPrisma.adminUser.findUnique.mockResolvedValueOnce({
      id: 1,
      email: "roles@example.com",
      role: "ROLES_ADMIN",
      enabled: true,
    });
    mockPrisma.adminUser.findMany.mockResolvedValue([
      {
        id: 1,
        email: "roles@example.com",
        role: "ROLES_ADMIN",
        enabled: true,
        createdAt: new Date("2026-01-01"),
        updatedAt: new Date("2026-01-01"),
      },
    ]);

    const app = Fastify();
    await app.register(authRoutes);
    const response = await app.inject({
      method: "GET",
      url: "/admins",
      headers: { authorization: `Bearer ${jwt.sign({ id: 1 }, jwtSecret)}` },
    });

    expect(response.statusCode).toBe(200);
    expect(response.json().admins).toHaveLength(1);
    expect(mockPrisma.adminUser.findMany).toHaveBeenCalled();
  });

  it("disables another admin account", async () => {
    mockPrisma.adminUser.findUnique
      .mockResolvedValueOnce({
        id: 1,
        email: "roles@example.com",
        role: "ROLES_ADMIN",
        enabled: true,
      })
      .mockResolvedValueOnce({
        id: 2,
        email: "index@example.com",
        role: "INDEX_ADMIN",
        enabled: true,
      });
    mockPrisma.adminUser.update.mockResolvedValue({});

    const app = Fastify();
    await app.register(authRoutes);
    const response = await app.inject({
      method: "DELETE",
      url: "/admins/2",
      headers: { authorization: `Bearer ${jwt.sign({ id: 1 }, jwtSecret)}` },
    });

    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({
      id: 2,
      email: "index@example.com",
      enabled: false,
    });
    expect(mockPrisma.adminUser.update).toHaveBeenCalledWith({
      where: { id: 2 },
      data: { enabled: false },
    });
  });

  it("enables another admin account", async () => {
    mockPrisma.adminUser.findUnique
      .mockResolvedValueOnce({
        id: 1,
        email: "roles@example.com",
        role: "ROLES_ADMIN",
        enabled: true,
      })
      .mockResolvedValueOnce({
        id: 2,
        email: "index@example.com",
        role: "INDEX_ADMIN",
        enabled: false,
      });
    mockPrisma.adminUser.update.mockResolvedValue({});

    const app = Fastify();
    await app.register(authRoutes);
    const response = await app.inject({
      method: "PATCH",
      url: "/admins/2/enable",
      headers: { authorization: `Bearer ${jwt.sign({ id: 1 }, jwtSecret)}` },
    });

    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({
      id: 2,
      email: "index@example.com",
      enabled: true,
    });
    expect(mockPrisma.adminUser.update).toHaveBeenCalledWith({
      where: { id: 2 },
      data: { enabled: true },
    });
  });
});
