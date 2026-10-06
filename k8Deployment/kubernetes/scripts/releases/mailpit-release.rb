#!/usr/bin/env ruby
# Mailpit's exact local Helm boundary. The shared helper validates
# source/chart/live parity before adoption; its separately guarded fresh
# install path requires an absent release and four absent objects. This entry
# point supplies Mailpit's reviewed names, route, and smoke Job. Its optional
# Flux adapter reserves writes for an existing HelmRelease and checks the
# controller's exact labels/chart revision during verification and smoke.
require_relative '../lib/paths'
require_relative '../lib/helm-release'
require_relative '../gitops/mailpit-flux-ownership'

HelmRelease.new(
  component: 'mailpit',
  namespace: 'clouddsp-data',
  release: 'clouddsp-mailpit',
  source_files: %w[mailpit-deployment.yaml mailpit-services.yaml mailpit-ingress.yaml],
  resources: %w[deployment/clouddsp-mailpit service/clouddsp-mailpit-smtp service/clouddsp-mailpit ingress/clouddsp-mailpit],
  pod_selector: 'app.kubernetes.io/name=mailpit,app.kubernetes.io/instance=clouddsp-mailpit,app.kubernetes.io/component=test-email',
  health_host: 'mailpit.localhost',
  health_path: '/readyz',
  allow_fresh_install: true,
  smoke_job: {
    name: 'mailpit-smtp-capture-smoke',
    manifest: 'tests/mailpit-smoke/mailpit-smtp-capture-smoke-job.yaml'
  }
).extend(MailpitFluxOwnership).run(ARGV.length == 1 ? ARGV.first : nil)
