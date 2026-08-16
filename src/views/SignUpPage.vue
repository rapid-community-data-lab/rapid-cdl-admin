<script setup lang="ts">
import { ref } from "vue";

const email = ref("");
const password = ref("");
const role = ref<"ADMIN" | "SUPER_ADMIN">("ADMIN");

async function signup() {
  const response = await fetch("http://localhost:8083/signup", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      email: email.value,
      password: password.value,
      role: role.value
    })
  });

  const data = await response.json();
  alert(data.message);
}
</script>

<template>
  <el-card style="max-width:400px;margin:auto;">
    <h2>Sign Up</h2>
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
    <el-select
      v-model="role"
      placeholder="Select role"
      style="width: 100%;"
    >
      <el-option
        label="Admin"
        value="ADMIN"
      />
      <el-option
        label="Super Admin"
        value="SUPER_ADMIN"
      />
    </el-select>
    <br><br>
    <el-button
      type="primary"
      @click="signup"
    >
      Create Account
    </el-button>
  </el-card>
</template>