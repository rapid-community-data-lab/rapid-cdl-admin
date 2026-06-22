<script setup lang="ts">
import type { FilterNodeMethodFunction, TreeNodeData } from 'element-plus'
import { ElInput, ElTree } from 'element-plus'
import { ref, watch } from 'vue'

interface Collection extends TreeNodeData {
  id: string
  name: string
  path: string
  indexed?: boolean
  children: Collection[]
}

const apiBase = import.meta.env.VITE_API_BASE_URL
const apiKey = import.meta.env.VITE_API_TOKEN
const collections = ref<Collection[]>([])
const errorMessage = ref('')
const statusMessage = ref('')
const statusType = ref<'success' | 'error'>('success')
const filterText = ref('')
const treeRef = ref()
const uploadUrl = ref('')
const uploadLoading = ref(false)
const getIndexedRepos = (): string[] => {
  const data = localStorage.getItem('indexedRepos')
  return data ? JSON.parse(data) : []
}

const saveIndexedRepos = (ids: string[]) => {
  localStorage.setItem('indexedRepos', JSON.stringify(ids))
}

const buildTree = (items: Collection[]): Collection[] => {
  const map = new Map<string, Collection>(items.map(item => [item.id, { ...item, indexed: false, children: [] }]));
  const roots: Collection[] = [];

  for (const [id, item] of map) {
    const parts = id.split('/');
    // check for all possible parent paths by popping parts until we find a match in the map
    let hasParent = false;
    while (parts.length) {
      if (!parts.pop()) continue;
      const parentId = parts.join('/');
      const parent = map.get(parentId) || map.get(parentId + '/');
      if (parent) {
        parent.children.push(item);
        parent.children.sort();
        console.log(id, parentId)
        hasParent = true;
        break;
      }
    }
    if (!hasParent) {
      roots.push(item);
    }
  }
  console.log(roots);
  return roots.sort();
}

// Fetch available repos from API
const fetchCollections = async () => {
  errorMessage.value = ''
  collections.value = []

  try {
    const response = await fetch(`${apiBase}/admin/repository`, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${apiKey}`
      }
    })

    if (!response.ok) {
      throw new Error('Unauthorized or server error')
    }

    const data = await response.json()
    //const indexedRepos = getIndexedRepos()
    // const list = data.map((c: Collection) => ({
    //   ...c,
    //   indexed: indexedRepos.includes(c.id)
    // }))

    collections.value = buildTree(data)
  } catch (error) {
    errorMessage.value = 'Failed to fetch collections'
    statusType.value = 'success'
  }
}

const markIndexedRecursively = (node: Collection, value: boolean) => {
  node.indexed = value
  if (node.children) {
    node.children.forEach(child => markIndexedRecursively(child, value))
  }
}

const getAllIds = (node: Collection): string[] => {
  return [node.id, ...(node.children ? node.children.flatMap(getAllIds) : [])]
}

// Index all fetched repos
const indexAll = async () => {
  statusMessage.value = ''

  if (collections.value.every(c => c.indexed)) {
    statusMessage.value = 'All repos have been indexed.'
    statusType.value = 'success'
    return
  }

  try {
    const response = await fetch(`${apiBase}/admin/index/`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`
      }
    })

    if (!response.ok) {
      throw new Error('Index failed')
    }

    collections.value.forEach(c =>
      markIndexedRecursively(c, true)
    )
    const allIds = collections.value.flatMap(getAllIds)
    saveIndexedRepos(allIds)
    statusMessage.value = 'Indexing succeeded!'
    statusType.value = 'success'
    collections.value.forEach(c => c.indexed = true)
  } catch (error) {
    statusMessage.value = 'Indexing failed!'
    statusType.value = 'error'
  }
}


// Delete all fetched repos
const deleteAll = async () => {
  statusMessage.value = ''

  try {
    const response = await fetch(`${apiBase}/admin/index/`, {
      method: 'DELETE',
      headers: {
        'Authorization': `Bearer ${apiKey}`
      }
    })

    if (!response.ok) {
      throw new Error('Delete failed')
    }

    collections.value.forEach(c =>
      markIndexedRecursively(c, false)
    )
    saveIndexedRepos([])
    statusMessage.value = 'All indexes deleted successfully!'
    statusType.value = 'success'
  } catch (error) {
    statusMessage.value = 'Delete failed!'
    statusType.value = 'error'
  }
}

const findCollectionById = (id: string, nodes: Collection[]): Collection | undefined => {
  for (const node of nodes) {
    if (node.id === id) return node
    if (node.children) {
      const found = findCollectionById(id, node.children)
      if (found) return found
    }
  }
  return undefined
}

// Index specific fetched repos
const indexCollection = async (collectionId: string, collectionName: string) => {
  statusMessage.value = ''
  const collection = findCollectionById(collectionId, collections.value)
  if (collection?.indexed) return

  try {
    const response = await fetch(`${apiBase}/admin/index/${encodeURIComponent(collectionId)}`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`
      }
    })

    if (!response.ok) {
      throw new Error('Index failed')
    }

    statusMessage.value = `Indexing succeeded for ${collectionName}!`
    statusType.value = 'success'
    if (collection) {
      markIndexedRecursively(collection, true)

      const indexedRepos = getIndexedRepos()
      const allIds = getAllIds(collection)
      saveIndexedRepos([...indexedRepos, ...allIds])
    }
  } catch (error) {
    statusMessage.value = `Indexing failed for ${collectionName}!`
    statusType.value = 'error'
  }
}


// Delete specific fetched repos
const deleteCollection = async (collectionId: string, collectionName: string) => {
  statusMessage.value = ''

  try {
    const response = await fetch(`${apiBase}/admin/index/${encodeURIComponent(collectionId)}`, {
      method: 'DELETE',
      headers: {
        'Authorization': `Bearer ${apiKey}`
      }
    })

    if (!response.ok) {
      throw new Error('Delete failed')
    }

    statusMessage.value = `Deleted index for ${collectionName} successfully!`
    statusType.value = 'success'
    const collection = findCollectionById(collectionId, collections.value)
    if (collection) {
      markIndexedRecursively(collection, false)
      const allIds = getAllIds(collection)
      const indexedRepos = getIndexedRepos().filter(id => !allIds.includes(id))
      saveIndexedRepos(indexedRepos)
    }

  } catch (error) {
    statusMessage.value = `Delete failed for ${collectionName}!`
    statusType.value = 'error'
  }
}

// Upload specific collection
const uploadCollection = async () => {
  statusMessage.value = ''

  if (!uploadUrl.value) {
    statusMessage.value = 'Please enter a ZIP URL'
    statusType.value = 'error'
    return
  }

  uploadLoading.value = true

  try {
    const response = await fetch(`${apiBase}/admin/upload`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        url: uploadUrl.value
      })
    })

    if (!response.ok) {
      throw new Error('Upload failed')
    }

    const result = await response.json()

    statusMessage.value = result.message || 'Upload started'
    statusType.value = 'success'

    uploadUrl.value = ''
  } catch (error) {
    console.error(error)

    statusMessage.value = 'Upload failed!'
    statusType.value = 'error'
  } finally {
    uploadLoading.value = false
  }
}

watch(filterText, (val) => {
  treeRef.value!.filter(val)
})

const filterNode: FilterNodeMethodFunction = (value: string, data: TreeNodeData) => {
  if (!value) return true
  return data.name.search(new RegExp(value, "i")) !== -1
}
</script>

<template>
  <div class="page-container">
    <p v-if="errorMessage" style="color:red">
      {{ errorMessage }}
    </p>

    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
      <el-button type="primary" @click="fetchCollections">
        Load Collections
      </el-button>

      <div style="display:flex; gap:10px;">
        <el-button type="success" @click="indexAll">
          Index All
        </el-button>

        <el-button type="danger" @click="deleteAll">
          Delete All
        </el-button>
      </div>
    </div>

    <div class="upload-bar">
      <el-input
        v-model="uploadUrl"
        placeholder="Enter RO-Crate ZIP URL"
        clearable
      />

      <el-button
        type="primary"
        :loading="uploadLoading"
        @click="uploadCollection"
      >
        Upload
      </el-button>
    </div>

    <p v-if="statusMessage" :style="{ color: statusType === 'success' ? 'green' : 'red', fontWeight: 'bold' }">
      {{ statusMessage }}
    </p>
    <template v-if="collections.length">
      <el-input v-model="filterText" class="w-60 mb-2" placeholder="Filter keyword" />
      <el-tree ref="treeRef" :data="collections" node-key="id" :filter-node-method="filterNode">
        <template #default="{ data }">
          <div class="tree-row">
            <span class="tree-label">
              {{ data.name }}
              <span class="tree-label-id">
                &nbsp; {{ data.id }}
              </span>
            </span>

            <span class="tree-actions">
              <el-button type="success" @click.stop="indexCollection(data.id, data.name)" >
                {{ data.indexed ? 'Indexed' : 'Index' }}
              </el-button>

              <el-button type="danger" @click.stop="deleteCollection(data.id, data.name)" >
                Delete
              </el-button>
            </span>
          </div>
        </template>
      </el-tree>
    </template>
  </div>
</template>


<style scoped>
.page-container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 0 16px;
}

.navbar {
  height: 50px;
  display: flex;
  align-items: center;
  padding: 0 16px;
  background-color: #146472;
  color: white;
  font-size: 18px;
  font-weight: bold;
  margin-bottom: 16px;
}

.navbar-title {
  user-select: none;
}

input {
  margin-right: 10px;
  padding: 5px;
}

button {
  padding: 5px 10px;
}

:deep(.el-tree-node__content) {
  height: 40px;
}

.tree-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.tree-label {
  flex: 1;
}

.tree-label-id {
  color: #999;
  font-size: 0.9em;
}

.tree-actions {
  display: flex;
  gap: 8px;
}

.el-button {
  min-width: 80px;
}

.upload-bar {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
}
</style>