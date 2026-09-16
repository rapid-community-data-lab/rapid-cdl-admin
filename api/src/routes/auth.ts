import type { FastifyInstance, FastifyRequest } from "fastify";
import bcrypt from "bcrypt";
import jwt from "jsonwebtoken";
import { prisma } from "../index.ts";

function getUserFromToken(request: FastifyRequest) {
  const authorization = request.headers.authorization;

  if (!authorization) {
    return null;
  }

  const token = authorization.replace("Bearer ", "");

  try {
    return jwt.verify(
      token,
      process.env.API_AUTH_JWT_SECRET!
    ) as {
      id: string;
      email: string;
      role: "ADMIN" | "SUPER_ADMIN";
    };
  } catch {
    return null;
  }
}

export default async function authRoutes(app: FastifyInstance) {

  app.post("/create-admin", async (request, reply) => {
    const currentUser = getUserFromToken(request);

    if (!currentUser) {
      return reply.status(401).send({
        message: "You must be logged in."
      });
    }

    if (currentUser.role !== "SUPER_ADMIN") {
      return reply.status(403).send({
        message: "Only super admins can create accounts."
      });
    }

    const { email, password, role } = request.body as {
      email: string;
      password: string;
      role: "ADMIN" | "SUPER_ADMIN";
    };

    if (!email || !password || !role) {
      return reply.status(400).send({
        message: "Email, password and role are required."
      });
    }

    if (role !== "ADMIN" && role !== "SUPER_ADMIN") {
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
      message: "Created new admin successful."
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
    if (!user) {
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