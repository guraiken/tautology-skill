<script setup lang="ts">
import { ref, watch } from "vue";

const props = defineProps<{ placeholder?: string }>();
const emit = defineEmits<{ (e: "search", q: string): void; (e: "submit", q: string): void }>();
const term = ref("");
let handle: ReturnType<typeof setTimeout> | undefined;

watch(term, (q) => {
  clearTimeout(handle);
  handle = setTimeout(() => emit("search", q), 300);
});

function reset() {
  term.value = "";
}
</script>

<template>
  <div class="filter">
    <SearchIcon class="filter-icon" />
    <input v-model="term" type="search" :placeholder="props.placeholder ?? 'Search orders'" class="filter-input" @keydown.enter="emit('submit', term)" />
    <button v-if="term" type="button" class="filter-reset" @click="reset">
      <XIcon />
    </button>
  </div>
</template>

<style scoped>
.filter { display: flex; gap: 8px; align-items: center; background: var(--surface-2); border-radius: 8px; padding: 0 12px; }
.filter-icon { width: 16px; height: 16px; opacity: .6; }
.filter-input { flex: 1; height: 40px; background: transparent; border: 0; outline: none; }
.filter-reset { border-radius: 999px; padding: 4px; }
.filter-reset:hover { background: var(--hover); }
</style>
