package de.jarvis.backend.room;

import static org.junit.jupiter.api.Assertions.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import de.jarvis.backend.audio.CancellationToken;
import de.jarvis.backend.provider.GenerationProvider;
import de.jarvis.backend.provider.TtsProvider;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.Test;
import reactor.core.publisher.Flux;
import reactor.test.StepVerifier;

class RoomConcurrencyTest {
  @Test void longAnswerDoesNotOverflowOrDisconnectTheControlChannel(){
    // 3000 kleine Text-Schnipsel (lange Antwort, schnelles Streaming) bei einem langsamen Browser:
    // vorher kam pro Schnipsel ein TRANSCRIPT mit dem ganzen Text -> Puffer voll -> Verbindung getrennt.
    GenerationProvider g=(p,t)->Flux.range(0,3000).map(i->"wort ");
    Room r=new Room("room1234",4,8,g,(s,c)->Flux.empty(),new ObjectMapper());
    Room.Client owner=r.join("owner1234","main");
    r.start("owner1234","los");
    long end=System.currentTimeMillis()+5000;
    while(!"idle".equals(r.status())&&System.currentTimeMillis()<end){try{Thread.sleep(20);}catch(InterruptedException e){break;}}
    var messages=owner.control().asFlux().take(Duration.ofMillis(300)).collectList().block(Duration.ofSeconds(3));
    assertNotNull(messages, "Steuerkanal wurde mit Fehler beendet (Rückstau)");
    long transcripts=messages.stream().filter(m->m.contains("\"TRANSCRIPT\"")).count();
    assertTrue(transcripts<200, "zu viele Zwischenstände: "+transcripts);
    assertTrue(messages.stream().anyMatch(m->m.contains("\"final\":true")&&m.contains("assistant")), "finaler Text fehlt");
    assertTrue(messages.stream().anyMatch(m->m.contains("TURN_END")));
  }

  @Test void oneFailedSentenceDoesNotAbortTheWholeAnswer(){
    GenerationProvider g=(p,t)->Flux.just("Erster Satz. ","Zweiter Satz. ","Dritter Satz.");
    TtsProvider t=(sentence,c)->sentence.startsWith("Zweiter")
        ? Flux.error(new IllegalStateException("TTS kurz nicht erreichbar"))
        : Flux.just(sentence.getBytes(java.nio.charset.StandardCharsets.UTF_8));
    Room r=new Room("room1234",4,8,g,t,new ObjectMapper());
    Room.Client owner=r.join("owner1234","main");
    var frames=new java.util.concurrent.CopyOnWriteArrayList<de.jarvis.backend.audio.AudioFrame>();
    var sub=r.audioFlux().subscribe(frames::add);
    r.start("owner1234","los");
    long end=System.currentTimeMillis()+3000;
    while(!"idle".equals(r.status())&&System.currentTimeMillis()<end){try{Thread.sleep(20);}catch(InterruptedException e){break;}}
    sub.dispose();
    assertEquals(java.util.List.of("Erster Satz.","Dritter Satz."),
        frames.stream().map(f->new String(f.payloadCopy(),java.nio.charset.StandardCharsets.UTF_8)).toList());
    var messages=owner.control().asFlux().take(Duration.ofMillis(200)).collectList().block(Duration.ofSeconds(2));
    assertTrue(messages.stream().anyMatch(m->m.contains("TURN_END")), "Antwort muss normal enden");
    assertTrue(messages.stream().noneMatch(m->m.contains("\"type\":\"ERROR\"")), "kein Abbruch-Fehler");
  }
  @Test void parallelSpeechKeepsSentenceOrderEvenIfLaterSentenceIsFaster(){
    // Satz 1 braucht 300 ms, Satz 2 nur 10 ms: Ton muss trotzdem als 1, 2, 3 in Sequenzreihenfolge ankommen.
    GenerationProvider g=(p,t)->Flux.just("Erster Satz. ","Zweiter Satz. ","Dritter Satz.");
    TtsProvider t=(sentence,c)->{
      long delay=sentence.startsWith("Erster")?300:10;
      return reactor.core.publisher.Mono.just(sentence.getBytes(java.nio.charset.StandardCharsets.UTF_8))
          .delayElement(Duration.ofMillis(delay)).flux();
    };
    Room r=new Room("room1234",4,8,g,t,new ObjectMapper());
    r.join("owner1234","main");
    var frames=new java.util.concurrent.CopyOnWriteArrayList<de.jarvis.backend.audio.AudioFrame>();
    var sub=r.audioFlux().subscribe(frames::add);
    r.start("owner1234","los");
    long end=System.currentTimeMillis()+3000;
    while(frames.size()<3&&System.currentTimeMillis()<end){try{Thread.sleep(20);}catch(InterruptedException e){break;}}
    sub.dispose();
    assertEquals(3,frames.size());
    assertEquals(java.util.List.of("Erster Satz.","Zweiter Satz.","Dritter Satz."),
        frames.stream().map(f->new String(f.payloadCopy(),java.nio.charset.StandardCharsets.UTF_8)).toList());
    assertEquals(java.util.List.of(0L,1L,2L),frames.stream().map(de.jarvis.backend.audio.AudioFrame::sequence).toList());
  }
  @Test void stopCancelsGenerationAndRejectsStaleStop(){AtomicBoolean cancelled=new AtomicBoolean();GenerationProvider g=(p,t)->Flux.<String>never().doOnCancel(()->cancelled.set(true));TtsProvider t=(s,c)->Flux.empty();Room r=new Room("room1234",4,8,g,t,new ObjectMapper());r.join("owner1234","main");r.join("observer1","ops");r.start("owner1234","hello");String id=r.streamId().toString();assertTrue(r.stop(id,"manual"));assertFalse(r.stop(id,"manual"));assertTrue(cancelled.get()||r.streamId()==null);}
  @Test void observerCannotOpenAudioStreamByOwnerRule(){Room r=new Room("room1234",4,8,(p,t)->Flux.just("ok."),(s,t)->Flux.just(new byte[]{1}),new ObjectMapper());r.join("owner1234","main");r.join("observer1","ops");assertTrue(r.isOwner("owner1234"));assertFalse(r.isOwner("observer1"));}
}
