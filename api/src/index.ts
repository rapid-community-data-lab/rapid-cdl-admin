import Fastify from "fastify";
import cors from "@fastify/cors";
import dotenv from "dotenv";
import { PrismaPg } from "@prisma/adapter-pg";
import { PrismaClient } from "./generated/prisma/client.ts";
import authRoutes from "./routes/auth.ts";

dotenv.config();

export const prisma = new PrismaClient({
  adapter: new PrismaPg({
    connectionString: process.env.DATABASE_URL!
  })
});

const app = Fastify({ logger: true });

await app.register(cors, {
  origin: true
});

await app.register(authRoutes);

app.get("/health", async () => {
  return { status: "ok" };
});

app.listen({
  port: Number(process.env.PORT),
  host: "0.0.0.0"
});