import { hostname } from 'node:os';
import { join } from 'node:path';
import { app, BrowserWindow, ipcMain } from 'electron';
import { CHANNELS } from '../shared/api';
import { Backoffice } from './backoffice';
import { loadBundle } from './bundle';
import { Campaigns } from './campaigns';
import { Catalog } from './catalog';
import { Floor } from './floor';
import { loadSettings } from './settings';
import { Stores } from './stores';
import { Transport } from './transport';
import { Users } from './users';
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
  const floor = new Floor(office);
  ipcMain.handle(CHANNELS.stores, () => floor.stores());
  ipcMain.handle(CHANNELS.stockLevels, (_e, query: unknown) => floor.stockLevels(query));
  ipcMain.handle(CHANNELS.listSales, (_e, query: unknown) => floor.listSales(query));
  ipcMain.handle(CHANNELS.getSale, (_e, saleId: unknown) => floor.getSale(saleId));
  ipcMain.handle(CHANNELS.engagementSummary, (_e, query: unknown) => floor.engagementSummary(query));
  ipcMain.handle(CHANNELS.listEngagementEvents, (_e, query: unknown) => floor.listEngagementEvents(query));
  ipcMain.handle(CHANNELS.voidEngagementEvent, (_e, eventId: unknown, reason: unknown) =>
    floor.voidEngagementEvent(eventId, reason),
  );
  ipcMain.handle(CHANNELS.listCheckIns, (_e, query: unknown) => floor.listCheckIns(query));
  ipcMain.handle(CHANNELS.getCheckIn, (_e, checkInId: unknown) => floor.getCheckIn(checkInId));
  ipcMain.handle(CHANNELS.reviewCheckIn, (_e, checkInId: unknown, verified: unknown, reason: unknown) =>
    floor.reviewCheckIn(checkInId, verified, reason),
  );
  const users = new Users(office);
  ipcMain.handle(CHANNELS.listUsers, (_e, query: unknown) => users.listUsers(query));
  ipcMain.handle(CHANNELS.getUser, (_e, userId: unknown) => users.getUser(userId));
  ipcMain.handle(CHANNELS.listRoles, () => users.listRoles());
  ipcMain.handle(CHANNELS.createUser, (_e, draft: unknown) => users.createUser(draft));
  ipcMain.handle(CHANNELS.updateUser, (_e, userId: unknown, changes: unknown) =>
    users.updateUser(userId, changes),
  );
  ipcMain.handle(CHANNELS.deactivateUser, (_e, userId: unknown) => users.deactivateUser(userId));
  ipcMain.handle(CHANNELS.reactivateUser, (_e, userId: unknown) => users.reactivateUser(userId));
  ipcMain.handle(CHANNELS.resetUserPassword, (_e, userId: unknown, newPassword: unknown) =>
    users.resetUserPassword(userId, newPassword),
  );
  ipcMain.handle(CHANNELS.changeOwnPassword, (_e, current: unknown, next: unknown) =>
    users.changeOwnPassword(current, next),
  );
  ipcMain.handle(CHANNELS.transferAdministrator, (_e, successor: unknown) =>
    users.transferAdministrator(successor),
  );
  const stores = new Stores(office);
  ipcMain.handle(CHANNELS.listStores, (_e, query: unknown) => stores.listStores(query));
  ipcMain.handle(CHANNELS.getStore, (_e, storeId: unknown) => stores.getStore(storeId));
  ipcMain.handle(CHANNELS.setStoreLocation, (_e, storeId: unknown, location: unknown) =>
    stores.setLocation(storeId, location),
  );
  ipcMain.handle(CHANNELS.removeStoreLocation, (_e, storeId: unknown) => stores.removeLocation(storeId));
  const campaigns = new Campaigns(office);
  ipcMain.handle(CHANNELS.listCampaigns, (_e, query: unknown) => campaigns.listCampaigns(query));
  ipcMain.handle(CHANNELS.getCampaign, (_e, campaignId: unknown) => campaigns.getCampaign(campaignId));
  ipcMain.handle(CHANNELS.createCampaign, (_e, draft: unknown) => campaigns.createCampaign(draft));
  ipcMain.handle(CHANNELS.updateCampaign, (_e, campaignId: unknown, changes: unknown) =>
    campaigns.updateCampaign(campaignId, changes),
  );
  ipcMain.handle(CHANNELS.transitionCampaign, (_e, campaignId: unknown, to: unknown, expected: unknown) =>
    campaigns.transitionCampaign(campaignId, to, expected),
  );
  ipcMain.handle(CHANNELS.listCampaignLabels, () => campaigns.listLabels());
  office.onStateChange((state) => {
    for (const w of BrowserWindow.getAllWindows()) w.webContents.send(CHANNELS.stateChanged, state);
  });

  hardenSession();
  void office.restore().finally(() => createSecureWindow(distDir));
}

void app.whenReady().then(start);
app.on('window-all-closed', () => app.quit());
