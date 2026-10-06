const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('kanbanDesktop', Object.freeze({
  selectRepository: () => ipcRenderer.invoke('select-repository'),
}));
