package de.jarvis.backend.provider;

import java.util.regex.Pattern;

/** Ein Stück vertonter Sprache mit Format ("audio/mpeg" ganz, "audio/pcm;rate=N" als 16-bit-Mono-Stück). */
public record TtsChunk(String mime, byte[] bytes) {
    private static final Pattern PCM = Pattern.compile("audio/pcm;rate=(8000|16000|22050|24000|44100|48000)");

    public static boolean allowedMime(String mime) {
        return "audio/mpeg".equals(mime) || "audio/wav".equals(mime) || (mime != null && PCM.matcher(mime).matches());
    }

    public static boolean isPcm(String mime) {
        return mime != null && mime.startsWith("audio/pcm;");
    }
}
