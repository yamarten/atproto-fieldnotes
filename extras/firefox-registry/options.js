"use strict";

const field = document.getElementById("registry-url");
const status = document.getElementById("status");
browser.storage.local.get("registryUrl").then((settings) => { field.value = settings.registryUrl || ""; })
  .catch(() => { status.textContent = "接続先を読み込めませんでした。拡張を再読込してください。"; });
status.textContent = new URLSearchParams(location.search).get("error") || "";
document.getElementById("settings").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const registryUrl = registryBaseUrl(field.value.trim());
    await browser.storage.local.set({registryUrl});
    field.value = registryUrl;
    status.textContent = "保存しました。サービスのタブに戻り、拡張ボタンを押してください。";
  } catch (error) { status.textContent = error.message; }
});
