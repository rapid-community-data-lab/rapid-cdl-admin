<script setup lang="ts">
import NavBar from '@/components/Nav.vue'
import FooterBar from '@/components/Footer.vue'
</script>

<template>
  <div class="layout">
    <NavBar />

    <main class="content">
      <router-view />
    </main>

    <FooterBar />
  </div>
</template>

<style>
body {
  margin: 0;
}
.layout {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}
.content {
  flex: 1;
  width: 100%;
  max-width: var(--rapid-max-width);
  margin: 0 auto;
  padding: 28px 20px 48px;
  box-sizing: border-box;
}
@media (max-width: 640px) {
  .content {
    padding: 20px 14px 36px;
  }
}
</style>
