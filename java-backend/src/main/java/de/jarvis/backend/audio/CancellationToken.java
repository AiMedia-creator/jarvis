package de.jarvis.backend.audio;

import java.util.concurrent.atomic.AtomicBoolean;

public final class CancellationToken {
  private final AtomicBoolean cancelled = new AtomicBoolean();
  private volatile Thread worker;
  public void bind(Thread thread) { worker = thread; if (cancelled.get()) thread.interrupt(); }
  public boolean isCancelled() { return cancelled.get(); }
  public boolean cancel() { boolean changed=cancelled.compareAndSet(false,true); Thread t=worker; if(t!=null)t.interrupt(); return changed; }
  public void checkpoint() { if (isCancelled()) throw new CancellationException(); }
  public static final class CancellationException extends RuntimeException { public CancellationException(){super("cancelled");} }
}
