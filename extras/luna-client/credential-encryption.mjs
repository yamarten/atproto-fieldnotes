// Derived from OpenAI SIWC DevKit examples/paste-perfect/electron/credential-encryption.ts
// Copyright 2026 OpenAI. See LICENSE.devkit (Noncommercial License v1.0).
// Modified 2026-10-09: JavaScript export for this standalone manual client.
function createElectronCredentialEncryption(storage, isReady, platform = process.platform) {
  const isAvailable = () => {
    if (!isReady() || !storage.isEncryptionAvailable()) return false;
    return platform !== "linux" || ["gnome_libsecret", "kwallet", "kwallet5", "kwallet6"].includes(storage.getSelectedStorageBackend());
  };
  const requireAvailable = () => {
    if (!isAvailable()) throw new Error("OS credential encryption is unavailable.");
  };
  return {
    id: "electron-safe-storage-v1",
    isAvailable,
    encrypt(plaintext) {
      requireAvailable();
      return storage.encryptString(plaintext);
    },
    decrypt(ciphertext) {
      requireAvailable();
      return storage.decryptString(Buffer.from(ciphertext));
    }
  };
}
export {
  createElectronCredentialEncryption
};
