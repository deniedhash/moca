const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("moca", {
  close: () => ipcRenderer.send("display-close"),
  getContent: () => ipcRenderer.invoke("display-get-content"),
});
