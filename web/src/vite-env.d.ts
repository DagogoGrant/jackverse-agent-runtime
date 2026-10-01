/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_JACKVERSE_DEV_AUTH?: string;
  readonly VITE_CASEWORKER_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
