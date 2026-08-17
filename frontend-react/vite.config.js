import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { contentSecurityPolicy } from './csp.js'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, process.cwd(), '')
  const policyOptions = {
    jobApiUrl: environment.VITE_JOB_API_URL,
    webSocketUrl: environment.VITE_WEBSOCKET_URL,
    userPoolId: environment.VITE_COGNITO_USER_POOL_ID,
  }
  const productionPolicy = contentSecurityPolicy(policyOptions)
  const developmentPolicy = contentSecurityPolicy({ ...policyOptions, development: true })
  const contentSecurityPolicyMeta = {
    name: 'clouddsp-content-security-policy',
    transformIndexHtml: {
      order: 'pre',
      handler(_html, context) {
        return [{
          tag: 'meta',
          attrs: {
            'http-equiv': 'Content-Security-Policy',
            content: context.server ? developmentPolicy : productionPolicy,
          },
          injectTo: 'head-prepend',
        }]
      },
    },
  }

  return {
    plugins: [react(), contentSecurityPolicyMeta],
    server: {
      headers: {
        'Content-Security-Policy': developmentPolicy,
      },
    },
    preview: {
      headers: {
        'Content-Security-Policy': productionPolicy,
      },
    },
    // amazon-cognito-identity-js includes the browser `buffer` package, which
    // still expects Node's legacy `global` identifier. Browsers expose the
    // equivalent global object as `globalThis`; Vite does not inject this shim.
    define: {
      global: 'globalThis',
    },
  }
})
