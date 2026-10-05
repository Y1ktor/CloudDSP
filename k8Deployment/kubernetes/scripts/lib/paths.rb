# Central path adapter for every Ruby deployment runner.
#
# A runner's directory describes its responsibility, not its deployment root.
# Resolve versioned inputs from this library so moving a stage cannot redirect
# Secret sources, charts, manifests, or child commands to another directory.
require 'pathname'

module CloudDSPPaths
  SCRIPT_ROOT = Pathname.new(__dir__).parent.freeze
  KUBERNETES_ROOT = SCRIPT_ROOT.parent.freeze
  REPOSITORY_ROOT = KUBERNETES_ROOT.parent.parent.freeze

  # The registry is explicit: unknown names fail before a deployment command.
  # Coordinators retain their reviewed basename/order tables while this one
  # catalog owns where those commands live within scripts/.
  LOCATIONS = {
    'gitops' => %w[
      bootstrap-flux.sh
      mailpit-flux-ownership.rb
    ],
    'images' => %w[
      build-adtof-image.sh
      build-adtof-worker-smoke-client-image.sh
      build-basic-pitch-image.sh
      build-basic-pitch-worker-smoke-client-image.sh
      build-demucs-image.sh
      build-demucs-worker-smoke-client-image.sh
      build-dispatcher-image.sh
      build-dispatcher-smoke-client-image.sh
      build-frontend-image.sh
      build-generic-dispatcher-basic-pitch-smoke-client-image.sh
      build-job-api-image.sh
      build-minio-source-intake-smoke-client-image.sh
      build-upload-intake-image.sh
      image-registry-stage.rb
      verify-job-api-local-image.sh
      verify-registry.sh
    ],
    'lib' => %w[
      credential-catalog.rb
      helm-release.rb
      worker-scaling-policy.rb
    ],
    'maintenance' => %w[
      cleanup-cluster.sh
      minio-backup-and-restore-test.py
      postgresql-backup-and-restore-test.sh
      purge-registry.sh
      rabbitmq-backup-and-restore-test.py
      reconcile-demucs-scaling.sh
      reconcile-one-upload-intake-job.sh
      verify-http-routing.sh
    ],
    'orchestration' => %w[
      cluster.sh
      deploy-local-foundation.rb
      deploy-local-mailpit.rb
      deploy-local-minio.rb
      deploy-local-plan.rb
      deploy-local-platform.rb
      deploy-local-postgresql.rb
      deploy-local-prepare.rb
      deploy-local-rabbitmq.rb
      deploy-local-reconcile.rb
      deploy-local-verify.rb
    ],
    'releases' => %w[
      adtof-release.rb
      basic-pitch-release.rb
      demucs-release.rb
      dispatcher-release.rb
      frontend-release.rb
      generic-dispatcher-release.rb
      install-keda.sh
      job-api-release.rb
      keda-release-stage.rb
      keycloak-release.rb
      mailpit-release.rb
      minio-release.rb
      postgresql-release.rb
      rabbitmq-release.rb
      scaling-auth-release.rb
      upload-intake-release.rb
    ],
    'stages/credentials' => %w[
      adtof-minio-secret-stage.rb
      application-identity-stage.rb
      basic-pitch-minio-secret-stage.rb
      credential-init.rb
      demucs-minio-secret-stage.rb
      job-api-database-secret-stage.rb
      job-api-minio-secret-stage.rb
      keycloak-admin-secret-stage.rb
      minio-amqp-secret-stage.rb
      minio-root-secret-stage.rb
      postgresql-secret-stage.rb
      rabbitmq-secret-stage.rb
      upload-intake-minio-secret-stage.rb
      upload-intake-rabbitmq-secret-stage.rb
    ],
    'stages/database' => %w[
      job-api-database-bootstrap.rb
      job-api-migrations.rb
      job-api-postgresql-stage.rb
      worker-database-stage.rb
    ],
    'stages/keycloak' => %w[
      keycloak-config-verify.rb
      keycloak-database-stage.rb
      keycloak-realm-stage.rb
    ],
    'stages/minio' => %w[
      minio-adtof-iam-stage.rb
      minio-basic-pitch-iam-stage.rb
      minio-buckets-stage.rb
      minio-demucs-iam-stage.rb
      minio-fresh-buckets-stage.rb
      minio-fresh-samples-stage.rb
      minio-job-api-iam-stage.rb
      minio-notification-stage.rb
      minio-state-verify.rb
      minio-upload-intake-iam-stage.rb
    ],
    'stages/rabbitmq' => %w[
      rabbitmq-processing-topology.rb
      rabbitmq-source-intake-bootstrap.rb
    ],
  }.freeze
  SCRIPTS = LOCATIONS.each_with_object({}) do |(directory, names), paths|
    names.each { |name| paths[name] = SCRIPT_ROOT.join(directory, name).freeze }
  end.freeze

  def self.script(name)
    SCRIPTS.fetch(name)
  end

  def self.repository_script(name)
    "./#{script(name).relative_path_from(REPOSITORY_ROOT)}"
  end
end
