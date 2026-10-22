import { contextBridge, ipcRenderer } from 'electron';
import { CHANNELS, type AppState, type MosaiqBridge } from '../shared/api';

// Named operations only: no generic invoke, no fetch, no ipcRenderer.
const bridge: MosaiqBridge = {
  getState: () => ipcRenderer.invoke(CHANNELS.getState),
  signIn: (credentials) => ipcRenderer.invoke(CHANNELS.signIn, credentials),
  signOut: () => ipcRenderer.invoke(CHANNELS.signOut),
  probeService: (service) => ipcRenderer.invoke(CHANNELS.probeService, service),
  onStateChange: (listener) => {
    const handler = (_event: unknown, state: AppState): void => listener(state);
    ipcRenderer.on(CHANNELS.stateChanged, handler);
    return () => ipcRenderer.removeListener(CHANNELS.stateChanged, handler);
  },
  listProducts: (query) => ipcRenderer.invoke(CHANNELS.listProducts, query),
  getProduct: (productId) => ipcRenderer.invoke(CHANNELS.getProduct, productId),
  createProduct: (draft) => ipcRenderer.invoke(CHANNELS.createProduct, draft),
  updateProduct: (productId, changes) => ipcRenderer.invoke(CHANNELS.updateProduct, productId, changes),
  deactivateProduct: (productId) => ipcRenderer.invoke(CHANNELS.deactivateProduct, productId),
  categoryTree: () => ipcRenderer.invoke(CHANNELS.categoryTree),
  listCategories: (parentId, page) => ipcRenderer.invoke(CHANNELS.listCategories, parentId, page),
  categoryDescendants: (categoryId, page) =>
    ipcRenderer.invoke(CHANNELS.categoryDescendants, categoryId, page),
  createCategory: (draft) => ipcRenderer.invoke(CHANNELS.createCategory, draft),
  updateCategory: (categoryId, changes) => ipcRenderer.invoke(CHANNELS.updateCategory, categoryId, changes),
  deleteCategory: (categoryId) => ipcRenderer.invoke(CHANNELS.deleteCategory, categoryId),
  stores: () => ipcRenderer.invoke(CHANNELS.stores),
  listStores: (query) => ipcRenderer.invoke(CHANNELS.listStores, query),
  getStore: (storeId) => ipcRenderer.invoke(CHANNELS.getStore, storeId),
  setStoreLocation: (storeId, location) => ipcRenderer.invoke(CHANNELS.setStoreLocation, storeId, location),
  removeStoreLocation: (storeId) => ipcRenderer.invoke(CHANNELS.removeStoreLocation, storeId),
  listCampaigns: (query) => ipcRenderer.invoke(CHANNELS.listCampaigns, query),
  getCampaign: (campaignId) => ipcRenderer.invoke(CHANNELS.getCampaign, campaignId),
  createCampaign: (draft) => ipcRenderer.invoke(CHANNELS.createCampaign, draft),
  updateCampaign: (campaignId, changes) => ipcRenderer.invoke(CHANNELS.updateCampaign, campaignId, changes),
  transitionCampaign: (campaignId, to, expectedStatus) =>
    ipcRenderer.invoke(CHANNELS.transitionCampaign, campaignId, to, expectedStatus),
  listCampaignLabels: () => ipcRenderer.invoke(CHANNELS.listCampaignLabels),
  listAuditEntries: (query) => ipcRenderer.invoke(CHANNELS.listAuditEntries, query),
  getAuditEntry: (auditId) => ipcRenderer.invoke(CHANNELS.getAuditEntry, auditId),
  stockLevels: (query) => ipcRenderer.invoke(CHANNELS.stockLevels, query),
  listSales: (query) => ipcRenderer.invoke(CHANNELS.listSales, query),
  getSale: (saleId) => ipcRenderer.invoke(CHANNELS.getSale, saleId),
  engagementSummary: (query) => ipcRenderer.invoke(CHANNELS.engagementSummary, query),
  listEngagementEvents: (query) => ipcRenderer.invoke(CHANNELS.listEngagementEvents, query),
  voidEngagementEvent: (eventId, reason) => ipcRenderer.invoke(CHANNELS.voidEngagementEvent, eventId, reason),
  listCheckIns: (query) => ipcRenderer.invoke(CHANNELS.listCheckIns, query),
  getCheckIn: (checkInId) => ipcRenderer.invoke(CHANNELS.getCheckIn, checkInId),
  reviewCheckIn: (checkInId, verified, reason) =>
    ipcRenderer.invoke(CHANNELS.reviewCheckIn, checkInId, verified, reason),
  listUsers: (query) => ipcRenderer.invoke(CHANNELS.listUsers, query),
  getUser: (userId) => ipcRenderer.invoke(CHANNELS.getUser, userId),
  listRoles: () => ipcRenderer.invoke(CHANNELS.listRoles),
  createUser: (draft) => ipcRenderer.invoke(CHANNELS.createUser, draft),
  updateUser: (userId, changes) => ipcRenderer.invoke(CHANNELS.updateUser, userId, changes),
  deactivateUser: (userId) => ipcRenderer.invoke(CHANNELS.deactivateUser, userId),
  reactivateUser: (userId) => ipcRenderer.invoke(CHANNELS.reactivateUser, userId),
  resetUserPassword: (userId, newPassword) =>
    ipcRenderer.invoke(CHANNELS.resetUserPassword, userId, newPassword),
  changeOwnPassword: (currentPassword, newPassword) =>
    ipcRenderer.invoke(CHANNELS.changeOwnPassword, currentPassword, newPassword),
  transferAdministrator: (successorUserId) =>
    ipcRenderer.invoke(CHANNELS.transferAdministrator, successorUserId),
};

contextBridge.exposeInMainWorld('mosaiq', bridge);
