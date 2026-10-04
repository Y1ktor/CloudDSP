/**
 * Configures cloud and local Vite builds, selecting platform adapters and public
 * settings and emitting the selected deployment's Content Security Policy.
 */
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { parseEnv } from 'node:util';
import { contentSecurityPolicy } from './csp.js';

const sharedPublicKeys = ['VITE_JOB_API_URL', 'VITE_WEBSOCKET_URL', 'VITE_DEMO_ASSET_ORIGIN'];
const cloudPublicKeys = ['VITE_COGNITO_USER_POOL_ID', 'VITE_COGNITO_BROWSER_CLIENT_ID'];
const localPublicKeys = [
  'VITE_OIDC_ISSUER', 'VITE_OIDC_CLIENT_ID', 'VITE_OIDC_REDIRECT_URI',
  'VITE_OIDC_POST_LOGOUT_REDIRECT_URI', 'VITE_OBJECT_STORAGE_URL',
];

export function profileForMode(mode) {
  if (mode === 'cloud') return 'cloud';
  // Vite reserves "local" as an environment suffix; k8 selects the local app.
  if (mode === 'k8') return 'local';
  throw new Error('Choose a CloudDSP frontend mode: cloud or k8.');
}

/**
 * Read only the selected profile's public settings. Legacy .env/.env.production
 * files may remain on a developer's machine; using Vite's usual shared fallback
 * would silently send a local build to that machine's cloud endpoints. Process
 * arguments still override files for the non-interactive local Docker builder.
 */
export function publicEnvironmentForMode(mode, directory = process.cwd(), inherited = process.env) {
  const selected = profileForMode(mode);
  const values = {};
  for (const filename of [`.env.${mode}`, `.env.${mode}.local`]) {
    const path = resolve(directory, filename);
    if (existsSync(path)) Object.assign(values, parseEnv(readFileSync(path, 'utf8')));
  }
  const activeKeys = [...sharedPublicKeys, ...(selected === 'cloud' ? cloudPublicKeys : localPublicKeys)];
  return Object.fromEntries(activeKeys.map((key) => [key, inherited[key] ?? values[key] ?? '']));
}

export default defineConfig(({ mode }) => {
  const selected = profileForMode(mode);
  const rootDirectory = process.cwd();
  const outDir = resolve(rootDirectory, 'dist', selected);
  const environment = publicEnvironmentForMode(mode, rootDirectory);
  const configuredDemoOrigin = environment.VITE_DEMO_ASSET_ORIGIN?.trim();
  let demoAssetOrigin;
  try {
    const parsed = configuredDemoOrigin ? new URL(configuredDemoOrigin) : null;
    demoAssetOrigin = parsed?.protocol === 'https:' ? parsed.origin : undefined;
  } catch {
    demoAssetOrigin = undefined;
  }
  const policyOptions = {
    profile: selected,
    jobApiUrl: environment.VITE_JOB_API_URL,
    webSocketUrl: environment.VITE_WEBSOCKET_URL,
    userPoolId: environment.VITE_COGNITO_USER_POOL_ID,
    objectStorageUrl: environment.VITE_OBJECT_STORAGE_URL,
    oidcIssuer: environment.VITE_OIDC_ISSUER,
  };
  const productionPolicy = contentSecurityPolicy(policyOptions);
  const developmentPolicy = contentSecurityPolicy({ ...policyOptions, development: true });
  const policyMeta = {
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
        }];
      },
    },
  };
  const localPolicyHeader = {
    name: 'clouddsp-local-csp-response-header',
    apply: 'build',
    closeBundle() {
      if (selected !== 'local') return;
      // NGINX includes this outside its document root. Use the same policy as
      // the HTML metadata, with validation before writing a quoted directive.
      if (/["\r\n]/.test(productionPolicy)) {
        throw new Error('Generated Content-Security-Policy cannot be emitted safely by NGINX.');
      }
      writeFileSync(
        resolve(outDir, 'csp-header.conf'),
        `# Generated from frontend/csp.js during the local Vite build.\nadd_header Content-Security-Policy "${productionPolicy}" always;\n`,
        'utf8',
      );
    },
  };

  return {
    // @platform resolves at build time, so one output contains only its selected
    // auth/sample integrations. Runtime components and assets have one owner.
    resolve: { alias: { '@platform': resolve(rootDirectory, 'src/platform', selected) } },
    build: { outDir, emptyOutDir: true },
    // Disable generic environment fallback and automatic prefix discovery. Only
    // the reviewed public keys below enter JavaScript; they are never secrets.
    envDir: false,
    envPrefix: [],
    define: {
      ...Object.fromEntries([...sharedPublicKeys, ...cloudPublicKeys, ...localPublicKeys]
        .map((key) => [`import.meta.env.${key}`, JSON.stringify(environment[key] ?? '')])),
      // Cognito's browser buffer dependency expects the legacy global name.
      ...(selected === 'cloud' ? { global: 'globalThis' } : {}),
    },
    plugins: [react(), policyMeta, localPolicyHeader],
    server: {
      // Keep the reviewed script policy in development; ordinary refresh avoids
      // introducing unsafe-inline merely for Fast Refresh's inline preamble.
      hmr: false,
      proxy: demoAssetOrigin ? {
        '/demo': { target: demoAssetOrigin, changeOrigin: true, secure: true },
      } : undefined,
      headers: { 'Content-Security-Policy': developmentPolicy },
    },
    preview: { headers: { 'Content-Security-Policy': productionPolicy } },
  };
});
