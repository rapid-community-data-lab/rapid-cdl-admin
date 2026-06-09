<script setup lang="ts">
import logo from '@/assets/logo.svg'
</script>

<template>
  <div class="nav-wrapper">
    <el-menu mode="horizontal" :ellipsis="false" class="nav">
      
      <el-menu-item index="home">
        <a href="/" class="nav-left">
          <img class="logo" :src="logo" />
          <span class="home-text">Home Test</span>
        </a>
      </el-menu-item>

      <div class="flex-grow" />

      <el-menu-item index="admin" :route="'/'">
          <span>Admin</span>
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

.home-text {
  font-size: 14px;
  color: #409eff;
}

.flex-grow {
  flex: 1;
}
</style>