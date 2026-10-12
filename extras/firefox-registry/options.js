"use strict";

const field = document.getElementById("registry-url");
const status = document.getElementById("status");
const mode = document.getElementById("open-mode");
browser.storage.local.get(["registryUrl", "openMode"]).then((settings) => {
  field.value = settings.registryUrl || "";
  mode.value = settings.openMode === "tab" ? "tab" : "popup";
})
  .catch(() => { status.textContent = "接続先を読み込めませんでした。拡張を再読込してください。"; });
status.textContent = new URLSearchParams(location.search).get("error") || "";
document.getElementById("settings").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const registryUrl = registryBaseUrl(field.value.trim());
    await browser.storage.local.set({registryUrl, openMode: mode.value});
    field.value = registryUrl;
    status.textContent = "保存しました。サービスのタブに戻り、拡張ボタンを押してください。";
  } catch (error) { status.textContent = error.message; }
});
