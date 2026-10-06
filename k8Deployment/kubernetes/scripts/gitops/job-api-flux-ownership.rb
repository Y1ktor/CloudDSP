# Opt in only the reviewed Job API release to Flux ownership verification.
# Database migrations, MinIO/Keycloak state, and credentials stay outside the
# chart. The parent helper retains exact specs, the running locked digest,
# readiness, and HTTP 401 checks for both protected browser routes.
require_relative 'flux-ownership'

module JobApiFluxOwnership
  include FluxOwnership

  HELMRELEASE_NAME = FluxOwnership::BINDINGS.fetch('job-api').fetch(:helmrelease_name)
  HELMRELEASE_NAMESPACE = FluxOwnership::BINDINGS.fetch('job-api').fetch(:helmrelease_namespace)
  RELEASE_NAMESPACE = FluxOwnership::BINDINGS.fetch('job-api').fetch(:release_namespace)
  ORIGIN_LABELS = FluxOwnership::BINDINGS.fetch('job-api').fetch(:origin_labels)

  private

  def flux_ownership_component
    'job-api'
  end
end
