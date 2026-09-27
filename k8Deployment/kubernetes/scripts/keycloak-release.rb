#!/usr/bin/env ruby
# Transfer only the running Keycloak Deployment, ClusterIP Service, and
# browser Ingress to one Helm release after source/render/live parity checks.
# Its PostgreSQL database, bootstrap administrator Secret, realm/client/SMTP
# bootstrap Jobs, and identity data remain separate ownership boundaries.
require_relative 'stateless-release'

StatelessRelease.new(
  component: 'keycloak',
  namespace: 'clouddsp-data',
  release: 'clouddsp-keycloak',
  source_files: %w[keycloak-deployment.yaml keycloak-service.yaml keycloak-ingress.yaml],
  resources: %w[deployment/clouddsp-keycloak service/clouddsp-keycloak ingress/clouddsp-keycloak],
  pod_selector: 'app.kubernetes.io/name=keycloak,app.kubernetes.io/instance=clouddsp-keycloak,app.kubernetes.io/component=identity-provider',
  health_host: 'keycloak.localhost',
  health_path: '/realms/master/.well-known/openid-configuration',
  smoke_job: {
    name: 'keycloak-oidc-discovery-smoke',
    manifest: 'tests/keycloak-smoke/keycloak-oidc-discovery-smoke-job.yaml'
  }
).run(ARGV.length == 1 ? ARGV.first : nil)
