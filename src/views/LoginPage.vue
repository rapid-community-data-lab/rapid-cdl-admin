<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { login as saveLogin } from "@/auth";

const apiBase = import.meta.env.VITE_ADMIN_API_BASE_URL;
const router = useRouter();
const email = ref("");
const password = ref("");
const message = ref("");
const messageType = ref<"success" | "error">("success");
const isSubmitting = ref(false);

async function login() {
  message.value = "";
  isSubmitting.value = true;

  // const response = await fetch("http://localhost:8083/login", {
  try {
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
      messageType.value = "error";
      message.value = data.message;
      return;
    }

    saveLogin(data.token, data.user.role);
    messageType.value = "success";
    message.value = "Login successful. Redirecting...";
    setTimeout(() => router.push("/"), 700);
  } catch {
    messageType.value = "error";
    message.value = "Unable to connect to the login service.";
  } finally {
    isSubmitting.value = false;
  }
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
    <p v-if="message" class="message" :class="messageType" role="status">
      {{ message }}
    </p>
    <el-button
      type="primary"
      :loading="isSubmitting"
      :disabled="isSubmitting"
      @click="login"
    >
      Login
    </el-button>
  </el-card>
</template>

<style scoped>
.message {
  font-weight: bold;
}

.success {
  color: #3e0323;
}

.error {
  color: #b42318;
}
</style>
