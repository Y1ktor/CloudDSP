# Kubernetes architecture icons

These local SVG assets identify the technologies used by CloudDSP's Kubernetes
deployment. The page serves them from its own origin without a runtime CDN or
an icon package dependency. Logos identify their respective projects; they do
not imply endorsement of CloudDSP.

The Simple Icons files retain their original path geometry, titles and view
boxes. Their root `fill` attributes use the brand colors in the same revision's
metadata. KEDA retains the original color artwork, including its embedded PNG
shading and internal SVG gradient.

## Sources

Simple Icons revision:
[`1089fb7d2bf0e323f834c205ab76265005a6d5e8`](https://github.com/simple-icons/simple-icons/tree/1089fb7d2bf0e323f834c205ab76265005a6d5e8).
The upstream source and color metadata come from its
[`data/simple-icons.json`](https://github.com/simple-icons/simple-icons/blob/1089fb7d2bf0e323f834c205ab76265005a6d5e8/data/simple-icons.json).

| Local file | Icon source | Brand color | Original project source |
| --- | --- | --- | --- |
| `keycloak.svg` | [Simple Icons: Keycloak](https://github.com/simple-icons/simple-icons/blob/1089fb7d2bf0e323f834c205ab76265005a6d5e8/icons/keycloak.svg) | `#4D4D4D` | [Keycloak artwork](https://github.com/keycloak/keycloak-misc/blob/dee033f2d6d6b5c3a6ce8eb84e285f7e5626dbf6/logo/icon-black.svg) |
| `postgresql.svg` | [Simple Icons: PostgreSQL](https://github.com/simple-icons/simple-icons/blob/1089fb7d2bf0e323f834c205ab76265005a6d5e8/icons/postgresql.svg) | `#4169E1` | [PostgreSQL logo](https://wiki.postgresql.org/wiki/Logo) |
| `rabbitmq.svg` | [Simple Icons: RabbitMQ](https://github.com/simple-icons/simple-icons/blob/1089fb7d2bf0e323f834c205ab76265005a6d5e8/icons/rabbitmq.svg) | `#FF6600` | [RabbitMQ](https://www.rabbitmq.com) |
| `minio.svg` | [Simple Icons: MinIO](https://github.com/simple-icons/simple-icons/blob/1089fb7d2bf0e323f834c205ab76265005a6d5e8/icons/minio.svg) | `#C72E49` | [MinIO logo](https://min.io/logo) |
| `helm.svg` | [Simple Icons: Helm](https://github.com/simple-icons/simple-icons/blob/1089fb7d2bf0e323f834c205ab76265005a6d5e8/icons/helm.svg) | `#0F1689` | [Helm](https://helm.sh) |
| `react.svg` | [Simple Icons: React](https://github.com/simple-icons/simple-icons/blob/1089fb7d2bf0e323f834c205ab76265005a6d5e8/icons/react.svg) | `#61DAFB` | [React artwork](https://github.com/facebook/create-react-app/blob/282c03f9525fdf8061ffa1ec50dce89296d916bd/test/fixtures/relative-paths/src/logo.svg) |
| `kubernetes.svg` | [Simple Icons: Kubernetes](https://github.com/simple-icons/simple-icons/blob/1089fb7d2bf0e323f834c205ab76265005a6d5e8/icons/kubernetes.svg) | `#326CE5` | [Kubernetes logos](https://github.com/kubernetes/kubernetes/tree/cac53883f4714452f3084a22e4be20d042a9df33/logo) |
| `docker.svg` | [Simple Icons: Docker](https://github.com/simple-icons/simple-icons/blob/1089fb7d2bf0e323f834c205ab76265005a6d5e8/icons/docker.svg) | `#2496ED` | [Docker media resources](https://www.docker.com/company/newsroom/media-resources) |
| `traefik.svg` | [Simple Icons: Traefik Proxy](https://github.com/simple-icons/simple-icons/blob/1089fb7d2bf0e323f834c205ab76265005a6d5e8/icons/traefikproxy.svg) | `#24A1C1` | [Traefik Proxy](https://traefik.io/traefik) |
| `keda.svg` | [CNCF: KEDA color icon](https://github.com/cncf/artwork/blob/002662490acb2303c7301acc0256c00790e03e9f/projects/keda/icon/color/keda-icon-color.svg) | Original multicolor | [KEDA project artwork](https://github.com/cncf/artwork/tree/002662490acb2303c7301acc0256c00790e03e9f/projects/keda) |

## Licenses and trademarks

The Simple Icons collection is published under CC0 1.0 Universal; a copy is
included in [LICENSE.simple-icons.md](LICENSE.simple-icons.md). Brand trademarks
and any separate project logo policies still apply. The upstream Keycloak
metadata specifically points to the Linux Foundation trademark policy;
PostgreSQL, RabbitMQ and MinIO also provide their own logo or trademark guidance
through the sources above.

The CNCF artwork repository supplies KEDA's original logo under its trademark
and logo policy. Its license text is included in
[LICENSE.cncf-artwork.md](LICENSE.cncf-artwork.md); see also the
[Linux Foundation trademark usage policy](https://www.linuxfoundation.org/legal/trademark-usage).

## Static SVG review

The assets were parsed as XML and checked for scripts, event-handler attributes,
foreign objects, animation, external image URLs, external stylesheet URLs and
external references. None were present. KEDA uses one embedded `data:image/png`
image and one internal `url(#Gradient_bez_nazwy)` reference; these require no
network request or executable content.
