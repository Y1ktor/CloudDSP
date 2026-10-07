#!/usr/bin/env ruby
# Frontend's exact local adoption boundary. Its OIDC redirect host, Service
# selector, static-image digest, and Pod identity are checked by the shared
# Helm release helper before any live ownership transition. The optional Flux
# adapter reserves direct writes for an existing HelmRelease and verifies the
# controller's precise native release revision and top-level origin labels.
require_relative '../lib/paths'
require_relative '../lib/helm-release'
require_relative '../gitops/frontend-flux-ownership'

HelmRelease.new(
  component: 'frontend',
  namespace: 'clouddsp-app',
  release: 'clouddsp-frontend',
  source_files: %w[frontend-deployment.yaml frontend-service.yaml frontend-ingress.yaml],
  resources: %w[deployment/clouddsp-frontend service/clouddsp-frontend ingress/clouddsp-frontend],
  pod_selector: 'app.kubernetes.io/name=clouddsp-frontend,app.kubernetes.io/component=frontend',
  health_host: 'clouddsp.localhost',
  health_path: '/healthz',
  browser_shell: true,
  # React owns these navigation paths, including the public architecture
  # asset directory's name. Verify deep links without relying on client-side
  # navigation or following an NGINX directory redirect.
  browser_routes: %w[/architecture /architecture/ /k8 /cost /score-to-midi],
  allow_fresh_install: true,
  before_install: [
    %w[ruby ./k8Deployment/kubernetes/scripts/stages/keycloak/keycloak-config-verify.rb verify],
    %w[ruby ./k8Deployment/kubernetes/scripts/releases/job-api-release.rb verify]
  ]
).extend(FrontendFluxOwnership).run(ARGV.length == 1 ? ARGV.first : nil)
