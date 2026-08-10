import { createRouter, createWebHistory } from 'vue-router'
import IndexPage from '@/views/IndexPage.vue'
import SignupPage from "@/views/SignUpPage.vue";

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
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router