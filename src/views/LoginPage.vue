<script setup lang="ts">
import { ref } from "vue";

const email = ref("");
const password = ref("");

async function login() {
  const response = await fetch("http://localhost:8083/login", {
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
  localStorage.setItem("token", data.token);
  alert(data.message);
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