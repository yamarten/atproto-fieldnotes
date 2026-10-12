"use strict";

function registryBaseUrl(value) {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== "/") {
    throw new Error("Web UIのルートURLを指定してください。認証情報・クエリ・フラグメントは含められません。");
  }
  return url.origin + "/";
}

function registryEntryUrl(base, page) {
  const source = new URL(page);
  if (!["http:", "https:"].includes(source.protocol) || source.username || source.password) {
    throw new Error("HTTP(S)のサービスやリポジトリのページで実行してください。");
  }
  source.search = "";
  source.hash = "";
  const target = new URL(registryBaseUrl(base));
  target.searchParams.set("screen", "browser");
  target.searchParams.set("url", source.href);
  return target.href;
}
