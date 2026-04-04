const fs = require("fs");
const { Window } = require("happy-dom");

const EXPOSE_PATCH = "return o?r?.[n(63)]?ce({so:o,c:r[n(63)]},t):o:null},t.token=ye,t}({});";
const EXPOSE_REPLACEMENT =
  "return o?r?.[n(63)]?ce({so:o,c:r[n(63)]},t):o:null},t.token=ye,t.__debug_n=_n,t.__debug_bindProof=D,t}({});";
const INSTANCE_PATCH = "var P=new _;";
const INSTANCE_REPLACEMENT = "var P=new _;globalThis.__debugP=P;";
const SDK_GLOBAL_PATCH = "var SentinelSDK=";
const SDK_GLOBAL_REPLACEMENT = "globalThis.SentinelSDK=";

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function setNavigator(window, payload) {
  Object.defineProperty(window.navigator, "userAgent", {
    value: payload.user_agent,
    configurable: true,
  });
  Object.defineProperty(window.navigator, "language", {
    value: payload.language,
    configurable: true,
  });
  Object.defineProperty(window.navigator, "languages", {
    value: payload.languages,
    configurable: true,
  });
  Object.defineProperty(window.navigator, "hardwareConcurrency", {
    value: Number(payload.hardware_concurrency || 12),
    configurable: true,
  });
}

function prepareWindow(payload) {
  const window = new Window({ url: "https://auth.openai.com/" });
  window.document.cookie = `oai-did=${encodeURIComponent(payload.device_id || "")}`;
  setNavigator(window, payload);
  window.screen.width = Number(payload.screen_width || 1366);
  window.screen.height = Number(payload.screen_height || 768);
  window.performance.now = () => Number(payload.performance_now || 12345.67);
  window.performance.timeOrigin = Number(payload.time_origin || Date.now() - 12345.67);
  window.performance.memory = {
    jsHeapSizeLimit: Number(payload.js_heap_size_limit || 4294967296),
  };
  window.requestIdleCallback = (cb) =>
    window.setTimeout(() => cb({ didTimeout: false, timeRemaining: () => 50 }), 0);
  window.fetch = async () => {
    throw new Error("fetch should not be called");
  };

  global.window = window;
  global.document = window.document;
  global.self = window;
  global.localStorage = window.localStorage;
  global.sessionStorage = window.sessionStorage;
  global.screen = window.screen;
  global.performance = window.performance;
  global.atob = (input) => Buffer.from(input, "base64").toString("binary");
  global.btoa = (input) => Buffer.from(input, "binary").toString("base64");
  global.fetch = window.fetch;
  Object.defineProperty(global, "navigator", {
    value: window.navigator,
    configurable: true,
  });
  Object.defineProperty(global, "location", {
    value: window.location,
    configurable: true,
  });
}

function loadSdk(payload) {
  let sdk = fs.readFileSync(payload.sdk_path, "utf8");
  sdk = sdk.replace(SDK_GLOBAL_PATCH, SDK_GLOBAL_REPLACEMENT);
  sdk = sdk.replace(INSTANCE_PATCH, INSTANCE_REPLACEMENT);
  sdk = sdk.replace(EXPOSE_PATCH, EXPOSE_REPLACEMENT);
  eval(sdk);
}

async function run(payload) {
  prepareWindow(payload);
  loadSdk(payload);

  if (payload.action === "requirements") {
    const requestP = await globalThis.__debugP.getRequirementsToken();
    return { request_p: requestP };
  }

  if (payload.action === "solve") {
    const challenge = payload.challenge || {};
    const requestP = String(payload.request_p || "").trim();
    if (!requestP) {
      throw new Error("missing request_p");
    }
    const finalP = await globalThis.__debugP.getEnforcementToken(challenge);
    global.SentinelSDK.__debug_bindProof(challenge, requestP);
    const dx = challenge?.turnstile?.dx;
    const tValue = dx ? await global.SentinelSDK.__debug_n(challenge, dx) : null;
    return {
      final_p: finalP,
      t: tValue,
    };
  }

  throw new Error(`unsupported action: ${payload.action}`);
}

(async () => {
  try {
    const raw = await readStdin();
    const payload = JSON.parse(raw || "{}");
    const result = await run(payload);
    process.stdout.write(JSON.stringify(result));
  } catch (error) {
    const message = error && error.stack ? error.stack : String(error);
    process.stderr.write(message);
    process.exit(1);
  }
})();