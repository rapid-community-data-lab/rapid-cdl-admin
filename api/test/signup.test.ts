import { describe, it, expect, vi, beforeEach } from "vitest";
import Fastify from "fastify";

const mockPrisma = {
  adminUser: {
    findUnique: vi.fn(),
    create: vi.fn(),
  },
};

vi.mock("../src/index.ts", () => ({
  prisma: mockPrisma,
}));

const { default: authRoutes } = await import("../src/routes/auth.ts");

describe("POST /signup", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // Test case for creating a new admin user
  it("creates a new admin user", async () => {
    mockPrisma.adminUser.findUnique.mockResolvedValue(null);

    mockPrisma.adminUser.create.mockResolvedValue({
      id: 1,
      email: "test@example.com",
      password: "$2b$10$hashed-password",
      role: "ADMIN",
      enabled: true,
      createdAt: new Date(),
      updatedAt: new Date(),
    });
    const app = Fastify();
    await app.register(authRoutes);
    const response = await app.inject({
      method: "POST",
      url: "/signup",
      payload: {
        email: "test@example.com",
        password: "test123",
      },
    });

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      id: 1,
      email: "test@example.com",
      message: "Sign up successful.",
    });
    expect(mockPrisma.adminUser.findUnique).toHaveBeenCalledWith({
      where: {
        email: "test@example.com",
      },
    });
    expect(mockPrisma.adminUser.create).toHaveBeenCalled();
    const createCall =
      mockPrisma.adminUser.create.mock.calls[0][0];
    expect(createCall.data.email).toBe("test@example.com");
    // Make sure the plain password is NOT stored.
    expect(createCall.data.password).not.toBe("test123");
  });

  // Test case for rejecting an existing email
  it("rejects an existing email", async () => {
    mockPrisma.adminUser.findUnique.mockResolvedValue({
      id: 1,
      email: "existing@example.com",
      password: "$2b$10$hashed-password",
      role: "ADMIN",
      enabled: true,
      createdAt: new Date(),
      updatedAt: new Date(),
    });

    const app = Fastify();
    await app.register(authRoutes);
    const response = await app.inject({
      method: "POST",
      url: "/signup",
      payload: {
        email: "existing@example.com",
        password: "test123",
      },
    });
    expect(response.statusCode).toBe(400);
    expect(response.json()).toEqual({
      message: "Email already exists.",
    });
    // A new database record must not be created.
    expect(mockPrisma.adminUser.create).not.toHaveBeenCalled();
  });

  // Test case for rejecting missing email or password
  it("rejects missing email or password", async () => {
    const app = Fastify();
    await app.register(authRoutes);
    const response = await app.inject({
      method: "POST",
      url: "/signup",
      payload: {
        email: "",
        password: "",
      },
    });
    expect(response.statusCode).toBe(400);
    expect(response.json()).toEqual({
      message: "Email and password are required.",
    });
    // Database should not be accessed.
    expect(mockPrisma.adminUser.findUnique).not.toHaveBeenCalled();
    expect(mockPrisma.adminUser.create).not.toHaveBeenCalled();
  });

});