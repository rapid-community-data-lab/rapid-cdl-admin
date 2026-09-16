<script setup lang="ts">
import { useRouter } from 'vue-router'
import logo from '@/assets/logo.svg'
import {
  isLoggedIn,
  isSuperAdmin,
  logout as clearAuth
} from '@/auth'

const router = useRouter()

function logout() {
  clearAuth()
  router.push('/login')
}
</script>

<template>
  <div class="nav-wrapper">
    <el-menu mode="horizontal" :ellipsis="false" class="nav">

      <el-menu-item index="home">
        <router-link to="/" class="nav-left">
          <img class="logo" :src="logo" />
          <span class="nav-text">Home</span>
        </router-link>
      </el-menu-item>

      <div class="flex-grow" />

      <!-- Not logged in -->
      <el-menu-item v-if="!isLoggedIn" index="login">
        <router-link to="/login" class="nav-left">
          <span class="nav-text">Login</span>
        </router-link>
      </el-menu-item>

      <!-- Super admin only -->
      <el-menu-item
        v-if="isLoggedIn && isSuperAdmin"
        index="create-admin"
      >
        <router-link to="/create-admin" class="nav-left">
          <span class="nav-text">Create Admin</span>
        </router-link>
      </el-menu-item>

      <!-- Logged in users -->
      <el-menu-item
        v-if="isLoggedIn"
        index="logout"
        @click="logout"
      >
        <span class="nav-text">Logout</span>
      </el-menu-item>

    </el-menu>
  </div>
</template>

<style scoped>
.nav-wrapper {
  border-bottom: 1px solid #e4e7ed;
}

.nav {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 16px;
}

.nav-left {
  display: flex;
  align-items: center;
  gap: 10px;
  text-decoration: none;
}

.logo {
  height: 28px;
}

.nav-text {
  font-size: 14px;
  color: #409eff;
}

.flex-grow {
  flex: 1;
}
</style>