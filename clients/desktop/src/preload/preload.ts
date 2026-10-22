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
};

contextBridge.exposeInMainWorld('mosaiq', bridge);
