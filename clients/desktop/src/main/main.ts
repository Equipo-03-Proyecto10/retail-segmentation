import { hostname } from 'node:os';
import { join } from 'node:path';
import { app, BrowserWindow, ipcMain } from 'electron';
import { CHANNELS } from '../shared/api';
import { Backoffice } from './backoffice';
import { loadBundle } from './bundle';
import { Catalog } from './catalog';
import { loadSettings } from './settings';
import { Transport } from './transport';
import { SafeStorageVault } from './vault';
import { createSecureWindow, hardenSession } from './window';
import { XsdValidator } from './xml';

// MOSAIQ_DESKTOP_CONFIG_DIR overrides the configuration directory (used by the window tests).
const configDir = process.env.MOSAIQ_DESKTOP_CONFIG_DIR;
if (configDir) app.setPath('userData', configDir);

const distDir = __dirname;

function start(): void {
  const dir = app.getPath('userData');
  const settings = loadSettings(dir);
  const bundle = loadBundle(join(distDir, '..', 'bundled'));
  const transport = new Transport(bundle, new XsdValidator(bundle), () => settings.baseUrl);
  const label = `MOSAIQ Desktop on ${hostname()}`.slice(0, 100);
  const office = new Backoffice(bundle, transport, new SafeStorageVault(dir), label);

  ipcMain.handle(CHANNELS.getState, () => office.state());
  ipcMain.handle(CHANNELS.signIn, (_e, c: unknown) => {
    const { email, password } = (c ?? {}) as { email?: unknown; password?: unknown };
    if (typeof email !== 'string' || typeof password !== 'string') {
      return {
        ok: false,
        state: office.state(),
        error: {
          code: 'invalid_input',
          message: 'Enter an email and a password.',
          correlation_id: '-',
          details: [],
        },
      };
    }
    return office.signIn({ email, password });
  });
  ipcMain.handle(CHANNELS.signOut, () => office.signOut());
  ipcMain.handle(CHANNELS.probeService, (_e, service: unknown) =>
    typeof service === 'string'
      ? office.probeService(service)
      : {
          ok: false,
          error: { code: 'invalid_input', message: 'Unknown service.', correlation_id: '-', details: [] },
        },
  );
  // Each handler passes the renderer's values on unchanged; Catalog checks every one of them.
  const catalog = new Catalog(office);
  ipcMain.handle(CHANNELS.listProducts, (_e, query: unknown) => catalog.listProducts(query));
  ipcMain.handle(CHANNELS.getProduct, (_e, productId: unknown) => catalog.getProduct(productId));
  ipcMain.handle(CHANNELS.createProduct, (_e, draft: unknown) => catalog.createProduct(draft));
  ipcMain.handle(CHANNELS.updateProduct, (_e, productId: unknown, changes: unknown) =>
    catalog.updateProduct(productId, changes),
  );
  ipcMain.handle(CHANNELS.deactivateProduct, (_e, productId: unknown) =>
    catalog.deactivateProduct(productId),
  );
  ipcMain.handle(CHANNELS.categoryTree, () => catalog.categoryTree());
  ipcMain.handle(CHANNELS.listCategories, (_e, parentId: unknown, page: unknown) =>
    catalog.listCategories(parentId, page),
  );
  ipcMain.handle(CHANNELS.categoryDescendants, (_e, categoryId: unknown, page: unknown) =>
    catalog.categoryDescendants(categoryId, page),
  );
  ipcMain.handle(CHANNELS.createCategory, (_e, draft: unknown) => catalog.createCategory(draft));
  ipcMain.handle(CHANNELS.updateCategory, (_e, categoryId: unknown, changes: unknown) =>
    catalog.updateCategory(categoryId, changes),
  );
  ipcMain.handle(CHANNELS.deleteCategory, (_e, categoryId: unknown) => catalog.deleteCategory(categoryId));
  office.onStateChange((state) => {
    for (const w of BrowserWindow.getAllWindows()) w.webContents.send(CHANNELS.stateChanged, state);
  });

  hardenSession();
  void office.restore().finally(() => createSecureWindow(distDir));
}

void app.whenReady().then(start);
app.on('window-all-closed', () => app.quit());
