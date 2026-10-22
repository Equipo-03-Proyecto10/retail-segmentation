# #417 — Desktop review of PR #654

Local review on 2026-10-22, Linux, Node 22.23.2 and Electron 38.8.6:

- `npm run check`: ESLint, Prettier, TypeScript and all 90 unit tests pass.
- `npm run build` and `npx playwright test`: all 18 real Electron window tests
  pass, including CUSTOMER rejection, role-specific screens, stock and sale
  readback, engagement voids and append-only check-in reviews.
- Removed request reflection from the test gateway: product ids become
  positive safe integers, the review response uses a fixed fixture reason and
  asserts the submitted reason, and XML responses disable MIME sniffing.
  This addresses CodeQL's reflected-XSS finding without suppressing it.
- BrowserWindow security settings and the named IPC operations remain intact.

These tests use contract examples. The JSON-created sale read back in XML
against the deployed stack and its F18-05 screenshot remain outstanding under
#417; this review does not claim that deployment demonstration is complete.
