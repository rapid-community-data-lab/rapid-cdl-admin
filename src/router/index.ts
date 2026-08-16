import { createRouter, createWebHistory } from 'vue-router'
import IndexPage from '@/views/IndexPage.vue'
import SignupPage from "@/views/SignUpPage.vue";
import LoginPage from "@/views/LoginPage.vue";

const routes = [
  {
    path: '/',
    name: 'index',
    component: IndexPage,
  },
  {
    path: "/signup",
    name: "signup",
    component: SignupPage,
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

export default router