/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Öffentlicher Mapbox-Zugangsschlüssel (pk.…) für die World-View. Nie einen geheimen sk.-Schlüssel eintragen. */
  readonly VITE_MAPBOX_ACCESS_TOKEN?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}
