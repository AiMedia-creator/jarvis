package de.jarvis.backend.audio;

import static org.junit.jupiter.api.Assertions.*;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class AudioRingBufferTest {
  @Test void purgeInvalidatesStaleFrames(){var b=new AudioRingBuffer(4);var id=UUID.randomUUID();long e=b.epoch();assertTrue(b.offer(new AudioFrame(id,e,1,"audio/wav",new byte[]{1})));long next=b.purge();assertTrue(next>e);assertNull(b.poll());assertFalse(b.offer(new AudioFrame(id,e,2,"audio/wav",new byte[]{2})));assertTrue(b.offer(new AudioFrame(id,next,3,"audio/wav",new byte[]{3})));assertEquals(3,b.poll().sequence());}
  @Test void cancellationInterruptsBoundWorker(){var token=new CancellationToken();var interrupted=new boolean[1];Thread t=new Thread(()->{token.bind(Thread.currentThread());try{Thread.sleep(5000);}catch(InterruptedException x){interrupted[0]=true;}});t.start();token.cancel();try{t.join(500);}catch(InterruptedException ignored){}assertTrue(token.isCancelled());assertTrue(interrupted[0]);}
}
