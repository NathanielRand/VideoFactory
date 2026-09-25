import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  main: {},
  preload: {},
  renderer: {
    plugins: [react(), tailwindcss()],
    // A fixed port of our own, not Vite's default 5173: another project's dev
    // server on 5173 made Vite silently fall back to 5174, which the engine's
    // CORS list (server/api.py) does not allow, so every request failed with
    // "Could not reach the Video Factory engine". strictPort turns a clash
    // into a clear startup error instead.
    server: { port: 5273, strictPort: true }
  }
})
