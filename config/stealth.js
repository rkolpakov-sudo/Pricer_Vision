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

// === ДОПОЛНИТЕЛЬНЫЕ ПАТЧИ (13-17) ===
try {
  // 13. Canvas Fingerprint Randomization
  const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
  const originalToBlob = HTMLCanvasElement.prototype.toBlob;
  const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;

  function addNoise(canvas) {
    try {
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      const imageData = originalGetImageData.call(ctx, 0, 0, canvas.width, canvas.height);
      const data = imageData.data;
      for (let i = 0; i < data.length; i += 4) {
        data[i] = Math.max(0, Math.min(255, data[i] + (Math.random() * 6 - 3)));
        data[i+1] = Math.max(0, Math.min(255, data[i+1] + (Math.random() * 6 - 3)));
        data[i+2] = Math.max(0, Math.min(255, data[i+2] + (Math.random() * 6 - 3)));
      }
      ctx.putImageData(imageData, 0, 0);
    } catch(e) {}
  }

  HTMLCanvasElement.prototype.toDataURL = function(type) {
    addNoise(this);
    return originalToDataURL.call(this, type);
  };

  HTMLCanvasElement.prototype.toBlob = function(callback, type, quality) {
    addNoise(this);
    return originalToBlob.call(this, callback, type, quality);
  };

  // 14. AudioContext Fingerprint — нейтрализуем анализ частотной характеристики
  const OriginalAudioContext = window.AudioContext || window.webkitAudioContext;
  if (OriginalAudioContext) {
    const originalCreateAnalyser = OriginalAudioContext.prototype.createAnalyser;
    OriginalAudioContext.prototype.createAnalyser = function() {
      const analyser = originalCreateAnalyser.call(this);
      const origGet = analyser.getFloatFrequencyData;
      analyser.getFloatFrequencyData = function(array) {
        origGet.call(this, array);
        for (let i = 0; i < array.length; i++) {
          array[i] = array[i] + (Math.random() * 2 - 1);
        }
      };
      return analyser;
    };
  }

  // 15. WebRTC Leak Prevention — подменяем локальные кандидаты
  if (window.RTCPeerConnection) {
    const OriginalRTC = window.RTCPeerConnection;
    window.RTCPeerConnection = function(...args) {
      const pc = new OriginalRTC(...args);
      const origAddIce = pc.addIceCandidate;
      pc.addIceCandidate = function(candidate) {
        if (candidate && candidate.candidate &&
            (candidate.candidate.includes('srflx') ||
             candidate.candidate.includes('host'))) {
          return Promise.resolve();
        }
        return origAddIce.apply(this, arguments);
      };
      return pc;
    };
    window.RTCPeerConnection.prototype = OriginalRTC.prototype;
  }

  // 16. Font Enumeration Protection — ограничиваем доступный набор шрифтов
  const originalFontsCheck = document.fonts ? document.fonts.check.bind(document.fonts) : null;
  if (originalFontsCheck) {
    document.fonts.check = function(font, text) {
      const allowedFonts = ['Arial', 'Times New Roman', 'Courier New',
                            'Verdana', 'Helvetica', 'Georgia'];
      const fontFamily = String(font).split(' ')[0].replace(/['"]/g, '');
      if (!allowedFonts.includes(fontFamily)) {
        return false;
      }
      return originalFontsCheck(font, text);
    };
  }

  // 17. WebGL Vendor/Renderer Masking
  const glGetParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 0x9245) return 'Google Inc. (Intel)';
    if (parameter === 0x9246) return 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0)';
    return glGetParameter.call(this, parameter);
  };
} catch(e) { /* silently fail */ }
