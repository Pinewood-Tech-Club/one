// Redirects www.pinewood.one/* to pinewood.one/*, preserving path and query.
//
// This runs at the edge rather than in the app because the apex record is
// DNS-only (grey cloud) so Traefik can hold its own Let's Encrypt certificate;
// with no proxy on the apex there is no Cloudflare rule layer to redirect from,
// and routing www through the origin just to bounce it would need a second
// certificate for a hostname that never serves content.

export default {
  fetch(request) {
    const url = new URL(request.url);
    url.hostname = "pinewood.one";
    url.protocol = "https:";
    url.port = "";
    return Response.redirect(url.toString(), 301);
  },
};
