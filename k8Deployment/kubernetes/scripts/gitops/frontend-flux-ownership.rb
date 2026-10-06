# Only the existing frontend release may opt in to this Flux handoff. Browser
# routes, locked static image, selectors, and Pod templates continue through
# the parent's strict checks after the two reviewed top-level origin labels.
require_relative 'flux-ownership'

module FrontendFluxOwnership
  include FluxOwnership

  HELMRELEASE_NAME = FluxOwnership::BINDINGS.fetch('frontend').fetch(:helmrelease_name)
  HELMRELEASE_NAMESPACE = FluxOwnership::BINDINGS.fetch('frontend').fetch(:helmrelease_namespace)
  RELEASE_NAMESPACE = FluxOwnership::BINDINGS.fetch('frontend').fetch(:release_namespace)
  ORIGIN_LABELS = FluxOwnership::BINDINGS.fetch('frontend').fetch(:origin_labels)

  private

  def flux_ownership_component
    'frontend'
  end
end
