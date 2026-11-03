<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import logo from '@/assets/logo.svg'
import {
  isLoggedIn,
  isRolesAdmin,
  logout as clearAuth
} from '@/auth'

const router = useRouter()
const route = useRoute()
const open = ref(false)

watch(
  () => route.fullPath,
  () => {
    open.value = false
  },
)

function logout() {
  clearAuth()
  router.push('/login')
}
</script>

<template>
  <header class="app-header">
    <div class="app-header__inner">
      <router-link to="/" class="brand">
        <img class="brand__logo" :src="logo" alt="RAPID-CDL" />
        <span class="brand__text">Admin</span>
      </router-link>

      <button
        class="menu-toggle"
        type="button"
        :aria-expanded="open"
        aria-controls="primary-navigation"
        aria-label="Toggle navigation"
        @click="open = !open"
      >
        <span />
        <span />
        <span />
      </button>

      <nav id="primary-navigation" class="app-nav" :class="{ 'is-open': open }">
        <router-link v-if="isLoggedIn" to="/" class="app-nav__link" exact-active-class="is-active">Home</router-link>
        <router-link v-if="isLoggedIn" to="/ingest" class="app-nav__link" active-class="is-active">
          Ingest Data
        </router-link>
        <router-link
          v-if="isLoggedIn && isRolesAdmin"
          to="/create-admin"
          class="app-nav__link"
          active-class="is-active"
        >
          Create Index Admin
        </router-link>
        <router-link v-if="!isLoggedIn" to="/login" class="app-nav__link" active-class="is-active">Login</router-link>
        <button v-if="isLoggedIn" type="button" class="app-nav__link app-nav__button" @click="logout">Logout</button>
      </nav>
    </div>
  </header>
</template>

<style scoped>
.app-header {
  position: sticky;
  top: 0;
  z-index: 20;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: saturate(160%) blur(10px);
  border-bottom: 1px solid var(--rapid-border);
}

.app-header__inner {
  max-width: var(--rapid-max-width);
  margin: 0 auto;
  padding: 0 20px;
  min-height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  position: relative;
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  text-decoration: none;
  color: var(--rapid-brand);
}

.brand__logo {
  height: 32px;
}

.brand__text {
  font-size: 0.8rem;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  padding: 3px 8px;
  border-radius: 999px;
  background: var(--rapid-brand-50);
}

.app-nav {
  display: flex;
  align-items: center;
  gap: 4px;
}

.app-nav__link {
  display: inline-flex;
  align-items: center;
  padding: 8px 14px;
  border-radius: 999px;
  color: var(--rapid-brand);
  text-decoration: none;
  font-size: 0.95rem;
  transition: background 0.15s ease;
}

.app-nav__link:hover {
  background: var(--rapid-brand-50);
}

.app-nav__link.is-active {
  background: var(--rapid-brand);
  color: #fff;
}

.app-nav__button {
  border: 1px solid var(--rapid-border);
  background: transparent;
  cursor: pointer;
  font: inherit;
  margin-left: 6px;
}

.menu-toggle {
  display: none;
  width: 40px;
  height: 40px;
  border: 1px solid var(--rapid-border);
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  gap: 4px;
}

.menu-toggle span {
  display: block;
  width: 18px;
  height: 2px;
  background: var(--rapid-brand);
  border-radius: 2px;
}

@media (max-width: 720px) {
  .menu-toggle {
    display: inline-flex;
  }

  .app-nav {
    display: none;
    position: absolute;
    top: 100%;
    left: 0;
    right: 0;
    flex-direction: column;
    align-items: stretch;
    padding: 10px 16px 16px;
    background: #fff;
    border-bottom: 1px solid var(--rapid-border);
    box-shadow: var(--rapid-shadow);
  }

  .app-nav.is-open {
    display: flex;
  }

  .app-nav__link {
    border-radius: 10px;
    padding: 12px 14px;
  }

  .app-nav__button {
    margin: 6px 0 0;
    justify-content: center;
  }
}
</style>
