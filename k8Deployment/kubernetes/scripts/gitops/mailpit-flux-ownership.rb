# Mailpit's opt-in adapter retains its reviewed identity and public constants.
# The shared checks support both HelmRelease and its older StatelessRelease
# implementation without enabling Flux tolerance for other release instances.
require_relative 'flux-ownership'

module MailpitFluxOwnership
  include FluxOwnership

  HELMRELEASE_NAME = FluxOwnership::BINDINGS.fetch('mailpit').fetch(:helmrelease_name)
  HELMRELEASE_NAMESPACE = FluxOwnership::BINDINGS.fetch('mailpit').fetch(:helmrelease_namespace)
  RELEASE_NAMESPACE = FluxOwnership::BINDINGS.fetch('mailpit').fetch(:release_namespace)
  ORIGIN_LABELS = FluxOwnership::BINDINGS.fetch('mailpit').fetch(:origin_labels)

  private

  def flux_ownership_component
    'mailpit'
  end
end
