const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("mocaPanel", {
  sendChat: (message) => ipcRenderer.invoke("panel-chat", message),
  getStatus: () => ipcRenderer.invoke("panel-get-status"),
  closePanel: () => ipcRenderer.send("panel-close"),
  onServerStatus: (cb) => ipcRenderer.on("panel-server-status", (_e, status) => cb(status)),
  onDisplayUpdate: (cb) => ipcRenderer.on("panel-display-update", (_e, data) => cb(data)),
  onSlideIn: (cb) => ipcRenderer.on("panel-slide-in", () => cb()),
  onSlideOut: (cb) => ipcRenderer.on("panel-slide-out", () => cb()),
  ready: () => ipcRenderer.send("panel-ready"),
});
