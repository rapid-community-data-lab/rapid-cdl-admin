<script setup lang="ts">
import { ref } from "vue";

const email = ref("");
const password = ref("");

async function signup() {
  const response = await fetch("http://localhost:8083/signup", {
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
    <el-button
      type="primary"
      @click="signup"
    >
      Create Account
    </el-button>
  </el-card>
</template>