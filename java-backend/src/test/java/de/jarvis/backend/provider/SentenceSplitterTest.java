package de.jarvis.backend.provider;

import static org.junit.jupiter.api.Assertions.*;
import java.util.List;
import org.junit.jupiter.api.Test;

class SentenceSplitterTest {
  @Test void firstChunkMayEndAtCommaButLaterOnesWaitForSentenceEnd(){
    var s=new SentenceSplitter();
    assertEquals(List.of(),s.accept("Ich habe mir erlaubt, "));                       // zu kurz: warten
    assertEquals(List.of("Ich habe mir erlaubt, den Tag bereits zu sortieren,"),
        s.accept("den Tag bereits zu sortieren, während Sie noch beschäftigt waren"));
    assertEquals(List.of("während Sie noch beschäftigt waren."),s.accept(". Danach, wenn Sie wollen, "));
    assertEquals(List.of(),s.accept("geht es weiter, ganz in Ruhe, Schritt für Schritt"));   // kein zweites Komma-Stück
    assertEquals("Danach, wenn Sie wollen, geht es weiter, ganz in Ruhe, Schritt für Schritt",s.flush());
  }
  @Test void splitsIncrementalTextAndFlushesTail(){var s=new SentenceSplitter();assertEquals(List.of(),s.accept("Hal"));assertEquals(List.of("Hallo."),s.accept("lo. Rest"));assertEquals("Rest",s.flush());}
}
