<script setup lang="ts">
import { ElMessage, ElMessageBox } from "element-plus";
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { logout as clearAuth } from "@/auth";

type AdminRole = "INDEX_ADMIN" | "ROLES_ADMIN";

interface AdminAccount {
  id: number;
  email: string;
  role: AdminRole;
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
}

const apiBase = import.meta.env.VITE_ADMIN_API_BASE_URL;
const router = useRouter();
const email = ref("");
const password = ref("");
const role = ref<AdminRole>("INDEX_ADMIN");
const admins = ref<AdminAccount[]>([]);
const isLoading = ref(false);
const isCreating = ref(false);
const updatingId = ref<number | null>(null);

function authHeaders(): HeadersInit {
  const token = localStorage.getItem("token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function responseMessage(response: Response): Promise<string> {
  const data = await response.json().catch(() => ({}));
  return data.message || "The request could not be completed.";
}

async function loadAdmins() {
  const token = localStorage.getItem("token");
  if (!token) {
    router.push("/login");
    return;
  }

  isLoading.value = true;
  try {
    const response = await fetch(`${apiBase}/admins`, {
      headers: authHeaders()
    });

    if (response.status === 401) {
      clearAuth();
      router.push("/login");
      return;
    }
    if (!response.ok) {
      if (response.status === 401) {
        clearAuth();
        router.push("/login");
        return;
      }
      throw new Error(await responseMessage(response));
    }

    const data = await response.json();
    admins.value = data.admins;
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "Unable to load admin accounts.");
  } finally {
    isLoading.value = false;
  }
}

async function createAdmin() {
  const token = localStorage.getItem("token");
  if (!token) {
    router.push("/login");
    return;
  }
  isCreating.value = true;
  try {
    const response = await fetch(`${apiBase}/create-admin`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders()
      },
      body: JSON.stringify({
        email: email.value,
        password: password.value,
        role: role.value
      })
    });

    if (!response.ok) {
      throw new Error(await responseMessage(response));
    }

    email.value = "";
    password.value = "";
    ElMessage.success("Admin account created successfully.");
    await loadAdmins();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "Unable to create admin account.");
  } finally {
    isCreating.value = false;
  }
}

async function disableAdmin(admin: AdminAccount) {
  try {
    await ElMessageBox.confirm(
      `Disable ${admin.email}? They will no longer be able to log in.`,
      "Disable admin account",
      {
        confirmButtonText: "Disable",
        cancelButtonText: "Cancel",
        type: "warning"
      }
    );
  } catch {
    return;
  }

  updatingId.value = admin.id;
  try {
    const response = await fetch(`${apiBase}/admins/${admin.id}`, {
      method: "DELETE",
      headers: authHeaders()
    });

    if (!response.ok) {
      throw new Error(await responseMessage(response));
    }

    ElMessage.success("Admin account disabled successfully.");
    await loadAdmins();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "Unable to disable admin account.");
  } finally {
    updatingId.value = null;
  }
}

async function enableAdmin(admin: AdminAccount) {
  updatingId.value = admin.id;
  try {
    const response = await fetch(`${apiBase}/admins/${admin.id}/enable`, {
      method: "PATCH",
      headers: authHeaders()
    });

    if (!response.ok) {
      throw new Error(await responseMessage(response));
    }

    ElMessage.success("Admin account enabled successfully.");
    await loadAdmins();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : "Unable to enable admin account.");
  } finally {
    updatingId.value = null;
  }
}

onMounted(loadAdmins);
</script>

<template>
  <div class="manage-admin-page">
    <el-card>
      <h2>Manage Admins</h2>
      <p class="page-description">Create admin accounts and control which accounts can access the admin app.</p>

      <h3>Create admin account</h3>
      <el-form label-position="top" @submit.prevent="createAdmin">
        <el-form-item label="Email">
          <el-input v-model="email" placeholder="Email" />
        </el-form-item>
        <el-form-item label="Password">
          <el-input v-model="password" type="password" show-password placeholder="Password" />
        </el-form-item>
        <el-form-item label="Role">
          <el-select v-model="role" placeholder="Select account role" style="width: 100%;">
            <el-option label="Index Admin" value="INDEX_ADMIN" />
            <el-option label="Roles Admin" value="ROLES_ADMIN" />
          </el-select>
        </el-form-item>
        <el-button type="primary" :loading="isCreating" @click="createAdmin">
          Create Account
        </el-button>
      </el-form>
    </el-card>

    <el-card>
      <div class="admin-list-header">
        <h3>Admin accounts</h3>
        <el-button :loading="isLoading" @click="loadAdmins">Refresh</el-button>
      </div>

      <el-table v-loading="isLoading" :data="admins" empty-text="No admin accounts found.">
        <el-table-column prop="email" label="Email" min-width="220" />
        <el-table-column label="Role" width="150">
          <template #default="{ row }">
            {{ row.role === "ROLES_ADMIN" ? "Roles Admin" : "Index Admin" }}
          </template>
        </el-table-column>
        <el-table-column label="Status" width="120">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'">
              {{ row.enabled ? "Active" : "Disabled" }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Actions" width="130" align="right">
          <template #default="{ row }">
            <el-button
              v-if="row.enabled"
              type="danger"
              link
              :loading="updatingId === row.id"
              @click="disableAdmin(row)"
            >
              Disable
            </el-button>
            <el-button
              v-else
              type="success"
              link
              :loading="updatingId === row.id"
              @click="enableAdmin(row)"
            >
              Enable
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.manage-admin-page {
  display: grid;
  gap: 24px;
  max-width: 960px;
  margin: 0 auto;
}

.manage-admin-page h2,
.manage-admin-page h3 {
  margin-top: 0;
}

.page-description {
  color: var(--rapid-muted);
}

.admin-list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.admin-list-header h3 {
  margin-bottom: 0;
}

</style>
