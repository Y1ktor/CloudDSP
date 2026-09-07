import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
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
    // The direct-upload form posts to MinIO rather than relaying audio through
    // the Job API. Its one public origin must enter connect-src explicitly.
    objectStorageUrl: environment.VITE_OBJECT_STORAGE_URL,
    webSocketUrl: environment.VITE_WEBSOCKET_URL,
    // OIDC discovery and the code/token exchange are browser `fetch` calls.
    // Feed the Keycloak realm issuer into CSP generation so a strict policy
    // allows only this reviewed local identity origin, not arbitrary HTTP.
    oidcIssuer: environment.VITE_OIDC_ISSUER,
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
  const cspResponseHeaderConfig = {
    // A CSP meta tag protects ordinary browser navigation, but the production
    // NGINX server must emit the identical policy as a response header too.
    // Generating the include from the same function makes it impossible for a
    // later local endpoint change to update one policy representation only.
    name: 'clouddsp-csp-response-header',
    apply: 'build',
    closeBundle() {
      // CSP source values are validated URL origins above, and the shared
      // directive list contains no double quote or newline. Reject either
      // defensively because this text becomes an NGINX quoted directive.
      if (/["\r\n]/.test(productionPolicy)) {
        throw new Error('Generated Content-Security-Policy cannot be emitted safely by NGINX.');
      }
      writeFileSync(
        resolve(process.cwd(), 'dist', 'csp-header.conf'),
        `# Generated from app/csp.js during the Vite production build.\nadd_header Content-Security-Policy "${productionPolicy}" always;\n`,
        'utf8',
      )
    },
  }

  return {
    plugins: [react(), contentSecurityPolicyMeta, cspResponseHeaderConfig],
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
  }
})
