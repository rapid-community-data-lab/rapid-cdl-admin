import { createRouter, createWebHistory } from 'vue-router'
import IndexPage from '@/views/IndexPage.vue'
import CreateAdminPage from '@/views/CreateAdminPage.vue'
import LoginPage from "@/views/LoginPage.vue";

const routes = [
  {
    path: '/',
    name: 'index',
    component: IndexPage,
    meta: {
      requiresAuth: true,
    },
  },
  {
    path: "/create-admin",
    name: "create-admin",
    component: CreateAdminPage,
    meta: {
      requiresAuth: true,
      requiresSuperAdmin: true,
    },
  },
  {
    path: "/login",
    name: "login",
    component: LoginPage,
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  const token = localStorage.getItem('token')
  if (to.meta.requiresAuth && !token) {
    return '/login'
  }
  if (to.name === 'login' && token) {
    return '/'
  }
  if (to.meta.requiresSuperAdmin) {
    const role = localStorage.getItem('role')

    if (role !== 'SUPER_ADMIN') {
      return '/'
    }
  }
  return true
})

export default router