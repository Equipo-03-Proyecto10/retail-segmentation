// The API documentation page's setup (#1192, ADR-0090).
//
// The service list comes from swagger-config.json, generated from
// contracts/registry.yaml. Each entry is the contract the service serves
// itself, so nothing here is a copy of a contract.
//
// Most contracts declare no `servers`, and their paths start at /v1/. A
// "Try it out" call from this page would then go to /v1/... on this host,
// which is the web application. The interceptor puts the selected service's
// /api/<name> prefix in front of any same-origin path outside /api/, which is
// how NGINX routes that service on the instance and on the local gateway.
//
// Credentials: the bearer token is whatever the user types into "Authorize".
// It lives in this tab's memory only (persistAuthorization is off), and no
// cookie is sent with a call (withCredentials is off).
(function () {
  "use strict";

  var SPEC = /^\/api\/([a-z0-9-]+)\/openapi\.yaml$/;
  var ui;

  function servicePrefix() {
    var match = ui && SPEC.exec(new URL(ui.specSelectors.url(), window.location.href).pathname);
    return match ? "/api/" + match[1] : null;
  }

  function requestInterceptor(request) {
    var url = new URL(request.url, window.location.href);
    if (url.origin === window.location.origin && url.pathname.indexOf("/api/") !== 0) {
      var prefix = servicePrefix();
      if (prefix) {
        url.pathname = prefix + url.pathname;
        request.url = url.toString();
      }
    }
    return request;
  }

  window.addEventListener("DOMContentLoaded", function () {
    ui = window.SwaggerUIBundle({
      dom_id: "#swagger-ui",
      configUrl: "swagger-config.json",
      presets: [window.SwaggerUIBundle.presets.apis, window.SwaggerUIStandalonePreset],
      plugins: [window.SwaggerUIBundle.plugins.DownloadUrl],
      layout: "StandaloneLayout",
      deepLinking: true,
      // A ?url= or ?configUrl= in the address never replaces the registry list.
      queryConfigEnabled: false,
      // swagger.io's validator is a third-party call; the edge's CSP refuses
      // it anyway, so it is never attempted.
      validatorUrl: "none",
      persistAuthorization: false,
      withCredentials: false,
      requestInterceptor: requestInterceptor,
    });
  });
})();
