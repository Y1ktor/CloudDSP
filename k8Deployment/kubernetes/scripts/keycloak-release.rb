#!/usr/bin/env ruby
# Transfer only the running Keycloak Deployment, ClusterIP Service, and
# browser Ingress to one Helm release after source/render/live parity checks.
# Fresh install is allowed only after the isolated database and ignored
# bootstrap-administrator Secret verify. Realm/client/SMTP bootstrap Jobs and
# identity data remain separate ownership boundaries.
require_relative 'stateless-release'

module CloudDSPKeycloakRelease
  def self.build
    StatelessRelease.new(
      component: 'keycloak',
      namespace: 'clouddsp-data',
      release: 'clouddsp-keycloak',
      source_files: %w[keycloak-deployment.yaml keycloak-service.yaml keycloak-ingress.yaml],
      resources: %w[deployment/clouddsp-keycloak service/clouddsp-keycloak ingress/clouddsp-keycloak],
      pod_selector: 'app.kubernetes.io/name=keycloak,app.kubernetes.io/instance=clouddsp-keycloak,app.kubernetes.io/component=identity-provider',
      health_host: 'keycloak.localhost',
      health_path: '/realms/master/.well-known/openid-configuration',
      # The generic fresh path checks that Helm and all three workload objects
      # are absent. These read-only prerequisites then catch a missing/changed
      # login before Helm starts a Pod that could initialize the wrong realm.
      allow_fresh_install: true,
      before_install: [
        ['ruby', StatelessRelease::ROOT.join('scripts', 'keycloak-database-stage.rb').to_s, 'verify'],
        ['ruby', StatelessRelease::ROOT.join('scripts', 'keycloak-admin-secret-stage.rb').to_s, 'verify']
      ],
      fresh_install_timeout: '7m',
      smoke_job: {
        name: 'keycloak-oidc-discovery-smoke',
        manifest: 'tests/keycloak-smoke/keycloak-oidc-discovery-smoke-job.yaml'
      }
    )
  end
end

CloudDSPKeycloakRelease.build.run(ARGV.length == 1 ? ARGV.first : nil) if $PROGRAM_NAME == __FILE__
