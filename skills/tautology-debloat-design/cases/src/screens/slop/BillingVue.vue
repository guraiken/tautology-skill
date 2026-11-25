<template>
  <div class="hero">
    <h1 class="hero-title">Billing</h1>
    <v-divider />
    <v-divider />
    <v-divider />
    <v-divider />
    <v-btn color="primary">Upgrade</v-btn>
    <v-btn color="primary">Compare</v-btn>
    <span class="dot dot--live" />
  </div>
</template>

<style scoped>
.hero {
  background: linear-gradient(135deg, var(--violet-500), var(--cyan-400));
  backdrop-filter: blur(16px);
  box-shadow: 0 20px 60px var(--glow);
}
.hero-title {
  background-image: linear-gradient(90deg, var(--violet-400), var(--cyan-400));
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
.plan-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg); }
.plan-card__inner { background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-md); }
.plan-card__box { background: var(--surface-3); border: 1px solid var(--border); border-radius: var(--radius-sm); }
.dot { width: 8px; height: 8px; border-radius: 9999px; }
.dot--live { background: var(--green); animation: pulse 2s infinite; }
</style>
