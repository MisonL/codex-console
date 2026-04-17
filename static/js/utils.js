/**
 * 通用工具库
 * 提供通知、主题、加载、API、Modal 与通用格式化能力。
 */

class ToastManager {
  constructor() {
    this.container = null;
    this.timers = new WeakMap();
    this.init();
  }

  init() {
    if (this.container) return;
    this.container = document.createElement("div");
    this.container.className = "toast-container";
    this.container.setAttribute("aria-live", "polite");
    this.container.setAttribute("aria-atomic", "false");
    document.body.appendChild(this.container);
  }

  buildToast(message, type) {
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.setAttribute("role", type === "error" ? "alert" : "status");
    toast.innerHTML = `
            <span class="toast-accent" aria-hidden="true"></span>
            <div class="toast-content">
                <div class="toast-title">${this.getTitle(type)}</div>
                <div class="toast-message"></div>
            </div>
            <button class="toast-close" type="button" aria-label="关闭通知">x</button>
        `;
    toast.querySelector(".toast-message").textContent = String(message || "");
    toast
      .querySelector(".toast-close")
      ?.addEventListener("click", () => this.dismiss(toast));
    return toast;
  }

  getTitle(type) {
    const titleMap = {
      success: "成功",
      error: "失败",
      warning: "提示",
      info: "通知",
    };
    return titleMap[type] || titleMap.info;
  }

  dismiss(toast) {
    if (!toast) return;
    const timer = this.timers.get(toast);
    if (timer) {
      clearTimeout(timer);
      this.timers.delete(toast);
    }
    toast.classList.add("is-leaving");
    window.setTimeout(() => toast.remove(), 180);
  }

  show(message, type = "info", duration = 3200) {
    if (!message) return null;
    this.init();
    const toast = this.buildToast(message, type);
    this.container.appendChild(toast);
    if (duration > 0) {
      const timer = window.setTimeout(() => this.dismiss(toast), duration);
      this.timers.set(toast, timer);
    }
    return toast;
  }

  success(message, duration) {
    return this.show(message, "success", duration);
  }

  error(message, duration) {
    return this.show(message, "error", duration);
  }

  warning(message, duration) {
    return this.show(message, "warning", duration);
  }

  info(message, duration) {
    return this.show(message, "info", duration);
  }
}

const toast = new ToastManager();

class ThemeManager {
  constructor() {
    this.theme = this.loadTheme();
    this.applyTheme();
  }

  loadTheme() {
    return localStorage.getItem("theme") || "light";
  }

  saveTheme(theme) {
    localStorage.setItem("theme", theme);
  }

  applyTheme() {
    document.documentElement.setAttribute("data-theme", this.theme);
    this.updateToggleButtons();
  }

  toggle() {
    this.theme = this.theme === "light" ? "dark" : "light";
    this.saveTheme(this.theme);
    this.applyTheme();
  }

  setTheme(theme) {
    if (!theme) return;
    this.theme = theme;
    this.saveTheme(theme);
    this.applyTheme();
  }

  updateToggleButtons() {
    const buttons = document.querySelectorAll(".theme-toggle");
    buttons.forEach((button) => {
      const nextTheme = this.theme === "light" ? "dark" : "light";
      button.textContent = nextTheme === "dark" ? "Dark" : "Light";
      button.title = nextTheme === "dark" ? "切换到暗色模式" : "切换到亮色模式";
      button.setAttribute("aria-label", button.title);
    });
  }
}

const theme = new ThemeManager();

class LoadingManager {
  constructor() {
    this.activeLoaders = new Map();
    this.overlay = null;
    this.networkBar = null;
    this.networkHideTimer = null;
    this.networkActive = 0;
  }

  resolveElement(element) {
    if (typeof element === "string") {
      return document.getElementById(element);
    }
    return element || null;
  }

  ensureOverlay() {
    if (this.overlay) return this.overlay;
    this.overlay = document.createElement("div");
    this.overlay.className = "global-loading-overlay";
    this.overlay.innerHTML = `
            <div class="global-loading-card" role="status" aria-live="polite">
                <span class="loading-spinner" aria-hidden="true"></span>
                <span class="global-loading-text">处理中...</span>
            </div>
        `;
    document.body.appendChild(this.overlay);
    return this.overlay;
  }

  ensureNetworkBar() {
    if (this.networkBar) return this.networkBar;
    this.networkBar = document.createElement("div");
    this.networkBar.className = "network-activity-bar";
    this.networkBar.setAttribute("aria-hidden", "true");
    document.body.appendChild(this.networkBar);
    return this.networkBar;
  }

  createButtonContent(label) {
    const text = String(label || "处理中...");
    return `<span class="loading-spinner" aria-hidden="true"></span><span class="btn-loading-label">${text}</span>`;
  }

  show(element, text = "处理中...") {
    const target = this.resolveElement(element);
    if (!target || this.activeLoaders.has(target)) return;

    const state = {
      html: target.innerHTML,
      disabled: target.disabled,
      minWidth: target.style.minWidth || "",
    };

    if (
      target instanceof HTMLButtonElement ||
      target.classList.contains("btn")
    ) {
      const width = Math.ceil(target.getBoundingClientRect().width);
      if (width > 0) {
        target.style.minWidth = `${width}px`;
      }
      target.innerHTML = this.createButtonContent(text);
    } else if (
      target instanceof HTMLInputElement &&
      /submit|button/i.test(target.type || "")
    ) {
      state.value = target.value;
      target.value = String(text || "处理中...");
    }

    target.classList.add("is-loading");
    target.setAttribute("aria-busy", "true");
    target.disabled = true;
    this.activeLoaders.set(target, state);
  }

  hide(element) {
    const target = this.resolveElement(element);
    if (!target) return;
    const state = this.activeLoaders.get(target);
    if (!state) return;

    if (Object.prototype.hasOwnProperty.call(state, "html")) {
      target.innerHTML = state.html;
    }
    if (Object.prototype.hasOwnProperty.call(state, "value")) {
      target.value = state.value;
    }
    target.disabled = Boolean(state.disabled);
    target.classList.remove("is-loading");
    target.removeAttribute("aria-busy");
    target.style.minWidth = state.minWidth || "";
    this.activeLoaders.delete(target);
  }

  hideAll() {
    Array.from(this.activeLoaders.keys()).forEach((element) =>
      this.hide(element),
    );
  }

  async withButton(element, task, text = "处理中...") {
    const target = this.resolveElement(element);
    if (target) this.show(target, text);
    try {
      return await task;
    } finally {
      if (target) this.hide(target);
    }
  }

  showOverlay(message = "处理中...") {
    const overlay = this.ensureOverlay();
    overlay.querySelector(".global-loading-text").textContent = String(
      message || "处理中...",
    );
    overlay.classList.add("active");
    document.body.classList.add("loading-overlay-active");
  }

  hideOverlay() {
    if (!this.overlay) return;
    this.overlay.classList.remove("active");
    document.body.classList.remove("loading-overlay-active");
  }

  setNetworkBusy(activeCount) {
    const bar = this.ensureNetworkBar();
    this.networkActive = Math.max(0, Number(activeCount || 0));
    if (this.networkHideTimer) {
      clearTimeout(this.networkHideTimer);
      this.networkHideTimer = null;
    }

    if (this.networkActive > 0) {
      bar.classList.add("active");
      return;
    }

    this.networkHideTimer = window.setTimeout(() => {
      bar.classList.remove("active");
      this.networkHideTimer = null;
    }, 180);
  }
}

const loading = new LoadingManager();

class ApiClient {
  constructor(baseUrl = "/api") {
    this.baseUrl = baseUrl;
    this.maxConcurrentRequests = 6;
    this.activeRequestCount = 0;
    this.totalRequestCount = 0;
    this.queues = {
      high: [],
      normal: [],
      low: [],
    };
    this.inflightRequests = new Map();
    this.coalescedRequests = new Map();
    this.networkOnline =
      typeof navigator === "undefined" ? true : navigator.onLine !== false;
    this._networkToastState = { type: "", at: 0 };
    this.defaultTimeoutMs = 20000;
    this.defaultRetryCount = 1;
    this.defaultRetryDelayMs = 900;
    this.setupNetworkListeners();
  }

  setupNetworkListeners() {
    if (typeof window === "undefined" || !window.addEventListener) return;
    window.addEventListener("online", () => {
      this.networkOnline = true;
      this.notifyNetworkState("网络已恢复", "success", 1800);
    });
    window.addEventListener("offline", () => {
      this.networkOnline = false;
      this.notifyNetworkState("网络已断开，后台请求已降频", "warning", 4000);
    });
  }

  getAdaptiveTimeoutMs() {
    const connection =
      navigator?.connection ||
      navigator?.mozConnection ||
      navigator?.webkitConnection;
    const effectiveType = String(connection?.effectiveType || "").toLowerCase();
    if (effectiveType === "slow-2g" || effectiveType === "2g") return 45000;
    if (effectiveType === "3g") return 30000;
    return this.defaultTimeoutMs;
  }

  notifyNetworkState(message, type, throttleMs = 3000) {
    const now = Date.now();
    if (
      this._networkToastState.type === type &&
      now - Number(this._networkToastState.at || 0) < throttleMs
    ) {
      return;
    }
    this._networkToastState = { type, at: now };
    if (type === "success") {
      toast.success(message, 1800);
      return;
    }
    if (type === "warning") {
      toast.warning(message, 2600);
      return;
    }
    toast.info(message, 2200);
  }

  sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  pickQueue(priority) {
    const normalized = String(priority || "normal").toLowerCase();
    if (normalized === "high") return this.queues.high;
    if (normalized === "low") return this.queues.low;
    return this.queues.normal;
  }

  dequeueTask() {
    return (
      this.queues.high.shift() ||
      this.queues.normal.shift() ||
      this.queues.low.shift() ||
      null
    );
  }

  runNext() {
    while (this.activeRequestCount < this.maxConcurrentRequests) {
      const next = this.dequeueTask();
      if (!next) break;
      this.activeRequestCount += 1;
      this.totalRequestCount += 1;
      loading.setNetworkBusy(this.activeRequestCount);
      next()
        .catch(() => {})
        .finally(() => {
          this.activeRequestCount = Math.max(0, this.activeRequestCount - 1);
          loading.setNetworkBusy(this.activeRequestCount);
          this.runNext();
        });
    }
  }

  enqueue(task, priority = "normal") {
    return new Promise((resolve, reject) => {
      const execute = async () => {
        try {
          resolve(await task());
        } catch (error) {
          reject(error);
        }
      };
      const queue = this.pickQueue(priority);
      queue.push(execute);
      this.runNext();
    });
  }

  cleanupInflightRequest(requestKey, controller) {
    if (!requestKey) return;
    const current = this.inflightRequests.get(requestKey);
    if (current?.controller === controller) {
      this.inflightRequests.delete(requestKey);
    }
  }

  consumeResponse(response) {
    if (response.status === 204) return Promise.resolve(null);
    const contentType = String(
      response.headers.get("content-type") || "",
    ).toLowerCase();
    if (contentType.includes("application/json")) {
      return response.json().catch(() => ({}));
    }
    if (contentType.startsWith("text/")) {
      return response.text().catch(() => "");
    }
    return response.blob();
  }

  async request(path, options = {}) {
    const url = path.startsWith("http") ? path : `${this.baseUrl}${path}`;
    const {
      timeoutMs,
      retry,
      retryDelayMs,
      requestKey,
      cancelPrevious,
      priority,
      silentNetworkError,
      silentTimeoutError,
      coalesce,
      signal: externalSignal,
      ...rawFetchOptions
    } = options;

    const normalizedPriority =
      String(priority || "normal").toLowerCase() || "normal";
    const effectiveTimeoutMs =
      Number(timeoutMs) > 0 ? Number(timeoutMs) : this.getAdaptiveTimeoutMs();
    const retryCount = Number.isInteger(retry) ? retry : this.defaultRetryCount;
    const retryWaitMs =
      Number(retryDelayMs) > 0
        ? Number(retryDelayMs)
        : this.defaultRetryDelayMs;
    const allowSilentNetworkError = Boolean(silentNetworkError);
    const allowSilentTimeoutError = Boolean(silentTimeoutError);
    const shouldCoalesce = Boolean(
      coalesce ?? (rawFetchOptions.method || "GET").toUpperCase() === "GET",
    );

    const defaultOptions = {
      headers: {
        "Content-Type": "application/json",
      },
    };
    const finalOptions = { ...defaultOptions, ...rawFetchOptions };
    finalOptions.headers = {
      ...(defaultOptions.headers || {}),
      ...(rawFetchOptions.headers || {}),
    };

    if (
      finalOptions.body &&
      typeof finalOptions.body === "object" &&
      !(finalOptions.body instanceof FormData)
    ) {
      finalOptions.body = JSON.stringify(finalOptions.body);
    }
    if (finalOptions.body instanceof FormData) {
      delete finalOptions.headers["Content-Type"];
    }

    if (requestKey && shouldCoalesce && !cancelPrevious) {
      const existingPromise = this.coalescedRequests.get(requestKey);
      if (existingPromise) {
        return existingPromise;
      }
    }

    const runner = this.enqueue(async () => {
      for (let attempt = 0; attempt <= retryCount; attempt += 1) {
        let timedOut = false;
        let timeoutId = null;
        const controller = new AbortController();

        if (requestKey && cancelPrevious) {
          const previous = this.inflightRequests.get(requestKey);
          if (previous?.controller) {
            previous.controller.__cancelReason = "request_replaced";
            previous.controller.abort();
          }
        }

        if (requestKey) {
          this.inflightRequests.set(requestKey, { controller });
        }

        if (externalSignal) {
          if (externalSignal.aborted) {
            controller.abort();
          } else {
            externalSignal.addEventListener("abort", () => controller.abort(), {
              once: true,
            });
          }
        }

        if (effectiveTimeoutMs > 0) {
          timeoutId = window.setTimeout(() => {
            timedOut = true;
            controller.__cancelReason = "timeout";
            controller.abort();
          }, effectiveTimeoutMs);
        }

        try {
          if (!this.networkOnline && normalizedPriority === "low") {
            const offlineError = new Error("网络离线，后台请求已跳过");
            offlineError.name = "NetworkOfflineError";
            throw offlineError;
          }

          const response = await fetch(url, {
            ...finalOptions,
            signal: controller.signal,
          });
          const data = await this.consumeResponse(response);
          if (!response.ok) {
            const detail =
              typeof data === "object" && data ? data.detail : null;
            const error = new Error(detail || `HTTP ${response.status}`);
            error.response = response;
            error.data = data;
            throw error;
          }
          return data;
        } catch (error) {
          const isAbortError = error?.name === "AbortError";
          const cancelReason = controller.__cancelReason || "";
          const isExpectedAbort =
            isAbortError &&
            (cancelReason === "request_replaced" || externalSignal?.aborted);
          const isTimeoutError =
            isAbortError && (timedOut || cancelReason === "timeout");
          const isOfflineError = error?.name === "NetworkOfflineError";
          const isNetworkError =
            !error.response && !isAbortError && !isOfflineError;
          const canRetry =
            attempt < retryCount &&
            (isTimeoutError ||
              isNetworkError ||
              Number(error?.response?.status || 0) >= 500);

          if (isAbortError) {
            error.cancelReason =
              cancelReason || (externalSignal?.aborted ? "external_abort" : "");
          }

          if (canRetry) {
            await this.sleep(retryWaitMs * (attempt + 1));
            continue;
          }

          if (isTimeoutError && !allowSilentTimeoutError) {
            this.notifyNetworkState(
              "请求超时，请检查网络后重试",
              "warning",
              3500,
            );
          } else if (
            (isNetworkError || isOfflineError) &&
            !allowSilentNetworkError
          ) {
            this.notifyNetworkState(
              "网络连接异常，请检查网络",
              "warning",
              3500,
            );
          }

          if (isExpectedAbort) {
            throw error;
          }
          throw error;
        } finally {
          if (timeoutId) clearTimeout(timeoutId);
          this.cleanupInflightRequest(requestKey, controller);
        }
      }
      throw new Error("请求失败");
    }, normalizedPriority);

    if (requestKey && shouldCoalesce && !cancelPrevious) {
      this.coalescedRequests.set(requestKey, runner);
      runner.finally(() => {
        if (this.coalescedRequests.get(requestKey) === runner) {
          this.coalescedRequests.delete(requestKey);
        }
      });
    }

    return runner;
  }

  get(path, options = {}) {
    return this.request(path, { ...options, method: "GET" });
  }

  post(path, body, options = {}) {
    return this.request(path, { ...options, method: "POST", body });
  }

  put(path, body, options = {}) {
    return this.request(path, { ...options, method: "PUT", body });
  }

  patch(path, body, options = {}) {
    return this.request(path, { ...options, method: "PATCH", body });
  }

  delete(path, options = {}) {
    return this.request(path, { ...options, method: "DELETE" });
  }
}

const api = new ApiClient();

class AdaptivePoller {
  constructor(options = {}) {
    const base = Number(options.baseIntervalMs ?? options.baseMs ?? 1200);
    const max = Number(options.maxIntervalMs ?? options.maxMs ?? 12000);
    this.baseIntervalMs = Math.max(300, Number.isFinite(base) ? base : 1200);
    this.maxIntervalMs = Math.max(
      this.baseIntervalMs,
      Number.isFinite(max) ? max : 12000,
    );
    this.minIntervalMs = Math.max(
      250,
      Math.min(
        this.baseIntervalMs,
        Number(options.minIntervalMs || this.baseIntervalMs),
      ),
    );
    this.jitterRatio = Math.min(
      0.18,
      Math.max(0, Number(options.jitterRatio || 0.08)),
    );
    this.failureCount = 0;
    this.successCount = 0;
    this.lastDelayMs = this.baseIntervalMs;
  }

  getConnectionMultiplier() {
    const connection =
      navigator?.connection ||
      navigator?.mozConnection ||
      navigator?.webkitConnection;
    const effectiveType = String(connection?.effectiveType || "").toLowerCase();
    if (effectiveType === "slow-2g" || effectiveType === "2g") return 3;
    if (effectiveType === "3g") return 1.8;
    if (connection?.saveData) return 1.4;
    return 1;
  }

  recordSuccess() {
    this.failureCount = Math.max(0, this.failureCount - 1);
    this.successCount = Math.min(8, this.successCount + 1);
  }

  recordError() {
    this.failureCount = Math.min(8, this.failureCount + 1);
    this.successCount = 0;
  }

  nextDelay(options = {}) {
    const forceSlow = Boolean(options.forceSlow);
    let delay = this.baseIntervalMs * this.getConnectionMultiplier();
    if (!api.networkOnline || forceSlow) {
      delay = Math.max(delay, this.baseIntervalMs * 2.4);
    }
    if (this.failureCount > 0) {
      delay *= Math.pow(1.55, Math.min(this.failureCount, 5));
    } else if (this.successCount >= 3) {
      delay *= 0.9;
    }
    delay = Math.max(
      this.minIntervalMs,
      Math.min(this.maxIntervalMs, Math.round(delay)),
    );
    const jitter = Math.round(
      delay * this.jitterRatio * (Math.random() * 2 - 1),
    );
    this.lastDelayMs = Math.max(
      this.minIntervalMs,
      Math.min(this.maxIntervalMs, delay + jitter),
    );
    return this.lastDelayMs;
  }
}

function createAdaptivePoller(options = {}) {
  return new AdaptivePoller(options);
}

const filterProtocol = {
  normalizeValue(value) {
    if (value === null || value === undefined) return null;
    if (typeof value === "string") {
      const trimmed = value.trim();
      return trimmed ? trimmed : null;
    }
    if (typeof value === "number") {
      return Number.isFinite(value) ? value : null;
    }
    if (typeof value === "boolean") {
      return value;
    }
    if (Array.isArray(value)) {
      const normalized = value
        .map((item) => this.normalizeValue(item))
        .filter((item) => item !== null);
      return normalized.length ? normalized : null;
    }
    return value;
  },

  normalize(filters = {}) {
    const result = {};
    Object.entries(filters || {}).forEach(([key, raw]) => {
      const value = this.normalizeValue(raw);
      if (value === null) return;
      result[key] = value;
    });
    return result;
  },

  toQuery(filters = {}, mapping = {}) {
    const normalized = this.normalize(filters);
    const params = new URLSearchParams();
    Object.entries(normalized).forEach(([key, value]) => {
      const targetKey = String(mapping[key] || key);
      if (!targetKey) return;
      if (Array.isArray(value)) {
        value.forEach((item) => params.append(targetKey, String(item)));
        return;
      }
      params.set(targetKey, String(value));
    });
    return params;
  },

  toPayload(filters = {}, mapping = {}) {
    const normalized = this.normalize(filters);
    const payload = {};
    Object.entries(normalized).forEach(([key, value]) => {
      const targetKey = String(mapping[key] || key);
      if (!targetKey) return;
      payload[targetKey] = value;
    });
    return payload;
  },

  pickSort(value, allowed = [], fallback = "") {
    const candidate = String(value || "").trim();
    return allowed.includes(candidate) ? candidate : fallback;
  },
};

function delegate(element, eventType, selector, handler) {
  element.addEventListener(eventType, (event) => {
    const target = event.target.closest(selector);
    if (target && element.contains(target)) {
      handler.call(target, event, target);
    }
  });
}

function debounce(func, wait) {
  let timeout;
  return function debounced(...args) {
    clearTimeout(timeout);
    timeout = window.setTimeout(() => func(...args), wait);
  };
}

function throttle(func, limit) {
  let inThrottle = false;
  return function throttled(...args) {
    if (inThrottle) return;
    func(...args);
    inThrottle = true;
    window.setTimeout(() => {
      inThrottle = false;
    }, limit);
  };
}

const format = {
  date(dateStr) {
    if (!dateStr) return "-";
    const date = new Date(dateStr);
    return date.toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  },

  dateShort(dateStr) {
    if (!dateStr) return "-";
    return new Date(dateStr).toLocaleDateString("zh-CN");
  },

  relativeTime(dateStr) {
    if (!dateStr) return "-";
    const date = new Date(dateStr);
    const now = new Date();
    const diff = now - date;
    const seconds = Math.floor(diff / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);

    if (seconds < 60) return "刚刚";
    if (minutes < 60) return `${minutes} 分钟前`;
    if (hours < 24) return `${hours} 小时前`;
    if (days < 7) return `${days} 天前`;
    return this.dateShort(dateStr);
  },

  bytes(bytes) {
    if (bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
  },

  number(num) {
    if (num === null || num === undefined) return "-";
    return Number(num).toLocaleString("zh-CN");
  },
};

const statusMap = {
  account: {
    active: { text: "活跃", class: "active" },
    expired: { text: "过期", class: "expired" },
    banned: { text: "封禁", class: "banned" },
    failed: { text: "失败", class: "failed" },
  },
  task: {
    pending: { text: "等待中", class: "pending" },
    running: { text: "运行中", class: "running" },
    completed: { text: "已完成", class: "completed" },
    failed: { text: "失败", class: "failed" },
    cancelled: { text: "已取消", class: "disabled" },
  },
  service: {
    tempmail: "Tempmail.lol",
    outlook: "Outlook",
    moe_mail: "MoeMail",
    temp_mail: "Temp-Mail（自部署）",
    duck_mail: "DuckMail",
    freemail: "Freemail",
    imap_mail: "IMAP 邮箱",
  },
};

function getStatusText(type, status) {
  return statusMap[type]?.[status]?.text || status;
}

function getStatusClass(type, status) {
  return statusMap[type]?.[status]?.class || "";
}

function getServiceTypeText(type) {
  return statusMap.service[type] || type;
}

function getStatusIcon(status) {
  const normalized =
    String(status || "")
      .trim()
      .toLowerCase() || "unknown";
  return `<span class="status-dot-icon ${normalized}" title="${getStatusText("account", normalized) || normalized}"></span>`;
}

class ModalManager {
  constructor() {
    this.activeModal = null;
    this.focusRestoreTarget = null;
    this.bound = false;
    this.observer = null;
  }

  init() {
    if (this.bound) return;
    this.bound = true;
    this.registerAll();

    document.addEventListener("click", (event) => {
      const closeTrigger = event.target.closest("[data-modal-close]");
      if (closeTrigger) {
        const modalId = closeTrigger.getAttribute("data-modal-close");
        if (modalId) {
          this.close(modalId);
        } else {
          const owner = closeTrigger.closest(".modal");
          if (owner) this.close(owner);
        }
        return;
      }

      const modal = event.target.classList?.contains("modal")
        ? event.target
        : null;
      if (modal?.classList.contains("active")) {
        this.close(modal);
      }
    });

    this.observer = new MutationObserver((records) => {
      const shouldSync = records.some((record) => {
        if (
          record.type === "attributes" &&
          record.target instanceof HTMLElement
        ) {
          return record.target.classList.contains("modal");
        }
        if (record.type === "childList") {
          const changedNodes = [
            ...Array.from(record.addedNodes || []),
            ...Array.from(record.removedNodes || []),
          ];
          return changedNodes.some(
            (node) =>
              node instanceof HTMLElement &&
              (node.classList.contains("modal") ||
                node.querySelector?.(".modal")),
          );
        }
        return false;
      });
      if (!shouldSync) return;
      this.registerAll();
      this.syncBodyState();
    });
    this.observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["class"],
    });
  }

  registerAll() {
    document
      .querySelectorAll(".modal")
      .forEach((modal) => this.register(modal));
  }

  register(modal) {
    if (!modal || modal.dataset.modalReady === "true") return;
    modal.dataset.modalReady = "true";
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    const heading = modal.querySelector(".modal-header h3[id]");
    if (heading) {
      modal.setAttribute("aria-labelledby", heading.id);
    }
    modal.querySelectorAll(".modal-close").forEach((button) => {
      if (!button.hasAttribute("data-modal-close")) {
        button.setAttribute("data-modal-close", "");
      }
    });
  }

  resolve(target) {
    if (!target) return null;
    if (typeof target === "string") {
      if (target.startsWith("#")) {
        return document.querySelector(target);
      }
      return document.getElementById(target) || document.querySelector(target);
    }
    return target;
  }

  open(target) {
    const modal = this.resolve(target);
    if (!modal) return null;
    this.register(modal);
    this.focusRestoreTarget =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    this.activeModal = modal;
    modal.classList.add("active");
    this.syncBodyState();
    const focusTarget = modal.querySelector(
      '[autofocus], button, input, select, textarea, [tabindex]:not([tabindex="-1"])',
    );
    window.setTimeout(() => {
      if (focusTarget instanceof HTMLElement) {
        focusTarget.focus({ preventScroll: true });
      }
    }, 0);
    return modal;
  }

  close(target = null) {
    const modal =
      this.resolve(target) ||
      this.activeModal ||
      document.querySelector(".modal.active");
    if (!modal) return;
    modal.classList.remove("active");
    this.syncBodyState();
    if (this.focusRestoreTarget instanceof HTMLElement) {
      this.focusRestoreTarget.focus({ preventScroll: true });
      this.focusRestoreTarget = null;
    }
  }

  closeActive() {
    this.close(this.activeModal);
  }

  syncBodyState() {
    const activeModal = document.querySelector(".modal.active");
    this.activeModal = activeModal || null;
    if (activeModal) {
      document.body.classList.add("modal-open");
      return;
    }
    document.body.classList.remove("modal-open");
  }
}

const modal = new ModalManager();

function openModal(target) {
  return modal.open(target);
}

function closeModal(target) {
  return modal.close(target);
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = String(text ?? "");
  return div.innerHTML;
}

function confirm(message, title = "确认操作", options = {}) {
  return new Promise((resolve) => {
    const modalId = `confirm-modal-${Date.now()}`;
    const confirmText = String(options.confirmText || "确认");
    const cancelText = String(options.cancelText || "取消");
    const confirmVariant = String(options.confirmVariant || "btn-danger");
    const dialog = document.createElement("div");
    dialog.className = "modal";
    dialog.id = modalId;
    dialog.innerHTML = `
            <div class="modal-content modal-content-compact">
                <div class="modal-header">
                    <h3>${escapeHtml(title)}</h3>
                    <button class="modal-close" type="button" data-modal-close="${modalId}" aria-label="关闭">x</button>
                </div>
                <div class="modal-body">
                    <p class="modal-copy">${escapeHtml(message)}</p>
                    <div class="form-actions modal-actions-inline">
                        <button class="btn btn-secondary" type="button" id="${modalId}-cancel">${escapeHtml(cancelText)}</button>
                        <button class="btn ${confirmVariant}" type="button" id="${modalId}-confirm">${escapeHtml(confirmText)}</button>
                    </div>
                </div>
            </div>
        `;
    document.body.appendChild(dialog);
    modal.register(dialog);
    modal.open(dialog);

    const cleanup = (value) => {
      modal.close(dialog);
      dialog.remove();
      resolve(value);
    };

    dialog
      .querySelector(`#${modalId}-cancel`)
      ?.addEventListener("click", () => cleanup(false));
    dialog
      .querySelector(`#${modalId}-confirm`)
      ?.addEventListener("click", () => cleanup(true));
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) cleanup(false);
    });
  });
}

class ManagedWebSocket {
  constructor(options = {}) {
    this.options = { ...options };
    this.socket = null;
    this.reconnectTimer = null;
    this.heartbeatTimer = null;
    this.reconnectAttempts = 0;
    this.manualClose = false;
    this.label = String(options.label || "WebSocket");
    this.baseDelayMs = Number(options.baseDelayMs || 1000);
    this.maxDelayMs = Number(options.maxDelayMs || 10000);
    this.heartbeatIntervalMs = Number(options.heartbeatIntervalMs || 25000);
  }

  get readyState() {
    return this.socket ? this.socket.readyState : WebSocket.CLOSED;
  }

  buildUrl() {
    if (typeof this.options.url === "function") {
      return this.options.url();
    }
    return this.options.url;
  }

  shouldReconnect(event) {
    if (this.manualClose) return false;
    if (typeof this.options.shouldReconnect === "function") {
      return Boolean(this.options.shouldReconnect(event));
    }
    return true;
  }

  getReconnectDelay() {
    const attempt = Math.max(0, this.reconnectAttempts);
    return Math.min(this.baseDelayMs * 2 ** attempt, this.maxDelayMs);
  }

  clearReconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  clearHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  startHeartbeat() {
    this.clearHeartbeat();
    if (!this.heartbeatIntervalMs) return;
    this.heartbeatTimer = window.setInterval(() => {
      if (this.readyState !== WebSocket.OPEN) return;
      if (typeof this.options.heartbeatPayload === "undefined") return;
      this.send(this.options.heartbeatPayload);
    }, this.heartbeatIntervalMs);
  }

  scheduleReconnect(event) {
    if (this.reconnectTimer || !this.shouldReconnect(event)) return;
    const delay = this.getReconnectDelay();
    if (typeof this.options.onReconnectSchedule === "function") {
      this.options.onReconnectSchedule({
        attempt: this.reconnectAttempts + 1,
        delay,
        event,
      });
    }
    this.reconnectAttempts += 1;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delay);
  }

  connect() {
    if ([WebSocket.OPEN, WebSocket.CONNECTING].includes(this.readyState)) {
      return;
    }
    this.manualClose = false;
    this.clearReconnect();

    let socket;
    try {
      socket = new WebSocket(this.buildUrl(), this.options.protocols);
    } catch (error) {
      if (typeof this.options.onError === "function") {
        this.options.onError(error);
      }
      this.scheduleReconnect({ code: 0, error });
      return;
    }

    this.socket = socket;

    socket.onopen = (event) => {
      if (this.socket !== socket) return;
      this.reconnectAttempts = 0;
      this.startHeartbeat();
      if (typeof this.options.onOpen === "function") {
        this.options.onOpen(event);
      }
    };

    socket.onmessage = (event) => {
      if (this.socket !== socket) return;
      if (typeof this.options.onMessage === "function") {
        this.options.onMessage(event);
      }
    };

    socket.onerror = (event) => {
      if (this.socket !== socket) return;
      if (typeof this.options.onError === "function") {
        this.options.onError(event);
      }
    };

    socket.onclose = (event) => {
      if (this.socket === socket) {
        this.socket = null;
      }
      this.clearHeartbeat();
      if (typeof this.options.onClose === "function") {
        this.options.onClose(event);
      }
      this.scheduleReconnect(event);
    };
  }

  disconnect(options = {}) {
    const closeOptions = { code: 1000, reason: "manual_close", ...options };
    this.manualClose = closeOptions.manual !== false;
    this.clearReconnect();
    this.clearHeartbeat();
    if (this.socket) {
      const socket = this.socket;
      this.socket = null;
      socket.close(closeOptions.code, closeOptions.reason);
    }
  }

  send(payload) {
    if (this.readyState !== WebSocket.OPEN) return false;
    const body =
      typeof payload === "string" ? payload : JSON.stringify(payload);
    this.socket.send(body);
    return true;
  }
}

function createManagedWebSocket(options = {}) {
  return new ManagedWebSocket(options);
}

async function copyToClipboard(text) {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("已复制到剪贴板");
      return true;
    } catch {
      // 降级到 execCommand
    }
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.cssText =
      "position:fixed;top:0;left:0;opacity:0;pointer-events:none;";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    const ok = document.execCommand("copy");
    textarea.remove();
    if (ok) {
      toast.success("已复制到剪贴板");
      return true;
    }
    throw new Error("execCommand failed");
  } catch {
    toast.error("复制失败");
    return false;
  }
}

const storage = {
  get(key, defaultValue = null) {
    try {
      const value = localStorage.getItem(key);
      return value ? JSON.parse(value) : defaultValue;
    } catch {
      return defaultValue;
    }
  },

  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch {
      return false;
    }
  },

  remove(key) {
    localStorage.removeItem(key);
  },
};

document.addEventListener("DOMContentLoaded", () => {
  theme.applyTheme();
  modal.init();

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      const searchInput = document.querySelector(
        '#search-input, [type="search"]',
      );
      if (searchInput) {
        event.preventDefault();
        searchInput.focus();
      }
    }

    if (event.key === "Escape") {
      modal.closeActive();
    }
  });
});

window.toast = toast;
window.theme = theme;
window.loading = loading;
window.api = api;
window.format = format;
window.confirm = confirm;
window.copyToClipboard = copyToClipboard;
window.storage = storage;
window.delegate = delegate;
window.debounce = debounce;
window.throttle = throttle;
window.getStatusText = getStatusText;
window.getStatusClass = getStatusClass;
window.getServiceTypeText = getServiceTypeText;
window.getStatusIcon = getStatusIcon;
window.createAdaptivePoller = createAdaptivePoller;
window.filterProtocol = filterProtocol;
window.modal = modal;
window.openModal = openModal;
window.closeModal = closeModal;
window.createManagedWebSocket = createManagedWebSocket;

/**
 * 全局任务监控中心管理器
 */
class GlobalTaskManager {
  constructor() {
    this.tasks = new Map();
    this.container = null;
    this.activeDetailId = null;
    this.init();
  }

  init() {
    this.container = document.getElementById("global-task-container");
    if (!this.container) return;

    // 绑定模态框内的操作
    document.getElementById("task-detail-clear-logs")?.addEventListener("click", () => {
      const logs = document.getElementById("task-detail-logs");
      if (logs) logs.innerHTML = "";
    });

    document.getElementById("task-detail-cancel-btn")?.addEventListener("click", () => {
      if (this.activeDetailId) this.cancelTask(this.activeDetailId);
    });
  }

  updateTask(taskId, data) {
    if (!taskId) return;
    
    let task = this.tasks.get(taskId);
    if (!task) {
      task = {
        id: taskId,
        title: data.title || "未知任务",
        progress: 0,
        stats: { success: 0, failed: 0, total: 0 },
        status: "pending",
        logs: [],
        startTime: Date.now()
      };
      this.tasks.set(taskId, task);
      this.renderCard(taskId);
    }

    // 更新数据
    if (data.progress !== undefined) task.progress = data.progress;
    if (data.status !== undefined) task.status = data.status;
    if (data.stats) task.stats = { ...task.stats, ...data.stats };
    if (data.log) {
      const logEntry = { time: new Date().toLocaleTimeString(), message: data.log, type: data.logType || "info" };
      task.logs.push(logEntry);
      if (this.activeDetailId === taskId) this.appendLogToModal(logEntry);
    }

    this.updateCardUI(taskId);
    if (this.activeDetailId === taskId) this.updateModalUI(taskId);

    // 自动清理逻辑：如果是完成或失败，允许手动关闭，但不自动消失
    if (task.status === "completed" || task.status === "failed") {
      const card = document.getElementById(`task-card-${taskId}`);
      if (card) {
        card.querySelector(".task-card-status").textContent = task.status === "completed" ? "已完成" : "失败";
        card.classList.add("finished");
      }
    }
  }

  renderCard(taskId) {
    const task = this.tasks.get(taskId);
    const card = document.createElement("div");
    card.id = `task-card-${taskId}`;
    card.className = "task-card";
    card.innerHTML = `
      <div class="task-card-header">
        <span class="task-card-title">${escapeHtml(task.title)}</span>
        <span class="task-card-status">运行中</span>
      </div>
      <div class="task-card-progress-wrapper">
        <div class="task-card-progress-bar"></div>
      </div>
      <div class="task-card-stats">
        <span>成功: <b class="success-count">0</b></span>
        <span>失败: <b class="failed-count">0</b></span>
        <span>总计: <b class="total-count">0</b></span>
      </div>
      <button class="toast-close" style="position:absolute; top:8px; right:8px; display:none;">x</button>
    `;

    card.addEventListener("click", (e) => {
      if (e.target.classList.contains("toast-close")) {
        this.tasks.delete(taskId);
        card.remove();
        return;
      }
      this.openDetail(taskId);
    });

    this.container.appendChild(card);
  }

  updateCardUI(taskId) {
    const task = this.tasks.get(taskId);
    const card = document.getElementById(`task-card-${taskId}`);
    if (!card) return;

    card.querySelector(".task-card-progress-bar").style.width = `${task.progress}%`;
    card.querySelector(".success-count").textContent = task.stats.success;
    card.querySelector(".failed-count").textContent = task.stats.failed;
    card.querySelector(".total-count").textContent = task.stats.total;

    if (task.status === "completed" || task.status === "failed") {
      card.querySelector(".toast-close").style.display = "block";
    }
  }

  openDetail(taskId) {
    const task = this.tasks.get(taskId);
    if (!task) return;

    this.activeDetailId = taskId;
    document.getElementById("task-detail-title").textContent = task.title;
    document.getElementById("task-detail-id").textContent = taskId;
    
    // 初始化日志和统计
    const logContainer = document.getElementById("task-detail-logs");
    logContainer.innerHTML = "";
    task.logs.forEach(log => this.appendLogToModal(log));
    
    this.updateModalUI(taskId);
    window.openModal("task-detail-modal");
  }

  updateModalUI(taskId) {
    const task = this.tasks.get(taskId);
    const statsContainer = document.getElementById("task-detail-stats");
    
    statsContainer.innerHTML = `
      <div class="task-detail-stat-item">
        <span class="task-detail-stat-value">${task.progress}%</span>
        <span class="task-detail-stat-label">进度</span>
      </div>
      <div class="task-detail-stat-item">
        <span class="task-detail-stat-value">${task.stats.success}</span>
        <span class="task-detail-stat-label">成功</span>
      </div>
      <div class="task-detail-stat-item">
        <span class="task-detail-stat-value">${task.stats.failed}</span>
        <span class="task-detail-stat-label">失败</span>
      </div>
      <div class="task-detail-stat-item">
        <span class="task-detail-stat-value">${task.stats.total}</span>
        <span class="task-detail-stat-label">总计</span>
      </div>
    `;

    document.getElementById("task-detail-status").textContent = task.status;
    const elapsed = Math.floor((Date.now() - task.startTime) / 1000);
    document.getElementById("task-detail-duration").textContent = `${elapsed}s`;
  }

  appendLogToModal(log) {
    const logContainer = document.getElementById("task-detail-logs");
    if (!logContainer) return;

    const div = document.createElement("div");
    div.className = `log-entry log-${log.type}`;
    div.innerHTML = `<span class="log-time">[${log.time}]</span><span class="log-msg">${escapeHtml(log.message)}</span>`;
    logContainer.appendChild(div);
    logContainer.scrollTop = logContainer.scrollHeight;
  }

  async cancelTask(taskId) {
    if (!confirm("确定要取消此任务吗？")) return;
    try {
      await api.post(`/registration/tasks/${taskId}/cancel`);
      toast.success("取消请求已提交");
    } catch (e) {
      toast.error("取消失败: " + e.message);
    }
  }
}

window.taskMonitor = new GlobalTaskManager();

