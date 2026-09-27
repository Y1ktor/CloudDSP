#!/usr/bin/env ruby
# Frontend's exact local adoption boundary. Its OIDC redirect host, Service
# selector, static-image digest, and Pod identity are checked by the shared
# stateless-release helper before any live ownership transition.
require_relative 'stateless-release'

StatelessRelease.new(
  component: 'frontend',
  namespace: 'clouddsp-app',
  release: 'clouddsp-frontend',
  source_files: %w[frontend-deployment.yaml frontend-service.yaml frontend-ingress.yaml],
  resources: %w[deployment/clouddsp-frontend service/clouddsp-frontend ingress/clouddsp-frontend],
  pod_selector: 'app.kubernetes.io/name=clouddsp-frontend,app.kubernetes.io/component=frontend',
  health_host: 'clouddsp.localhost',
  health_path: '/healthz',
  browser_shell: true
).run(ARGV.length == 1 ? ARGV.first : nil)
