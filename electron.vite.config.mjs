import { defineConfig } from 'electron-vite'

// Three parts: main (Node side, owns the window), preload (the safe bridge), renderer (the page).
export default defineConfig({
  main: {},
  preload: {},
  renderer: {}
})
