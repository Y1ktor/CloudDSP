import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // amazon-cognito-identity-js includes the browser `buffer` package, which
  // still expects Node's legacy `global` identifier. Browsers expose the
  // equivalent global object as `globalThis`; Vite does not inject this shim.
  define: {
    global: 'globalThis',
  },
})
