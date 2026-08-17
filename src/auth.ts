import { ref, computed } from 'vue'

const token = ref(localStorage.getItem('token'))
const role = ref(localStorage.getItem('role'))

export const isLoggedIn = computed(() => !!token.value)
export const isSuperAdmin = computed(() => role.value === 'SUPER_ADMIN')

export function login(newToken: string, newRole: string) {
  localStorage.setItem('token', newToken)
  localStorage.setItem('role', newRole)

  token.value = newToken
  role.value = newRole
}

export function logout() {
  localStorage.removeItem('token')
  localStorage.removeItem('role')

  token.value = null
  role.value = null
}

export function getToken() {
  return token.value
}