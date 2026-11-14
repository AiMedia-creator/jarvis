/**
 * Ansichts-Absicht aus einer FERTIGEN Nutzeräußerung (finales Transkript), nie aus laufenden Modell-Tokens.
 * Negationen, Rückkehr-Befehle und explizite Ansichtsbefehle haben Vorrang; im Zweifel bleibt das Gespräch.
 */
export type ContentView =
  | "conversation"
  | "news"
  | "market"
  | "cameras"
  | "memory"
  | "ops"
  | "command"
  | "world";
export type OpenableView = Exclude<ContentView, "conversation">;

export type Intent =
  | { kind: "none" }
  | { kind: "back" }
  | {
      kind: "open";
      view: OpenableView;
      /** Ausdrücklicher Befehl ("zeig mir …", "öffne …") statt nur thematischer Frage. */
      explicit: boolean;
      /** Wertpapier-Name oder Symbol (Markt) bzw. Ort (Nachrichten). */
      query?: string;
    };

export type SecurityName = { symbol: string; name: string; aliases: string[] };

/** Wortgrenzen, die auch mit Umlauten funktionieren (\b in JS kennt nur ASCII). */
const L = "\\p{L}\\p{N}";
const words = (source: string) =>
  new RegExp(`(?<![${L}])(?:${source})(?![${L}])`, "u");

const BACK = words(
  "zurück(?: zum gespräch)?|zurueck|nur (?:noch )?mit dir (?:sprechen|reden)|schlie(?:ß|ss)e?\\s+(?:die\\s+|diese\\s+)?ansicht|ansicht\\s+(?:schlie(?:ß|ss)en|zu|weg)|blende\\s+(?:das|die ansicht|alles)\\s+aus|anderes thema|etwas anderes",
);
const NEGATION = words("nicht|kein|keine|keinen|keiner|ohne|nie|niemals");
const EXPLICIT = words(
  "zeig|zeige|zeigt|öffne|oeffne|öffnen|wechsel|wechsle|wechseln|geh(?:e)?\\s+(?:zu|in|auf)|bring mich|ruf\\p{L}*|blende\\s+\\p{L}+\\s+ein",
);
const DEFINITION =
  /^\s*(?:was (?:ist|sind) (?:eine?|der|die|das)\s|was bedeute(?:t|n)(?![\p{L}])|erkl(?:ä|ae)r)/u;

const NEWS = words(
  "nachrichten\\p{L}*|schlagzeilen|news|meldungen|neuigkeiten|weltlage|krisenherde?|was gibt(?:'s| es)(?: heute)? neues|was ist (?:heute |gerade )?(?:in|im|auf) [\\p{L} -]+ los",
);
const MARKET_WORDS = words(
  "kurs\\p{L}*|aktie\\p{L}*|börse\\p{L}*|boerse\\p{L}*|chart\\p{L}*|wertpapier\\p{L}*|dax|nasdaq",
);
const MARKET_VERBS = words(
  "entwickelt|entwicklung|steht|stehen|läuft|laeuft|laufen|performance|gemacht|notiert",
);
const CAMERAS = words("kamera\\p{L}*|cctv|livebild\\p{L}*|überwachung\\p{L}*");
const MEMORY = words(
  "gedächtnis|gedaechtnis|erinnerungen|gespeichert\\p{L}*|gesprächsverlauf|verlauf",
);
const OPS = words(
  "system\\p{L}*|operations|ops|metrik\\p{L}*|protokoll\\p{L}*|logs?|telemetrie|jobs",
);
const ASKING = words("welche|was|gibt es|verfügbar|zeig|liste");
const WORLD = words("weltkarte|weltansicht|world[ -]?view|globus|landkarte|karte");
const FLY = /(?:^|\s)(?:flieg\p{L}*|bring mich|zoom\p{L}*)\s+(?:mich\s+)?(?:nach|zu|zur|zum|über|ueber|auf|in)\s+(.+)$/iu;
const ON_MAP = /(?:zeig\p{L}*\s+(?:mir\s+)?|wo (?:ist|liegt)\s+)(.+?)\s+(?:auf|in) der (?:welt)?karte/iu;
/** Ortsbefehl ohne Zusatz: „Zeig Berlin“, „Zeig mir bitte München“. Nur bekannte Orte und nur der ganze Teilsatz –
 *  „Zeig Nachrichten aus Berlin“ oder „Zeig mir den Kurs von …“ bleiben bei Nachrichten bzw. Markt. */
const CITIES = "berlin|hamburg|münchen|muenchen|köln|koeln|frankfurt|stuttgart|düsseldorf|duesseldorf|leipzig|dresden|hannover|nürnberg|nuernberg|bremen|essen|dortmund|wien|zürich|zuerich|paris|london|new york|tokio|tokyo|dubai|singapur|rom|madrid|amsterdam";
const SHOW_PLACE = new RegExp(`^(?:zeig\\p{L}*|öffne|oeffne)\\s+(?:mir\\s+)?(?:bitte\\s+)?(?:die stadt\\s+)?(${CITIES})\\s*$`, "iu");
/** Ägyptisch: „وريني برلين“ / „فرجني القاهرة“ / „افتح خريطة دبي“ -> Karte. Nur bekannte Städte (arabisch -> Kartenname). */
const ORTE_AR: Record<string, string> = {
  "برلين": "Berlin", "القاهرة": "Kairo", "القاهره": "Kairo", "الاسكندرية": "Alexandria", "الإسكندرية": "Alexandria",
  "اسكندرية": "Alexandria", "ميونخ": "München", "هامبورج": "Hamburg", "فرانكفورت": "Frankfurt", "كولونيا": "Köln",
  "دبي": "Dubai", "لندن": "London", "باريس": "Paris", "نيويورك": "New York", "طوكيو": "Tokio", "روما": "Rom",
  "شتوتجارت": "Stuttgart", "الجونة": "El Gouna", "شرم الشيخ": "Sharm El-Sheikh", "الغردقة": "Hurghada", "الأقصر": "Luxor", "أسوان": "Assuan",
};
const SHOW_PLACE_AR = /^(?:وريني|ورّيني|ورينى|فرجني|فرّجني|اعرض(?:لي)?|افتح(?:لي)?)\s+(?:لي\s+|خريطة\s+|خريطه\s+)?(.+?)[\s.!؟?]*$/u;
const COMMAND = words(
  "kommandozentrale|command center|kalender\\p{L}*|agenda|terminübersicht|terminuebersicht|aufträge|auftraege|auftragsboard|kanban|freigaben|freigabe\\p{L}*",
);
const SECTION: [RegExp, string][] = [
  [words("kalender\\p{L}*"), "kalender"],
  [words("agenda|terminübersicht|terminuebersicht"), "agenda"],
  [words("aufträge|auftraege|auftragsboard|kanban"), "auftraege"],
  [words("freigaben|freigabe\\p{L}*"), "freigaben"],
];
const MEMORY_QUESTION = words("was (?:hast|weißt|weisst) du");
const STORE_REQUEST = words("merk\\p{L}*|speicher\\p{L}*");
const TICKER =
  /(?:[Kk]urs von|[Aa]ktie von|[Ss]ymbol)\s+([A-Z][A-Z0-9]{0,5}(?:\.[A-Z]{2,4})?)(?![\p{L}\p{N}])/u;
const PLACE =
  /(?:^|\s)(?:in|im|aus|über|ueber)\s+(\p{Lu}[\p{L}-]+(?:\s\p{Lu}[\p{L}-]+)?)/u;

function clauses(text: string): string[] {
  return text
    .split(/[.;!?,]|(?<![\p{L}])(?:sondern|aber|und dann)(?![\p{L}])/iu)
    .map((part) => part.trim())
    .filter(Boolean);
}

function escape(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function findSecurity(
  original: string,
  lower: string,
  directory: SecurityName[],
): string | undefined {
  const ticker = original.match(TICKER);
  if (ticker) return ticker[1];
  for (const entry of directory)
    for (const alias of entry.aliases)
      if (words(escape(alias.toLowerCase())).test(lower)) return alias;
  return undefined;
}

export function classifyUtterance(
  text: string,
  directory: SecurityName[] = [],
): Intent {
  const original = (text || "").trim().slice(0, 600);
  if (!original) return { kind: "none" };
  let best: Intent = { kind: "none" };
  // Pro Teilsatz prüfen: "Nicht die Nachrichten, sondern die Kameras" -> Kameras.
  for (const clauseOriginal of clauses(original)) {
    const clause = clauseOriginal.toLowerCase();
    if (NEGATION.test(clause)) continue;
    if (BACK.test(clause)) return { kind: "back" };
    const candidate = classifyClause(
      clauseOriginal,
      clause,
      EXPLICIT.test(clause),
      directory,
    );
    if (
      candidate.kind === "open" &&
      (best.kind !== "open" || (candidate.explicit && !best.explicit))
    )
      best = candidate;
  }
  return best;
}

function classifyClause(
  original: string,
  clause: string,
  explicit: boolean,
  directory: SecurityName[],
): Intent {
  const fly = original.match(FLY);
  if (fly) return { kind: "open", view: "world", explicit: true, query: fly[1].trim() };
  const onMap = original.match(ON_MAP);
  if (onMap) return { kind: "open", view: "world", explicit: true, query: onMap[1].trim() };
  if (WORLD.test(clause) && explicit) return { kind: "open", view: "world", explicit };
  if (COMMAND.test(clause) && explicit) {
    const section = SECTION.find(([pattern]) => pattern.test(clause))?.[1];
    return { kind: "open", view: "command", explicit, ...(section ? { query: section } : {}) };
  }
  if (OPS.test(clause) && explicit)
    return { kind: "open", view: "ops", explicit };
  if (CAMERAS.test(clause) && (explicit || ASKING.test(clause)))
    return { kind: "open", view: "cameras", explicit };
  if (
    MEMORY.test(clause) &&
    (explicit || MEMORY_QUESTION.test(clause)) &&
    !STORE_REQUEST.test(clause.replace(/gespeichert\p{L}*/u, ""))
  )
    return { kind: "open", view: "memory", explicit };

  const definitional = DEFINITION.test(clause);
  const security = findSecurity(original, clause, directory);
  if (
    security &&
    !definitional &&
    (MARKET_WORDS.test(clause) || MARKET_VERBS.test(clause) || explicit)
  )
    return { kind: "open", view: "market", explicit, query: security };
  if (MARKET_WORDS.test(clause) && !definitional && explicit)
    return { kind: "open", view: "market", explicit };

  if (NEWS.test(clause) && !definitional) {
    const place = original.match(PLACE)?.[1];
    return {
      kind: "open",
      view: "news",
      explicit,
      ...(place ? { query: place } : {}),
    };
  }
  // Niedrigste Priorität: erst wenn kein Nachrichten-, Markt-, Kamera- oder Bereichsbefehl passt.
  const showPlaceAr = original.trim().match(SHOW_PLACE_AR);
  if (showPlaceAr && ORTE_AR[showPlaceAr[1].trim()]) return { kind: "open", view: "world", explicit: true, query: ORTE_AR[showPlaceAr[1].trim()] };
  const showPlace = original.trim().match(SHOW_PLACE);
  if (showPlace) return { kind: "open", view: "world", explicit: true, query: showPlace[1].trim() };
  return { kind: "none" };
}
