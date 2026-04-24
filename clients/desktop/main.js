const { app, Tray, Menu, nativeImage, BrowserWindow, screen, ipcMain } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const http = require("http");

// Use venv python so listener/speaker have their dependencies
const PYTHON = path.join(__dirname, ".venv", "bin", "python3");

// Load config
const configPath = path.join(__dirname, "config.json");
const config = JSON.parse(fs.readFileSync(configPath, "utf-8"));

const SERVER = config.server;

let tray = null;
let listener = null;
let speaker = null;
let muted = false;
let online = false;
let processing = false;

// --- Panel state ---
let panelWindow = null;
let panelOpen = false;
let overlayWindow = null;


// --- Display windows ---

const displayWindows = new Map(); // title -> BrowserWindow

function createDisplayWindow(windowSpec, bounds) {
  const preloadPath = path.join(__dirname, "preload.js");

  const win = new BrowserWindow({
    width: bounds.width,
    height: bounds.height,
    x: bounds.x,
    y: bounds.y,
    frame: false,
    titleBarStyle: "hidden",
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    focusable: false,
    resizable: true,
    movable: true,
    hasShadow: true,
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Show on all macOS Spaces
  win.setVisibleOnAllWorkspaces(true, { skipTransformProcessType: true, visibleOnFullScreen: true });

  // Store content for this window so preload can fetch it
  win._mocaContent = {
    title: windowSpec.title || "",
    content: windowSpec.content || "",
  };

  win._mocaTitle = windowSpec.title || "";
  displayWindows.set(win._mocaTitle, win);

  win.on("closed", () => {
    displayWindows.delete(win._mocaTitle);
  });

  // Make focusable when user clicks
  win.on("focus", () => {
    win.setFocusable(true);
  });

  win.on("blur", () => {
    win.setFocusable(false);
  });

  win.loadFile("display.html");
  log("DISPLAY", `Opened: ${win._mocaTitle}`);
}

function showDisplay(display) {
  // Close existing display windows first
  closeAllDisplayWindows();

  const layout = display.layout || "single";
  const windows = display.windows || [];

  if (windows.length === 0) return;

  const primaryDisplay = screen.getPrimaryDisplay();
  const { width: screenW, height: screenH } = primaryDisplay.workAreaSize;
  const { x: workX, y: workY } = primaryDisplay.workArea;

  const maxWidth = panelOpen ? screenW - 400 : screenW;
  const positions = computeLayout(layout, windows, maxWidth, screenH, workX, workY);

  for (let i = 0; i < windows.length; i++) {
    createDisplayWindow(windows[i], positions[i]);
  }
}

function computeLayout(layout, windows, screenW, screenH, workX, workY) {
  const defaultW = 900;
  const defaultH = 600;
  const gap = 20;

  switch (layout) {
    case "side_by_side": {
      const w = Math.min(Math.floor((screenW - gap * 3) / 2), defaultW);
      const h = Math.min(defaultH, screenH - gap * 2);
      const y = workY + Math.floor((screenH - h) / 2);
      return windows.map((_, i) => ({
        width: w,
        height: h,
        x: workX + gap + i * (w + gap),
        y,
      }));
    }

    case "grid": {
      const cols = 2;
      const rows = Math.ceil(windows.length / cols);
      const w = Math.floor((screenW - gap * 3) / cols);
      const h = Math.floor((screenH - gap * (rows + 1)) / rows);
      return windows.map((_, i) => ({
        width: w,
        height: h,
        x: workX + gap + (i % cols) * (w + gap),
        y: workY + gap + Math.floor(i / cols) * (h + gap),
      }));
    }

    case "stack": {
      const offset = 30;
      const w = Math.min(defaultW, screenW - gap * 2 - offset * windows.length);
      const h = Math.min(defaultH, screenH - gap * 2 - offset * windows.length);
      const baseX = workX + Math.floor((screenW - w) / 2) - Math.floor(offset * (windows.length - 1) / 2);
      const baseY = workY + Math.floor((screenH - h) / 2) - Math.floor(offset * (windows.length - 1) / 2);
      return windows.map((_, i) => ({
        width: w,
        height: h,
        x: baseX + i * offset,
        y: baseY + i * offset,
      }));
    }

    case "collage": {
      return windows.map((win) => ({
        width: win.width || defaultW,
        height: win.height || defaultH,
        x: workX + (win.x || 0),
        y: workY + (win.y || 0),
      }));
    }

    case "single":
    default: {
      const w = Math.min(defaultW, screenW - gap * 2);
      const h = Math.min(defaultH, screenH - gap * 2);
      return windows.map(() => ({
        width: w,
        height: h,
        x: workX + Math.floor((screenW - w) / 2),
        y: workY + Math.floor((screenH - h) / 2),
      }));
    }
  }
}

function repositionDisplayWindows() {
  if (displayWindows.size === 0) return;

  const primaryDisplay = screen.getPrimaryDisplay();
  const { width: screenW, height: screenH } = primaryDisplay.workAreaSize;
  const { x: workX, y: workY } = primaryDisplay.workArea;
  const maxWidth = panelOpen ? screenW - 400 : screenW;

  // Recalculate positions — use single layout as fallback
  const windowList = Array.from(displayWindows.values()).filter(w => !w.isDestroyed());
  if (windowList.length === 0) return;

  // Simple recentre within available space
  const gap = 20;
  for (const win of windowList) {
    const bounds = win.getBounds();
    // Clamp x so window stays within maxWidth
    let newX = bounds.x;
    if (bounds.x + bounds.width > workX + maxWidth) {
      newX = workX + maxWidth - bounds.width - gap;
      if (newX < workX + gap) newX = workX + gap;
    }
    if (newX !== bounds.x) {
      win.setBounds({ x: newX, y: bounds.y, width: bounds.width, height: bounds.height }, true);
    }
  }
}

function closeAllDisplayWindows() {
  for (const [title, win] of displayWindows) {
    if (!win.isDestroyed()) {
      win.close();
    }
  }
  displayWindows.clear();
  log("DISPLAY", "All windows closed");
}

function closeDisplayByTitle(title) {
  const win = displayWindows.get(title);
  if (win && !win.isDestroyed()) {
    win.close();
    log("DISPLAY", `Closed: ${title}`);
  }
}

// IPC handlers for display windows
ipcMain.on("display-close", (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (win) {
    const title = win._mocaTitle;
    if (!win.isDestroyed()) win.close();
    log("DISPLAY", `User closed: ${title}`);
  }
});

ipcMain.handle("display-get-content", (event) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  return win ? win._mocaContent : null;
});

// --- Panel window ---

function getActiveDisplay() {
  const cursor = screen.getCursorScreenPoint();
  return screen.getDisplayNearestPoint(cursor);
}

// Return the usable screen area (respects menu bar and dock)
function getDisplayBounds() {
  const display = getActiveDisplay();
  return display.workArea;
}

function createPanelWindow() {
  const { x: bx, y: by, width: bw, height: bh } = getDisplayBounds();

  const panelPreloadPath = path.join(__dirname, "panel-preload.js");

  const win = new BrowserWindow({
    width: 400,
    height: bh,
    x: bx + bw - 400,
    y: by,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    focusable: true,
    resizable: false,
    movable: false,
    hasShadow: false,
    show: false,
    webPreferences: {
      preload: panelPreloadPath,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Show on all macOS Spaces
  win.setVisibleOnAllWorkspaces(true, { skipTransformProcessType: true, visibleOnFullScreen: true });

  win.loadFile("panel.html");

  win.on("closed", () => {
    if (panelWindow === win) {
      panelWindow = null;
      panelOpen = false;
    }
  });

  // Resize to fit active display when panel gains focus (e.g. switching monitors)
  win.on("focus", () => {
    if (panelOpen) resizePanelToScreen();
  });

  log("PANEL", "Window created");
  return win;
}

function createOverlay() {
  destroyOverlay();

  const { x, y, width, height } = getDisplayBounds();

  overlayWindow = new BrowserWindow({
    width,
    height,
    x,
    y,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    focusable: true,
    resizable: false,
    movable: false,
    hasShadow: false,
  });

  // Show on all macOS Spaces
  overlayWindow.setVisibleOnAllWorkspaces(true, { skipTransformProcessType: true, visibleOnFullScreen: true });

  // Near-invisible overlay — rgba(0,0,0,0.01) so clicks register (fully transparent = click-through)
  overlayWindow.loadURL("data:text/html,<html><body style='margin:0;background:rgba(0,0,0,0.01);width:100vw;height:100vh'></body></html>");

  overlayWindow.on("closed", () => {
    overlayWindow = null;
  });

  // Click on overlay → it gains focus → close panel
  overlayWindow.on("focus", () => {
    if (panelOpen) slideOutPanel();
  });
}

function destroyOverlay() {
  if (overlayWindow && !overlayWindow.isDestroyed()) {
    overlayWindow.destroy();
  }
  overlayWindow = null;
}

function destroyPanel() {
  if (panelWindow && !panelWindow.isDestroyed()) {
    panelWindow.destroy();
  }
  panelWindow = null;
}

function togglePanel() {
  if (panelOpen) {
    slideOutPanel();
  } else {
    slideInPanel();
  }
}

function slideInPanel() {
  // Destroy old panel — fresh window always appears on current Space (BUG 3 fix)
  destroyPanel();
  destroyOverlay();

  // Create fresh panel on current Space
  panelWindow = createPanelWindow();

  // Create overlay behind panel to catch outside clicks (BUG 2 — replaces blur)
  createOverlay();

  // Show panel on top, focus it
  panelWindow.show();
  panelWindow.focus();
  panelOpen = true;

  // Tell renderer to animate in
  panelWindow.webContents.send("panel-slide-in");
  panelWindow.webContents.send("panel-server-status", online);

  repositionDisplayWindows();
  log("PANEL", "Opened");
}

function resizePanelToScreen() {
  const { x: bx, y: by, width: bw, height: bh } = getDisplayBounds();

  if (panelWindow && !panelWindow.isDestroyed()) {
    panelWindow.setBounds({ x: bx + bw - 400, y: by, width: 400, height: bh }, true);
  }

  if (overlayWindow && !overlayWindow.isDestroyed()) {
    overlayWindow.setBounds({ x: bx, y: by, width: bw, height: bh }, true);
  }
}

function slideOutPanel() {
  if (!panelWindow || panelWindow.isDestroyed()) return;

  panelOpen = false;
  destroyOverlay();

  // Tell renderer to animate out, then destroy
  panelWindow.webContents.send("panel-slide-out");
  const win = panelWindow;
  setTimeout(() => {
    if (win && !win.isDestroyed()) {
      win.destroy();
    }
    if (panelWindow === win) panelWindow = null;
    repositionDisplayWindows();

    // Return focus to the previously active app (macOS dock is hidden)
    if (displayWindows.size === 0 && app.hide) {
      app.hide();
    }
  }, 250);

  log("PANEL", "Closed");
}

// --- Panel IPC handlers ---

ipcMain.on("panel-ready", (event) => {
  // Panel loaded — trigger slide in + send status
  if (panelWindow && !panelWindow.isDestroyed()) {
    panelWindow.webContents.send("panel-slide-in");
    panelWindow.webContents.send("panel-server-status", online);
  }
});

ipcMain.on("panel-close", () => {
  slideOutPanel();
});

ipcMain.handle("panel-chat", async (_event, message) => {
  log("PANEL-CHAT", message);
  try {
    const response = await httpRequest("POST", `${SERVER}/chat`, {
      message,
      device: "mac_panel",
    });

    if (response) {
      // Handle display payload from chat
      if (response.display && response.display.windows) {
        showDisplay(response.display);
        // Also send display info to panel
        if (panelWindow && !panelWindow.isDestroyed()) {
          panelWindow.webContents.send("panel-display-update", {
            reply: null, // reply already returned to chat
            hasDisplay: true,
          });
        }
      }

      // Handle close_display action
      if (response.action === "close_display") {
        if (response.target) {
          closeDisplayByTitle(response.target);
        } else {
          closeAllDisplayWindows();
        }
      }

      return { reply: response.reply || "" };
    }
    return { reply: "No response from server." };
  } catch (err) {
    log("ERROR", `Panel chat error: ${err.message}`);
    return { reply: "Connection error." };
  }
});

ipcMain.handle("panel-get-status", async () => {
  try {
    return await httpRequest("GET", `${SERVER}/status`);
  } catch {
    return null;
  }
});

// --- HTTP helpers (no external deps) ---

function httpRequest(method, urlStr, body) {
  return new Promise((resolve, reject) => {
    const url = new URL(urlStr);
    const options = {
      hostname: url.hostname,
      port: url.port,
      path: url.pathname,
      method,
      headers: {},
      timeout: 15000,
    };

    if (body) {
      const data = JSON.stringify(body);
      options.headers["Content-Type"] = "application/json";
      options.headers["Content-Length"] = Buffer.byteLength(data);
    }

    const req = http.request(options, (res) => {
      let chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => {
        try {
          resolve(JSON.parse(Buffer.concat(chunks).toString()));
        } catch {
          resolve(null);
        }
      });
    });

    req.on("error", reject);
    req.on("timeout", () => {
      req.destroy();
      reject(new Error("timeout"));
    });

    if (body) req.write(JSON.stringify(body));
    req.end();
  });
}

// --- Tray ---

function loadTrayIcon(filename) {
  const iconPath = path.join(__dirname, "assets", filename);
  const icon = nativeImage.createFromPath(iconPath).resize({ width: 18, height: 18 });
  icon.setTemplateImage(true);
  return icon;
}

function createTray() {
  const icon = loadTrayIcon("icon.png");
  tray = new Tray(icon);
  tray.setToolTip("MOCA");

  // Left click toggles panel
  tray.on("click", () => {
    togglePanel();
  });

  // Right click shows context menu
  tray.on("right-click", () => {
    tray.popUpContextMenu(buildTrayMenu());
  });

  updateTrayMenu();
}

function createStatusDot(color) {
  // Create a 12x12 colored circle as a native image for menu item icon
  const size = 12;
  const canvas = Buffer.alloc(size * size * 4); // RGBA
  const cx = size / 2, cy = size / 2, r = size / 2 - 1;

  const colors = {
    green: [52, 199, 89],    // macOS system green
    red: [255, 59, 48],      // macOS system red
  };
  const [cr, cg, cb] = colors[color] || colors.red;

  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dist = Math.sqrt((x - cx) ** 2 + (y - cy) ** 2);
      const idx = (y * size + x) * 4;
      if (dist <= r) {
        canvas[idx] = cr;
        canvas[idx + 1] = cg;
        canvas[idx + 2] = cb;
        canvas[idx + 3] = 255;
      } else {
        canvas[idx + 3] = 0; // transparent
      }
    }
  }

  return nativeImage.createFromBuffer(canvas, { width: size, height: size });
}

function buildTrayMenu() {
  return Menu.buildFromTemplate([
    { label: "MOCA v1.0", enabled: false },
    { type: "separator" },
    {
      label: muted ? "Unmute" : "Mute",
      click: () => {
        muted = !muted;
        if (listener && listener.stdin.writable) {
          listener.stdin.write(muted ? "PAUSE\n" : "RESUME\n");
        }
        updateTrayMenu();
        log("TRAY", muted ? "Muted" : "Unmuted");
      },
    },
    {
      label: "Close Displays",
      click: () => {
        closeAllDisplayWindows();
      },
    },
    { type: "separator" },
    {
      label: "Quit",
      click: () => {
        cleanup();
        app.quit();
      },
    },
  ]);
}

function updateTrayMenu() {
  const icon = loadTrayIcon(muted ? "icon-muted.png" : "icon.png");
  tray.setImage(icon);
}

// --- Logging ---

function log(tag, msg) {
  const ts = new Date().toLocaleTimeString("en-GB");
  console.log(`[${ts}] [${tag}] ${msg}`);
}

// --- Speaker ---

function spawnSpeaker() {
  speaker = spawn(PYTHON, [path.join(__dirname, "speaker.py")], {
    cwd: __dirname,
    stdio: ["pipe", "pipe", "pipe"],
  });

  speaker.stderr.on("data", (data) => {
    log("SPEAKER", data.toString().trim());
  });

  speaker.on("close", (code) => {
    log("SPEAKER", `Exited with code ${code}`);
    speaker = null;
  });
}

function speakAndWait(text) {
  return new Promise((resolve) => {
    if (!speaker || !speaker.stdin.writable) {
      spawnSpeaker();
    }

    const onData = (data) => {
      const line = data.toString().trim();
      if (line === "DONE") {
        speaker.stdout.removeListener("data", onData);
        resolve();
      }
    };

    speaker.stdout.on("data", onData);
    speaker.stdin.write(text + "\n");
  });
}

// --- Listener ---

function spawnListener() {
  listener = spawn(PYTHON, [path.join(__dirname, "listener.py")], {
    cwd: __dirname,
    stdio: ["pipe", "pipe", "pipe"],
  });

  let buffer = "";

  listener.stdout.on("data", (data) => {
    buffer += data.toString();
    const lines = buffer.split("\n");
    buffer = lines.pop(); // keep incomplete line

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      if (trimmed === "LISTENER_READY") {
        log("LISTENER", "Ready");
        continue;
      }

      handleUtterance(trimmed);
    }
  });

  listener.stderr.on("data", (data) => {
    log("LISTENER", data.toString().trim());
  });

  listener.on("close", (code) => {
    log("LISTENER", `Exited with code ${code}`);
    // Restart listener if it crashes
    if (!app.isQuitting) {
      log("LISTENER", "Restarting...");
      setTimeout(spawnListener, 2000);
    }
  });
}

async function handleUtterance(text) {
  if (muted || processing) return;

  processing = true;
  log("HEARD", text);

  // Pause listener while processing
  if (listener && listener.stdin.writable) {
    listener.stdin.write("PAUSE\n");
  }

  try {
    const response = await httpRequest("POST", `${SERVER}/chat`, {
      message: text,
      device: "mac_electron",
    });

    if (response) {
      // Handle display payload
      if (response.display && response.display.windows) {
        // Auto-open panel when display arrives via voice
        if (!panelOpen) slideInPanel();
        showDisplay(response.display);
      }

      // Handle close_display action
      if (response.action === "close_display") {
        if (response.target) {
          closeDisplayByTitle(response.target);
        } else {
          closeAllDisplayWindows();
        }
      }

      // Send voice conversation to panel chat
      if (panelWindow && !panelWindow.isDestroyed()) {
        panelWindow.webContents.send("panel-display-update", {
          userMessage: text,
          reply: response.reply || "",
        });
      }

      // Speak reply
      if (response.reply) {
        log("MOCA", response.reply);
        await speakAndWait(response.reply);
      }
    }
  } catch (err) {
    log("ERROR", `Server error: ${err.message}`);
    await speakAndWait("Having trouble reaching my brain Boss");
  }

  // Resume listener
  if (listener && listener.stdin.writable && !muted) {
    listener.stdin.write("RESUME\n");
  }

  processing = false;
}

// --- Health check ---

function startHealthCheck() {
  const check = async () => {
    try {
      const res = await httpRequest("GET", `${SERVER}/health`);
      const wasOnline = online;
      online = res && res.status === "online";
      if (online !== wasOnline) {
        log("HEALTH", online ? "Server online" : "Server offline");
        updateTrayMenu();
        // Send status to panel
        if (panelWindow && !panelWindow.isDestroyed()) {
          panelWindow.webContents.send("panel-server-status", online);
        }
      }
    } catch {
      if (online) {
        online = false;
        log("HEALTH", "Server offline");
        updateTrayMenu();
        if (panelWindow && !panelWindow.isDestroyed()) {
          panelWindow.webContents.send("panel-server-status", false);
        }
      }
    }
  };

  check();
  setInterval(check, 30000);
}

// --- Cleanup ---

function cleanup() {
  app.isQuitting = true;
  closeAllDisplayWindows();
  destroyOverlay();
  destroyPanel();
  if (listener) {
    listener.kill();
    listener = null;
  }
  if (speaker) {
    speaker.kill();
    speaker = null;
  }
}

// --- App lifecycle ---

app.on("ready", () => {
  // Hide from dock on Mac
  if (process.platform === "darwin" && app.dock) {
    app.dock.hide();
  }

  // Auto-start on login
  app.setLoginItemSettings({
    openAtLogin: true,
    openAsHidden: true,
  });

  createTray();
  spawnSpeaker();
  spawnListener();
  startHealthCheck();

  // Resize panel/overlay when display metrics change (e.g. switching Spaces, dock show/hide)
  screen.on("display-metrics-changed", (_event, _display, changedMetrics) => {
    log("DISPLAY", `Metrics changed: ${changedMetrics.join(", ")}`);
    if (panelOpen) {
      // Delay to let macOS finalize Space transition
      setTimeout(() => {
        const bounds = getDisplayBounds();
        log("DISPLAY", `Resizing panel to: x=${bounds.x} y=${bounds.y} w=${bounds.width} h=${bounds.height}`);
        resizePanelToScreen();
      }, 300);
    }
  });

  log("BOOT", "MOCA Desktop started");
  log("BOOT", `Server: ${SERVER}`);
});

app.on("window-all-closed", (e) => {
  e.preventDefault(); // Keep running — tray app
});

app.on("before-quit", cleanup);
