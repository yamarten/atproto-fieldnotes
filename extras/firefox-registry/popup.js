"use strict";

const frame = document.getElementById("registry");
const openTab = document.getElementById("open-tab");
const popupStatus = document.getElementById("status");
const widthControl = document.getElementById("popup-width");
const widthValue = document.getElementById("popup-width-value");
let registryRoot = "", targetUrl = "";
let navigationPort = null;

function applyPopupWidth(value) {
  const numeric = Number(value);
  const width = Number.isFinite(numeric) && numeric > 0
    ? Math.min(780, Math.max(360, Math.round(numeric / 20) * 20)) : 480;
  document.documentElement.style.setProperty("--popup-width", `${width}px`);
  widthControl.value = String(width);
  widthValue.value = `${width}px`;
  return width;
}
widthControl.addEventListener("input", () => applyPopupWidth(widthControl.value));
widthControl.addEventListener("change", async () => {
  try { await browser.storage.local.set({popupWidth: applyPopupWidth(widthControl.value)}); }
  catch (_) { popupStatus.textContent = "横幅を保存できませんでした。次回は以前の幅で開きます。"; }
});

document.getElementById("options").addEventListener("click", async () => {
  await browser.runtime.openOptionsPage();
  window.close();
});
openTab.addEventListener("click", async () => {
  try {
    if (!targetUrl) return;
    await browser.tabs.create({url: targetUrl});
    window.close();
  } catch (error) { popupStatus.textContent = error.message; }
});
frame.addEventListener("load", () => {
  if (!registryRoot) return;
  navigationPort?.close();
  const channel = new MessageChannel();
  navigationPort = channel.port1;
  navigationPort.onmessage = (event) => {
    if (event.data?.type !== "registry-popup-location") return;
    try {
      targetUrl = registryPopupTarget(registryRoot, event.data);
      popupStatus.textContent = "";
    } catch (_) { /* Invalid messages cannot change the tab destination. */ }
  };
  // Only this frame at the configured Registry origin receives the port.
  frame.contentWindow.postMessage({type: "registry-popup-connect"}, new URL(registryRoot).origin, [channel.port2]);
});

async function initializePopup() {
  try {
    const settings = await browser.storage.local.get(["registryUrl", "popupWidth"]);
    applyPopupWidth(settings.popupWidth);
    widthControl.disabled = false;
    if (!settings.registryUrl) {
      popupStatus.textContent = "設定からWeb UIの接続先を指定してください。";
      return;
    }
    registryRoot = registryBaseUrl(settings.registryUrl);
    const [tab] = await browser.tabs.query({active: true, currentWindow: true});
    targetUrl = registryEntryUrl(registryRoot, tab?.url || "");
    openTab.disabled = false;
    const embedded = new URL(targetUrl);
    embedded.searchParams.set("embed", "popup");
    embedded.searchParams.set("popup_parent", browser.runtime.getURL("").replace(/\/$/, ""));
    frame.src = embedded.href;
    frame.hidden = false;
    popupStatus.textContent = "表示されない場合は「タブで開く」で確認できます。";
  } catch (error) { popupStatus.textContent = error.message; }
}
initializePopup();
