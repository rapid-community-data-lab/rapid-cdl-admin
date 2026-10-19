<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";

const apiBase = import.meta.env.VITE_ADMIN_API_BASE_URL;
const router = useRouter();
const email = ref("");
const password = ref("");
const role = ref<"INDEX_ADMIN" | "ROLES_ADMIN">("INDEX_ADMIN");

async function createAdmin() {
  const token = localStorage.getItem("token");
  if (!token) {
    router.push("/login");
    return;
  }
  // const response = await fetch("http://localhost:8083/create-admin", {
  const response = await fetch(`${apiBase}/create-admin`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`
    },
    body: JSON.stringify({
      email: email.value,
      password: password.value,
      role: role.value
    })
  });
  const data = await response.json();
  if (!response.ok) {
    alert(data.message);
    return;
  }
  alert(data.message);
  router.push("/");
}
</script>

<template>
  <el-card style="max-width:400px;margin:auto;">
    <h2>Create New Index Admin</h2>

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
      placeholder="Select account role"
      style="width: 100%;"
    >
      <el-option
        label="Index Admin"
        value="INDEX_ADMIN"
      />
      <el-option
        label="Roles Admin"
        value="ROLES_ADMIN"
      />
    </el-select>
    <br><br>
    <el-button
      type="primary"
      @click="createAdmin"
    >
      Create Account
    </el-button>
  </el-card>
</template>
