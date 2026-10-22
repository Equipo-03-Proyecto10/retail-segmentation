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
};

contextBridge.exposeInMainWorld('mosaiq', bridge);
