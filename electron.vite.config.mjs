import { defineConfig } from 'electron-vite'
import { resolve } from 'node:path'

// Three parts: main (Node side, owns the window), preload (the safe bridge), renderer (the page).
// The renderer has two pages: the app and the design sheet.
export default defineConfig({
  main: {},
  preload: {},
  renderer: {
    build: {
      rollupOptions: {
        input: { index: resolve('src/renderer/index.html'), design: resolve('src/renderer/design.html') }
      }
    }
  }
})
