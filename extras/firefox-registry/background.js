"use strict";

async function configurePopup() {
  const settings = await browser.storage.local.get("openMode");
  await browser.action.setPopup({popup: settings.openMode === "tab" ? "" : "popup.html"});
}
configurePopup().catch(console.error);
browser.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.openMode) configurePopup().catch(console.error);
});

browser.action.onClicked.addListener(async (tab) => {
  try {
    const settings = await browser.storage.local.get("registryUrl");
    if (!settings.registryUrl) {
      await browser.runtime.openOptionsPage();
      return;
    }
    const target = registryEntryUrl(settings.registryUrl, tab.url || "");
    await browser.tabs.create({url:target});
  } catch (error) {
    // Show an actionable error in the extension's own page, without persisting
    // the visited URL or requesting notification/history/site permissions.
    const target = new URL(browser.runtime.getURL("options.html"));
    target.searchParams.set("error", error.message);
    await browser.tabs.create({url:target.href});
  }
});
