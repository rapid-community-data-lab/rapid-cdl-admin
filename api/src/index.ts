import Fastify from "fastify";
import cors from "@fastify/cors";
import dotenv from "dotenv";

dotenv.config();

const app = Fastify({ logger: true });

await app.register(cors, {
  origin: true
});

app.get("/health", async () => {
  return { status: "ok" };
});

app.listen({
  port: Number(process.env.PORT ?? 3001),
  host: "0.0.0.0"
});