# Opt in only the reviewed upload-intake release to Flux verification.
# Its one outbound consumer Deployment keeps the same Pod template, image,
# and restricted Secret references. Broker topology, notifications, database
# state, and the disposable source-to-outbox smoke remain separate boundaries.
require_relative 'flux-ownership'

module UploadIntakeFluxOwnership
  include FluxOwnership

  HELMRELEASE_NAME = FluxOwnership::BINDINGS.fetch('upload-intake').fetch(:helmrelease_name)
  HELMRELEASE_NAMESPACE = FluxOwnership::BINDINGS.fetch('upload-intake').fetch(:helmrelease_namespace)
  RELEASE_NAMESPACE = FluxOwnership::BINDINGS.fetch('upload-intake').fetch(:release_namespace)
  ORIGIN_LABELS = FluxOwnership::BINDINGS.fetch('upload-intake').fetch(:origin_labels)

  private

  def flux_ownership_component
    'upload-intake'
  end
end
