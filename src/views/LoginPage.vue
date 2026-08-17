<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { login as saveLogin } from "@/auth";

const apiBase = import.meta.env.VITE_ADMIN_API_BASE_URL;
const router = useRouter();
const email = ref("");
const password = ref("");

async function login() {
  // const response = await fetch("http://localhost:8083/login", {
  const response = await fetch(`${apiBase}/login`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      email: email.value,
      password: password.value
    })
  });
  const data = await response.json();
  if (!response.ok) {
    alert(data.message);
    return;
  }
  saveLogin(data.token, data.user.role);
  alert(data.message);
  router.push("/");
}
</script>

<template>
  <el-card style="max-width:400px;margin:auto;">
    <h2>Login</h2>
    <el-input
      v-model="email"
      placeholder="Email"
    />
    <br><br>
    <el-input
      v-model="password"
      type="password"
      placeholder="Password"
    />
    <br><br>
    <el-button
      type="primary"
      @click="login"
    >
      Login
    </el-button>
  </el-card>
</template>