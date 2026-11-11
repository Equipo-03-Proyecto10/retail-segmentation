import { contextBridge, ipcRenderer } from 'electron';
import { CHANNELS, type AppState, type MosaiqBridge } from '../shared/api';

// Named operations only: no generic invoke, no fetch, no ipcRenderer.
const bridge: MosaiqBridge = {
  getState: () => ipcRenderer.invoke(CHANNELS.getState),
  signIn: (credentials) => ipcRenderer.invoke(CHANNELS.signIn, credentials),
  signOut: () => ipcRenderer.invoke(CHANNELS.signOut),
  probeService: (service) => ipcRenderer.invoke(CHANNELS.probeService, service),
  exportTable: (request) => ipcRenderer.invoke(CHANNELS.exportTable, request),
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
  getCampaignAudience: (campaignId) => ipcRenderer.invoke(CHANNELS.getCampaignAudience, campaignId),
  putCampaignAudience: (campaignId, draft) =>
    ipcRenderer.invoke(CHANNELS.putCampaignAudience, campaignId, draft),
  getCampaignBudget: (campaignId) => ipcRenderer.invoke(CHANNELS.getCampaignBudget, campaignId),
  putCampaignBudget: (campaignId, draft) => ipcRenderer.invoke(CHANNELS.putCampaignBudget, campaignId, draft),
  getCampaignEligibilityRules: (campaignId) =>
    ipcRenderer.invoke(CHANNELS.getCampaignEligibilityRules, campaignId),
  putCampaignEligibilityRules: (campaignId, draft) =>
    ipcRenderer.invoke(CHANNELS.putCampaignEligibilityRules, campaignId, draft),
  getCampaignExclusions: (campaignId) => ipcRenderer.invoke(CHANNELS.getCampaignExclusions, campaignId),
  putCampaignExclusions: (campaignId, draft) =>
    ipcRenderer.invoke(CHANNELS.putCampaignExclusions, campaignId, draft),
  previewCampaignAudience: (campaignId) => ipcRenderer.invoke(CHANNELS.previewCampaignAudience, campaignId),
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
  searchCustomers: (query) => ipcRenderer.invoke(CHANNELS.searchCustomers, query),
  getCustomer: (customerId) => ipcRenderer.invoke(CHANNELS.getCustomer, customerId),
  correctCustomer: (customerId, changes) => ipcRenderer.invoke(CHANNELS.correctCustomer, customerId, changes),
  listChannels: () => ipcRenderer.invoke(CHANNELS.listChannels),
  getPreferences: (customerId) => ipcRenderer.invoke(CHANNELS.getPreferences, customerId),
  replacePreferredChannels: (customerId, channelIds) =>
    ipcRenderer.invoke(CHANNELS.replacePreferredChannels, customerId, channelIds),
  replaceInterestCategories: (customerId, categoryIds) =>
    ipcRenderer.invoke(CHANNELS.replaceInterestCategories, customerId, categoryIds),
  segmentVocabulary: () => ipcRenderer.invoke(CHANNELS.segmentVocabulary),
  currentAssignment: (customerId) => ipcRenderer.invoke(CHANNELS.currentAssignment, customerId),
  listAssignments: (query) => ipcRenderer.invoke(CHANNELS.listAssignments, query),
  getRecommendations: (customerId) => ipcRenderer.invoke(CHANNELS.getRecommendations, customerId),
  kpiDefinitions: () => ipcRenderer.invoke(CHANNELS.kpiDefinitions),
  kpiReport: (query) => ipcRenderer.invoke(CHANNELS.kpiReport, query),
  exportReport: (format) => ipcRenderer.invoke(CHANNELS.exportReport, format),
  inspectRecommendation: (query) => ipcRenderer.invoke(CHANNELS.inspectRecommendation, query),
  listRecommendationDecisions: (query) => ipcRenderer.invoke(CHANNELS.listRecommendationDecisions, query),
  openRecommendationDecision: (decisionId) =>
    ipcRenderer.invoke(CHANNELS.openRecommendationDecision, decisionId),
  listDriftReports: (query) => ipcRenderer.invoke(CHANNELS.listDriftReports, query),
  getDriftReport: (reportId) => ipcRenderer.invoke(CHANNELS.getDriftReport, reportId),
  listDriftAlerts: (query) => ipcRenderer.invoke(CHANNELS.listDriftAlerts, query),
  getDriftAlert: (alertId) => ipcRenderer.invoke(CHANNELS.getDriftAlert, alertId),
  migrationMatrix: (query) => ipcRenderer.invoke(CHANNELS.migrationMatrix, query),
  listMigrations: (query) => ipcRenderer.invoke(CHANNELS.listMigrations, query),
  explainMigration: (query) => ipcRenderer.invoke(CHANNELS.explainMigration, query),
  listExperiments: (query) => ipcRenderer.invoke(CHANNELS.listExperiments, query),
  getExperiment: (experimentId) => ipcRenderer.invoke(CHANNELS.getExperiment, experimentId),
  planExperiment: (draft) => ipcRenderer.invoke(CHANNELS.planExperiment, draft),
  assignExperiment: (experimentId, acknowledgeUnderpowered) =>
    ipcRenderer.invoke(CHANNELS.assignExperiment, experimentId, acknowledgeUnderpowered),
  getExperimentResult: (experimentId, basis) =>
    ipcRenderer.invoke(CHANNELS.getExperimentResult, experimentId, basis),
  listModelVersions: (query) => ipcRenderer.invoke(CHANNELS.listModelVersions, query),
  getModelVersion: (versionId) => ipcRenderer.invoke(CHANNELS.getModelVersion, versionId),
  listTrainings: (query) => ipcRenderer.invoke(CHANNELS.listTrainings, query),
  getTraining: (trainingId) => ipcRenderer.invoke(CHANNELS.getTraining, trainingId),
  startTraining: (draft) => ipcRenderer.invoke(CHANNELS.startTraining, draft),
  publishModelVersion: (versionId, reason) =>
    ipcRenderer.invoke(CHANNELS.publishModelVersion, versionId, reason),
};

contextBridge.exposeInMainWorld('mosaiq', bridge);
