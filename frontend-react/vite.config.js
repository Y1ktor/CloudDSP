import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { contentSecurityPolicy } from './csp.js'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const environment = loadEnv(mode, process.cwd(), '')
  const configuredDemoOrigin = environment.VITE_DEMO_ASSET_ORIGIN?.trim()
  let demoAssetOrigin
  try {
    const parsedDemoOrigin = configuredDemoOrigin ? new URL(configuredDemoOrigin) : null
    demoAssetOrigin = parsedDemoOrigin?.protocol === 'https:' ? parsedDemoOrigin.origin : undefined
  } catch {
    demoAssetOrigin = undefined
  }
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
      // React Fast Refresh injects an inline preamble. Keep the production CSP
      // policy intact in development as well, so use ordinary browser refreshes
      // after edits instead of loosening script-src with unsafe-inline.
      hmr: false,
      // Optional local-only bridge to the published, private CloudFront demo
      // route. The browser still requests same-origin /demo/* resources, so
      // demoCatalog's URL validation and the strict development CSP apply.
      proxy: demoAssetOrigin ? {
        '/demo': {
          target: demoAssetOrigin,
          changeOrigin: true,
          secure: true,
        },
      } : undefined,
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
