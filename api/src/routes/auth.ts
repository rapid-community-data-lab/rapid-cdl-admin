import type { FastifyInstance } from "fastify";
import bcrypt from "bcrypt";
import { prisma } from "../index.ts";

export default async function authRoutes(app: FastifyInstance) {

  app.post("/signup", async (request, reply) => {
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
      message: "Sign up successful."
    };
  });

}