<script setup lang="ts">
import { ref, watch } from "vue";

const props = defineProps<{ placeholder?: string }>();
const emit = defineEmits<{ (e: "search", q: string): void }>();
const query = ref("");
let timer: ReturnType<typeof setTimeout> | undefined;

watch(query, (q) => {
  clearTimeout(timer);
  timer = setTimeout(() => emit("search", q), 300);
});

function clear() {
  query.value = "";
}
</script>

<template>
  <div class="search">
    <SearchIcon class="search__icon" />
    <input v-model="query" type="search" :placeholder="props.placeholder ?? 'Search users'" class="search__input" />
    <button v-if="query" type="button" class="search__clear" @click="clear">
      <XIcon />
    </button>
  </div>
</template>

<style scoped>
.search { position: relative; display: flex; align-items: center; }
.search__icon { position: absolute; left: 12px; width: 16px; height: 16px; color: var(--muted); }
.search__input { width: 100%; height: 36px; padding: 0 32px 0 36px; border: 1px solid var(--border); border-radius: 6px; }
.search__clear { position: absolute; right: 8px; color: var(--muted); }
</style>
