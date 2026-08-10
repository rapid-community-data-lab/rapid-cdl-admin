import type { FastifyInstance } from "fastify";
import bcrypt from "bcrypt";
import { prisma } from "../index.ts";

export default async function authRoutes(app: FastifyInstance) {

  app.post("/signup", async (request, reply) => {

    const { email, password } = request.body as {
      email: string;
      password: string;
    };

    if (!email || !password) {
      return reply.status(400).send({
        message: "Email and password are required."
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
        password: hashedPassword
      }
    });

    return {
      id: user.id,
      email: user.email,
      message: "Sign up successful."
    };
  });

}