<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { filteredNav } from '@/app/router/navigation'
import { useAuthStore } from '@/modules/auth/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const drawerOpen = ref(false)
const immersive = computed(() => route.meta.immersive === true)

const groups = computed(() =>
  filteredNav((code) => auth.hasPermission(code))
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => router.hasRoute(item.name)),
    }))
    .filter((group) => group.items.length > 0),
)

const breadcrumbs = computed(() => {
  const chain = route.matched.filter((record) => record.name)
  return chain.map((record) => ({
    name: record.name as string,
    title: (record.meta.title as string) ?? (record.name as string),
  }))
})

const initials = computed(() => (auth.user?.display_name ?? auth.user?.username ?? '?').slice(0, 1))

function goHome() {
  drawerOpen.value = false
  void router.push({ name: 'chat' })
}
</script>

<template>
  <div class="shell" :class="{ 'shell--immersive': immersive }">
    <aside v-if="!immersive" class="shell__sidebar" :class="{ 'shell__sidebar--open': drawerOpen }">
      <button
        type="button"
        class="shell__brand"
        aria-label="返回 CampusPilot 对话首页"
        @click="goHome"
      >
        <span class="shell__logo">CP</span>
        <span class="shell__name">CampusPilot</span>
      </button>
      <nav class="shell__nav" aria-label="主导航">
        <section v-for="group in groups" :key="group.title" class="shell__group">
          <p class="shell__group-title">{{ group.title }}</p>
          <RouterLink
            v-for="item in group.items"
            :key="item.name"
            class="shell__link"
            :class="{ 'shell__link--active': route.name === item.name }"
            :to="{ name: item.name }"
            @click="drawerOpen = false"
          >
            {{ item.title }}
          </RouterLink>
        </section>
      </nav>
    </aside>

    <div v-if="!immersive && drawerOpen" class="shell__scrim" @click="drawerOpen = false" />

    <div class="shell__main">
      <header v-if="!immersive" class="shell__topbar">
        <button type="button" class="shell__home" aria-label="返回对话首页" @click="goHome">
          <span aria-hidden="true">←</span>
          <span>返回对话</span>
        </button>
        <button type="button" class="shell__menu" aria-label="打开导航" @click="drawerOpen = true">
          ☰
        </button>
        <nav class="shell__crumbs" aria-label="面包屑">
          <template v-for="(crumb, index) in breadcrumbs" :key="crumb.name">
            <span v-if="index" class="shell__crumb-sep">/</span>
            <span class="shell__crumb">{{ crumb.title }}</span>
          </template>
        </nav>
        <div class="shell__user">
          <span class="shell__avatar" aria-hidden="true">{{ initials }}</span>
          <div class="shell__user-meta">
            <span class="shell__user-name">{{
              auth.user?.display_name ?? auth.user?.username
            }}</span>
            <span class="shell__user-role">{{
              auth.user?.roles.map((role) => role.name).join(' · ')
            }}</span>
          </div>
        </div>
      </header>

      <main class="shell__content" :class="{ 'shell__content--immersive': immersive }">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  min-height: 100vh;
  background: var(--cp-canvas);
}

.shell__sidebar {
  width: 240px;
  flex-shrink: 0;
  background: var(--cp-surface-card);
  border-right: 1px solid var(--cp-hairline);
  display: flex;
  flex-direction: column;
  padding: var(--cp-space-4) var(--cp-space-3);
  z-index: 30;
}

.shell__brand {
  width: 100%;
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
  padding: var(--cp-space-2) var(--cp-space-2) var(--cp-space-4);
  border: 0;
  background: transparent;
  cursor: pointer;
  font-family: inherit;
  text-align: left;
}

.shell__logo {
  width: 28px;
  height: 28px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  background: var(--cp-primary);
  color: var(--cp-on-primary);
  font-size: 12px;
  font-weight: 700;
}

.shell__name {
  font-weight: 600;
  color: var(--cp-ink);
  letter-spacing: -0.01em;
}

.shell__nav {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--cp-space-4);
}

.shell__group-title {
  margin: 0 0 var(--cp-space-1);
  padding: 0 var(--cp-space-2);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  color: var(--cp-muted-soft);
  text-transform: uppercase;
}

.shell__link {
  display: block;
  padding: 8px var(--cp-space-2);
  border-radius: var(--cp-radius-button);
  color: var(--cp-body);
  font-size: 14px;
  text-decoration: none;
}

.shell__link:hover {
  background: var(--cp-canvas-soft);
  color: var(--cp-ink);
}

.shell__link--active {
  background: color-mix(in srgb, var(--cp-primary) 9%, white);
  color: var(--cp-primary);
  font-weight: 500;
}

.shell__main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.shell__topbar {
  display: flex;
  align-items: center;
  gap: var(--cp-space-3);
  padding: 0 var(--cp-space-4);
  min-height: 56px;
  background: var(--cp-surface-card);
  border-bottom: 1px solid var(--cp-hairline);
}

.shell__home,
.shell__menu {
  height: 40px;
  border: 1px solid var(--cp-hairline-strong);
  border-radius: var(--cp-radius-button);
  background: var(--cp-surface-card);
  color: var(--cp-body);
  cursor: pointer;
  font-family: inherit;
}

.shell__home {
  display: inline-flex;
  align-items: center;
  gap: var(--cp-space-1);
  padding: 0 var(--cp-space-3);
  font-size: 12px;
}

.shell__home:hover,
.shell__home:focus-visible,
.shell__menu:hover,
.shell__menu:focus-visible {
  background: var(--cp-canvas-soft);
  color: var(--cp-ink);
}

.shell__menu {
  display: none;
  width: 40px;
  font-size: 16px;
}

.shell__crumbs {
  flex: 1;
  display: flex;
  gap: var(--cp-space-1);
  font-size: 13px;
  color: var(--cp-muted);
  overflow: hidden;
  white-space: nowrap;
}

.shell__crumb-sep {
  color: var(--cp-muted-soft);
}

.shell__user {
  display: flex;
  align-items: center;
  gap: var(--cp-space-2);
}

.shell__avatar {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--cp-ink);
  color: var(--cp-canvas);
  font-size: 13px;
  font-weight: 600;
}

.shell__user-meta {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}

.shell__user-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--cp-ink);
}

.shell__user-role {
  font-size: 11px;
  color: var(--cp-muted);
}

.shell__content {
  flex: 1;
  padding: var(--cp-space-5);
  max-width: 1280px;
  width: 100%;
  margin: 0 auto;
}

.shell__content--immersive {
  max-width: none;
  height: 100vh;
  padding: 0;
  overflow: hidden;
}

.shell__scrim {
  display: none;
}

@media (max-width: 1023px) {
  .shell__sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    transform: translateX(-100%);
    transition: transform 0.2s ease;
  }

  .shell__sidebar--open {
    transform: translateX(0);
  }

  .shell__scrim {
    display: block;
    position: fixed;
    inset: 0;
    background: rgba(38, 37, 30, 0.35);
    z-index: 20;
  }

  .shell__menu {
    display: grid;
    place-items: center;
  }

  .shell__home {
    width: 40px;
    justify-content: center;
    padding: 0;
  }

  .shell__home span:last-child {
    display: none;
  }

  .shell__content {
    padding: var(--cp-space-4);
  }
}
</style>
