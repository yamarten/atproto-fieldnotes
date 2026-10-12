"use strict";

const frame = document.getElementById("registry");
const openTab = document.getElementById("open-tab");
const popupStatus = document.getElementById("status");
let registryRoot = "", targetUrl = "";
let navigationPort = null;

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
    const settings = await browser.storage.local.get("registryUrl");
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
