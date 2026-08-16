// Антидетект — полный набор патчей (nodriver-совместимый)
try {
  // 1. WebDriver
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

  // 2. Plugins — реальные имена
  Object.defineProperty(navigator, 'plugins', {
    get: () => [
      { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
      { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
      { name: 'Native Client', filename: 'internal-nacl-plugin' },
    ]
  });

  // 3. Языки
  Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US', 'en'] });

  // 4. Hardware
  Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
  Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

  // 5. Chrome runtime
  window.chrome = {
    runtime: { connect: () => ({}) },
    loadTimes: () => ({}),
    csi: () => ({}),
    app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } }
  };

  // 6. Permissions
  const originalQuery = window.navigator.permissions.query;
  window.navigator.permissions.query = (p) => (
    p.name === 'notifications' ? Promise.resolve({ state: Notification.permission }) :
    originalQuery(p)
  );

  // 7. WebGL vendor
  const getParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function(p) {
    if (p === 37445) return 'Intel Open Source Technology Center';
    if (p === 37446) return 'Mesa DRI Intel(R) HD Graphics (KBL GT2)';
    return getParameter.call(this, p);
  };

  // 8. Screen
  Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
  Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });
  Object.defineProperty(screen, 'availWidth', { get: () => window.innerWidth });
  Object.defineProperty(screen, 'availHeight', { get: () => window.innerHeight });

  // 9. Connection
  if (navigator.connection) {
    Object.defineProperty(navigator.connection, 'rtt', { get: () => 50 });
    Object.defineProperty(navigator.connection, 'downlink', { get: () => 10 });
    Object.defineProperty(navigator.connection, 'effectiveType', { get: () => '4g' });
  }

  // 10. Platform
  Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });

  // 11. Media devices
  if (navigator.mediaDevices) {
    navigator.mediaDevices.enumerateDevices = () => Promise.resolve([
      { deviceId: '', kind: 'audioinput', label: '', groupId: '' },
      { deviceId: '', kind: 'audiooutput', label: '', groupId: '' },
      { deviceId: '', kind: 'videoinput', label: '', groupId: '' },
    ]);
  }

  // 12. Battery
  if (navigator.getBattery) {
    navigator.getBattery = () => Promise.resolve({
      charging: true, chargingTime: 0, dischargingTime: Infinity, level: 1
    });
  }
} catch(e) { /* silently fail */ }
