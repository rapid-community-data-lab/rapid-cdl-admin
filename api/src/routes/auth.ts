import type { FastifyInstance, FastifyRequest } from "fastify";
import bcrypt from "bcrypt";
import jwt from "jsonwebtoken";
import { prisma } from "../index.ts";

type AdminRole = "INDEX_ADMIN" | "ROLES_ADMIN";

type AuthenticatedAdmin = {
  id: number;
  email: string;
  role: AdminRole;
  enabled: boolean;
};

async function getUserFromToken(request: FastifyRequest): Promise<AuthenticatedAdmin | null> {
  const authorization = request.headers.authorization;

  if (!authorization) {
    return null;
  }

  const token = authorization.replace("Bearer ", "");

  try {
    const tokenUser = jwt.verify(
      token,
      process.env.API_AUTH_JWT_SECRET!
    ) as {
      id: string | number;
    };

    const id = Number(tokenUser.id);
    if (!Number.isInteger(id)) {
      return null;
    }

    return prisma.adminUser.findUnique({
      where: { id },
      select: {
        id: true,
        email: true,
        role: true,
        enabled: true
      }
    });
  } catch {
    return null;
  }
}

export default async function authRoutes(app: FastifyInstance) {

  app.post("/create-admin", async (request, reply) => {
    const currentUser = await getUserFromToken(request);

    if (!currentUser) {
      return reply.status(401).send({
        message: "You must be logged in."
      });
    }

    if (currentUser.role !== "ROLES_ADMIN") {
      return reply.status(403).send({
        message: "Only Roles Admins can create accounts."
      });
    }

    const { email, password, role } = request.body as {
      email: string;
      password: string;
      role: "INDEX_ADMIN" | "ROLES_ADMIN";
    };

    if (!email || !password || !role) {
      return reply.status(400).send({
        message: "Email, password and role are required."
      });
    }

    if (role !== "INDEX_ADMIN" && role !== "ROLES_ADMIN") {
      return reply.status(400).send({
        message: "Invalid role."
      });
    }

    const existingUser = await prisma.adminUser.findUnique({
      where: {
        email
      }
    });

    if (existingUser) {
      return reply.status(400).send({
        message: "Email already exists."
      });
    }

    const hashedPassword = await bcrypt.hash(password, 10);

    const user = await prisma.adminUser.create({
      data: {
        email,
        password: hashedPassword,
        role
      }
    });

    return {
      id: user.id,
      email: user.email,
      role: user.role,
      message: "Created new account successfully."
    };
  });

  app.get("/admins", async (request, reply) => {
    const currentUser = await getUserFromToken(request);

    if (!currentUser) {
      return reply.status(401).send({
        message: "You must be logged in."
      });
    }

    if (currentUser.role !== "ROLES_ADMIN") {
      return reply.status(403).send({
        message: "Only Roles Admins can manage accounts."
      });
    }

    const admins = await prisma.adminUser.findMany({
      select: {
        id: true,
        email: true,
        role: true,
        enabled: true,
        createdAt: true,
        updatedAt: true
      },
      orderBy: [
        { enabled: "desc" },
        { createdAt: "desc" }
      ]
    });

    return { admins };
  });

  app.delete("/admins/:id", async (request, reply) => {
    const currentUser = await getUserFromToken(request);

    if (!currentUser) {
      return reply.status(401).send({
        message: "You must be logged in."
      });
    }

    if (currentUser.role !== "ROLES_ADMIN") {
      return reply.status(403).send({
        message: "Only Roles Admins can manage accounts."
      });
    }

    const id = Number((request.params as { id: string }).id);
    if (!Number.isInteger(id)) {
      return reply.status(400).send({
        message: "Invalid admin id."
      });
    }

    if (currentUser.id === id) {
      return reply.status(400).send({
        message: "You cannot disable your own account."
      });
    }

    const targetUser = await prisma.adminUser.findUnique({
      where: { id },
      select: {
        id: true,
        email: true,
        role: true,
        enabled: true
      }
    });

    if (!targetUser) {
      return reply.status(404).send({
        message: "Admin account not found."
      });
    }

    if (targetUser.enabled && targetUser.role === "ROLES_ADMIN") {
      const activeRolesAdmins = await prisma.adminUser.count({
        where: {
          role: "ROLES_ADMIN",
          enabled: true
        }
      });

      if (activeRolesAdmins <= 1) {
        return reply.status(400).send({
          message: "You cannot disable the last active Roles Admin."
        });
      }
    }

    await prisma.adminUser.update({
      where: { id },
      data: { enabled: false }
    });

    return {
      id: targetUser.id,
      email: targetUser.email,
      role: targetUser.role,
      enabled: false,
      message: "Admin account disabled successfully."
    };
  });

  app.patch("/admins/:id/enable", async (request, reply) => {
    const currentUser = await getUserFromToken(request);

    if (!currentUser) {
      return reply.status(401).send({
        message: "You must be logged in."
      });
    }

    if (currentUser.role !== "ROLES_ADMIN") {
      return reply.status(403).send({
        message: "Only Roles Admins can manage accounts."
      });
    }

    const id = Number((request.params as { id: string }).id);
    if (!Number.isInteger(id)) {
      return reply.status(400).send({
        message: "Invalid admin id."
      });
    }

    const targetUser = await prisma.adminUser.findUnique({
      where: { id },
      select: {
        id: true,
        email: true,
        role: true
      }
    });

    if (!targetUser) {
      return reply.status(404).send({
        message: "Admin account not found."
      });
    }

    await prisma.adminUser.update({
      where: { id },
      data: { enabled: true }
    });

    return {
      id: targetUser.id,
      email: targetUser.email,
      role: targetUser.role,
      enabled: true,
      message: "Admin account enabled successfully."
    };
  });

  app.post("/login", async (request, reply) => {
    const { email, password } = request.body as {
      email: string;
      password: string;
    };

    if (!email || !password) {
      return reply.status(400).send({
        message: "Email and password are required."
      });
    }

    const user = await prisma.adminUser.findUnique({
      where: {
        email
      }
    });
    if (!user || !user.enabled) {
      return reply.status(401).send({
        message: "Invalid email or password."
      });
    }

    const passwordMatches = await bcrypt.compare(
      password,
      user.password
    );

    if (!passwordMatches) {
      return reply.status(401).send({
        message: "Invalid email or password."
      });
    }

    const token = jwt.sign(
      {
        id: user.id,
        email: user.email,
        role: user.role
      },
      process.env.API_AUTH_JWT_SECRET!,
      {
        expiresIn: "1h"
      }
    );

    return {
      token,
      user: {
        id: user.id,
        email: user.email,
        role: user.role
      },
      message: "Login successful."
    };
  });

}
